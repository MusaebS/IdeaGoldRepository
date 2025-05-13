import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

if st.button("Reset All", type="primary"):
    st.session_state.clear()
    st.experimental_rerun()

# Initialize session state keys
for key in ["shifts", "rotators", "leaves", "weights", "extra_oncalls"]:
    if key not in st.session_state:
        st.session_state[key] = {} if key in ["weights", "extra_oncalls"] else []

# --- SHIFT TEMPLATES ---
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5, col6 = st.columns([2, 1, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)", value="")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")
    thur_flag = col6.checkbox("Thursday = Weekend", key="thur_flag")

    if st.button("Add Shift", key="add_shift"):
        base = label.strip()
        if not base or start == end:
            st.warning("⚠️ Invalid shift definition.")
        else:
            existing = [s["label"] for s in st.session_state.shifts]
            count = 2
            unique = base
            while unique in existing:
                unique = f"{base} #{count}"
                count += 1
            st.session_state.shifts.append({
                "label": unique,
                "role": role,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "night_float": nf,
                "thur_weekend": thur_flag
            })

    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# --- PARTICIPANTS ---
with st.expander("📝 Participants", expanded=False):
    use_demo = st.checkbox("Use Demo Names", value=True)
    if use_demo:
        junior_list = ["Ashley", "Amanda", "Linda", "Nicole", "Emily", "Jessica", "Mary", "Elizabeth", "Sarah", "Laura", "Sophia"]
        senior_list = ["John", "Robert", "Thomas", "Andrew", "Daniel", "David", "James", "Joseph", "Michael", "William"]
    else:
        juniors_raw = st.text_area("Juniors (one per line)")
        seniors_raw = st.text_area("Seniors (one per line)")
        junior_list = list(dict.fromkeys([x.strip() for x in juniors_raw.splitlines() if x.strip()]))
        senior_list = list(dict.fromkeys([x.strip() for x in seniors_raw.splitlines() if x.strip()]))

    if not junior_list or not senior_list:
        st.error("❌ Please provide both junior and senior participant lists.")
        st.stop()

# --- NIGHT FLOAT ELIGIBILITY ---
with st.expander("🛌 Night Float Eligibility", expanded=False):
    nf_j = st.multiselect("NF-Eligible Juniors", junior_list, default=junior_list)
    nf_s = st.multiselect("NF-Eligible Seniors", senior_list, default=senior_list)
    st.session_state["nf_juniors"] = nf_j
    st.session_state["nf_seniors"] = nf_s

# --- DATE RANGE ---
st.subheader("Date Range")
col1, col2 = st.columns(2)
start_date = col1.date_input("Start Date", value=date.today())
end_date = col2.date_input("End Date", value=date.today() + timedelta(days=27))
if start_date > end_date:
    st.error("❌ End date must be after start date.")
    st.stop()

# --- RULES ---
st.subheader("⏱️ Rules")
col1, col2 = st.columns(2)
min_gap = col1.slider("Minimum gap between shifts (days)", 0, 7, 2)
nf_block = col2.slider("Night Float Block Length (days)", 1, 14, 7)
st.session_state["nf_block_length"] = nf_block

# --- ROTATORS ---
def show_table(items, fields):
    if not items:
        st.markdown("*(None yet)*")
        return
    df = pd.DataFrame([{f: item[i] for i, f in enumerate(fields)} for item in items])
    st.dataframe(df, use_container_width=True)

with st.expander("🔄 Rotators", expanded=False):
    r_name = st.selectbox("Name", [""] + junior_list + senior_list, key="rot_name")
    r_from = st.date_input("From", key="rot_from")
    r_to = st.date_input("To", key="rot_to")
    if st.button("Add Rotator", key="add_rotator"):
        if r_name and r_from <= r_to:
            st.session_state["rotators"].append((r_name, r_from, r_to))
    show_table(st.session_state["rotators"], ["Name", "From", "To"])

# --- LEAVES ---
with st.expander("✈️ Leaves", expanded=False):
    lv_name = st.selectbox("Name", [""] + junior_list + senior_list, key="lv_name")
    lv_from = st.date_input("From", key="lv_from")
    lv_to = st.date_input("To", key="lv_to")
    if st.button("Add Leave", key="add_leave"):
        if lv_name and lv_from <= lv_to:
            st.session_state["leaves"].append((lv_name, lv_from, lv_to))
    show_table(st.session_state["leaves"], ["Name", "From", "To"])

# --- EXTRA ONCALLS & BIAS ---
with st.expander("⚖️ Shift Load Bias & Extra Oncalls", expanded=False):
    for name in junior_list + senior_list:
        col1, col2 = st.columns([3, 2])
        with col1:
            st.session_state["weights"][name] = st.slider(f"Bias for {name}", 0.8, 1.5, 1.0, step=0.1, key=f"w_{name}")
        with col2:
            st.session_state["extra_oncalls"][name] = st.number_input(f"Extra Oncalls for {name}", 0, 10, 0, key=f"e_{name}")

# Continued: Schedule Engine, Summaries, Output

# --- Utilities ---
def is_weekend(d, shift):
    if shift.get("thur_weekend") and d.weekday() == 3:
        return True
    return d.weekday() in [4, 5]  # Friday, Saturday

def on_leave(name, d):
    for (n, f, t) in st.session_state.get("leaves", []):
        if name == n and f <= d <= t:
            return True
    return False

