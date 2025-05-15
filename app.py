import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta, date

# --- Streamlit Setup ---
st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("🪙 Idea Gold Scheduler – Fair & Stable")

# --- Session State Defaults & Reset ---
defaults = {
    "shifts": [],
    "rotators": [],
    "leaves": [],
    "extra_oncalls": {},
    "weights": {},
    "nf_juniors": [],
    "nf_seniors": [],
    "start_date": date.today(),
    "end_date": date.today() + timedelta(days=27),
    "min_gap": 2,
    "nf_block_length": 5,
    "thur_weekend": {},
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

if st.button("🔁 Reset All Data"):
    st.session_state.clear()
    st.experimental_rerun()

# --- Shift Template Input ---
with st.expander("⚙️ Shift Templates"):
    col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
    shift_label = col1.text_input("Shift Label (e.g., ER1)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    night_float = col3.checkbox("Night Float")
    thur_weekend = col4.checkbox("Thursday Night = Weekend")

    if st.button("Add Shift"):
        if not shift_label.strip():
            st.error("Shift label cannot be empty.")
        else:
            base_label = shift_label.strip()
            existing_labels = [s['label'] for s in st.session_state.shifts]
            count = 1
            unique_label = base_label
            while unique_label in existing_labels:
                count += 1
                unique_label = f"{base_label} #{count}"

            st.session_state.shifts.append({
                "label": unique_label,
                "role": role,
                "night_float": night_float,
                "thur_weekend": thur_weekend
            })

    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# --- Participants Input ---
with st.expander("👥 Participants"):
    use_demo = st.checkbox("Use Demo Participants", True)
    if use_demo:
        juniors = ["Alice", "Bob", "Charlie", "Dina"]
        seniors = ["Eli", "Fay", "Gina", "Hank"]
    else:
        juniors = [x.strip() for x in st.text_area("Juniors").splitlines() if x.strip()]
        seniors = [x.strip() for x in st.text_area("Seniors").splitlines() if x.strip()]

    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# --- Night Float Eligibility ---
with st.expander("🌙 Night Float Eligibility", expanded=False):
    nf_j = st.multiselect(
        "NF-Eligible Juniors",
        options=st.session_state.juniors,
        default=st.session_state.nf_juniors,
        key="nf_juniors_select"
    )
    nf_s = st.multiselect(
        "NF-Eligible Seniors",
        options=st.session_state.seniors,
        default=st.session_state.nf_seniors,
        key="nf_seniors_select"
    )
    # save back into session_state
    st.session_state.nf_juniors = nf_j
    st.session_state.nf_seniors = nf_s
    
# --- Extra Oncalls ---
with st.expander("⚖️ Extra Oncalls"):
    for name in juniors + seniors:
        st.session_state.extra_oncalls[name] = st.number_input(
            f"Extra oncalls for {name}", 0, 10, st.session_state.extra_oncalls.get(name, 0)
        )

# --- Date Range & Rules ---
st.subheader("📅 Schedule Settings")
st.session_state.start_date = st.date_input("Start Date", st.session_state.start_date)
st.session_state.end_date = st.date_input("End Date", st.session_state.end_date)
if st.session_state.start_date > st.session_state.end_date:
    st.error("Start date must be before end date.")
    st.stop()

st.session_state.min_gap = st.slider("Minimum Days Between Shifts", 0, 7, st.session_state.min_gap)
st.session_state.nf_block_length = st.slider("Night Float Block Length", 1, 10, st.session_state.nf_block_length)

# --- Leaves & Rotators ---
with st.expander("✈️ Leaves"):
    leave_name = st.selectbox("Leave Name", [""] + juniors + seniors)
    leave_from, leave_to = st.date_input("Leave Period", [st.session_state.start_date, st.session_state.start_date])
    if st.button("Add Leave") and leave_name:
        if leave_from <= leave_to:
            st.session_state.leaves.append((leave_name, leave_from, leave_to))

with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Rotator Name", [""] + juniors + seniors)
    rot_from, rot_to = st.date_input("Rotator Period", [st.session_state.start_date, st.session_state.start_date])
    if st.button("Add Rotator") and rot_name:
        if rot_from <= rot_to:
            st.session_state.rotators.append((rot_name, rot_from, rot_to))

# --- Helper Functions ---
def is_weekend(date, shift):
    return date.weekday() in [4, 5] or (date.weekday() == 3 and shift.get("thur_weekend", False))

def on_leave(person, date):
    return any(name == person and start <= date <= end for name, start, end in st.session_state.leaves)

def is_active_rotator(person, date):
    rotator_windows = [(name, start, end) for name, start, end in st.session_state.rotators]
    person_windows = [w for w in rotator_windows if w[0] == person]
    if not person_windows:
        return True  # Not a rotator, always active
    return any(start <= date <= end for _, start, end in person_windows)

# --- Schedule Building Logic ---
def build_schedule():
    shifts = st.session_state.shifts
    start_date = st.session_state.start_date
    end_date = st.session_state.end_date
    days = pd.date_range(start_date, end_date)

    juniors = st.session_state.juniors
    seniors = st.session_state.seniors
    pool = juniors + seniors

    # Compute leave bonus and active days
    total_span = (end_date - start_date).days + 1
    leave_days = {p: sum((min(end, end_date) - max(start, start_date)).days + 1 for name, start, end in st.session_state.leaves if name == p) for p in pool}

    active_days = {p: sum(1 for d in days if not on_leave(p, d.date()) and is_active_rotator(p, d.date())) for p in pool}

    weighted_days = {p: active_days[p] * (1 + leave_days[p] / total_span) * (1 + st.session_state.extra_oncalls.get(p, 0)) for p in pool}

    total_weight = sum(weighted_days.values()) or 1

    # Slot counts
    shift_labels = [s["label"] for s in shifts if not s["night_float"]]
    slot_counts = {lbl: 0 for lbl in shift_labels}
    weekend_slot_counts = {lbl: 0 for lbl in shift_labels}

    for day in days:
        for s in shifts:
            if s["night_float"]:
                continue
            slot_counts[s["label"]] += 1
            if is_weekend(day, s):
                weekend_slot_counts[s["label"]] += 1

    # Expected quotas
    expected = {p: {lbl: {'total': slot_counts[lbl] * weighted_days[p] / total_weight, 'weekend': weekend_slot_counts[lbl] * weighted_days[p] / total_weight} for lbl in shift_labels} for p in pool}

    # Initialize stats
    stats = {p: {lbl: {'total': 0, 'weekend': 0} for lbl in shift_labels} for p in pool}
    last_assigned = {p: None for p in pool}

    # NF assignments
    nf_assignments = {}
    for s in [shift for shift in shifts if shift["night_float"]]:
        nf_pool = st.session_state.nf_juniors if s["role"] == "Junior" else st.session_state.nf_seniors
        for i, day in enumerate(days):
            block_idx = i // st.session_state.nf_block_length
            if nf_pool:
                person = nf_pool[block_idx % len(nf_pool)]
                if not on_leave(person, day.date()):
                    nf_assignments[(day.date(), s["label"])] = person

    # Daily scheduling
    schedule = []
    unfilled_slots = []
    for day in days:
        row = {"Date": day.date(), "Day": day.strftime("%A")}
        nf_today = {p for (d, lbl), p in nf_assignments.items() if d == day.date()}

        for s in shifts:
            lbl = s["label"]
            if s["night_float"]:
                row[lbl] = nf_assignments.get((day.date(), lbl), "Unfilled")
                continue

            role_pool = juniors if s["role"] == "Junior" else seniors
            candidates = [p for p in role_pool if p not in nf_today and not on_leave(p, day.date()) and is_active_rotator(p, day.date()) and (last_assigned[p] is None or (day - last_assigned[p]).days >= st.session_state.min_gap)]

            if not candidates:
                row[lbl] = "Unfilled"
                unfilled_slots.append((day.date(), lbl))
                continue

            # Lexicographic deficit scoring (weekend priority)
            def deficit_score(p):
                weekend_def = expected[p][lbl]['weekend'] - stats[p][lbl]['weekend']
                total_def = expected[p][lbl]['total'] - stats[p][lbl]['total']
                return (weekend_def, total_def)

            best = max(sorted(candidates), key=lambda p: deficit_score(p))
            row[lbl] = best
            stats[best][lbl]['total'] += 1
            if is_weekend(day, s):
                stats[best][lbl]['weekend'] += 1
            last_assigned[best] = day

        schedule.append(row)

    df_schedule = pd.DataFrame(schedule)
    df_summary = pd.DataFrame([{**{"Name": p}, **{f"{lbl}_assigned": stats[p][lbl]['total'] for lbl in shift_labels}, **{f"{lbl}_expected": round(expected[p][lbl]['total'], 1) for lbl in shift_labels}} for p in pool])
    df_unfilled = pd.DataFrame(unfilled_slots, columns=["Date", "Shift"])

    return df_schedule, df_summary, df_unfilled

# --- Generate Schedule Button ---
if st.button("🚀 Generate Schedule"):
    df, summary, unfilled = build_schedule()
    st.success("✅ Schedule generated!")
    st.dataframe(df)
    st.subheader("📊 Assignment Summary")
    st.dataframe(summary)
    if not unfilled.empty:
        st.warning("⚠️ Unfilled Slots Detected")
        st.dataframe(unfilled)
    st.download_button("Download Schedule CSV", df.to_csv(index=False), "schedule.csv")
    st.download_button("Download Summary CSV", summary.to_csv(index=False), "summary.csv")
    if not unfilled.empty:
        st.download_button("Download Unfilled Slots CSV", unfilled.to_csv(index=False), "unfilled.csv")
