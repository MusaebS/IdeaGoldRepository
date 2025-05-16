import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta, date
import math

"""
Idea Gold Scheduler – full Streamlit application
This version focuses on **fairness fixes**:
• Separate integer quotas for weekend and total per‑shift using the Hare–Niemeyer method.
• Weekend days now respect their own quotas before falling back to total‑deficit logic.
• Summary table shows assigned vs expected *total* and *weekend* counts.
• Fixed variable‑name mismatch for unfilled‑slot download.
"""

# ────────────────────────────────────────────────────────────────────────────────
# Helper: Hare–Niemeyer largest‑remainder → integer quotas
# ────────────────────────────────────────────────────────────────────────────────

def allocate_integer_quotas(float_quotas: dict, total_slots: int) -> dict:
    """Return integer quota per participant that sums to *total_slots*."""
    base = {p: math.floor(q) for p, q in float_quotas.items()}
    used = sum(base.values())
    remainder = {p: float_quotas[p] - base[p] for p in float_quotas}
    to_assign = total_slots - used
    if to_assign <= 0:
        return base
    extras = sorted(remainder.items(), key=lambda x: (-x[1], x[0]))[:to_assign]
    for p, _ in extras:
        base[p] += 1
    return base

# ────────────────────────────────────────────────────────────────────────────────
# Streamlit Page Config
# ────────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("🪙 Idea Gold Scheduler – Stable & Fair v2025‑05‑16")

# ────────────────────────────────────────────────────────────────────────────────
# Session‑state defaults & smart reset (preserve dates)
# ────────────────────────────────────────────────────────────────────────────────

