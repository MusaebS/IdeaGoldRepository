import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# RESET BUTTON
if st.button("🔁 Reset All Data"):
    for key in ["shifts", "rotators", "leaves", "weights", "extra_oncalls"]:
        if key in st.session_state:
            del st.session_state[key]
    st.experimental_rerun()

# INITIALIZATION
for key in ["shifts", "rotators", "leaves", "weights", "extra_oncalls"]:
    if key not in st.session_state:
        st.session_state[key] = [] if key in ["shifts", "rotators", "leaves"] else {}

# CONSTANTS
shift_type_weights = {"total": 1, "weekend": 2}

# SHIFT TEMPLATES
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")
    thur_weekend = st.checkbox("Treat Thursday night as weekend")

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
                "night_float": nf,
                "thur_weekend": thur_weekend
            })
    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# PARTICIPANTS
st.header("📝 Participants")
use_demo = st.checkbox("Use Demo Names", value=True)
if use_demo:
    juniors = ["Ali", "Reem", "Sara", "Omar", "Khalid", "Huda", "Layla", "Tariq", "Noura", "Rami", "Nada", "Salem"]
    seniors = ["Dr. A", "Dr. B", "Dr. C", "Dr. D", "Dr. E", "Dr. F", "Dr. G", "Dr. H"]
else:
    juniors = st.text_area("Juniors (one per line)").splitlines()
    seniors = st.text_area("Seniors (one per line)").splitlines()

juniors = [j.strip() for j in juniors if j.strip()]
seniors = [s.strip() for s in seniors if s.strip()]

# NIGHT FLOAT
with st.expander("🛌 Night-Float Eligibility"):
    nf_juniors = st.multiselect("NF-Eligible Juniors", juniors, default=juniors)
    nf_seniors = st.multiselect("NF-Eligible Seniors", seniors, default=seniors)

# DATES & RULES
start_date = st.date_input("Start Date", datetime.today())
end_date = st.date_input("End Date", datetime.today() + timedelta(days=28))
min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
nf_block_length = st.slider("Night Float Block Length (days)", 1, 14, 5)
st.session_state["start_date"] = start_date
st.session_state["end_date"] = end_date
st.session_state["min_gap"] = min_gap

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

# EXTRA ONCALLS & BIAS
with st.expander("⚖️ Shift Load Bias & Extra Oncalls"):
    for name in juniors + seniors:
        st.session_state.extra_oncalls[name] = st.number_input(f"Extra Oncalls for {name}", min_value=0, value=0, key=f"extra_{name}")
        st.session_state.weights[name] = st.slider(f"Shift Load Bias for {name}", 0.8, 1.5, 1.0, 0.1, key=f"bias_{name}")
# ... (previous code remains unchanged)

