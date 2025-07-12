import streamlit as st
import pandas as pd
import random
from datetime import date
import math


def allocate_integer_quotas(float_quotas: dict, total_slots: int) -> dict:
    """Convert fractional quotas to integers that sum to *total_slots*."""
    if total_slots <= 0 or not float_quotas:
        return {p: 0 for p in float_quotas}

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


def get_shift_points(dt: date, cfg: dict) -> float:
    base = cfg.get("points", 1)
    if cfg.get("night_float") or is_weekend(dt, cfg):
        return base * 2
    return base


def normalize_overall_quota(target_total, full_participants, tol=1):
    persons = list(full_participants)
    totals = {p: sum(target_total[lbl].get(p, 0) for lbl in target_total) for p in persons}
    ideal = round(sum(totals.values()) / len(persons))

    def most_over_under():
        over = max(persons, key=lambda x: totals[x] - ideal)
        under = min(persons, key=lambda x: totals[x] - ideal)
        return over, under

    min_allowed = {p: ideal - tol for p in persons}

    while True:
        over, under = most_over_under()
        if abs(totals[over] - ideal) <= tol and abs(totals[under] - ideal) <= tol:
            break

        moved = False
        for lbl, qdict in target_total.items():
            if (
                over in qdict and under in qdict
                and qdict[over] > qdict[under]
                and qdict[under] < min_allowed[under]
            ):
                qdict[over] -= 1
                qdict[under] += 1
                totals[over] -= 1
                totals[under] += 1
                moved = True
                break
        if not moved:
            break


def balance_weekends(schedule_rows, stats, target_weekend, shift_cfg_map, min_gap, shift_labels, last_assigned):
    def is_weekend_row(date_, label):
        cfg = shift_cfg_map[label]
        return date_.weekday() in (4, 5) or (date_.weekday() == 3 and cfg.get("thur_weekend", False))

    all_labels = [lbl for lbl in schedule_rows[0] if lbl not in ("Date", "Day")]

    changed = True
    while changed:
        changed = False
        for lbl in shift_labels:
            over = [p for p in stats if stats[p][lbl]["weekend"] > target_weekend[lbl].get(p, 0)]
            under = [p for p in stats if stats[p][lbl]["weekend"] < target_weekend[lbl].get(p, 0)]
            if not over or not under:
                continue

            wk_rows = [(idx, r) for idx, r in enumerate(schedule_rows) if is_weekend_row(r["Date"], lbl)]
            wd_rows = [(idx, r) for idx, r in enumerate(schedule_rows) if not is_weekend_row(r["Date"], lbl)]

            for p_over in over:
                w_idx, w_row = next(((i, r) for i, r in wk_rows if r[lbl] == p_over), (None, None))
                if w_row is None:
                    continue
                for p_under in under:
                    d_idx, d_row = next(((i, r) for i, r in wd_rows if r[lbl] == p_under), (None, None))
                    if d_row is None:
                        continue

                    w_date, d_date = w_row["Date"], d_row["Date"]

                    def violates(person, new_date, ignore_idx):
                        return (
                            any(
                                abs((new_date - r["Date"]).days) < min_gap and any(r[l] == person for l in all_labels)
                                for i, r in enumerate(schedule_rows) if i != ignore_idx
                            )
                            or on_leave(person, new_date)
                            or not is_active_rotator(person, new_date)
                        )

                    if violates(p_over, d_date, w_idx) or violates(p_under, w_date, d_idx):
                        continue

                    schedule_rows[w_idx][lbl] = p_under
                    schedule_rows[d_idx][lbl] = p_over
                    stats[p_over][lbl]["weekend"] -= 1
                    stats[p_under][lbl]["weekend"] += 1

                    def recompute_last(person):
                        dates = [r["Date"] for r in schedule_rows if any(r[l] == person for l in all_labels)]
                        last_assigned[person] = max(dates) if dates else None

                    recompute_last(p_over)
                    recompute_last(p_under)

                    changed = True
                    break
                if changed:
                    break

    for person in last_assigned:
        dates = [r["Date"] for r in schedule_rows if any(r[l] == person for l in all_labels)]
        last_assigned[person] = max(dates) if dates else None


