import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta, date
import math

# ────────────────────────────────────────────────────────────────────
# Page configuration – MUST precede every other Streamlit call
# ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("🪙 Idea Gold Scheduler – Stable & Fair v2025-05-16")

# ────────────────────────────────────────────────────────────────────────────────
# Helper: integer‑quota allocator (Hare–Niemeyer)
# ────────────────────────────────────────────────────────────────────────────────

def allocate_integer_quotas(float_quotas: dict, total_slots: int) -> dict:
    """
    Convert fractional quotas to integers that sum to *total_slots*.

    Early-return guard: if *total_slots* ≤ 0 or no participants were
    passed, immediately return a dict of zeros.  This prevents unnecessary
    work (and protects against weird negative remainders if the helper is
    reused elsewhere).
    """
    if total_slots <= 0 or not float_quotas:
        return {p: 0 for p in float_quotas}

    base = {p: math.floor(q) for p, q in float_quotas.items()}
    used = sum(base.values())
    remainder = {p: float_quotas[p] - base[p] for p in float_quotas}
    to_assign = total_slots - used
    if to_assign <= 0:
        return base

    # Give leftover slots to the largest remainders (alphabetical tie-break)
    extras = sorted(remainder.items(), key=lambda x: (-x[1], x[0]))[:to_assign]
    for p, _ in extras:
        base[p] += 1
    return base


# ────────────────────────────────────────────────────────────────────────────────
# Session‑state defaults and reset
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
    for k in (
        "shifts",
        "rotators",
        "leaves",
        "extra_oncalls",
        "weights",
        "nf_juniors",
        "nf_seniors",
    ):
        st.session_state.pop(k, None)
    st.experimental_rerun()

# ────────────────────────────────────────────────────────────────────────────────
# Shift template entry
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("⚙️ Shift Templates"):
    col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
    shift_label   = col1.text_input("Shift Label (e.g. ER1)")
    role          = col2.selectbox("Role", ["Junior", "Senior"])
    night_float   = col3.checkbox("Night Float")
    thur_weekend  = col4.checkbox("Thursday Night = Weekend")

    if st.button("Add Shift", key="btn_add_shift"):
        if not shift_label.strip():
            st.error("Shift label cannot be empty.")
        else:
            base = shift_label.strip()
            existing = {s["label"] for s in st.session_state.shifts}
            idx, unique = 1, base
            while unique in existing:
                idx += 1
                unique = f"{base} #{idx}"
            st.session_state.shifts.append(
                {
                    "label": unique,
                    "role": role,
                    "night_float": night_float,
                    "thur_weekend": thur_weekend,
                }
            )

    # Show list & delete picker
    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))
        delete_shift = st.selectbox(
            "Select a shift to delete",
            [""] + [s["label"] for s in st.session_state.shifts],
            key="del_shift_select",
        )
        if st.button("🗑️ Delete Shift") and delete_shift:
            st.session_state.shifts = [
                s for s in st.session_state.shifts if s["label"] != delete_shift
            ]
            st.session_state.pop("del_shift_select", None)  # forget picker state
            st.experimental_rerun()


# ────────────────────────────────────────────────────────────────────────────────
# Participants
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("👥 Participants"):
    use_demo = st.checkbox("Use Demo Participants", True)
    if use_demo:
        juniors = ["Alice", "Bob", "Charlie", "Dina"]
        seniors = ["Eli", "Fay", "Gina", "Hank"]
    else:
        juniors = [n.strip() for n in st.text_area("Juniors").splitlines() if n.strip()]
        seniors = [n.strip() for n in st.text_area("Seniors").splitlines() if n.strip()]

    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# ────────────────────────────────────────────────────────────────────────────────
# Night‑float eligibility
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("🌙 Night Float Eligibility"):
    st.session_state.nf_juniors = st.multiselect(
        "NF‑Eligible Juniors",
        juniors,
        default=st.session_state.nf_juniors,
        key="nf_j_select",
    )
    st.session_state.nf_seniors = st.multiselect(
        "NF‑Eligible Seniors",
        seniors,
        default=st.session_state.nf_seniors,
        key="nf_s_select",
    )