# FINAL SCHEDULE ENGINE
if st.button("🚀 Generate Schedule"):
    try:
        start_date = st.session_state.get("start_date")
        end_date = st.session_state.get("end_date")
        min_gap = st.session_state.get("min_gap")
        nf_block_length = st.session_state.get("nf_block_length")
        shifts = st.session_state.shifts
        all_days = pd.date_range(start_date, end_date)
        pool = juniors + seniors

        if not pool or not shifts:
            st.error("Participants and shift templates must be provided.")
            st.stop()

        def is_weekend(day, shift):
            return day.weekday() in [4, 5] or (day.weekday() == 3 and shift.get("thur_weekend", False))

        def on_leave(name, day):
            return any(n == name and f <= day.date() <= t for (n, f, t) in st.session_state.leaves)

        def active_days(name):
            days = [d for d in all_days if not on_leave(name, d)]
            for (n, f, t) in st.session_state.rotators:
                if n == name:
                    days = [d for d in pd.date_range(f, t) if not on_leave(name, d)]
                    break
            return len(days)

        weighted_days = {
            i: st.session_state.weights.get(i, 1.0) * active_days(i)
            for i in pool
        }
        total_weighted = sum(weighted_days.values())

        if total_weighted == 0:
            st.error("❌ No valid participants with weights found.")
            st.stop()

        total_slots = {"total": 0, "weekend": 0}
        for d in all_days:
            for s in shifts:
                if not s["night_float"]:
                    total_slots["total"] += 1
                    if is_weekend(d, s):
                        total_slots["weekend"] += 1

        expected = {
            i: {
                k: total_slots[k] * (weighted_days[i] / total_weighted) + (st.session_state.extra_oncalls.get(i, 0) if k == "total" else 0)
                for k in total_slots
            } for i in pool
        }

        stats = {i: {k: 0 for k in total_slots} for i in pool}
        last_assigned = {i: None for i in pool}
        history = {i: [] for i in pool}
        schedule = []
        unfilled = []

        nf_assignments = {}
        for s in [s for s in shifts if s["night_float"]]:
            nf_pool = nf_seniors if s["role"] == "Senior" else nf_juniors
            if not nf_pool:
                st.warning(f"⚠️ No NF-eligible participants for {s['label']}")
                continue
            label = s["label"]
            nf_map = {}
            idx = 0
            d_idx = 0
            while d_idx < len(all_days):
                person = nf_pool[idx % len(nf_pool)]
                block_days = all_days[d_idx:d_idx + nf_block_length]
                for d in block_days:
                    if not on_leave(person, d):
                        nf_map[d.date()] = person
                        history[person].append(d.date())
                        last_assigned[person] = d
                d_idx += nf_block_length
                idx += 1
            nf_assignments[label] = nf_map

        for d in all_days:
            row = {"Date": d.date(), "Day": d.strftime("%A")}
            assigned_today = set()
            nf_people_today = set()
            for s in [s for s in shifts if s["night_float"]]:
                label = s["label"]
                person = nf_assignments.get(label, {}).get(d.date(), "Unavailable")
                row[label] = person
                if person != "Unavailable":
                    nf_people_today.add(person)

            for s in sorted([s for s in shifts if not s["night_float"]], key=lambda x: x["start"]):
                label = s["label"]
                role = s["role"]
                is_wknd = is_weekend(d, s)
                candidates = [p for p in pool if p not in nf_people_today and not on_leave(p, d) and p not in assigned_today and (last_assigned[p] is None or (d - last_assigned[p]).days >= min_gap) and (p in juniors if role == "Junior" else p in seniors)]
                if not candidates:
                    row[label] = "Unavailable"
                    unfilled.append((d.date(), label))
                    continue
                deficit = {
                    p: sum(shift_type_weights[k] * (expected[p][k] - stats[p][k]) for k in total_slots)
                    for p in candidates
                }
                random.shuffle(candidates)
                chosen = max(candidates, key=lambda x: (deficit[x], x))
                row[label] = chosen
                stats[chosen]["total"] += 1
                if is_wknd:
                    stats[chosen]["weekend"] += 1
                last_assigned[chosen] = d
                assigned_today.add(chosen)
                history[chosen].append(d.date())
            schedule.append(row)

        df = pd.DataFrame(schedule)
        st.success("✅ Schedule generated!")
        st.dataframe(df)

        summary = pd.DataFrame.from_dict({name: {**stats[name], **expected[name]} for name in pool}, orient="index").reset_index().rename(columns={"index": "Name"})
        st.subheader("📊 Summary Table")
        st.dataframe(summary)

        if unfilled:
            st.subheader("⚠️ Unavailable Assignments")
            st.dataframe(pd.DataFrame(unfilled, columns=["Date", "Shift"]))

        st.download_button("📥 Download Schedule CSV", df.to_csv(index=False).encode("utf-8"), "schedule.csv", "text/csv")
        st.download_button("📊 Download Summary CSV", summary.to_csv(index=False).encode("utf-8"), "summary.csv", "text/csv")

    except Exception as e:
        st.error(f"❌ Error: {e}")
