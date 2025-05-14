import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# Reset All Button
if st.button("🔁 Reset All"):
    for key in ["shifts", "rotators", "leaves", "extra_oncalls", "juniors", "seniors", "nf_juniors", "nf_seniors"]:
        if key in st.session_state:
            del st.session_state[key]
    st.experimental_rerun()

# Ensure session state defaults
for key in ["shifts", "rotators", "leaves", "extra_oncalls", "juniors", "seniors", "nf_juniors", "nf_seniors"]:
    if key not in st.session_state:
        st.session_state[key] = [] if key in ["shifts", "rotators", "leaves"] else {}

# SHIFT TEMPLATE INPUT
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)", key="shift_label")
    role = col2.selectbox("Role", ["Junior", "Senior"], key="shift_role")
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time(), key="shift_start")
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time(), key="shift_end")
    nf = col5.checkbox("Night Float", key="shift_nf")
    thur_flag = st.checkbox("Treat Thursday as Weekend", key="thur_flag")

    if st.button("Add Shift", key="add_shift_btn"):
        if label.strip() and start < end:
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
                "night_float": nf,
                "thur_weekend": thur_flag
            })
        else:
            st.warning("⚠️ Please enter a valid label and ensure Start < End.")

    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# PARTICIPANTS
with st.expander("📝 Participants", expanded=False):
    use_demo = st.checkbox("Use Demo Names", value=True)
    if use_demo:
        junior_list = ["Ashley", "Amanda", "Linda", "Nicole", "Emily", "Rachel"]
        senior_list = ["John", "Robert", "Thomas", "Andrew", "Mark", "Chris"]
    else:
        junior_raw = st.text_area("Juniors (one per line)")
        senior_raw = st.text_area("Seniors (one per line)")
        junior_list = list(dict.fromkeys([r.strip() for r in junior_raw.splitlines() if r.strip()]))
        senior_list = list(dict.fromkeys([r.strip() for r in senior_raw.splitlines() if r.strip()]))

    if not junior_list or not senior_list:
        st.error("❌ Please enter at least one junior and one senior.")
        st.stop()

    st.session_state.juniors = junior_list
    st.session_state.seniors = senior_list
import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta

# Utility: Check if date is a weekend

def is_weekend(d, shift):
    if shift.get("thur_weekend"):
        return d.weekday() in [3, 4, 5]  # Thu/Fri/Sat
    return d.weekday() in [4, 5]  # Fri/Sat

# Utility: Check if name is on leave

def on_leave(name, day):
    return any(n == name and f <= day <= t for (n, f, t) in st.session_state.leaves)

# Utility: Check if someone is a rotator

def is_rotator(name):
    return any(n == name for (n, _, _) in st.session_state.rotators)

# Utility: Check if rotator is active on date

def is_active_rotator(name, day):
    return any(n == name and f <= day <= t for (n, f, t) in st.session_state.rotators)

# Count total & weekend slots

def count_slots(days, shift_defs):
    total, weekend = 0, 0
    for day in days:
        for s in shift_defs:
            if not s.get("night_float"):
                total += 1
                if is_weekend(day, s):
                    weekend += 1
    return total, weekend

# Count active days (used for weighting)

def active_days(name, days):
    if is_rotator(name):
        window = [(f, t) for (n, f, t) in st.session_state.rotators if n == name][0]
        days = [d for d in days if window[0] <= d <= window[1]]
    return [d for d in days if not on_leave(name, d)]

# Main Schedule Builder