# ────────────────────────────────────────────────────────────────────────────────
# Extra on‑calls bias
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("⚖️ Extra On‑Calls"):
    for nm in juniors + seniors:
        st.session_state.extra_oncalls[nm] = st.number_input(
            f"Extra on‑calls for {nm}",
            0,
            10,
            value=st.session_state.extra_oncalls.get(nm, 0),
            key=f"extra_{nm}",
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
    leave_from, leave_to = st.date_input(
        "Leave Period",
        [st.session_state.start_date, st.session_state.start_date],
        key="leave_period",
    )
    if st.button("Add Leave", key="btn_add_leave") and leave_name and leave_from <= leave_to:
        entry = (leave_name, leave_from, leave_to)
        if entry not in st.session_state.leaves:
            st.session_state.leaves.append(entry)

    # Show list & delete picker
    if st.session_state.leaves:
        leaves_df = pd.DataFrame(
            st.session_state.leaves, columns=["Name", "From", "To"]
        )
        st.table(leaves_df)
        leave_labels = [
            f"{n}  {f.strftime('%Y-%m-%d')} → {t.strftime('%Y-%m-%d')}"
            for n, f, t in st.session_state.leaves
        ]
        delete_leave = st.selectbox(
            "Select a leave to delete", [""] + leave_labels, key="del_leave_select"
        )
        if st.button("🗑️ Delete Leave") and delete_leave:
            idx = leave_labels.index(delete_leave)
            st.session_state.leaves.pop(idx)
            st.session_state.pop("del_leave_select", None)
            st.experimental_rerun()




with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Rotator Name", [""] + juniors + seniors)
    rot_from, rot_to = st.date_input(
        "Rotator Period",
        [st.session_state.start_date, st.session_state.start_date],
        key="rot_period",
    )
    if st.button("Add Rotator", key="btn_add_rotator") and rot_name and rot_from <= rot_to:
        entry = (rot_name, rot_from, rot_to)
        if entry not in st.session_state.rotators:
            st.session_state.rotators.append(entry)

    # Show list & delete picker
    if st.session_state.rotators:
        rot_df = pd.DataFrame(
            st.session_state.rotators, columns=["Name", "From", "To"]
        )
        st.table(rot_df)
        rot_labels = [
            f"{n}  {f.strftime('%Y-%m-%d')} → {t.strftime('%Y-%m-%d')}"
            for n, f, t in st.session_state.rotators
        ]
        delete_rot = st.selectbox(
            "Select a rotator period to delete", [""] + rot_labels, key="del_rot_select"
        )
        if st.button("🗑️ Delete Rotator") and delete_rot:
            idx = rot_labels.index(delete_rot)
            st.session_state.rotators.pop(idx)
            st.session_state.pop("del_rot_select", None)
            st.experimental_rerun()


# ────────────────────────────────────────────────────────────────────────────────
# Helper predicates
# ────────────────────────────────────────────────────────────────────────────────

def is_weekend(dt: date, shift_cfg: dict) -> bool:
    return dt.weekday() in (4, 5) or (
        dt.weekday() == 3 and shift_cfg.get("thur_weekend", False)
    )

def on_leave(p: str, dt: date) -> bool:
    return any(nm == p and start <= dt <= end for nm, start, end in st.session_state.leaves)

def is_active_rotator(p: str, dt: date) -> bool:
    for nm, start, end in st.session_state.rotators:
        if nm == p:
            return start <= dt <= end
    return True

# ────────────────────────────────────────────────────────────────────────────────
# Core schedule builder (fairness‑first)
# ────────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────────
# Core schedule builder (fairness-first)  ⬇️  REPLACE WHOLE FUNCTION
# ────────────────────────────────────────────────────────────────────────────────
def build_schedule():
    shifts_cfg = st.session_state.shifts
    start, end = st.session_state.start_date, st.session_state.end_date
    days = pd.date_range(start, end)

    juniors, seniors = st.session_state.juniors, st.session_state.seniors
    pool = juniors + seniors

    # 1️⃣ WEIGHTS  (active days × leave bonus × extra on-calls)
    span = (end - start).days + 1
    leave_days = {p: 0 for p in pool}
    for p in pool:
        for nm, lf, lt in st.session_state.leaves:
            if nm == p:
                overlap = (min(lt, end) - max(lf, start)).days + 1
                leave_days[p] += max(0, overlap)

    active_days = {p: 0 for p in pool}
    for d in days:
        for p in pool:
            if not on_leave(p, d.date()) and is_active_rotator(p, d.date()):
                active_days[p] += 1

    weight = {
        p: active_days[p]
        * (1 + leave_days[p] / span)
        * (1 + st.session_state.extra_oncalls.get(p, 0))
        for p in pool
    }
    total_weight = sum(weight.values()) or 1

    # 2️⃣ SLOT COUNTS PER SHIFT LABEL
    shift_labels   = [s["label"] for s in shifts_cfg if not s["night_float"]]
    slot_totals    = {lbl: 0 for lbl in shift_labels}
    slot_weekends  = {lbl: 0 for lbl in shift_labels}

    for d in days:
        for s in shifts_cfg:
            if s["night_float"]:
                continue
            lbl = s["label"]
            slot_totals[lbl] += 1
            if is_weekend(d.date(), s):
                slot_weekends[lbl] += 1

    # 3️⃣ EXPECTED FRACTIONAL QUOTAS
    expected_total = {
        p: {lbl: slot_totals[lbl]   * weight[p] / total_weight for lbl in shift_labels}
        for p in pool
    }
    expected_weekend = {
        p: {lbl: slot_weekends[lbl] * weight[p] / total_weight for lbl in shift_labels}
        for p in pool
    }

    # 4️⃣ INTEGER TARGETS VIA HARE–NIEMEYER
    target_total, target_weekend = {}, {}
    for lbl in shift_labels:
        target_total[lbl]   = allocate_integer_quotas(
            {p: expected_total[p][lbl]   for p in pool}, slot_totals[lbl]
        )
        target_weekend[lbl] = allocate_integer_quotas(
            {p: expected_weekend[p][lbl] for p in pool}, slot_weekends[lbl]
        )

    # 5️⃣ STATS & LAST-ASSIGNED
    stats = {
        p: {lbl: {"total": 0, "weekend": 0} for lbl in shift_labels} for p in pool
    }
    last_assigned = {p: None for p in pool}

    # 6️⃣ PRE-ASSIGN NIGHT-FLOAT BLOCKS
    nf_assignments, unfilled = {}, []
    for cfg in [c for c in shifts_cfg if c["night_float"]]:
        nf_pool = (
            st.session_state.nf_juniors if cfg["role"] == "Junior"
            else st.session_state.nf_seniors
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

    # 7️⃣ DAY-BY-DAY ASSIGNMENT LOOP
    schedule_rows = []

    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}

        nf_today = {
            pers
            for lbl, tbl in nf_assignments.items() if d.date() in tbl
            for pers in [tbl[d.date()]]
        }

        for cfg in shifts_cfg:
            lbl = cfg["label"]

            # Handle NF slots (already assigned above)
            if cfg["night_float"]:
                row[lbl] = nf_assignments.get(lbl, {}).get(d.date(), "Unfilled")
                continue

            # ————————————————— Candidate filter with optional debug —————————————————
            role_pool = juniors if cfg["role"] == "Junior" else seniors
            filters = {
                "NF_today": lambda p: p not in nf_today,
                "On_leave": lambda p: not on_leave(p, d.date()),
                "Rotator":  lambda p: is_active_rotator(p, d.date()),
                "Min_gap":  lambda p: last_assigned[p] is None
                                   or (d.date() - last_assigned[p]).days >= st.session_state.min_gap,
            }
            eligible = role_pool.copy()
            for rule, fn in filters.items():
                eligible = [p for p in eligible if fn(p)]

            if st.session_state.get("debug_filters", False):
                st.write(f"🔍 {d.date()} {lbl} → eligible:", eligible)

            if not eligible:
                row[lbl] = "Unfilled"
                unfilled.append((d.date(), lbl))
                continue

            # Priority: meet WEEKEND integer quotas first, then TOTAL
            wknd = is_weekend(d.date(), cfg)
            if wknd and slot_weekends[lbl] > 0:
                under = [p for p in eligible if stats[p][lbl]["weekend"] < target_weekend[lbl][p]]
            else:
                under = [p for p in eligible if stats[p][lbl]["total"] < target_total[lbl][p]]

            if under:
                pick = sorted(under)[0]                      # deterministic tie-break
            else:
                def deficit(p):
                    return (
                        target_weekend[lbl][p] - stats[p][lbl]["weekend"],
                        target_total[lbl][p]  - stats[p][lbl]["total"],
                    )
                pick = max(sorted(eligible), key=deficit)     # most behind gets slot

            row[lbl] = pick
            stats[pick][lbl]["total"] += 1
            if wknd:
                stats[pick][lbl]["weekend"] += 1
            last_assigned[pick] = d.date()

        schedule_rows.append(row)

    # 8️⃣ BUILD OUTPUT DATAFRAMES
    df_schedule = pd.DataFrame(schedule_rows)

    summary_rows = []
    for p in pool:
        entry = {"Name": p}
        for lbl in shift_labels:
            entry[f"{lbl}_assigned_total"]   = stats[p][lbl]["total"]
            entry[f"{lbl}_expected_total"]   = target_total[lbl][p]
            entry[f"{lbl}_assigned_weekend"] = stats[p][lbl]["weekend"]
            entry[f"{lbl}_expected_weekend"] = target_weekend[lbl][p]
        summary_rows.append(entry)

    df_summary  = pd.DataFrame(summary_rows)
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
