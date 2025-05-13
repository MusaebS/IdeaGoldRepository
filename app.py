# Idea Gold Scheduler (with Stability Fixes)
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# --- Reset Handling ---
if st.button("🔁 Reset All"):
    for key in ["shifts", "rotators", "leaves", "extra_oncalls", "weights", "nf_juniors", "nf_seniors"]:
        st.session_state.pop(key, None)
    st.experimental_rerun()

# --- Initialize Session State ---
for key in ["shifts", "rotators", "leaves"]:
    if key not in st.session_state:
        st.session_state[key] = []
for key in ["extra_oncalls", "weights"]:
    if key not in st.session_state:
        st.session_state[key] = {}

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
with st.expander("🧑‍⚕️ Participants", expanded=False):
    demo = st.checkbox("Use Demo Names", True)

    if demo:
        juniors = ["Amanda", "Emily", "Jessica", "Laura", "Linda", "Mary", "Nicole", "Ashley", "Sarah", "Sophia"]
        seniors = ["Andrew", "Daniel", "David", "James", "Joseph", "Michael", "Robert", "John", "Thomas", "William"]
    else:
        j_raw = st.text_area("Juniors")
        s_raw = st.text_area("Seniors")
        seen = set()
        juniors = [x.strip() for x in j_raw.splitlines() if x.strip() and not (x.strip() in seen or seen.add(x.strip()))]
        seniors = [x.strip() for x in s_raw.splitlines() if x.strip() and not (x.strip() in seen or seen.add(x.strip()))]

    if not juniors or not seniors:
        st.error("❌ Please enter at least one junior and one senior.")
        st.stop()

# --- Extra Oncalls ---
with st.expander("⚖️ Extra Oncalls and Shift Bias", expanded=False):
    all_people = juniors + seniors
    for name in all_people:
        if name not in st.session_state.weights:
            st.session_state.weights[name] = 1.0
        if name not in st.session_state.extra_oncalls:
            st.session_state.extra_oncalls[name] = 0
        st.session_state.weights[name] = st.slider(f"{name} Bias", 0.8, 1.5, st.session_state.weights[name], 0.1, key=f"bias_{name}")
        st.session_state.extra_oncalls[name] = st.number_input(f"{name} Extra Oncalls", 0, 10, st.session_state.extra_oncalls[name], 1, key=f"extra_{name}")

# --- Night Float Eligibility ---
with st.expander("🌙 Night-Float Eligibility", expanded=False):
    st.session_state.nf_juniors = st.multiselect("NF-Eligible Juniors", juniors, default=juniors)
    st.session_state.nf_seniors = st.multiselect("NF-Eligible Seniors", seniors, default=seniors)

# --- Dates & Rules ---
with st.expander("📅 Schedule Dates & Rules", expanded=True):
    start_date = st.date_input("Start Date", datetime.today())
    end_date = st.date_input("End Date", datetime.today() + timedelta(days=27))
    if start_date > end_date:
        st.error("❌ End date must be after start date.")
        st.stop()

    min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
    nf_block_length = st.slider("Night-Float Block Length (days)", 1, 14, 5)
    st.session_state["nf_block_length"] = nf_block_length

# --- Rotators & Leaves ---
def make_table(items, fields):
    if not items:
        st.write("*(None yet)*")
        return
    t = {f: [it[i] for it in items] for i, f in enumerate(fields)}
    st.table(pd.DataFrame(t))

with st.expander("🔁 Rotators", expanded=False):
    name = st.selectbox("Rotator Name", [""] + juniors + seniors, key="rot_name")
    from_ = st.date_input("From", key="rot_from")
    to_ = st.date_input("To", key="rot_to")
    if st.button("Add Rotator", key="add_rot"):
        if name and from_ <= to_:
            st.session_state.rotators.append((name, from_, to_))
    make_table(st.session_state.rotators, ["Name", "From", "To"])

with st.expander("🏖️ Leaves", expanded=False):
    name = st.selectbox("Leave Name", [""] + juniors + seniors, key="lv_name")
    from_ = st.date_input("From", key="lv_from")
    to_ = st.date_input("To", key="lv_to")
    if st.button("Add Leave", key="add_lv"):
        if name and from_ <= to_:
            st.session_state.leaves.append((name, from_, to_))
    make_table(st.session_state.leaves, ["Name", "From", "To"])

# Full code continuation: Scheduling Engine and Output

# --- Utility Functions ---
def is_weekend(date, shift):
    thur_flag = shift.get("thur_weekend", False)
    return date.weekday() in ([3, 4, 5] if thur_flag else [4, 5, 6])

