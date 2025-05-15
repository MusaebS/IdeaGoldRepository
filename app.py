import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta, date

# --- Streamlit Setup ---
st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("🪙 Idea Gold Scheduler – Stable & Robust")

import math

def allocate_integer_quotas(float_quotas: dict, total_slots: int) -> dict:
    """
    Largest‐remainder method: 
      - floor everyone’s float quota
      - distribute remaining slots to highest remainders
    """
    # 1) Floor everybody
    base = {p: math.floor(q) for p, q in float_quotas.items()}
    used = sum(base.values())
    # 2) Compute remainders
    remainder = {p: float_quotas[p] - base[p] for p in float_quotas}
    # 3) How many slots left?
    to_assign = total_slots - used
    if to_assign <= 0:
        return base
    # 4) Give one extra to those with largest remainders (tie by name)
    extras = sorted(remainder.items(), key=lambda x: (-x[1], x[0]))[:to_assign]
    for p, _ in extras:
        base[p] += 1
    return base

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
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

if st.button("🔁 Reset All Data", key="btn_reset"):
    for k in [
        "shifts", "rotators", "leaves",
        "extra_oncalls", "weights",
        "nf_juniors", "nf_seniors"
    ]:
        st.session_state.pop(k, None)
    st.experimental_rerun()

# --- Shift Template Input ---
with st.expander("⚙️ Shift Templates"):
    col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
    shift_label = col1.text_input("Shift Label (e.g., ER1)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    night_float = col3.checkbox("Night Float")
    thur_weekend = col4.checkbox("Thursday Night = Weekend")

    if st.button("Add Shift", key="btn_add_shift"):
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
            f"Extra oncalls for {name}",
            0, 10,
            key=f"extra_{name}",
            value=st.session_state.extra_oncalls.get(name, 0)
        )

# --- Date Range & Rules ---
st.subheader("📅 Schedule Settings")
st.session_state.start_date = st.date_input("Start Date", st.session_state.start_date)
st.session_state.end_date = st.date_input("End Date", st.session_state.end_date)

bad_dates = (st.session_state.start_date > st.session_state.end_date)
if bad_dates:
    st.error("Start date must be on or before End date.")

gen_disabled = bad_dates

st.session_state.min_gap = st.slider("Minimum Days Between Shifts", 0, 7, st.session_state.min_gap)
st.session_state.nf_block_length = st.slider("Night Float Block Length", 1, 10, st.session_state.nf_block_length)

# --- Leaves ---
with st.expander("✈️ Leaves"):
    leave_name = st.selectbox("Leave Name", [""] + juniors + seniors)
    leave_from, leave_to = st.date_input("Leave Period", [st.session_state.start_date, st.session_state.start_date])
    if st.button("Add Leave", key="btn_add_leave") and leave_name:
        leave_entry = (leave_name, leave_from, leave_to)
        if leave_from <= leave_to and leave_entry not in st.session_state.leaves:
            st.session_state.leaves.append(leave_entry)

# --- Rotators ---
with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Rotator Name", [""] + juniors + seniors)
    rot_from, rot_to = st.date_input("Rotator Period", [st.session_state.start_date, st.session_state.start_date])
    if st.button("Add Rotator", key="btn_add_rotator") and rot_name:
        rot_entry = (rot_name, rot_from, rot_to)
        if rot_from <= rot_to and rot_entry not in st.session_state.rotators:
            st.session_state.rotators.append(rot_entry)

# --- Helper Functions ---
def is_weekend(date, shift):
    return date.weekday() in [4, 5] or (date.weekday() == 3 and shift.get("thur_weekend", False))

def on_leave(person, date):
    return any(name == person and start <= date <= end for name, start, end in st.session_state.leaves)

def is_active_rotator(person, date):
    for name, start, end in st.session_state.rotators:
        if name == person:
            return start <= date <= end
    return True

