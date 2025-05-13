# Idea Gold Scheduler (with Critical Fixes)
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# --- Reset Handling ---
if st.button("🔁 Reset All"):
    st.session_state.clear()
    st.experimental_rerun()

# --- Initialize Session State ---
for k in ["shifts", "rotators", "leaves", "extra_oncalls", "weights"]:
    st.session_state.setdefault(k, [] if k in ["shifts", "rotators", "leaves"] else {})

# --- Shift Templates ---
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", datetime.strptime("20:00", "%H:%M").time())
    is_nf = col5.checkbox("Night Float")
    thur_weekend = st.checkbox("Thursday = Weekend", key="thur_flag")

    if st.button("Add Shift", key="add_shift"):
        if not label.strip():
            st.warning("⚠️ Please enter a valid label.")
        elif start >= end:
            st.warning("⚠️ Start time must be before end time.")
        else:
            base = label.strip()
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
                "night_float": is_nf,
                "thur_weekend": thur_weekend
            })

if st.session_state.shifts:
    st.dataframe(pd.DataFrame(st.session_state.shifts))

# --- Participants ---
st.header("👥 Participants")
demo = st.checkbox("Use Demo Names", True)

if demo:
    juniors = ["Amanda", "Emily", "Jessica", "Laura", "Linda", "Mary", "Nicole", "Ashley", "Sarah", "Sophia"]
    seniors = ["Andrew", "Daniel", "David", "James", "Joseph", "Michael", "Robert", "John", "Thomas", "William"]
else:
    j_raw = st.text_area("Juniors")
    s_raw = st.text_area("Seniors")
    juniors = list({x.strip() for x in j_raw.splitlines() if x.strip()})
    seniors = list({x.strip() for x in s_raw.splitlines() if x.strip()})

if not juniors or not seniors:
    st.error("❌ Please enter at least one junior and one senior.")
    st.stop()

# --- Extra Oncalls ---
st.subheader("⚖️ Shift Load Bias & Extra Oncalls")
all_people = juniors + seniors
for name in all_people:
    st.session_state.weights[name] = st.slider(f"{name} Bias", 0.8, 1.5, 1.0, 0.1, key=f"bias_{name}")
    st.session_state.extra_oncalls[name] = st.number_input(f"{name} Extra Oncalls", 0, 10, 0, 1, key=f"extra_{name}")

# --- Night Float Eligibility ---
with st.expander("🛌 Night Float Eligibility"):
    nf_juniors = st.multiselect("NF Juniors", juniors, default=juniors)
    nf_seniors = st.multiselect("NF Seniors", seniors, default=seniors)

# --- Date Range & Rules ---
st.subheader("⏱️ Schedule Rules")
col1, col2 = st.columns(2)
start_date = col1.date_input("Start Date", datetime.today())
end_date = col2.date_input("End Date", datetime.today() + timedelta(days=27))

if start_date > end_date:
    st.error("❌ End date must be after or equal to start date.")
    st.stop()

min_gap = st.slider("Minimum Days Between Shifts", 0, 7, 2)
nf_block_length = st.slider("Night Float Block Length (days)", 1, 14, 5)
st.session_state["nf_block_length"] = nf_block_length
# --- Rotators & Leaves ---
with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Name", [""] + all_people, key="rot_name")
    rot_from = st.date_input("Rotator From", key="rot_from")
    rot_to = st.date_input("Rotator To", key="rot_to")
    if st.button("Add Rotator", key="rot_btn"):
        if rot_name and rot_from <= rot_to:
            st.session_state.rotators.append((rot_name, rot_from, rot_to))
    if st.session_state.rotators:
        st.table(pd.DataFrame(st.session_state.rotators, columns=["Name", "From", "To"]))

with st.expander("✈️ Leaves"):
    lv_name = st.selectbox("Name", [""] + all_people, key="lv_name")
    lv_from = st.date_input("Leave From", key="lv_from")
    lv_to = st.date_input("Leave To", key="lv_to")
    if st.button("Add Leave", key="leave_btn"):
        if lv_name and lv_from <= lv_to:
            st.session_state.leaves.append((lv_name, lv_from, lv_to))
    if st.session_state.leaves:
        st.table(pd.DataFrame(st.session_state.leaves, columns=["Name", "From", "To"]))

# --- Utility Functions ---
def is_weekend(d, shift):
    return d.weekday() in [4, 5] or (d.weekday() == 3 and shift.get("thur_weekend", False))

def on_leave(name, day):
    return any(n == name and lo <= day.date() <= hi for (n, lo, hi) in st.session_state.leaves)

def is_active_rotator(name, day):
    return any(n == name and lo <= day.date() <= hi for (n, lo, hi) in st.session_state.rotators)

