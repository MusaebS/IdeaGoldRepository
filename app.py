import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# --- Reset Handling ---
if st.button("🔄 Reset All"):
    st.session_state.clear()
    st.experimental_rerun()

# --- Initialize Session State ---
for key in ["shifts", "rotators", "leaves", "extra_oncalls", "weights", "nf_juniors", "nf_seniors"]:
    st.session_state.setdefault(key, [] if key in ["shifts", "rotators", "leaves"] else {})

# --- Date & Rules ---
with st.expander("📅 Date Range & Rules", expanded=True):
    start_date = st.date_input("Start Date", datetime.today())
    end_date = st.date_input("End Date", datetime.today() + timedelta(days=27))
    if start_date > end_date:
        st.error("End date must be after start date")
        st.stop()
    min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
    nf_block_length = st.slider("Night-Float Block Length", 1, 14, 5)

# --- Shift Templates ---
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2,1,1,1,1])
    label = col1.text_input("Label")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")
    thur_weekend = st.checkbox("Thursday = Weekend")

    if st.button("Add Shift"):
        if start >= end:
            st.warning("Start must be before End")
        else:
            unique_label = label.strip()
            existing_labels = [s["label"] for s in st.session_state.shifts]
            count = 1
            while unique_label in existing_labels:
                count += 1
                unique_label = f"{label} #{count}"
            st.session_state.shifts.append({
                "label": unique_label, "role": role,
                "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"),
                "night_float": nf, "thur_weekend": thur_weekend
            })
    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# --- Participants ---
with st.expander("👥 Participants"):
    use_demo = st.checkbox("Use Demo Names", True)
    juniors = seniors = []
    if use_demo:
        juniors = ["Layla", "Sara", "Noura", "Omar", "Khalid"]
        seniors = ["Dr. A", "Dr. B", "Dr. C"]
    else:
        juniors = st.text_area("Juniors").splitlines()
        seniors = st.text_area("Seniors").splitlines()

# --- NF Eligibility ---
with st.expander("🌙 Night Float Eligibility"):
    nf_juniors = st.multiselect("NF Juniors", juniors, juniors)
    nf_seniors = st.multiselect("NF Seniors", seniors, seniors)

# --- Leaves & Rotators ---
with st.expander("✈️ Leaves & Rotators"):
    lv_name = st.selectbox("Leave Name", [""]+juniors+seniors)
    lv_from, lv_to = st.date_input("Leave Dates", [datetime.today(), datetime.today()])
    if st.button("Add Leave") and lv_name:
        st.session_state.leaves.append((lv_name, lv_from, lv_to))

    rot_name = st.selectbox("Rotator Name", [""]+juniors+seniors)
    rot_from, rot_to = st.date_input("Rotator Dates", [datetime.today(), datetime.today()])
    if st.button("Add Rotator") and rot_name:
        st.session_state.rotators.append((rot_name, rot_from, rot_to))

# --- Scheduling Logic ---
def build_schedule():
    dates = pd.date_range(start_date, end_date)
    schedule, stats = [], {}

    # Initialize stats
    for name in juniors+seniors:
        stats[name] = {"assigned": 0, "last_assigned": None}

    for date in dates:
        daily_schedule = {"Date": date.strftime("%Y-%m-%d"), "Day": date.strftime("%A")}
        for shift in st.session_state.shifts:
            pool = nf_juniors if shift["night_float"] and shift["role"] == "Junior" else \
                   nf_seniors if shift["night_float"] and shift["role"] == "Senior" else \
                   juniors if shift["role"] == "Junior" else seniors
            candidates = [p for p in pool if not any(lv[0] == p and lv[1] <= date.date() <= lv[2] for lv in st.session_state.leaves)]
            random.shuffle(candidates)
            assigned = "Unavailable"
            for c in candidates:
                last_date = stats[c]["last_assigned"]
                if last_date is None or (date - last_date).days >= min_gap:
                    assigned = c
                    stats[c]["assigned"] += 1
                    stats[c]["last_assigned"] = date
                    break
            daily_schedule[shift["label"]] = assigned
        schedule.append(daily_schedule)
    return pd.DataFrame(schedule)

# --- Generate Schedule ---
if st.button("🚀 Generate Schedule"):
    final_schedule = build_schedule()
    st.subheader("📅 Final Schedule")
    st.dataframe(final_schedule, use_container_width=True)
    st.download_button("📥 Download Schedule CSV", final_schedule.to_csv(index=False), "schedule.csv", "text/csv")
