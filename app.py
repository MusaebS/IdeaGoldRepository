import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, time
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# Reset All Button
if st.button("Reset All"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.experimental_rerun()

# Initialize Session State
for key in ["shifts", "rotators", "leaves", "weights", "extra_oncalls", "nf_juniors", "nf_seniors", "juniors", "seniors"]:
    if key not in st.session_state:
        st.session_state[key] = [] if key in ["shifts", "rotators", "leaves"] else {}

# Utility functions
def is_weekend(d, shift):
    if shift.get("thur_weekend"):
        return d.weekday() in [3, 4, 5]  # Thu, Fri, Sat
    return d.weekday() in [4, 5]  # Fri, Sat

def on_leave(name, date):
    return any(lv[0] == name and lv[1] <= date <= lv[2] for lv in st.session_state.leaves)

def is_rotator(name):
    return any(r[0] == name for r in st.session_state.rotators)

def is_active_rotator(name, date):
    return any(r[0] == name and r[1] <= date <= r[2] for r in st.session_state.rotators)

# Shift Templates
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=time(8, 0))
    end = col4.time_input("End", value=time(20, 0))
    nf = col5.checkbox("Night Float")
    thur_flag = st.checkbox("Treat Thursday as Weekend", key="thur_flag")

    if st.button("Add Shift", key="add_shift"):
        if label.strip() and start < end:
            base = label.strip()
            names = [s["label"] for s in st.session_state.shifts]
            count = 2
            unique = base
            while unique in names:
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
        else:
            st.warning("Please enter a non-empty label and valid time range.")

    if st.session_state.shifts:
        st.dataframe(pd.DataFrame(st.session_state.shifts))

# Participants
with st.expander("🧑 Participants"):
    use_demo = st.checkbox("Use Demo Names", value=True)
    if use_demo:
        juniors = [f"Junior{i}" for i in range(1, 11)]
        seniors = [f"Senior{i}" for i in range(1, 11)]
    else:
        j_raw = st.text_area("Juniors (one per line)")
        s_raw = st.text_area("Seniors (one per line)")
        juniors = list(dict.fromkeys([x.strip() for x in j_raw.splitlines() if x.strip()]))
        seniors = list(dict.fromkeys([x.strip() for x in s_raw.splitlines() if x.strip()]))

    if not juniors or not seniors:
        st.error("Please enter at least one junior and one senior.")
        st.stop()

    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# Extra Oncalls
with st.expander("➕ Extra Oncalls"):
    for name in juniors + seniors:
        if name not in st.session_state.extra_oncalls:
            st.session_state.extra_oncalls[name] = 0
        st.session_state.extra_oncalls[name] = st.number_input(f"{name} extra", min_value=0, value=st.session_state.extra_oncalls[name], key=f"extra_{name}")

# Night Float Eligibility
with st.expander("🌙 Night Float Eligibility"):
    st.session_state.nf_juniors = st.multiselect("NF-Eligible Juniors", juniors, default=juniors)
    st.session_state.nf_seniors = st.multiselect("NF-Eligible Seniors", seniors, default=seniors)

# Leaves and Rotators
with st.expander("✈️ Leaves"):
    name = st.selectbox("Name", juniors + seniors)
    from_d = st.date_input("From", value=datetime.today())
    to_d = st.date_input("To", value=datetime.today())
    if st.button("Add Leave"):
        st.session_state.leaves.append((name, from_d, to_d))
    if st.session_state.leaves:
        st.table(pd.DataFrame(st.session_state.leaves, columns=["Name", "From", "To"]))

with st.expander("🔄 Rotators"):
    name = st.selectbox("Rotator Name", juniors + seniors, key="rotator")
    from_d = st.date_input("Rotator From", value=datetime.today(), key="rf")
    to_d = st.date_input("Rotator To", value=datetime.today(), key="rt")
    if st.button("Add Rotator"):
        st.session_state.rotators.append((name, from_d, to_d))
    if st.session_state.rotators:
        st.table(pd.DataFrame(st.session_state.rotators, columns=["Name", "From", "To"]))

# Date Range and Rules
st.subheader("📅 Date Range and Rules")
start_date = st.date_input("Start Date", datetime.today())
end_date = st.date_input("End Date", datetime.today() + timedelta(days=28))