def is_active_rotator(name, d):
    for (n, f, t) in st.session_state.get("rotators", []):
        if name == n and f <= d <= t:
            return True
    return False

def active_days(name, all_days):
    days = [d for d in all_days if not on_leave(name, d)]
    for (n, f, t) in st.session_state.get("rotators", []):
        if name == n:
            days = [d for d in days if f <= d <= t]
            break
    return days

# --- Scheduler ---
def build_schedule():
    shifts = st.session_state.get("shifts", [])
    juniors = st.session_state.get("juniors", [])
    seniors = st.session_state.get("seniors", [])
    nf_j = st.session_state.get("nf_juniors", [])
    nf_s = st.session_state.get("nf_seniors", [])
    nf_block_length = st.session_state.get("nf_block_length", 5)
    min_gap = st.session_state.get("min_gap", 2)
    all_days = pd.date_range(st.session_state.start_date, st.session_state.end_date)

    if not juniors or not seniors:
        st.error("Missing participants.")
        return pd.DataFrame()

    shift_defs = shifts
    shift_labels = [s["label"] for s in shift_defs]

    # Count total slots
    total_slots = {"total": 0, "weekend": 0}
    for d in all_days:
        for s in shift_defs:
            if not s.get("night_float"):
                total_slots["total"] += 1
                if is_weekend(d, s):
                    total_slots["weekend"] += 1

    # Prepare expectations
    pool = list(set(juniors + seniors))
    expected = {}
    stats = {}
    weighted = {}
    total_weighted = 0
    for p in pool:
        days = active_days(p, all_days)
        active = len(days)
        w = st.session_state.extra_oncalls.get(p, 0) + 1.0
        weighted[p] = active * w
        total_weighted += weighted[p]

    if total_weighted == 0:
        st.error("All participants unavailable.")
        return pd.DataFrame()

    for p in pool:
        ratio = weighted[p] / total_weighted
        expected[p] = {
            "total": round(total_slots["total"] * ratio, 1),
            "weekend": round(total_slots["weekend"] * ratio, 1),
        }
        stats[p] = {"total": 0, "weekend": 0}

    # NF Assignment
    nf_assignments = {}
    last_assigned = {}
    for s in shift_defs:
        if s.get("night_float"):
            pool = nf_s if s["role"] == "Senior" else nf_j
            assigned = {}
            d_idx = 0
            while d_idx < len(all_days):
                for p in pool:
                    block = all_days[d_idx:d_idx+nf_block_length]
                    for d in block:
                        if on_leave(p, d):
                            continue
                        assigned[d.date()] = p
                        last_assigned[p] = d
                    d_idx += nf_block_length
            nf_assignments[s["label"]] = assigned

    # Regular Assignment
    rows = []
    unfilled = []
    for d in all_days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        assigned_today = set()
        nf_today = set()
        for s in shift_defs:
            if s.get("night_float"):
                person = nf_assignments.get(s["label"], {}).get(d.date(), "Unavailable")
                row[s["label"]] = person
                if person != "Unavailable":
                    nf_today.add(person)
                    last_assigned[person] = d

        for s in shift_defs:
            if s.get("night_float"):
                continue
            label = s["label"]
            role = s["role"]
            pool = juniors if role == "Junior" else seniors
            candidates = []
            for p in pool:
                if (
                    p not in nf_today
                    and p not in assigned_today
                    and not on_leave(p, d)
                    and (p not in last_assigned or (d - last_assigned[p]).days >= min_gap)
                    and (not is_active_rotator(p, d) if p in pool else True)
                ):
                    candidates.append(p)

            if not candidates:
                row[label] = "Unavailable"
                unfilled.append((d.date(), label))
                continue

            # Compute deficit
            scores = {}
            for c in candidates:
                scores[c] = (
                    expected[c]["total"] - stats[c]["total"]
                    + 2 * (expected[c]["weekend"] - stats[c]["weekend"])
                )
            top = max(scores.values())
            top_names = [p for p in candidates if scores[p] == top]
            chosen = random.choice(top_names)
            row[label] = chosen
            stats[chosen]["total"] += 1
            if is_weekend(d, s):
                stats[chosen]["weekend"] += 1
            last_assigned[chosen] = d
            assigned_today.add(chosen)

        rows.append(row)

    df = pd.DataFrame(rows)
    return df, stats, expected, unfilled

# --- Output ---
if st.button("🚀 Generate Schedule"):
    df, stats, expected, unfilled = build_schedule()
    if df.empty:
        st.warning("⚠️ No valid schedule generated.")
    else:
        st.success("✅ Schedule generated!")
        st.dataframe(df, use_container_width=True)
        st.download_button("📥 Download Schedule CSV", df.to_csv(index=False), f"{datetime.now():%Y-%m-%dT%H-%M}_schedule.csv")

        summary = pd.DataFrame([
            {
                "Name": p,
                "Total Assigned": stats[p]["total"],
                "Weekend Assigned": stats[p]["weekend"],
                "Expected Total": expected[p]["total"],
                "Expected Weekend": expected[p]["weekend"]
            }
            for p in stats
        ])

        with st.expander("📊 Summary by Person"):
            st.dataframe(summary)
            st.download_button("📥 Download Summary CSV", summary.to_csv(index=False), f"{datetime.now():%Y-%m-%dT%H-%M}_export.csv")

        if unfilled:
            with st.expander("⚠️ Unfilled Assignments"):
                st.write(pd.DataFrame(unfilled, columns=["Date", "Shift"]))