def balance_totals(schedule_rows, stats, target_total, min_gap, shift_labels, last_assigned):
    """Swap over-assigned shifts to under-assigned staff keeping constraints."""
    all_labels = [lbl for lbl in schedule_rows[0] if lbl not in ("Date", "Day")]

    changed = True
    while changed:
        changed = False
        for lbl in shift_labels:
            over = [p for p in stats if stats[p][lbl]["total"] > target_total[lbl].get(p, 0)]
            under = [p for p in stats if stats[p][lbl]["total"] < target_total[lbl].get(p, 0)]
            if not over or not under:
                continue

            for idx, row in enumerate(schedule_rows):
                p_over = row.get(lbl)
                if p_over not in over:
                    continue
                dt = row["Date"]

                def violates(person):
                    return (
                        any(
                            abs((dt - r["Date"]).days) < min_gap and any(r[l] == person for l in all_labels)
                            for i, r in enumerate(schedule_rows) if i != idx
                        )
                        or on_leave(person, dt)
                        or not is_active_rotator(person, dt)
                    )

                for p_under in under:
                    if p_under == p_over or violates(p_under):
                        continue

                    row[lbl] = p_under
                    stats[p_over][lbl]["total"] -= 1
                    stats[p_under][lbl]["total"] += 1
                    last_assigned[p_under] = dt

                    changed = True
                    break
                if changed:
                    break
            if changed:
                break

    for person in last_assigned:
        dates = [r["Date"] for r in schedule_rows if any(r[l] == person for l in all_labels)]
        last_assigned[person] = max(dates) if dates else None


def balance_points(
    schedule_rows,
    stats,
    shift_cfg_map,
    expected_points,
    points_assigned,
    min_gap,
    shift_labels,
    last_assigned,
    max_iter=10,
):
    """Adjust assignments to better match expected point totals."""

    all_labels = [lbl for lbl in schedule_rows[0] if lbl not in ("Date", "Day")]

    def violates(person, dt, ignore_idx):
        return (
            any(
                abs((dt - r["Date"]).days) < min_gap and any(r[l] == person for l in all_labels)
                for i, r in enumerate(schedule_rows) if i != ignore_idx
            )
            or on_leave(person, dt)
            or not is_active_rotator(person, dt)
        )

    for _ in range(max_iter):
        over = [p for p in points_assigned if points_assigned[p] - expected_points.get(p, 0) > 0]
        under = [p for p in points_assigned if points_assigned[p] - expected_points.get(p, 0) < 0]
        if not over or not under:
            break

        over.sort(key=lambda p: points_assigned[p] - expected_points.get(p, 0), reverse=True)
        under.sort(key=lambda p: expected_points.get(p, 0) - points_assigned[p], reverse=True)

        moved = False
        for idx, row in enumerate(schedule_rows):
            for lbl in shift_labels:
                p_over = row.get(lbl)
                if p_over not in over:
                    continue
                dt = row["Date"]
                cfg = shift_cfg_map[lbl]
                pts = get_shift_points(dt, cfg)
                candidates = [p for p in under if p != p_over]
                for p_under in candidates:
                    role_pool = st.session_state.juniors if cfg["role"] == "Junior" else st.session_state.seniors
                    if p_under not in role_pool:
                        continue
                    if any(row[l] == p_under for l in shift_labels):
                        continue
                    if violates(p_under, dt, idx):
                        continue
                    if points_assigned[p_over] - pts < expected_points.get(p_over, 0):
                        continue
                    if points_assigned[p_under] + pts > expected_points.get(p_under, 0):
                        continue

                    row[lbl] = p_under
                    stats[p_over][lbl]["total"] -= 1
                    stats[p_under][lbl]["total"] += 1
                    if is_weekend(dt, cfg):
                        stats[p_over][lbl]["weekend"] -= 1
                        stats[p_under][lbl]["weekend"] += 1
                    points_assigned[p_over] -= pts
                    points_assigned[p_under] += pts

                    last_assigned[p_under] = dt
                    moved = True
                    break
                if moved:
                    break
            if moved:
                break
        if not moved:
            break

    for person in last_assigned:
        dates = [r["Date"] for r in schedule_rows if any(r[l] == person for l in all_labels)]
        last_assigned[person] = max(dates) if dates else None


