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


# ────────────────────────────────────────────────────────────────────
# Post-process: swap a weekday with a weekend to fix weekend deficits
# Works label-by-label until every weekend quota is met (or no legal swap).
# ────────────────────────────────────────────────────────────────────
def balance_weekends(schedule_rows, stats, target_weekend, shift_cfg_map, min_gap, shift_labels):
    """
    schedule_rows : list[dict]   rows produced in the day-by-day loop
    stats          : stats[p][lbl][weekend/total]  (mutated in place)
    target_weekend : target counts from Hare–Niemeyer
    shift_cfg_map  : {label : cfg_dict}  so we can test Thursday-as-weekend
    min_gap        : int       days between same-person assignments
    shift_labels   : list[str] labels that are NOT night-float
    """
    def is_weekend_row(date_, label):
        cfg = shift_cfg_map[label]
        return date_.weekday() in (4, 5) or (date_.weekday() == 3 and cfg.get("thur_weekend", False))

    changed = True
    while changed:
        changed = False
        for lbl in shift_labels:
            # lists of over / under people for this label
            over  = [p for p in stats if stats[p][lbl]["weekend"] > target_weekend[lbl].get(p, 0)]
            under = [p for p in stats if stats[p][lbl]["weekend"] < target_weekend[lbl].get(p, 0)]
            if not over or not under:
                continue

            # pre-index rows by label assignment for quick look-up
            wk_rows = [(idx, r) for idx, r in enumerate(schedule_rows)
                       if is_weekend_row(r["Date"], lbl)]
            wd_rows = [(idx, r) for idx, r in enumerate(schedule_rows)
                       if not is_weekend_row(r["Date"], lbl)]

            for p_over in over:
                # a weekend row where p_over holds the slot
                w_idx, w_row = next(((i, r) for i, r in wk_rows if r[lbl] == p_over), (None, None))
                if w_row is None:
                    continue
                for p_under in under:
                    # a weekday row where p_under holds the slot
                    d_idx, d_row = next(((i, r) for i, r in wd_rows if r[lbl] == p_under), (None, None))
                    if d_row is None:
                        continue

                    # min-gap check for both people after the swap
                    w_date, d_date = w_row["Date"], d_row["Date"]

                    def violates(person, new_date):
                        """True if swapping would break any rule for *person*"""
                        return (
                            # violates min-gap for this label
                            any(
                                abs((new_date - r["Date"]).days) < min_gap
                                and r[lbl] == person
                                for r in schedule_rows
                            )
                            # on leave that day
                            or on_leave(person, new_date)
                            # outside rotator window that day
                            or not is_active_rotator(person, new_date)
                        )


                    if violates(p_over, d_date) or violates(p_under, w_date):
                        continue  # not legal, try next pair

                    # --- perform the swap ---
                    schedule_rows[w_idx][lbl] = p_under
                    schedule_rows[d_idx][lbl] = p_over
                    stats[p_over ][lbl]["weekend"] -= 1
                    stats[p_under][lbl]["weekend"] += 1
                    changed = True
                    break
                if changed:
                    break    # restart while-loop to recompute over/under

# ────────────────────────────────────────────────────────────────────
# Cross-bucket quota normaliser  (keeps everyone within ±tol overall)
# ────────────────────────────────────────────────────────────────────
def normalise_overall_quota(target_total: dict, tol: int = 1):
    """
    Adjust target_total IN-PLACE so each resident’s *sum over labels*
    differs from the overall mean by at most ±tol.
    """
    persons = list({p for q in target_total.values() for p in q})
    totals = {p: sum(q.get(p, 0) for q in target_total.values())
              for p in persons}
    ideal   = round(sum(totals.values()) / len(persons))

    def most_over_under():
        over  = max(persons, key=lambda x: totals[x] - ideal)
        under = min(persons, key=lambda x: totals[x] - ideal)
        return over, under

    while True:
        over, under = most_over_under()
        if (abs(totals[over] - ideal) <= tol and
            abs(totals[under] - ideal) <= tol):
            break  # all within tolerance

        moved = False
        for lbl, qdict in target_total.items():
            # only rebalance if BOTH people actually have an entry
            if over in qdict and under in qdict and qdict[over] > qdict[under]:
                qdict[over]    -= 1
                qdict[under]   += 1
                totals[over]   -= 1
                totals[under]  += 1
                moved = True
                break

        if not moved:
            break  # no further adjustments possible