min_gap = st.slider("Minimum Days Between Shifts", 0, 7, 2)
st.session_state["nf_block_length"] = st.slider("NF Block Length", 1, 14, 5)

if start_date > end_date:
    st.error("Start date must be before end date")
    st.stop()


# Deficit-based assignment with fairness tracking
def build_schedule():
    days = pd.date_range(st.session_state.start_date, st.session_state.end_date)
    all_names = st.session_state.juniors + st.session_state.seniors

    # Create quick lookup helpers
    def on_leave(name, date):
        return any(n == name and f <= date <= t for (n, f, t) in st.session_state.leaves)

    def is_rotator(name):
        return any(n == name for (n, _, _) in st.session_state.rotators)

    def is_active_rotator(name, date):
        for (n, f, t) in st.session_state.rotators:
            if n == name and f <= date <= t:
                return True
        return False

    def is_weekend(d, shift):
        return d.weekday() in [3, 4, 5] if shift.get("thur_weekend") else d.weekday() in [4, 5]

    # Compute active days
    active_days = {}
    for name in all_names:
        person_days = [d.date() for d in days]
        if is_rotator(name):
            person_days = [d.date() for d in days if is_active_rotator(name, d.date())]
        person_days = [d for d in person_days if not on_leave(name, d)]
        active_days[name] = person_days

    # Weighted fairness preparation
    extra = st.session_state.extra_oncalls
    weighted_days = {n: len(active_days[n]) * (1 + extra.get(n, 0)) for n in all_names}
    total_weighted = sum(weighted_days.values())
    if total_weighted == 0:
        st.error("All participants are on leave or have no active days.")
        return None, None, None

    # Count total slots
    total_slots = {"total": 0, "weekend": 0}
    for d in days:
        for s in st.session_state.shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if is_weekend(d, s):
                    total_slots["weekend"] += 1

    # Expected quotas
    expected = {}
    for n in all_names:
        ratio = weighted_days[n] / total_weighted
        expected[n] = {
            "total": total_slots["total"] * ratio,
            "weekend": total_slots["weekend"] * ratio
        }

    stats = {n: {"total": 0, "weekend": 0} for n in all_names}
    last_assigned = {}
    df_rows = []
    unfilled = []

    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        nf_today = set()

        for s in st.session_state.shifts:
            label = s["label"]
            role = s["role"]
            is_nf = s["night_float"]

            if is_nf:
                nf_pool = st.session_state.nf_seniors if role == "Senior" else st.session_state.nf_juniors
                person = "Unavailable"
                for p in nf_pool:
                    if d.date() in active_days.get(p, []) and last_assigned.get(p) != d.date():
                        person = p
                        last_assigned[p] = d
                        nf_today.add(p)
                        break
                row[label] = person
                if person == "Unavailable":
                    unfilled.append((d.date(), label))
                continue

            pool = st.session_state.seniors if role == "Senior" else st.session_state.juniors
            candidates = []
            for p in pool:
                if p in nf_today:
                    continue
                if p not in active_days or d.date() not in active_days[p]:
                    continue
                if last_assigned.get(p) == d.date():
                    continue
                last = last_assigned.get(p)
                if last and (d.date() - last).days < st.session_state.min_gap:
                    continue
                if is_rotator(p) and not is_active_rotator(p, d.date()):
                    continue
                candidates.append(p)

            if not candidates:
                row[label] = "Unavailable"
                unfilled.append((d.date(), label))
                continue

            deficits = {
                p: (expected[p]["weekend"] - stats[p]["weekend"] if is_weekend(d, s) else 0) +
                   (expected[p]["total"] - stats[p]["total"])
                for p in candidates
            }
            max_deficit = max(deficits.values())
            top = [p for p in candidates if deficits[p] == max_deficit]
            assigned = random.choice(top)
            row[label] = assigned
            stats[assigned]["total"] += 1
            if is_weekend(d, s):
                stats[assigned]["weekend"] += 1
            last_assigned[assigned] = d.date()

        df_rows.append(row)

    df = pd.DataFrame(df_rows)
    summary = pd.DataFrame([{**{"Name": n}, **stats[n],
                             **{f"Expected_{k}": round(expected[n][k], 1) for k in expected[n]}}
                            for n in all_names])
    return df, summary, pd.DataFrame(unfilled, columns=["Date", "Shift"])

# == END ==
