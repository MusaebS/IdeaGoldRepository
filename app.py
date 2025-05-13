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

# REMAINING UI AND LOGIC UNCHANGED UNTIL ENGINE EXECUTION POINT

# SCHEDULE ENGINE
if st.button("🚀 Generate Schedule"):
    if start_date > end_date:
        st.error("❌ End date must be after Start date.")
        st.stop()

    shifts = st.session_state.shifts
    pool = juniors + seniors
    all_days = pd.date_range(start_date, end_date)
    if not pool or not shifts:
        st.error("❌ You must define at least one shift and one participant.")
        st.stop()

    def on_leave(name, day):
        return any(n == name and f <= day.date() <= t for (n, f, t) in st.session_state.leaves)

    def is_rotator(name, day):
        for (n, f, t) in st.session_state.rotators:
            if n == name and f <= day.date() <= t:
                return True
        return False

    def active_days(name):
        total = sum(1 for d in all_days if not on_leave(name, d))
        for (n, f, t) in st.session_state.rotators:
            if n == name:
                return (t - f).days + 1
        return total

    weighted_days = {i: rank_weights.get(st.session_state.weights.get(i, "R2"), 1.0) * active_days(i) for i in pool}
    total_weighted = sum(weighted_days.values())

    if total_weighted == 0:
        st.error("❌ No valid participants with weights found.")
        st.stop()

    # Count shift types
    total_slots = {"total": 0, "weekend": 0, "ER": 0, "Ward": 0}
    for d in all_days:
        for s in shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if d.weekday() in [3, 4, 5]:  # Thu/Fri/Sat
                    total_slots["weekend"] += 1
                if "ER" in s["label"]:
                    total_slots["ER"] += 1
                elif "Ward" in s["label"]:
                    total_slots["Ward"] += 1

    expected = {
        i: {
            k: total_slots[k] * (weighted_days[i] / total_weighted) + (st.session_state.extra_oncalls[i] if k == "total" else 0)
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
            candidates = [p for p in pool if not on_leave(p, d) and p not in assigned_today and (last_assigned[p] is None or (d - last_assigned[p]).days >= min_gap) and (p in juniors if role == "Junior" else p in seniors)]
            if not candidates:
                row[label] = "Unavailable"
                continue
            deficit = {p: sum(shift_type_weights[k] * (expected[p][k] - stats[p][k]) for k in total_slots) for p in candidates}
            chosen = max(deficit, key=lambda x: (deficit[x], x))
            row[label] = chosen
            stats[chosen]["total"] += 1
            stats[chosen][shift_type] += 1
            if d.weekday() in [3, 4, 5]:
                stats[chosen]["weekend"] += 1
            last_assigned[chosen] = d
            assigned_today.add(chosen)
        schedule.append(row)

    df = pd.DataFrame(schedule)
    st.dataframe(df)

    summary = pd.DataFrame.from_dict({
        name: {**stats[name], **expected[name]} for name in pool
    }, orient="index").reset_index().rename(columns={"index": "Name"})
    st.subheader("📊 Summary")
    st.dataframe(summary)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download Schedule CSV", csv, "schedule.csv", "text/csv")

    summary_csv = summary.to_csv(index=False).encode("utf-8")
    st.download_button("📊 Download Summary CSV", summary_csv, "summary.csv", "text/csv")
