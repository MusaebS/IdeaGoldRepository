import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

if st.button("🔁 Reset All"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.experimental_rerun()

# SESSION STATE INIT
for k in ["shifts", "rotators", "leaves", "weights", "extra_oncalls"]:
    st.session_state.setdefault(k, [] if k in ["shifts", "rotators", "leaves"] else {})

# SHIFT TEMPLATE
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", datetime.strptime("20:00", "%H:%M").time())
    is_nf = col5.checkbox("Night Float")
    thur_weekend = st.checkbox("Thursday = Weekend")

    if st.button("Add Shift", key="add_shift"):
        if label.strip() and start < end:
            names = [s["label"] for s in st.session_state.shifts]
            base = label.strip()
            i = 2
            while base in names:
                base = f"{label.strip()} #{i}"
                i += 1
            st.session_state.shifts.append({"label": base, "role": role, "start": start, "end": end, "night_float": is_nf, "thur_weekend": thur_weekend})
        else:
            st.warning("Please enter a valid label and ensure start < end")

if st.session_state.shifts:
    st.dataframe(pd.DataFrame(st.session_state.shifts))

# PARTICIPANTS
st.header("🧑‍⚕️ Participants")
demo = st.checkbox("Use Demo Names", True)

if demo:
    juniors = ["Amanda", "Emily", "Jessica", "Laura", "Linda", "Mary", "Nicole", "Ashley", "Sarah", "Sophia"]
    seniors = ["Andrew", "Daniel", "David", "James", "Joseph", "Michael", "Robert", "John", "Thomas", "William"]
else:
    j_raw = st.text_area("Juniors")
    s_raw = st.text_area("Seniors")
    juniors = [x.strip() for x in j_raw.splitlines() if x.strip()]
    seniors = [x.strip() for x in s_raw.splitlines() if x.strip()]

# NF ELIGIBILITY
with st.expander("🛌 Night Float Eligibility"):
    nf_j = st.multiselect("NF Juniors", juniors)
    nf_s = st.multiselect("NF Seniors", seniors)

# DATE AND RULES
col1, col2 = st.columns(2)
start_date = col1.date_input("Start Date", datetime.today())
end_date = col2.date_input("End Date", datetime.today() + timedelta(days=27))
if start_date > end_date:
    st.stop()

min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
nf_block_length = st.slider("Night Float Block Length (days)", 1, 14, 7)
st.session_state["nf_block_length"] = nf_block_length

# ROTATORS
with st.expander("🔄 Rotators"):
    r_name = st.selectbox("Name", [""] + juniors + seniors, key="rname")
    r_from = st.date_input("From", key="rfrom")
    r_to = st.date_input("To", key="rto")
    if st.button("Add Rotator"):
        if r_name and r_from <= r_to:
            st.session_state.rotators.append((r_name, r_from, r_to))
    if st.session_state.rotators:
        st.table(pd.DataFrame(st.session_state.rotators, columns=["Name", "From", "To"]))

# LEAVES
with st.expander("✈️ Leaves"):
    l_name = st.selectbox("Name", [""] + juniors + seniors, key="lname")
    l_from = st.date_input("From", key="lfrom")
    l_to = st.date_input("To", key="lto")
    if st.button("Add Leave"):
        if l_name and l_from <= l_to:
            st.session_state.leaves.append((l_name, l_from, l_to))
    if st.session_state.leaves:
        st.table(pd.DataFrame(st.session_state.leaves, columns=["Name", "From", "To"]))

# SHIFT LOAD BIAS & ONCALLS
with st.expander("⚖️ Shift Load Bias & Extra Oncalls"):
    for name in juniors + seniors:
        st.session_state.weights[name] = st.slider(f"Bias: {name}", 0.8, 1.5, 1.0, step=0.1)
        st.session_state.extra_oncalls[name] = st.number_input(f"Extra Oncalls: {name}", value=0, step=1, key=f"xtra_{name}")
# ... previous code remains unchanged

# UTILITY FUNCTIONS
def on_leave(name, day):
    return any(n == name and lo <= day.date() <= hi for (n, lo, hi) in st.session_state.leaves)

def is_rotator(name):
    return any(n == name for (n, _, _) in st.session_state.rotators)

def is_active_rotator(name, day):
    for (n, lo, hi) in st.session_state.rotators:
        if n == name:
            return lo <= day.date() <= hi
    return False

def is_weekend(day, shift):
    return day.weekday() in [4, 5] or (day.weekday() == 3 and shift.get("thur_weekend", False))

# MAIN SCHEDULER
def build_schedule():
    all_days = pd.date_range(start_date, end_date)
    pool = juniors + seniors
    stats = {p: {"total": 0, "weekend": 0} for p in pool}
    expected = {}
    history = {p: [] for p in pool}
    last_assigned = {p: None for p in pool}
    total_slots = {"total": 0, "weekend": 0}

    weighted_days = {p: st.session_state.weights.get(p, 1.0) * len([d for d in all_days if not on_leave(p, d)]) for p in pool}
    total_weighted = sum(weighted_days.values())

    for p in pool:
        expected[p] = {
            "total": 0,
            "weekend": 0
        }

    for d in all_days:
        for s in st.session_state.shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if is_weekend(d, s):
                    total_slots["weekend"] += 1

    for p in pool:
        ratio = weighted_days[p] / total_weighted if total_weighted else 0
        expected[p]["total"] = total_slots["total"] * ratio + st.session_state.extra_oncalls.get(p, 0)
        expected[p]["weekend"] = total_slots["weekend"] * ratio

    # NF assignment
    nf_assignments = {}
    for s in [s for s in st.session_state.shifts if s["night_float"]]:
        nf_pool = nf_seniors if s["role"] == "Senior" else nf_juniors
        label = s["label"]
        nf_map = {}
        d_idx = 0
        idx = 0
        if not nf_pool:
            continue
        while d_idx < len(all_days):
            person = nf_pool[idx % len(nf_pool)]
            block = all_days[d_idx:d_idx + nf_block_length]
            for d in block:
                if not on_leave(person, d):
                    nf_map[d.date()] = person
                    last_assigned[person] = d
            d_idx += nf_block_length
            idx += 1
        nf_assignments[label] = nf_map

    schedule = []
    unfilled = []
    for d in all_days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        assigned_today = set()
        nf_people_today = set()

        for s in [s for s in st.session_state.shifts if s["night_float"]]:
            label = s["label"]
            assignee = nf_assignments.get(label, {}).get(d.date(), "Unavailable")
            row[label] = assignee
            if assignee != "Unavailable":
                nf_people_today.add(assignee)

        for s in [s for s in st.session_state.shifts if not s["night_float"]]:
            label = s["label"]
            role = s["role"]
            wknd = is_weekend(d, s)
            candidates = [
                p for p in pool
                if p not in nf_people_today and
                   (p in juniors if role == "Junior" else p in seniors) and
                   not on_leave(p, d) and
                   p not in assigned_today and
                   (not is_rotator(p) or is_active_rotator(p, d)) and
                   (last_assigned[p] is None or (d - last_assigned[p]).days >= min_gap)
            ]
            if not candidates:
                row[label] = "Unavailable"
                unfilled.append((d.date(), label))
                continue
            random.shuffle(candidates)
            deficits = {
                p: sum((expected[p][k] - stats[p][k]) for k in ["total", "weekend"])
                for p in candidates
            }
            max_deficit = max(deficits.values())
            top = [p for p in candidates if deficits[p] == max_deficit]
            chosen = random.choice(top)
            row[label] = chosen
            stats[chosen]["total"] += 1
            if wknd:
                stats[chosen]["weekend"] += 1
            last_assigned[chosen] = d
            assigned_today.add(chosen)
            history[chosen].append(d.date())

        schedule.append(row)

    return pd.DataFrame(schedule), stats, expected, unfilled

# GENERATE BUTTON
if st.button("🚀 Generate Schedule"):
    df, stats, expected, unfilled = build_schedule()
    st.subheader("📅 Schedule")
    st.dataframe(df)

    st.subheader("📊 Summary")
    merged = pd.DataFrame([{
        "Name": p,
        **stats[p],
        "Expected Total": round(expected[p]["total"], 1),
        "Expected Weekend": round(expected[p]["weekend"], 1)
    } for p in stats])
    st.dataframe(merged)

    if unfilled:
        st.subheader("⚠️ Unfilled Slots")
        st.dataframe(pd.DataFrame(unfilled, columns=["Date", "Shift"]))

    st.download_button("📥 Download Schedule CSV", df.to_csv(index=False).encode("utf-8"), "schedule.csv")
    st.download_button("📥 Download Summary CSV", merged.to_csv(index=False).encode("utf-8"), "summary.csv")