# --- Schedule Engine ---
def build_schedule():
    all_days = pd.date_range(start=start_date, end=end_date)
    pool = juniors + seniors

    weighted_days = {
        p: st.session_state.weights.get(p, 1.0) * len([d for d in all_days if not on_leave(p, d) and (not any(n == p for (n, _, _) in st.session_state.rotators) or is_active_rotator(p, d))])
        for p in pool
    }
    total_weighted = sum(weighted_days.values())

    stats = {p: {"total": 0, "weekend": 0} for p in pool}
    expected = {p: {"total": 0, "weekend": 0} for p in pool}
    last_assigned = {p: None for p in pool}

    total_slots = {"total": 0, "weekend": 0}
    for d in all_days:
        for s in st.session_state.shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if is_weekend(d, s):
                    total_slots["weekend"] += 1

    for p in pool:
        ratio = weighted_days[p] / total_weighted if total_weighted > 0 else 0
        expected[p]["total"] = total_slots["total"] * ratio + st.session_state.extra_oncalls.get(p, 0)
        expected[p]["weekend"] = total_slots["weekend"] * ratio

    # Assign Night Float blocks
    nf_assignments = {}
    for s in [s for s in st.session_state.shifts if s["night_float"]]:
        nf_pool = nf_seniors if s["role"] == "Senior" else nf_juniors
        if not nf_pool:
            st.warning(f"⚠️ NF pool empty for {s['label']}")
            continue
        nf_label = s["label"]
        nf_assignments[nf_label] = {}
        d_idx = 0
        idx = 0
        while d_idx < len(all_days):
            p = nf_pool[idx % len(nf_pool)]
            block = all_days[d_idx:d_idx + st.session_state["nf_block_length"]]
            for d in block:
                if not on_leave(p, d):
                    nf_assignments[nf_label][d.date()] = p
                    last_assigned[p] = d
            d_idx += st.session_state["nf_block_length"]
            idx += 1

    # Assign Regular Shifts
    schedule = []
    unfilled = []
    for d in all_days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        assigned_today = set()
        nf_today = set()
        for s in [s for s in st.session_state.shifts if s["night_float"]]:
            person = nf_assignments.get(s["label"], {}).get(d.date(), "Unavailable")
            row[s["label"]] = person
            if person != "Unavailable":
                nf_today.add(person)
        for s in [s for s in st.session_state.shifts if not s["night_float"]]:
            label, role = s["label"], s["role"]
            pool_role = juniors if role == "Junior" else seniors
            is_wknd = is_weekend(d, s)
            candidates = [p for p in pool_role if p not in nf_today and p not in assigned_today and not on_leave(p, d) and (not any(n == p for (n, _, _) in st.session_state.rotators) or is_active_rotator(p, d)) and (last_assigned[p] is None or (d - last_assigned[p]).days >= min_gap)]
            if not candidates:
                row[label] = "Unavailable"
                unfilled.append((d.date(), label))
                continue
            random.shuffle(candidates)
            deficit = {
                p: (expected[p]["total"] - stats[p]["total"]) + 2 * (expected[p]["weekend"] - stats[p]["weekend"]) if is_wknd else (expected[p]["total"] - stats[p]["total"])
                for p in candidates
            }
            top_score = max(deficit.values())
            top = [p for p in candidates if deficit[p] == top_score]
            chosen = random.choice(top)
            row[label] = chosen
            stats[chosen]["total"] += 1
            if is_wknd:
                stats[chosen]["weekend"] += 1
            last_assigned[chosen] = d
            assigned_today.add(chosen)
        schedule.append(row)

    return pd.DataFrame(schedule), stats, expected, unfilled

# --- Generate Schedule ---
if st.button("🚀 Generate Schedule"):
    df, stats, expected, unfilled = build_schedule()
    st.subheader("📅 Schedule")
    st.dataframe(df)

    st.subheader("📊 Summary")
    summary = pd.DataFrame([{
        "Name": p,
        "Total": stats[p]["total"],
        "Weekend": stats[p]["weekend"],
        "Expected Total": round(expected[p]["total"], 1),
        "Expected Weekend": round(expected[p]["weekend"], 1)
    } for p in stats])
    st.dataframe(summary)

    if unfilled:
        st.subheader("⚠️ Unfilled Slots")
        st.dataframe(pd.DataFrame(unfilled, columns=["Date", "Shift"]))

    st.download_button("📥 Download Schedule CSV", df.to_csv(index=False).encode("utf-8"), "schedule.csv")
    st.download_button("📥 Download Summary CSV", summary.to_csv(index=False).encode("utf-8"), "summary.csv")