def_state = {
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
for k, v in def_state.items():
    st.session_state.setdefault(k, v)

if st.button("🔁 Reset All Data", key="btn_reset"):
    for k in [
        "shifts",
        "rotators",
        "leaves",
        "extra_oncalls",
        "weights",
        "nf_juniors",
        "nf_seniors",
    ]:
        st.session_state.pop(k, None)
    st.experimental_rerun()

# ────────────────────────────────────────────────────────────────────────────────
# Shift templates – UI
# ────────────────────────────────────────────────────────────────────────────────

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
            base = shift_label.strip()
            existing = [s["label"] for s in st.session_state.shifts]
            i, unique = 1, base
            while unique in existing:
                i += 1
                unique = f"{base} #{i}"
            st.session_state.shifts.append(
                {
                    "label": unique,
                    "role": role,
                    "night_float": night_float,
                    "thur_weekend": thur_weekend,
                }
            )

    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# ────────────────────────────────────────────────────────────────────────────────
# Participant pools – demo vs custom
# ────────────────────────────────────────────────────────────────────────────────

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

# ────────────────────────────────────────────────────────────────────────────────
# Night‑float eligibility
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("🌙 Night Float Eligibility"):
    st.session_state.nf_juniors = st.multiselect(
        "NF‑Eligible Juniors",
        options=juniors,
        default=st.session_state.nf_juniors,
        key="nf_juniors_select",
    )
    st.session_state.nf_seniors = st.multiselect(
        "NF‑Eligible Seniors",
        options=seniors,
        default=st.session_state.nf_seniors,
        key="nf_seniors_select",
    )

# ────────────────────────────────────────────────────────────────────────────────
# Extra on‑calls bias
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("⚖️ Extra On‑Calls"):
    for nm in juniors + seniors:
        st.session_state.extra_oncalls[nm] = st.number_input(
            f"Extra on‑calls for {nm}",
            0,
            10,
            key=f"extra_{nm}",
            value=st.session_state.extra_oncalls.get(nm, 0),
        )

# ────────────────────────────────────────────────────────────────────────────────
# Date range & rules
# ────────────────────────────────────────────────────────────────────────────────

st.subheader("📅 Schedule Settings")
st.session_state.start_date = st.date_input("Start Date", st.session_state.start_date)
st.session_state.end_date = st.date_input("End Date", st.session_state.end_date)

if st.session_state.start_date > st.session_state.end_date:
    st.error("Start date must be on or before end date.")
    gen_disabled = True
else:
    gen_disabled = False

st.session_state.min_gap = st.slider(
    "Minimum Days Between Shifts", 0, 7, st.session_state.min_gap
)
st.session_state.nf_block_length = st.slider(
    "Night Float Block Length", 1, 10, st.session_state.nf_block_length
)

# ────────────────────────────────────────────────────────────────────────────────
# Leaves & rotators
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("✈️ Leaves"):
    leave_name = st.selectbox("Leave Name", [""] + juniors + seniors)
    leave_from, leave_to = st.date_input("Leave Period", [st.session_state.start_date, st.session_state.start_date])
    if st.button("Add Leave", key="btn_add_leave") and leave_name:
        if leave_from <= leave_to:
            entry = (leave_name, leave_from, leave_to)
            if entry not in st.session_state.leaves:
                st.session_state.leaves.append(entry)

with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Rotator Name", [""] + juniors + seniors)
    rot_from, rot_to = st.date_input("Rotator Period", [st.session_state.start_date, st.session_state.start_date])
    if st.button("Add Rotator", key="btn_add_rotator") and rot_name:
        if rot_from <= rot_to:
            entry = (rot_name, rot_from, rot_to)
            if entry not in st.session_state.rotators:
                st.session_state.rotators.append(entry)

# ────────────────────────────────────────────────────────────────────────────────
# Helper predicates
# ────────────────────────────────────────────────────────────────────────────────

def is_weekend(dt: date, shift_cfg: dict) -> bool:
    return dt.weekday() in (4, 5) or (dt.weekday() == 3 and shift_cfg.get("thur_weekend", False))


def on_leave(person: str, dt: date) -> bool:
    return any(nm == person and start <= dt <= end for nm, start, end in st.session_state.leaves)


def is_active_rotator(person: str, dt: date) -> bool:
    for nm, start, end in st.session_state.rotators:
        if nm == person:
            return start <= dt <= end
    return True  # not a rotator → always active

# ────────────────────────────────────────────────────────────────────────────────
# Core builder – NEW fairness logic
# ────────────────────────────────────────────────────────────────────────────────

def build_schedule():
    shifts_cfg = st.session_state.shifts
    start, end = st.session_state.start_date, st.session_state.end_date
    days = pd.date_range(start, end)

    juniors, seniors = st.session_state.juniors, st.session_state.seniors
    pool = juniors + seniors

    # 1️⃣  Weight each participant for quota calculation
    total_span = (end - start).days + 1
    leave_days = {p: 0 for p in pool}
    for p in pool:
        for nm, lv_from, lv_to in st.session_state.leaves:
            if nm == p:
                overlap = (min(lv_to, end) - max(lv_from, start)).days + 1
                leave_days[p] += max(0, overlap)

    active_days = {p: 0 for p in pool}
    for p in pool:
        for d in days:
            if not on_leave(p, d.date()) and is_active_rotator(p, d.date()):
                active_days[p] += 1

    weight = {
        p: active_days[p]
        * (1 + leave_days[p] / total_span)
        * (1 + st.session_state.extra_oncalls.get(p, 0))
        for p in pool
    }

    total_weight = sum(weight.values()) or 1

    # 2️⃣  Slot counts (total & weekend) per shift label (exclude NF)
    shift_labels = [s["label"] for s in shifts_cfg if not s["night_float"]]
    slot_totals = {lbl: 0 for lbl in shift_labels}
    slot_weekends = {lbl: 0 for lbl in shift_labels}

    for d in days:
        for s in shifts_cfg:
            if s["night_float"]:
                continue
            lbl = s["label"]
            slot_totals[lbl] += 1
            if is_weekend(d.date(), s):
                slot_weekends[lbl] += 1

    # 3️⃣  Expected fractional quotas
    expected_total = {
        p: {
            lbl: slot_totals[lbl] * weight[p] / total_weight
            for lbl in shift_labels
        }
        for p in pool
    }
    expected_weekend = {
        p: {
            lbl: slot_weekends[lbl] * weight[p] / total_weight
            for lbl in shift_labels
        }
        for p in pool
    }

    # 4️⃣  Integer targets via Hare–Niemeyer (total & weekend)
    target_total, target_weekend = {}, {}
    for lbl in shift_labels:
        float_tot = {p: expected_total[p][lbl] for p in pool}
        target_total[lbl] = allocate_integer_quotas(float_tot, slot_totals[lbl])

        float_wkd = {p: expected_weekend[p][lbl] for p in pool}
        target_weekend[lbl] = allocate_integer_quotas(
            float_wkd, slot_weekends
            slot_weekends[lbl] += 1

    # 3️⃣ Expected fractional quotas
    expected_total = {
        p: {
            lbl: slot_totals[lbl] * weight[p] / total_weight
            for lbl in shift_labels
        }
        for p in pool
    }
    expected_weekend = {
        p: {
            lbl: slot_weekends[lbl] * weight[p] / total_weight
            for lbl in shift_labels
        }
        for p in pool
    }

    # 4️⃣ Integer targets via Hare–Niemeyer (total & weekend)
    target_total, target_weekend = {}, {}
    for lbl in shift_labels:
        float_tot = {p: expected_total[p][lbl] for p in pool}
        target_total[lbl] = allocate_integer_quotas(float_tot, slot_totals[lbl])

        float_wkd = {p: expected_weekend[p][lbl] for p in pool}
        target_weekend[lbl] = allocate_integer_quotas(
            float_wkd, slot_weekends[lbl]
        )

    # 5️⃣ Stats init
    stats = {
        p: {lbl: {"total": 0, "weekend": 0} for lbl in shift_labels} for p in pool
    }
    last_assigned = {p: None for p in pool}

    # 6️⃣ Night‑float pre‑assignment (round‑robin blocks)
    nf_assignments = {}
    unfilled = []

    for cfg in [c for c in shifts_cfg if c["night_float"]]:
        nf_pool = (
            st.session_state.nf_juniors if cfg["role"] == "Junior" else st.session_state.nf_seniors
        )
        nf_assignments[cfg["label"]] = {}
        for i, d in enumerate(days):
            if not nf_pool:
                unfilled.append((d.date(), cfg["label"]))
                continue
            idx = (i // st.session_state.nf_block_length) % len(nf_pool)
            person = nf_pool[idx]
            if on_leave(person, d.date()) or not is_active_rotator(person, d.date()):
                unfilled.append((d.date(), cfg["label"]))
            else:
                nf_assignments[cfg["label"]][d.date()] = person

    # 7️⃣ Day‑by‑day assignment for non‑NF shifts
    schedule_rows = []

    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        nf_today = {
            pers
            for lbl, tbl in nf_assignments.items()
            if d.date() in tbl
            for pers in [tbl[d.date()]]
        }

        for cfg in shifts_cfg:
            lbl = cfg["label"]
            if cfg["night_float"]:
                row[lbl] = nf_assignments.get(lbl, {}).get(d.date(), "Unfilled")
                continue

            role_pool = juniors if cfg["role"] == "Junior" else seniors
            eligible = [
                p
                for p in role_pool
                if p not in nf_today
                and not on_leave(p, d.date())
                and is_active_rotator(p, d.date())
                and (
                    last_assigned[p] is None
                    or (d.date() - last_assigned[p]).days >= st.session_state.min_gap
                )
            ]

            if not eligible:
                row[lbl] = "Unfilled"
                unfilled.append((d.date(), lbl))
                continue

            wknd = is_weekend(d.date(), cfg)
            if wknd:
                under = [
                    p for p in eligible if stats[p][lbl]["weekend"] < target_weekend[lbl][p]
                ]
            else:
                under = [
                    p for p in eligible if stats[p][lbl]["total"] < target_total[lbl][p]
                ]

            if under:
                pick = sorted(under)[0]
            else:
                def deficit(p):
                    return (
                        target_weekend[lbl][p] - stats[p][lbl]["weekend"],
                        target_total[lbl][p] - stats[p][lbl]["total"],
                    )

                pick = max(sorted(eligible), key=deficit)

            row[lbl] = pick
            stats[pick][lbl]["total"] += 1
            if wknd:
                stats[pick][lbl]["weekend"] += 1
            last_assigned[pick] = d.date()

        schedule_rows.append(row)

    df_schedule = pd.DataFrame(schedule_rows)

    # 8️⃣ Summary table
    summary_rows = []
    for p in pool:
        ent = {"Name": p}
        for lbl in shift_labels:
            ent[f"{lbl}_assigned_total"] = stats[p][lbl]["total"]
            ent[f"{lbl}_expected_total"] = target_total[lbl][p]
            ent[f"{lbl}_assigned_weekend"] = stats[p][lbl]["weekend"]
            ent[f"{lbl}_expected_weekend"] = target_weekend[lbl][p]
        summary_rows.append(ent)

    df_summary = pd.DataFrame(summary_rows)
    df_unfilled = pd.DataFrame(unfilled, columns=["Date", "Shift"])

    return df_schedule, df_summary, df_unfilled

# ────────────────────────────────────────────────────────────────────────────────
# Generate button & outputs
# ────────────────────────────────────────────────────────────────────────────────

if st.button("🚀 Generate Schedule", disabled=False):
    df, summ, unf = build_schedule()
    st.success("✅ Schedule generated!")
    st.dataframe(df)
    st.subheader("📊 Assignment Summary")
    st.dataframe(summ)
    if not unf.empty:
        st.warning("⚠️ Unfilled Slots Detected")
        st.dataframe(unf)
    st.download_button("Download Schedule CSV", df.to_csv(index=False), "schedule.csv")
    st.download_button("Download Summary CSV", summ.to_csv(index=False), "summary.csv")
    if not unf.empty:
        st.download_button("Download Unfilled CSV", unf.to_csv(index=False), "unfilled.csv")
