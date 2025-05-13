import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# SHIFT TEMPLATE INPUT
if "shifts" not in st.session_state:
    st.session_state.shifts = []

with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")

    if st.button("Add Shift", key="add_shift_btn"):
        if label.strip() and start != end:
            base_label = label.strip()
            existing = [s["label"] for s in st.session_state.shifts]
            count = 2
            unique_label = base_label
            while unique_label in existing:
                unique_label = f"{base_label} #{count}"
                count += 1
            st.session_state.shifts.append({
                "label": unique_label,
                "role": role,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "night_float": nf
            })
        else:
            st.warning("⚠️ Please enter a label and make sure Start ≠ End.")

if st.session_state.shifts:
    st.table(pd.DataFrame(st.session_state.shifts))

# PARTICIPANTS
st.header("📝 Participants")
use_demo = st.checkbox("Use Demo Names", value=True)

if use_demo:
    junior_list = ["Ashley", "Amanda", "Linda", "Nicole"]
    senior_list = ["John", "Robert", "Thomas", "Andrew"]
else:
    junior_raw = st.text_area("Juniors (one per line)")
    senior_raw = st.text_area("Seniors (one per line)")
    junior_list = list({r.strip() for r in junior_raw.splitlines() if r.strip()})
    senior_list = list({r.strip() for r in senior_raw.splitlines() if r.strip()})

if not junior_list or not senior_list:
    st.warning("⚠️ Please enter at least one junior and one senior.")

# NIGHT FLOAT ELIGIBILITY
with st.expander("🛌 Night-Float Eligibility"):
    nf_juniors = st.multiselect("NF-Eligible Juniors", junior_list, default=junior_list)
    nf_seniors = st.multiselect("NF-Eligible Seniors", senior_list, default=senior_list)

# DATES AND RULES
start_date = st.date_input("Start Date", datetime.today())
end_date = st.date_input("End Date", datetime.today() + timedelta(days=27))

if start_date > end_date:
    st.error("❌ End date must be after or equal to start date.")

st.subheader("⏱️ Rules")
min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
nf_block_length = st.slider("Night-Float Block Length (days)", 1, 14, 7)
nf_style = st.radio("NF Distribution Style", ["Block", "One-by-one"], horizontal=True)

# ROTATORS & LEAVES
def _make_table(items, fields):
    if not items:
        st.write("*(None yet)*")
        return
    t = {f: [it[i] for it in items] for i, f in enumerate(fields)}
    st.table(pd.DataFrame(t))

with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Name", [""] + junior_list + senior_list, key="rot_n")
    rot_from = st.date_input("From", key="rot_f")
    rot_to = st.date_input("To", key="rot_t")
    if st.button("Add Rotator", key="add_rot_btn"):
        if rot_name and rot_from <= rot_to:
            st.session_state.setdefault("rotators", []).append((rot_name, rot_from, rot_to))
    _make_table(st.session_state.get("rotators", []), ["Name", "From", "To"])

with st.expander("✈️ Leaves"):
    lv_name = st.selectbox("Name", [""] + junior_list + senior_list, key="lv_n")
    lv_from = st.date_input("From", key="lv_f")
    lv_to = st.date_input("To", key="lv_t")
    if st.button("Add Leave", key="add_lv_btn"):
        if lv_name and lv_from <= lv_to:
            st.session_state.setdefault("leaves", []).append((lv_name, lv_from, lv_to))
    _make_table(st.session_state.get("leaves", []), ["Name", "From", "To"])

# AVAILABILITY CHECKER
def is_available(name, day, history, apply_gap=True):
    for (n, lo, hi) in st.session_state.get("leaves", []):
        if n == name and lo <= day.date() <= hi:
            return False
    for (n, lo, hi) in st.session_state.get("rotators", []):
        if n == name and lo <= day.date() <= hi:
            return True
    if any(n == name for (n, _, _) in st.session_state.get("rotators", [])):
        return False
    last = history.get(name)
    if apply_gap and last and (day - last).days < min_gap:
        return False
    return True

# NIGHT FLOAT & SCHEDULE BUILDING
def assign_nf_blocks(days, nf_pool, history, block_length):
    assignments = {}
    i = 0
    nf_index = 0
    if not nf_pool:
        return {}
    while i < len(days):
        person = nf_pool[nf_index % len(nf_pool)]
        block_days = days[i:i + block_length]
        for d in block_days:
            if is_available(person, d, history, apply_gap=False):
                assignments[d.date()] = person
                history[person] = d
        i += block_length
        nf_index += 1
    return assignments

def assign_nf_fair(days, nf_pool, history):
    assignments = {}
    if not nf_pool:
        return {}
    for d in days:
        person = pick_fairest_candidate(nf_pool, d, history, apply_gap=False)
        if person != "Unavailable":
            assignments[d.date()] = person
            history[person] = d
    return assignments

def pick_fairest_candidate(pool, day, history, apply_gap=True):
    candidates = [(p, (day - history.get(p)).days if history.get(p) else float('inf')) for p in pool]
    candidates = [c for c in candidates if is_available(c[0], day, history, apply_gap)]
    if not candidates:
        return "Unavailable"
    candidates.sort(key=lambda x: (-x[1], x[0]))
    return candidates[0][0]

def build_schedule():
    days = pd.date_range(start_date, end_date)
    history = {}
    rows = []

    shift_defs = st.session_state.shifts
    shift_labels = [s["label"] for s in shift_defs]

    nf_assignments = {}
    for shift in [s for s in shift_defs if s["night_float"]]:
        pool = nf_seniors if shift["role"] == "Senior" else nf_juniors
        nf_history = {}
        if nf_style == "Block":
            assigned = assign_nf_blocks(days, pool, nf_history, nf_block_length)
        else:
            assigned = assign_nf_fair(days, pool, nf_history)
        nf_assignments[shift["label"]] = assigned

    all_nf_people = set()
    for day_map in nf_assignments.values():
        all_nf_people.update(day_map.values())

    for day in days:
        row = {"Date": day.date(), "Day": day.strftime("%A")}
        for label in shift_labels:
            row[label] = "Unavailable"

        nf_people_today = set()
        for s in shift_defs:
            if s["night_float"]:
                person = nf_assignments.get(s["label"], {}).get(day.date(), "Unavailable")
                row[s["label"]] = person
                if person != "Unavailable":
                    nf_people_today.add(person)

        for shift in [s for s in shift_defs if not s["night_float"]]:
            label = shift["label"]
            pool = senior_list if shift["role"] == "Senior" else junior_list
            available_pool = [p for p in pool if p not in nf_people_today and p not in all_nf_people]
            assigned = pick_fairest_candidate(available_pool, day, history)
            if assigned != "Unavailable":
                history[assigned] = day
            row[label] = assigned

        rows.append(row)

    return pd.DataFrame(rows)

# GENERATE AND DISPLAY SCHEDULE
if st.button("🚀 Generate Schedule", key="generate_btn"):
    if not st.session_state.shifts:
        st.error("❌ Please add at least one shift template.")
        st.stop()
    if not junior_list or not senior_list:
        st.error("❌ Please provide both junior and senior participant lists.")
        st.stop()
    if start_date > end_date:
        st.error("❌ Invalid date range: End date must be after or equal to start date.")
        st.stop()

    df = build_schedule()
    if df.empty:
        st.warning("⚠️ No schedule generated. Please check your inputs.")
    else:
        st.success("✅ Schedule generated!")
        st.dataframe(df, use_container_width=True)

        # CSV Export
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download CSV", csv, "schedule.csv", "text/csv")