def build_schedule():
    start = st.session_state.start_date
    end = st.session_state.end_date
    if start > end:
        st.error("Start date must be before end date")
        return None, None, None

    days = pd.date_range(start, end).to_list()
    shift_defs = st.session_state.shifts
    juniors = st.session_state.juniors
    seniors = st.session_state.seniors
    nf_juniors = st.session_state.nf_juniors
    nf_seniors = st.session_state.nf_seniors
    min_gap = st.session_state.min_gap
    nf_block_length = st.session_state.nf_block_length

    pool = juniors + seniors
    if not pool or not shift_defs:
        st.error("No participants or shifts.")
        return None, None, None

    # Step 1: Prepare active/weighted days
    all_active = {}
    weights = {}
    for p in pool:
        days_available = active_days(p, days)
        all_active[p] = days_available
        weights[p] = len(days_available) * (1 + st.session_state.extra_oncalls.get(p, 0))

    total_weighted = sum(weights.values())
    if total_weighted == 0:
        st.error("Everyone is on leave!")
        return None, None, None

    # Step 2: Expected quotas
    total_slots, weekend_slots = count_slots(days, shift_defs)
    expected = {
        p: {
            "total": total_slots * (weights[p] / total_weighted),
            "weekend": weekend_slots * (weights[p] / total_weighted),
        } for p in pool
    }

    stats = {p: {"total": 0, "weekend": 0} for p in pool}
    last_assigned = {p: None for p in pool}
    rows = []
    unfilled = []

    # Step 3: Night Float
    nf_assigned = {}
    for shift in shift_defs:
        if shift.get("night_float"):
            label = shift["label"]
            role = shift["role"]
            nf_pool = nf_seniors if role == "Senior" else nf_juniors
            if not nf_pool:
                st.warning(f"No NF-eligible for {label}")
                continue

            d_idx = 0
            while d_idx < len(days):
                person = nf_pool[d_idx % len(nf_pool)]
                for i in range(nf_block_length):
                    if d_idx + i >= len(days):
                        break
                    d = days[d_idx + i].date()
                    nf_assigned.setdefault(d, set()).add(person)
                    last_assigned[person] = d
                d_idx += nf_block_length

    # Step 4: Regular Shifts
    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        assigned_today = set()
        for s in shift_defs:
            if s.get("night_float"):
                label = s["label"]
                found = [p for p in nf_assigned.get(d.date(), []) if p in pool]
                row[label] = found[0] if found else "Unavailable"
                continue

            role = s["role"]
            label = s["label"]
            is_wkend = is_weekend(d, s)

            candidates = []
            for p in (juniors if role == "Junior" else seniors):
                if p in assigned_today or p in nf_assigned.get(d.date(), set()):
                    continue
                if on_leave(p, d.date()):
                    continue
                if is_rotator(p) and not is_active_rotator(p, d.date()):
                    continue
                last = last_assigned.get(p)
                if last and (d.date() - last).days < min_gap:
                    continue
                candidates.append(p)

            if not candidates:
                row[label] = "Unavailable"
                unfilled.append((d.date(), label))
                continue

            deficits = {
                p: (
                    expected[p]["weekend"] - stats[p]["weekend"],
                    expected[p]["total"] - stats[p]["total"]
                ) for p in candidates
            }
            max_def = max(deficits.values())
            top = [p for p in candidates if deficits[p] == max_def]
            random.shuffle(top)
            chosen = top[0]

            row[label] = chosen
            last_assigned[chosen] = d.date()
            assigned_today.add(chosen)
            stats[chosen]["total"] += 1
            if is_wkend:
                stats[chosen]["weekend"] += 1

        rows.append(row)

    df = pd.DataFrame(rows)
    df_summary = pd.DataFrame.from_dict({
        p: {
            "Assigned_Total": stats[p]["total"],
            "Expected_Total": round(expected[p]["total"], 1),
            "Assigned_Weekend": stats[p]["weekend"],
            "Expected_Weekend": round(expected[p]["weekend"], 1),
        } for p in pool
    }, orient="index")

    return df, df_summary.reset_index(names=["Name"]), pd.DataFrame(unfilled, columns=["Date", "Shift"])

# Schedule Generator
if st.button("🚀 Generate Schedule"):
    df, summary, unfilled = build_schedule()
    if df is not None:
        st.success("✅ Schedule generated!")
        st.dataframe(df)
        st.download_button("Download Schedule CSV", df.to_csv(index=False), "schedule.csv")
        st.subheader("Summary")
        st.dataframe(summary)
        st.download_button("Download Summary CSV", summary.to_csv(index=False), "summary.csv")
        if not unfilled.empty:
            st.subheader("Unfilled Slots")
            st.dataframe(unfilled)
