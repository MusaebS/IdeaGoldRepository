import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# INITIALIZATION
if "shifts" not in st.session_state:
    st.session_state.shifts = []
if "rotators" not in st.session_state:
    st.session_state.rotators = []
if "leaves" not in st.session_state:
    st.session_state.leaves = []
if "weights" not in st.session_state:
    st.session_state.weights = {}
if "extra_oncalls" not in st.session_state:
    st.session_state.extra_oncalls = {}

# SHIFT TEMPLATES
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")

    if st.button("Add Shift"):
        if not label.strip():
            st.warning("Label is required.")
        elif start >= end:
            st.warning("Start must be before end.")
        else:
            unique_label = label.strip()
            labels = [s["label"] for s in st.session_state.shifts]
            i = 2
            while unique_label in labels:
                unique_label = f"{label.strip()} #{i}"
                i += 1
            st.session_state.shifts.append({
                "label": unique_label,
                "role": role,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "night_float": nf
            })
    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# PARTICIPANTS
st.header("📝 Participants")
use_demo = st.checkbox("Use Demo Names", value=True)
if use_demo:
    juniors = ["Ali", "Reem", "Sara", "Omar", "Khalid", "Huda"]
    seniors = ["Dr. A", "Dr. B", "Dr. C", "Dr. D"]
else:
    juniors = st.text_area("Juniors (one per line)").splitlines()
    seniors = st.text_area("Seniors (one per line)").splitlines()

juniors = [j.strip() for j in juniors if j.strip()]
seniors = [s.strip() for s in seniors if s.strip()]

# NIGHT FLOAT
with st.expander("🛌 Night-Float Eligibility"):
    nf_juniors = st.multiselect("NF-Eligible Juniors", juniors, default=juniors)
    nf_seniors = st.multiselect("NF-Eligible Seniors", seniors, default=seniors)

# DATES
start_date = st.date_input("Start Date", datetime.today())
end_date = st.date_input("End Date", datetime.today() + timedelta(days=28))
min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
nf_block = st.slider("NF Block Length (days)", 1, 14, 5)

# ROTATORS & LEAVES
with st.expander("🔄 Rotators"):
    name = st.selectbox("Name", [""] + juniors + seniors, key="r_name")
    from_ = st.date_input("From", key="r_from")
    to_ = st.date_input("To", key="r_to")
    if st.button("Add Rotator"):
        if name and from_ <= to_:
            st.session_state.rotators.append((name, from_, to_))
    if st.session_state.rotators:
        st.table(pd.DataFrame(st.session_state.rotators, columns=["Name", "From", "To"]))

with st.expander("✈️ Leaves"):
    name = st.selectbox("Name", [""] + juniors + seniors, key="l_name")
    from_ = st.date_input("From", key="l_from")
    to_ = st.date_input("To", key="l_to")
    if st.button("Add Leave"):
        if name and from_ <= to_:
            st.session_state.leaves.append((name, from_, to_))
    if st.session_state.leaves:
        st.table(pd.DataFrame(st.session_state.leaves, columns=["Name", "From", "To"]))

# EXTRA ONCALLS & WEIGHTS
with st.expander("⚖️ Extra Oncalls & Ranks"):
    for name in juniors + seniors:
        st.session_state.extra_oncalls[name] = st.number_input(f"Extra Oncalls for {name}", min_value=0, value=0)
        st.session_state.weights[name] = st.selectbox(f"Rank of {name}", ["R1", "R2", "R3", "R4"], key=f"rank_{name}")

rank_weights = {"R1": 1.2, "R2": 1.0, "R3": 1.2, "R4": 1.0}
shift_type_weights = {"total": 1, "weekend": 2, "ER": 1, "Ward": 1}

# SCHEDULE ENGINE
if st.button("🚀 Generate Schedule"):
    shifts = st.session_state.shifts
    pool = juniors + seniors
    all_days = pd.date_range(start_date, end_date)

    def on_leave(name, day):
        for (n, f, t) in st.session_state.leaves:
            if n == name and f <= day.date() <= t:
                return True
        return False

    def is_rotator(name, day):
        for (n, f, t) in st.session_state.rotators:
            if n == name and f <= day.date() <= t:
                return True
        return False

    def active_days(name):
        total = sum(1 for d in all_days if not on_leave(name, d))
        if any(n == name for (n, _, _) in st.session_state.rotators):
            for (n, f, t) in st.session_state.rotators:
                if n == name:
                    return (t - f).days + 1
        return total

    weighted_days = {i: rank_weights[st.session_state.weights[i]] * active_days(i) for i in pool}
    total_weighted = sum(weighted_days.values())

    # Count shift types
    total_slots = {"total": 0, "weekend": 0, "ER": 0, "Ward": 0}
    for d in all_days:
        for s in shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if d.weekday() >= 4:
                    total_slots["weekend"] += 1
                if "ER" in s["label"]:
                    total_slots["ER"] += 1
                elif "Ward" in s["label"]:
                    total_slots["Ward"] += 1

    expected = {
        i: {
            k: total_slots[k] * (weighted_days[i] / total_weighted) + st.session_state.extra_oncalls[i]
            if k == "total" else
            total_slots[k] * (weighted_days[i] / total_weighted)
            for k in total_slots
        } for i in pool
    }

    stats = {i: {k: 0 for k in total_slots} for i in pool}
    last_assigned = {i: None for i in pool}
    schedule = []

    for d in all_days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        assigned_today = set()
        for s in sorted([s for s in shifts if not s["night_float"]], key=lambda x: x["start"]):
            label = s["label"]
            role = s["role"]
            shift_type = "ER" if "ER" in label else "Ward"
            candidates = [p for p in pool
                if not on_leave(p, d)
                and p not in assigned_today
                and (last_assigned[p] is None or (d - last_assigned[p]).days >= min_gap)
                and (p in juniors if role == "Junior" else p in seniors)]
            if not candidates:
                row[label] = "Unavailable"
                continue
            deficit = {
                p: sum(
                    shift_type_weights[k] * (expected[p][k] - stats[p][k])
                    for k in total_slots
                ) for p in candidates
            }
            chosen = max(deficit, key=lambda x: (deficit[x], x))
            row[label] = chosen
            stats[chosen]["total"] += 1
            stats[chosen][shift_type] += 1
            if d.weekday() >= 4:
                stats[chosen]["weekend"] += 1
            last_assigned[chosen] = d
            assigned_today.add(chosen)
        schedule.append(row)

    df = pd.DataFrame(schedule)
    st.dataframe(df)

    summary = pd.DataFrame.from_dict({
        name: {**stats[name], **expected[name]}
        for name in pool
    }, orient="index").reset_index().rename(columns={"index": "Name"})
    st.subheader("📊 Summary")
    st.dataframe(summary)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download CSV", csv, "schedule.csv", "text/csv")