def on_leave(name, date):
    return any(n == name and lo <= date <= hi for (n, lo, hi) in st.session_state.leaves)

def is_active_rotator(name, date):
    return any(n == name and lo <= date <= hi for (n, lo, hi) in st.session_state.rotators)

def build_schedule():
    all_days = pd.date_range(start=start_date, end=end_date)
    shifts = st.session_state.shifts
    nf_juniors = st.session_state.get("nf_juniors", [])
    nf_seniors = st.session_state.get("nf_seniors", [])
    nf_block_len = st.session_state.get("nf_block_length", 5)
    people = juniors + seniors

    stats = {p: {"total": 0, "weekend": 0} for p in people}
    last_assigned = {p: None for p in people}
    history = {}
    unfilled = []

    weighted_days = {}
    for p in people:
        days_available = [d for d in all_days if not on_leave(p, d.date()) and (not is_active_rotator(p, d.date()) or is_active_rotator(p, d.date()))]
        weighted_days[p] = len(days_available) * st.session_state.weights.get(p, 1.0)
    total_weighted = sum(weighted_days.values())
    if total_weighted == 0:
        st.error("All participants unavailable during schedule window.")
        return pd.DataFrame()

    total_slots = {"total": 0, "weekend": 0}
    for d in all_days:
        for s in shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if is_weekend(d, s):
                    total_slots["weekend"] += 1

    expected = {
        p: {
            "total": total_slots["total"] * (weighted_days[p] / total_weighted) + st.session_state.extra_oncalls.get(p, 0),
            "weekend": total_slots["weekend"] * (weighted_days[p] / total_weighted)
        }
        for p in people
    }

    nf_people_today = set()
    schedule_rows = []

    for s in [s for s in shifts if s["night_float"]]:
        role = s["role"]
        nf_pool = nf_seniors if role == "Senior" else nf_juniors
        if not nf_pool:
            continue
        i = 0
        while i < len(all_days):
            person = nf_pool[i % len(nf_pool)]
            for d_idx in range(nf_block_len):
                if i + d_idx >= len(all_days):
                    break
                d = all_days[i + d_idx]
                if not on_leave(person, d.date()):
                    history[(d.date(), s["label"])] = person
                    last_assigned[person] = d.date()
            i += nf_block_len

    for d in all_days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        nf_people_today.clear()
        for s in [s for s in shifts if s["night_float"]]:
            assignee = history.get((d.date(), s["label"]), "Unavailable")
            row[s["label"]] = assignee
            if assignee != "Unavailable":
                nf_people_today.add(assignee)

        for s in [s for s in shifts if not s["night_float"]]:
            role = s["role"]
            pool = juniors if role == "Junior" else seniors
            candidates = [p for p in pool if p not in nf_people_today and not on_leave(p, d.date())
                          and (not is_active_rotator(p, d.date()) or is_active_rotator(p, d.date()))
                          and last_assigned[p] != d.date()
                          and (last_assigned[p] is None or (d.date() - last_assigned[p]).days >= min_gap)]
            if not candidates:
                row[s["label"]] = "Unavailable"
                unfilled.append((d.date(), s["label"]))
                continue

            deficits = {}
            for c in candidates:
                deficit = (expected[c]["total"] - stats[c]["total"]) + 2 * (expected[c]["weekend"] - stats[c]["weekend"])
                deficits[c] = deficit

            top_val = max(deficits.values())
            top = [c for c in candidates if deficits[c] == top_val]
            winner = random.choice(top)
            row[s["label"]] = winner
            stats[winner]["total"] += 1
            if is_weekend(d, s):
                stats[winner]["weekend"] += 1
            last_assigned[winner] = d.date()

        schedule_rows.append(row)

    df = pd.DataFrame(schedule_rows)
    if not df.empty:
        st.subheader("📋 Final Schedule")
        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Schedule CSV", csv, "schedule.csv", "text/csv")

        st.subheader("📊 Summary")
        summary = pd.DataFrame([{
            "Name": p,
            "Total Assigned": stats[p]["total"],
            "Weekend Assigned": stats[p]["weekend"],
            "Expected Total": round(expected[p]["total"], 1),
            "Expected Weekend": round(expected[p]["weekend"], 1)
        } for p in people])
        st.dataframe(summary)

        if unfilled:
            st.warning(f"⚠️ {len(unfilled)} shifts unfilled.")
            st.text("Unfilled slots:")
            for u in unfilled:
                st.text(f"{u[0]} → {u[1]}")

    return df

# --- Generate Schedule ---
if st.button("🚀 Generate Schedule"):
    build_schedule()