def fill_unassigned_shifts(
    schedule_rows,
    stats,
    unfilled,
    shift_cfg_map,
    points_assigned,
    expected_points_total,
    juniors,
    seniors,
    regular_pool,
    shift_labels,
    target_total,
    target_weekend,
):
    """Assign remaining unfilled slots to the most under‑scheduled staff."""

    def has_conflict(person: str, dt: date) -> bool:
        for row in schedule_rows:
            if any(row[l] == person for l in shift_labels):
                if abs((dt - row["Date"]).days) < st.session_state.min_gap:
                    return True
        return False

    point_deficit = {
        p: expected_points_total.get(p, 0) - points_assigned.get(p, 0)
        for p in regular_pool
    }

    new_unfilled = []

    for dt, lbl in unfilled:
        row_idx = next((i for i, r in enumerate(schedule_rows) if r["Date"] == dt), None)
        if row_idx is None:
            continue
        row = schedule_rows[row_idx]
        if row.get(lbl) != "Unfilled":
            continue

        cfg = shift_cfg_map[lbl]
        role_pool = juniors if cfg["role"] == "Junior" else seniors
        role_pool = [p for p in role_pool if p in regular_pool]

        def eligible(p: str) -> bool:
            if p not in role_pool:
                return False
            if any(row[l] == p for l in shift_labels):
                return False
            if on_leave(p, dt) or not is_active_rotator(p, dt):
                return False
            if has_conflict(p, dt):
                return False
            return True

        total_deficit = {
            p: target_total.get(lbl, {}).get(p, 0) - stats[p][lbl]["total"]
            for p in role_pool
        }
        wkd_deficit = {
            p: target_weekend.get(lbl, {}).get(p, 0) - stats[p][lbl]["weekend"]
            for p in role_pool
        }


        candidates = [p for p in role_pool if eligible(p)]
        if not candidates:
            new_unfilled.append((dt, lbl))
            continue

        def priority(p: str):
            return (
                point_deficit.get(p, 0),
                wkd_deficit.get(p, 0) if is_weekend(dt, cfg) else 0,
                total_deficit.get(p, 0),
            )

        pick = max(candidates, key=priority)

        row[lbl] = pick
        stats[pick][lbl]["total"] += 1
        if is_weekend(dt, cfg):
            stats[pick][lbl]["weekend"] += 1
        points_assigned[pick] += get_shift_points(dt, cfg)

        point_deficit[pick] = expected_points_total.get(pick, 0) - points_assigned.get(pick, 0)

    return new_unfilled





def build_median_report(summary_df: pd.DataFrame, tol: int = 0):
    rows = []
    for col in [c for c in summary_df.columns if c.endswith("_assigned_total")]:
        label = col.replace("_assigned_total", "")
        med_total = summary_df[f"{label}_assigned_total"].median()
        med_weekend = summary_df[f"{label}_assigned_weekend"].median()
        for _, r in summary_df.iterrows():
            d_tot = r[f"{label}_assigned_total"] - med_total
            d_wkd = r[f"{label}_assigned_weekend"] - med_weekend
            if abs(d_tot) > tol or abs(d_wkd) > tol:
                rows.append({
                    "Name": r["Name"],
                    "Label": label,
                    "Δ Total vs median": int(d_tot),
                    "Δ Weekend vs median": int(d_wkd),
                })
    if "Assigned Points" in summary_df.columns:
        med_pts = summary_df["Assigned Points"].median()
        for _, r in summary_df.iterrows():
            d_pts = r["Assigned Points"] - med_pts
            if abs(d_pts) > tol:
                rows.append({
                    "Name": r["Name"],
                    "Label": "Points",
                    "Δ Points vs median": int(d_pts),
                })
    return pd.DataFrame(rows)


def build_expectation_report(summary_df: pd.DataFrame, tol: int = 0) -> pd.DataFrame:
    """Highlight deviations from expected totals, weekends and points."""
    rows = []
    records = summary_df.to_dict(orient="records")
    if not records:
        return pd.DataFrame(rows)

    cols = getattr(summary_df, "columns", None) or list(records[0].keys())
    for col in [c for c in cols if c.endswith("_assigned_total")]:
        label = col.replace("_assigned_total", "")
        for r in records:
            d_tot = r[f"{label}_assigned_total"] - r[f"{label}_expected_total"]
            d_wkd = r[f"{label}_assigned_weekend"] - r[f"{label}_expected_weekend"]
            if abs(d_tot) > tol or abs(d_wkd) > tol:
                rows.append({
                    "Name": r["Name"],
                    "Label": label,
                    "Δ Total vs expected": int(d_tot),
                    "Δ Weekend vs expected": int(d_wkd),
                })
    if "Assigned Points" in cols and "Expected Points" in cols:
        for r in records:
            d_pts = r["Assigned Points"] - r["Expected Points"]
            if abs(d_pts) > tol:
                rows.append({
                    "Name": r["Name"],
                    "Label": "Points",
                    "Δ Points vs expected": int(d_pts),
                })
    return pd.DataFrame(rows)