# --- Build Schedule ---
def build_schedule():
    shifts = st.session_state.shifts
    start_date = st.session_state.start_date
    end_date = st.session_state.end_date
    days = pd.date_range(start_date, end_date)

    juniors = st.session_state.juniors
    seniors = st.session_state.seniors
    pool = juniors + seniors

    total_span = (end_date - start_date).days + 1
    leave_days = {p: 0 for p in pool}
    for p in pool:
        for name, start, end in st.session_state.leaves:
            if name == p:
                overlap = (min(end, end_date) - max(start, start_date)).days + 1
                leave_days[p] += max(0, overlap)

    active_days = {p: 0 for p in pool}
    for p in pool:
        for d in days:
            if not on_leave(p, d.date()) and is_active_rotator(p, d.date()):
                active_days[p] += 1

    weighted = {
        p: active_days[p] * (1 + leave_days[p] / total_span) * (1 + st.session_state.extra_oncalls.get(p, 0))
        for p in pool
    }

    total_weight = sum(weighted.values()) or 1

    shift_labels = [s["label"] for s in shifts if not s["night_float"]]
    slot_counts = {lbl: 0 for lbl in shift_labels}
    weekend_slot_counts = {lbl: 0 for lbl in shift_labels}

    for d in days:
        for s in shifts:
            lbl = s["label"]
            if s["night_float"]:
                continue
            slot_counts[lbl] += 1
            if is_weekend(d, s):
                weekend_slot_counts[lbl] += 1

    expected = {
        p: {
            lbl: {
                'total': slot_counts[lbl] * weighted[p] / total_weight,
                'weekend': weekend_slot_counts[lbl] * weighted[p] / total_weight
            }
            for lbl in shift_labels
        } for p in pool
    }

    stats = {
        p: {
            lbl: {"total": 0, "weekend": 0} for lbl in shift_labels
        } for p in pool
    }
    last_assigned = {p: None for p in pool}

    nf_assignments = {}
    unfilled = []

    for s in [s for s in shifts if s["night_float"]]:
        nf_pool = st.session_state.nf_juniors if s["role"] == "Junior" else st.session_state.nf_seniors
        nf_assignments[s["label"]] = {}
        for i, d in enumerate(days):
            idx = (i // st.session_state.nf_block_length) % max(1, len(nf_pool))
            person = nf_pool[idx] if nf_pool else None
            if not person or on_leave(person, d.date()):
                unfilled.append((d.date(), s["label"]))
            else:
                nf_assignments[s["label"]][d.date()] = person

    schedule = []
    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        nf_today = set()

        for s in shifts:
            lbl = s["label"]
            if s["night_float"]:
                assigned = nf_assignments.get(lbl, {}).get(d.date(), "Unfilled")
                row[lbl] = assigned
                if assigned != "Unfilled":
                    nf_today.add(assigned)
                continue

            role_pool = juniors if s["role"] == "Junior" else seniors
            candidates = [
                p for p in role_pool
                if p not in nf_today
                and not on_leave(p, d.date())
                and is_active_rotator(p, d.date())
                and (last_assigned[p] is None or (d.date() - last_assigned[p]).days >= st.session_state.min_gap)
            ]

            if not candidates:
                row[lbl] = "Unfilled"
                unfilled.append((d.date(), lbl))
                continue

            def score(p):
                w_def = expected[p][lbl]['weekend'] - stats[p][lbl]['weekend']
                t_def = expected[p][lbl]['total'] - stats[p][lbl]['total']
                return (w_def, t_def)

            best = max(sorted(candidates), key=score)
            row[lbl] = best
            stats[best][lbl]['total'] += 1
            if is_weekend(d, s):
                stats[best][lbl]['weekend'] += 1
            last_assigned[best] = d.date()

        schedule.append(row)

    df = pd.DataFrame(schedule)
    summary = pd.DataFrame([
    {
        **{"Name": p},
        **{f"{lbl}_assigned": stats[p][lbl]['total'] for lbl in shift_labels},
        **{f"{lbl}_expected": round(expected[p][lbl]['total'], 1) for lbl in shift_labels},
    }
    for p in pool
])
    df_unfilled = pd.DataFrame(unfilled, columns=["Date", "Shift"])

    return df, summary, df_unfilled

# --- Generate Button ---
if st.button("🚀 Generate Schedule", disabled=gen_disabled, key="btn_generate"):
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
        st.download_button("Download Unfilled CSV", df_unfilled.to_csv(index=False), "unfilled.csv")