# ────────────────────────────────────────────────────────────────────
# Fairness-vs-Median helper
# ────────────────────────────────────────────────────────────────────
def build_median_report(summary_df: pd.DataFrame, tol: int = 0):
    """
    Return rows where a resident's assigned_total or assigned_weekend
    differs from the *median assigned* for that label by more than `tol`.
    """
    rows = []
    # detect each shift label once
    for col in [c for c in summary_df.columns if c.endswith("_assigned_total")]:
        label = col.replace("_assigned_total", "")

        # compute medians of actual workload
        med_total   = summary_df[f"{label}_assigned_total"].median()
        med_weekend = summary_df[f"{label}_assigned_weekend"].median()

        for _, r in summary_df.iterrows():
            d_tot = r[f"{label}_assigned_total"]   - med_total
            d_wkd = r[f"{label}_assigned_weekend"] - med_weekend
            if abs(d_tot) > tol or abs(d_wkd) > tol:
                rows.append({
                    "Name": r["Name"],
                    "Label": label,
                    "Δ Total vs median":   int(d_tot),
                    "Δ Weekend vs median": int(d_wkd),
                })
    return pd.DataFrame(rows)



# ────────────────────────────────────────────────────────────────────────────────
# Core schedule builder (fairness‑first)
# ────────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────────
# Core schedule builder (fairness-first, role-aware, NF isolated)
# ────────────────────────────────────────────────────────────────────────────────
def build_schedule():
    shifts_cfg = st.session_state.shifts
    start, end = st.session_state.start_date, st.session_state.end_date
    days       = pd.date_range(start, end)

    juniors, seniors = st.session_state.juniors, st.session_state.seniors
    pool              = juniors + seniors                      # for summary only

    # 0️⃣  Staff who ever cover a night-float shift
    nf_staff = set()
    for cfg in shifts_cfg:
        if cfg["night_float"]:
            nf_pool = (
                st.session_state.nf_juniors if cfg["role"] == "Junior"
                else st.session_state.nf_seniors
            )
            nf_staff.update(nf_pool)

    regular_pool = [p for p in pool if p not in nf_staff]
    if not regular_pool:
        st.error("Everyone is on Night-Float – no one left for day shifts!")
        return None, None, None

    # 1️⃣  WEIGHTS  (active days × leave bonus × extra on-calls)
    span = (end - start).days + 1
    leave_days = {p: 0 for p in regular_pool}
    for p in regular_pool:
        for nm, lf, lt in st.session_state.leaves:
            if nm == p:
                overlap = (min(lt, end) - max(lf, start)).days + 1
                leave_days[p] += max(0, overlap)

    active_days = {p: 0 for p in regular_pool}
    for d in days:
        for p in regular_pool:
            if not on_leave(p, d.date()) and is_active_rotator(p, d.date()):
                active_days[p] += 1

    weight = {
        p: span * (1 + st.session_state.extra_oncalls.get(p, 0))
        for p in regular_pool
    }
    total_weight = sum(weight.values()) or 1  # not used after role split but kept

    # 2️⃣  SLOT COUNTS (total & weekend)
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

    # 3️⃣  EXPECTED FRACTIONAL QUOTAS  (role-aware)
    expected_total   = {p: {} for p in regular_pool}
    expected_weekend = {p: {} for p in regular_pool}

    for cfg in shifts_cfg:
        if cfg["night_float"]:
            continue
        lbl        = cfg["label"]
        role_pool  = juniors if cfg["role"] == "Junior" else seniors
        role_pool  = [p for p in role_pool if p in regular_pool]

        role_weight   = sum(weight[p] for p in role_pool) or 1
        total_slots   = slot_totals[lbl]
        weekend_slots = slot_weekends[lbl]

        for p in regular_pool:
            if p in role_pool:
                expected_total[p][lbl]   = total_slots   * weight[p] / role_weight
                expected_weekend[p][lbl] = weekend_slots * weight[p] / role_weight
            else:
                expected_total[p][lbl]   = 0.0
                expected_weekend[p][lbl] = 0.0

    # 4️⃣  INTEGER TARGETS VIA HARE–NIEMEYER  (role-aware)
    target_total, target_weekend = {}, {}
    for cfg in shifts_cfg:
        if cfg["night_float"]:
            continue
        lbl        = cfg["label"]
        role_pool  = juniors if cfg["role"] == "Junior" else seniors
        role_pool  = [p for p in role_pool if p in regular_pool]

        target_total[lbl] = allocate_integer_quotas(
            {p: expected_total[p][lbl]   for p in role_pool},
            slot_totals[lbl],
        )
        target_weekend[lbl] = allocate_integer_quotas(
            {p: expected_weekend[p][lbl] for p in role_pool},
            slot_weekends[lbl],
        )
    normalise_overall_quota(target_total, tol=1)

    # 5️⃣  STATS SETUP
    stats = {
        p: {lbl: {"total": 0, "weekend": 0} for lbl in shift_labels}
        for p in regular_pool
    }
    last_assigned = {p: None for p in regular_pool}

    # 6️⃣  PRE-ASSIGN NIGHT-FLOAT BLOCKS
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
            idx    = (i // st.session_state.nf_block_length) % len(nf_pool)
            person = nf_pool[idx]
            if on_leave(person, d.date()) or not is_active_rotator(person, d.date()):
                unfilled.append((d.date(), cfg["label"]))
            else:
                nf_assignments[cfg["label"]][d.date()] = person

    # 7️⃣  DAY-BY-DAY ASSIGNMENT LOOP
    schedule_rows = []

    for d in days:
        row      = {"Date": d.date(), "Day": d.strftime("%A")}
        nf_today = {
            pers for lbl, tbl in nf_assignments.items()
            if d.date() in tbl for pers in [tbl[d.date()]]
        }

        for cfg in shifts_cfg:
            lbl = cfg["label"]

            # Night-float cell already assigned
            if cfg["night_float"]:
                row[lbl] = nf_assignments.get(lbl, {}).get(d.date(), "Unfilled")
                continue

            role_pool = juniors if cfg["role"] == "Junior" else seniors
            role_pool = [p for p in role_pool if p in regular_pool]

            # ----- candidate filters -----
            filters = {
                "NF_today": lambda p: p not in nf_today,
                "On_leave": lambda p: not on_leave(p, d.date()),
                "Rotator":  lambda p: is_active_rotator(p, d.date()),
                "Min_gap":  lambda p: last_assigned[p] is None
                                   or (d.date() - last_assigned[p]).days >= st.session_state.min_gap,
            }
            eligible = role_pool.copy()
            for fn in filters.values():
                eligible = [p for p in eligible if fn(p)]

                        # Hard cap: no more than target_total + 1
            eligible = [
                p for p in eligible
                if stats[p][lbl]["total"] < target_total[lbl].get(p, 0) + 1
            ]

            # Weekend-only filter: drop anyone who has ALREADY met
            # their weekend integer quota for this label.
            wknd = is_weekend(d.date(), cfg)
            if wknd and slot_weekends[lbl] > 0:
                eligible = [
                    p for p in eligible
                    if stats[p][lbl]["weekend"] < target_weekend[lbl][p]
                ]

            if not eligible:
                row[lbl] = "Unfilled"
                unfilled.append((d.date(), lbl))
                continue


            wknd  = is_weekend(d.date(), cfg)
            under = []
            if wknd and slot_weekends[lbl] > 0:
                under = [p for p in eligible if stats[p][lbl]["weekend"] < target_weekend[lbl][p]]
            if not under:
                under = [p for p in eligible if stats[p][lbl]["total"] < target_total[lbl][p]]

            if under:
                random.shuffle(under)
                pick = under[0]
            else:
                random.shuffle(eligible)
                def deficit(p):
                    return (
                        target_weekend[lbl][p] - stats[p][lbl]["weekend"],
                        target_total[lbl][p]   - stats[p][lbl]["total"],
                    )
                pick = max(eligible, key=deficit)

            row[lbl] = pick
            stats[pick][lbl]["total"]   += 1
            if wknd:
                stats[pick][lbl]["weekend"] += 1
            last_assigned[pick] = d.date()

        # record today's assignments
        schedule_rows.append(row)

    # ---------------- post-process: perfect weekend balance ----------------
    balance_weekends(
        schedule_rows,
        stats,
        target_weekend,
        {cfg["label"]: cfg for cfg in shifts_cfg if not cfg["night_float"]},
        st.session_state.min_gap,
        shift_labels,
    )


    # 8️⃣  OUTPUT DATAFRAMES
    df_schedule = pd.DataFrame(schedule_rows)

    summary_rows = []
    for p in pool:                               # include NF staff with zeros
        entry = {"Name": p}
        for lbl in shift_labels:
            entry[f"{lbl}_assigned_total"]   = stats.get(p, {}).get(lbl, {}).get("total", 0)
            entry[f"{lbl}_expected_total"]   = target_total.get(lbl, {}).get(p, 0)
            entry[f"{lbl}_assigned_weekend"] = stats.get(p, {}).get(lbl, {}).get("weekend", 0)
            entry[f"{lbl}_expected_weekend"] = target_weekend.get(lbl, {}).get(p, 0)
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
    # ---------- fairness vs MEDIAN ----------
    FAIR_TOL = 0    # 0 = show every deviation, 1 = ignore ±1
    median_df = build_median_report(summ, FAIR_TOL)

    if not median_df.empty:
        st.warning("⚖️  Median fairness – residents above / below peer median")
        st.dataframe(median_df, hide_index=True)
        st.download_button(
            "Download median fairness CSV",
            median_df.to_csv(index=False),
            "median_fairness.csv",
            key="btn_dl_median",
        )
    else:
        st.info(f"✨ Everyone is within ±{FAIR_TOL} of the median for every label.")

    if not unf.empty:
        st.warning("⚠️ Unfilled Slots Detected")
        st.dataframe(unf)
    st.download_button("Download Schedule CSV", df.to_csv(index=False), "schedule.csv")
    st.download_button("Download Summary CSV", summ.to_csv(index=False), "summary.csv")
    if not unf.empty:
        st.download_button("Download Unfilled CSV", unf.to_csv(index=False), "unfilled.csv")