def build_schedule(group_by: str | None = None):
    shifts_cfg = st.session_state.shifts
    start, end = st.session_state.start_date, st.session_state.end_date
    days = pd.date_range(start, end)

    juniors, seniors = st.session_state.juniors, st.session_state.seniors
    pool = juniors + seniors

    points_assigned = {p: 0 for p in pool}
    expected_points_total = {p: 0.0 for p in pool}

    nf_staff = set()
    for cfg in shifts_cfg:
        if cfg["night_float"]:
            nf_pool = (
                st.session_state.nf_juniors if cfg["role"] == "Junior" else st.session_state.nf_seniors
            )
            nf_staff.update(nf_pool)

    regular_pool = [p for p in pool if p not in nf_staff]
    if not regular_pool:
        st.error("Everyone is on Night-Float – no one left for day shifts!")
        return None, None, None

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

    weight = {p: span * (1 + st.session_state.extra_oncalls.get(p, 0)) for p in regular_pool}

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

    expected_total = {p: {} for p in regular_pool}
    expected_weekend = {p: {} for p in regular_pool}

    for cfg in shifts_cfg:
        if cfg["night_float"]:
            continue
        lbl = cfg["label"]
        role_pool = juniors if cfg["role"] == "Junior" else seniors
        role_pool = [p for p in role_pool if p in regular_pool]
        base_pts = cfg.get("points", 1)

        role_weight = sum(weight[p] for p in role_pool) or 1
        total_slots = slot_totals[lbl]
        weekend_slots = slot_weekends[lbl]

        for p in regular_pool:
            if p in role_pool:
                expected_total[p][lbl] = total_slots * weight[p] / role_weight
                expected_weekend[p][lbl] = weekend_slots * weight[p] / role_weight
            else:
                expected_total[p][lbl] = 0.0
                expected_weekend[p][lbl] = 0.0

            weekday_share = expected_total[p][lbl] - expected_weekend[p][lbl]
            expected_points_total[p] += base_pts * weekday_share + base_pts * 2 * expected_weekend[p][lbl]

    target_total, target_weekend = {}, {}
    for cfg in shifts_cfg:
        if cfg["night_float"]:
            continue
        lbl = cfg["label"]
        role_pool = juniors if cfg["role"] == "Junior" else seniors
        role_pool = [p for p in role_pool if p in regular_pool]

        target_total[lbl] = allocate_integer_quotas({p: expected_total[p][lbl] for p in role_pool}, slot_totals[lbl])
        target_weekend[lbl] = allocate_integer_quotas({p: expected_weekend[p][lbl] for p in role_pool}, slot_weekends[lbl])

    span = (end - start).days + 1

    rotator_set = {p for p in regular_pool if active_days[p] < span and any(nm == p for nm, _, _ in st.session_state.rotators)}
    leave_set = {p for p in regular_pool if active_days[p] < span and any(nm == p for nm, _, _ in st.session_state.leaves)}

    full_participants = {p for p in regular_pool if active_days[p] == span or p in leave_set}

    for lbl, qdict in target_total.items():
        for p in rotator_set:
            if p in qdict:
                availability_ratio = active_days[p] / span
                qdict[p] = round(qdict[p] * availability_ratio)

    normalize_overall_quota(target_total, full_participants, tol=1)

    stats = {p: {lbl: {"total": 0, "weekend": 0} for lbl in shift_labels} for p in regular_pool}
    last_assigned = {p: None for p in regular_pool}

    nf_assignments, unfilled = {}, []
    for cfg in [c for c in shifts_cfg if c["night_float"]]:
        nf_pool = (st.session_state.nf_juniors if cfg["role"] == "Junior" else st.session_state.nf_seniors)
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
                pt = get_shift_points(d.date(), cfg)
                points_assigned[person] += pt
                expected_points_total[person] += pt

    schedule_rows = []
    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        nf_today = {pers for lbl, tbl in nf_assignments.items() if d.date() in tbl for pers in [tbl[d.date()]]}

        for cfg in shifts_cfg:
            lbl = cfg["label"]
            if cfg["night_float"]:
                row[lbl] = nf_assignments.get(lbl, {}).get(d.date(), "Unfilled")
                continue

            role_pool = juniors if cfg["role"] == "Junior" else seniors
            role_pool = [p for p in role_pool if p in regular_pool]
            filters = {
                "NF_today": lambda p: p not in nf_today,
                "On_leave": lambda p: not on_leave(p, d.date()),
                "Rotator": lambda p: is_active_rotator(p, d.date()),
                "Min_gap": lambda p: last_assigned[p] is None or (d.date() - last_assigned[p]).days >= st.session_state.min_gap,
            }
            eligible = role_pool.copy()
            for fn in filters.values():
                eligible = [p for p in eligible if fn(p)]
            eligible = [p for p in eligible if stats[p][lbl]["total"] < target_total[lbl].get(p, 0) + 1]
            wknd = is_weekend(d.date(), cfg)
            if wknd and slot_weekends[lbl] > 0:
                wk_eligible = [p for p in eligible if stats[p][lbl]["weekend"] < target_weekend[lbl][p]]
                if wk_eligible:
                    eligible = wk_eligible
            if not eligible:
                row[lbl] = "Unfilled"
                unfilled.append((d.date(), lbl))
                continue

            wknd = is_weekend(d.date(), cfg)
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
                        target_total[lbl][p] - stats[p][lbl]["total"],
                    )
                pick = max(eligible, key=deficit)

            row[lbl] = pick
            stats[pick][lbl]["total"] += 1
            if wknd:
                stats[pick][lbl]["weekend"] += 1
            points_assigned[pick] += get_shift_points(d.date(), cfg)
            last_assigned[pick] = d.date()

        schedule_rows.append(row)

    balance_weekends(
        schedule_rows,
        stats,
        target_weekend,
        {cfg["label"]: cfg for cfg in shifts_cfg if not cfg["night_float"]},
        st.session_state.min_gap,
        shift_labels,
        last_assigned,
    )

    balance_totals(
        schedule_rows,
        stats,
        target_total,
        st.session_state.min_gap,
        shift_labels,
        last_assigned,
    )

    balance_points(
        schedule_rows,
        stats,
        {cfg["label"]: cfg for cfg in shifts_cfg},
        {p: round(expected_points_total.get(p, 0), 1) for p in pool},
        points_assigned,
        st.session_state.min_gap,
        shift_labels,
        last_assigned,
    )

    prev = None
    for _ in range(3):
        unfilled = fill_unassigned_shifts(
            schedule_rows,
            stats,
            unfilled,
            {cfg["label"]: cfg for cfg in shifts_cfg},
            points_assigned,
            expected_points_total,
            juniors,
            seniors,
            regular_pool,
            shift_labels,

            target_total,
            target_weekend,
        )
        if prev is not None and len(unfilled) >= prev:
            break
        prev = len(unfilled)



    df_schedule = pd.DataFrame(schedule_rows)

    summary_rows = []
    for p in pool:
        entry = {
            "Name": p,
            "Assigned Points": points_assigned.get(p, 0),
            "Expected Points": round(expected_points_total.get(p, 0), 1),
        }
        for lbl in shift_labels:
            entry[f"{lbl}_assigned_total"] = stats.get(p, {}).get(lbl, {}).get("total", 0)
            entry[f"{lbl}_expected_total"] = target_total.get(lbl, {}).get(p, 0)
            entry[f"{lbl}_assigned_weekend"] = stats.get(p, {}).get(lbl, {}).get("weekend", 0)
            entry[f"{lbl}_expected_weekend"] = target_weekend.get(lbl, {}).get(p, 0)
        summary_rows.append(entry)

    df_summary = pd.DataFrame(summary_rows)
    df_unfilled = pd.DataFrame(unfilled, columns=["Date", "Shift"])

    # ------------------------------------------------------------------
    # Build compact summary
    # ------------------------------------------------------------------
    compact_rows = []
    for p in pool:
        tot = sum(stats.get(p, {}).get(lbl, {}).get("total", 0) for lbl in shift_labels)
        wkd = sum(stats.get(p, {}).get(lbl, {}).get("weekend", 0) for lbl in shift_labels)
        compact_rows.append({
            "Name": p,
            "Role": "Junior" if p in juniors else "Senior",
            "Total Assigned": tot,
            "Weekend Assigned": wkd,
            "Assigned Points": points_assigned.get(p, 0),
            "Expected Points": round(expected_points_total.get(p, 0), 1),
        })

    df_compact = pd.DataFrame(compact_rows)

    if group_by == "role":
        df_compact = (
            df_compact.groupby("Role")
            .sum(numeric_only=True)
            .reset_index()
        )
    elif group_by == "shift":
        rows = []
        for lbl in shift_labels:
            rows.append({
                "Shift": lbl,
                "Total Assigned": sum(stats[p][lbl]["total"] for p in stats),
                "Weekend Assigned": sum(stats[p][lbl]["weekend"] for p in stats),
            })
        df_compact = pd.DataFrame(rows)

    return df_schedule, df_summary, df_unfilled, df_compact
