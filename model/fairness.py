from __future__ import annotations

from typing import Dict, List, TypedDict

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import (
    ShiftTemplate,
    InputData,
    normalized_blackouts,
    normalized_leaves,
    normalized_perks,
    normalized_reductions,
)
from .points import classify_slot, slot_points
from .utils import (
    compact_date_range,
    effective_points,
    is_weekend,
    weekend_holiday_dates,
)

__all__ = [
    "ResidentPoints",
    "calculate_points",
    "calculate_label_counts",
    "format_fairness_log",
    "fairness_range_lines",
    "load_annotation_notes",
    "preference_satisfaction",
    "schedule_quality",
    "quality_diagnosis",
    "assignment_rationale",
]


class ResidentPoints(TypedDict):
    """Per-resident summary produced by :func:`calculate_points`.

    ``total`` / ``weekend`` / ``labels`` are *regular* points. Night-float work
    is a separate coverage overlay outside the regular point system, so
    ``night_float`` here is an informational **duty-day count** (how many NF
    cells the resident covered), never regular points.
    """

    total: float
    weekend: float
    night_float: float  # NF duty *days* (informational), not regular points
    labels: Dict[str, float]


def _empty_points() -> ResidentPoints:
    return {"total": 0.0, "weekend": 0.0, "labels": {}, "night_float": 0.0}


def _nf_cell_keys(df) -> set:
    """(date-iso, label) cells covered by the night-float overlay, from attrs."""
    from .night_float import nf_cells_from_attr

    return set(nf_cells_from_attr(df))


def _closed_cell_keys(df) -> set:
    """(date-iso, label) cells that are closed (stood down), from attrs."""
    from .closures import closed_cells_from_attr

    return set(closed_cells_from_attr(df))


def calculate_points(df: pd.DataFrame, data: InputData) -> Dict[str, ResidentPoints]:
    """Per-resident regular points; night-float-covered cells are excluded."""
    summary: Dict[str, ResidentPoints] = {
        name: _empty_points() for name in data.juniors + data.seniors
    }
    weekend_dates = weekend_holiday_dates(data)
    nf_cells = _nf_cell_keys(df)
    closed_cells = _closed_cell_keys(df)
    for row in df.to_dict("records"):
        day = row.get("Date")
        day_key = day.isoformat() if hasattr(day, "isoformat") else day
        for sh in data.shifts:
            if (day_key, sh.label) in closed_cells:
                # Closed cell: the shift is stood down — no resident, no points.
                continue
            person = row.get(sh.label)
            if person in (None, "Unfilled") or not isinstance(person, str):
                # None, the explicit Unfilled marker, or a NaN a hand-built /
                # re-imported frame may carry (pandas coerces None to NaN in
                # string columns) — all mean "no resident".
                continue
            info = summary.setdefault(person, _empty_points())
            if (day_key, sh.label) in nf_cells:
                # Night-float overlay cell: outside the regular point system;
                # record the duty day and move on (no regular points).
                info["night_float"] += 1
                continue
            # Shared classification (model.points) — the same source the solver
            # optimises against, so reporting can never drift from it.
            slot = classify_slot(day, sh, data, weekend_dates)
            info["total"] += slot.points
            info["labels"][sh.label] = info["labels"].get(sh.label, 0.0) + slot.points
            if slot.weekend:
                info["weekend"] += slot.points
    return summary


def calculate_label_counts(df: pd.DataFrame, data: InputData) -> Dict[str, Dict[str, int]]:
    """Number of *regular* calls per shift label per resident (counts, not points).

    Night-float-covered cells are excluded — they are the coverage overlay, not
    regular calls.
    """
    counts: Dict[str, Dict[str, int]] = {
        name: {} for name in data.juniors + data.seniors
    }
    reserved = _nf_cell_keys(df) | _closed_cell_keys(df)
    for row in df.to_dict("records"):
        day = row.get("Date")
        day_key = day.isoformat() if hasattr(day, "isoformat") else day
        for sh in data.shifts:
            if (day_key, sh.label) in reserved:
                continue  # NF overlay or closed cell — not a regular call
            person = row.get(sh.label)
            if person in (None, "Unfilled") or not isinstance(person, str):
                continue  # no resident (incl. NaN from re-imported frames)
            per = counts.setdefault(person, {})
            per[sh.label] = per.get(sh.label, 0) + 1
    return counts


def preference_satisfaction(df: pd.DataFrame, data: InputData) -> Dict[str, tuple]:
    """Per person with preferences: (matched criteria, criteria opportunities).

    Each assignment the person worked contributes one opportunity per
    configured preference axis (shift type, day type) — the same counting the
    solver's preference rewards use — so "7/10" reads: of 10 axis-checks
    across their calls, 7 came out preferred.
    """
    preferred = data.preferred_shifts or {}
    day_type = data.preferred_day_type or {}
    people = set(preferred) | set(day_type)
    if not people:
        return {}
    weekend_dates = weekend_holiday_dates(data)
    reserved = _nf_cell_keys(df) | _closed_cell_keys(df)
    counters: Dict[str, list] = {p: [0, 0] for p in people}
    for row in df.to_dict("records"):
        day = row.get("Date")
        day_key = day.isoformat() if hasattr(day, "isoformat") else day
        for sh in data.shifts:
            if (day_key, sh.label) in reserved:
                continue
            person = row.get(sh.label)
            if person not in counters:
                continue
            slot = classify_slot(day, sh, data, weekend_dates)
            labels = preferred.get(person)
            if labels:
                counters[person][1] += 1
                if sh.label in labels:
                    counters[person][0] += 1
            wants = day_type.get(person)
            if wants in ("weekend", "weekday"):
                counters[person][1] += 1
                if (wants == "weekend") == bool(slot.weekend):
                    counters[person][0] += 1
    return {p: (matched, total) for p, (matched, total) in counters.items()}


def fairness_range_lines(points: Dict[str, ResidentPoints]) -> List[str]:
    """Return human-readable range summaries for totals and weekends."""
    lines: List[str] = []
    totals = [v["total"] for v in points.values()]
    if totals:
        total_min = min(totals)
        total_max = max(totals)
        lines.append(
            f"Total points min {total_min:.1f}, max {total_max:.1f}, range {total_max - total_min:.1f}"
        )

    wk_totals = [v["weekend"] for v in points.values()]
    if wk_totals:
        wk_min = min(wk_totals)
        wk_max = max(wk_totals)
        lines.append(
            f"Weekend points min {wk_min:.1f}, max {wk_max:.1f}, range {wk_max - wk_min:.1f}"
        )

    nf_days = [v.get("night_float", 0.0) for v in points.values()]
    if any(nf_days):  # NF duty days (informational — outside regular fairness)
        lines.append(
            f"Night-float duty days min {min(nf_days):.0f}, max {max(nf_days):.0f} "
            "(outside regular fairness)"
        )

    return lines


def _resolved_target(df, key: str, fallback):
    """Prefer a solver-resolved target stashed on ``df.attrs`` over the input.

    ``build_schedule`` no longer mutates the caller's ``InputData``; it exposes the
    auto-computed targets on the frame instead, so deviation reporting reads them
    from there and falls back to any target the caller set explicitly.
    """
    attrs = getattr(df, "attrs", {}) or {}
    return attrs[key] if key in attrs and attrs[key] is not None else fallback


def load_annotation_notes(person: str, data: InputData) -> List[str]:
    """Load-shaping notes for a resident: group / perk / exemption / leave.

    The fairness targets already embed the group and perk factors (via the
    availability weights), so deviations stay honest against the reduced or
    raised share — these notes just make the *why* visible. Shared by the
    fairness log lines and the fairness table's Notes column, so the two can
    never disagree. Only configured features produce notes.
    """
    notes: List[str] = []
    group = (data.resident_groups or {}).get(person)
    if group is not None:
        factor = (data.group_factors or {}).get(group)
        if factor is not None and factor != 1.0:
            notes.append(f"[{group} ×{factor:.2f}]")
    for perk in normalized_perks(data.perks):
        if perk.name != person:
            continue
        start = perk.start.isoformat() if perk.start else ""
        end = perk.end.isoformat() if perk.end else "forever"
        notes.append(f"[perk ×{perk.factor:.2f} {start}→{end}]")
    labels = (data.exempt_shifts or {}).get(person)
    if labels:
        notes.append(f"[exempt: {', '.join(sorted(labels))}]")
    for b in normalized_blackouts(data.blackouts):
        covered = (
            (data.named_groups or {}).get(b.group, ())
            if b.group is not None
            else b.members
        )
        if person not in covered:
            continue
        # Compact date range (e.g. "12–17 Jul") — the full ISO form made these
        # tokens so long they wrecked the fairness table and PDF layout.
        note = f"[blackout {b.group or 'ad-hoc'} {compact_date_range(b.start, b.end)}"
        if b.night_before:
            note += " +night-before"
        if not b.compensated:
            note += " uncomp"
        notes.append(note + "]")
    for red in normalized_reductions(data.reductions):
        covered = (
            (data.named_groups or {}).get(red.group, ())
            if red.group is not None
            else red.members
        )
        if person not in covered:
            continue
        mode = "same-total" if red.keep_total else "repay-later"
        notes.append(
            f"[reduced {', '.join(sorted(red.labels))} ×{red.factor:.2f} "
            f"{compact_date_range(red.start, red.end)} {mode}]"
        )
    prefer_bits = []
    labels = (data.preferred_shifts or {}).get(person)
    if labels:
        prefer_bits.append(", ".join(sorted(labels)))
    wants = (data.preferred_day_type or {}).get(person)
    if wants in ("weekend", "weekday"):
        prefer_bits.append(f"{wants}s")
    if prefer_bits:
        notes.append(f"[prefers: {'; '.join(prefer_bits)}]")
    partners = sorted(
        other
        for pair in (data.avoid_pairs or [])
        for who, other in ((pair[0], pair[1]), (pair[1], pair[0]))
        if who == person and other != person
    )
    if partners:
        notes.append(f"[avoids: {', '.join(partners)}]")
    # Leave summary, clipped to the block (a window outside it has no effect).
    comp_days = uncomp_days = 0
    for name, start, end, compensated in normalized_leaves(data.leaves):
        if name != person:
            continue
        lo = max(start, data.start_date)
        hi = min(end, data.end_date)
        days = (hi - lo).days + 1
        if days <= 0:
            continue
        if compensated:
            comp_days += days
        else:
            uncomp_days += days
    if comp_days:
        notes.append(f"[leave {comp_days}d comp]")
    if uncomp_days:
        notes.append(f"[leave {uncomp_days}d uncomp]")
    return notes


def _load_annotations(person: str, data: InputData) -> str:
    """The log-line rendering of :func:`load_annotation_notes`."""
    notes = load_annotation_notes(person, data)
    return (" " + " ".join(notes)) if notes else ""


def format_fairness_log(
    df: pd.DataFrame, data: InputData, points: Dict[str, ResidentPoints] | None = None
) -> str:
    """Generate a human-readable fairness log.

    Built to be a reliable verification artifact: it opens with a health line
    (slots filled / unfilled), flags any resident whose total load is more than
    one point off their target as ``[OVER]`` / ``[UNDER]``, and ends with an
    explicit list of unfilled slots — so coverage gaps and unfair outliers can't
    be missed when skimming the log.
    """
    pts = points or calculate_points(df, data)
    target_total = _resolved_target(df, "target_total", data.target_total)
    target_total_map = _resolved_target(df, "target_total_map", data.target_total_map)
    target_weekend = _resolved_target(df, "target_weekend", data.target_weekend)
    target_label = _resolved_target(df, "target_label", data.target_label)

    records = df.to_dict("records")
    shift_by_label = {s.label: s for s in data.shifts}
    labels = list(shift_by_label)
    nf_cells = _nf_cell_keys(df)
    closed_cells = _closed_cell_keys(df)

    def _is_nf_cell(day, label) -> bool:
        key = day.isoformat() if hasattr(day, "isoformat") else day
        return (key, label) in nf_cells

    def _is_reserved(day, label) -> bool:
        key = day.isoformat() if hasattr(day, "isoformat") else day
        return (key, label) in nf_cells or (key, label) in closed_cells

    # A closed cell holds "Closed" (not None/"Unfilled"), so it is neither a
    # coverage gap nor a regular assignment — exclude reserved cells from both.
    unfilled = [
        (row.get("Date"), label)
        for row in records
        for label in labels
        if row.get(label) in (None, "Unfilled")
        and not _is_reserved(row.get("Date"), label)
    ]
    nf_covered = sum(
        1 for row in records for label in labels if _is_nf_cell(row.get("Date"), label)
    )
    closed = sum(
        1 for row in records for label in labels
        if _is_reserved(row.get("Date"), label) and not _is_nf_cell(row.get("Date"), label)
    )
    total_slots = len(records) * len(labels) - nf_covered - closed  # regular demand
    filled = total_slots - len(unfilled)

    # Checksum over *regular* demand: assigned + unfilled = available (reserved
    # cells — NF overlay and closed — are outside the regular point system).
    assigned_pts = sum(info["total"] for info in pts.values())
    unfilled_pts = sum(
        effective_points(day, shift_by_label[label], data) for day, label in unfilled
    )
    available_pts = sum(
        effective_points(row.get("Date"), sh, data)
        for row in records for sh in data.shifts
        if not _is_reserved(row.get("Date"), sh.label)
    )

    def _person_total_target(person):
        return (target_total_map or {}).get(person, target_total)

    def _total_dev(person):
        tgt = _person_total_target(person)
        return None if tgt is None else pts[person]["total"] - tgt

    closed_note = f", {closed} closed" if closed else ""
    lines: List[str] = [
        f"Schedule health: {filled}/{total_slots} slots filled "
        f"({len(unfilled)} unfilled{closed_note}).",
        f"Points: {assigned_pts:.1f} assigned + {unfilled_pts:.1f} unfilled "
        f"= {available_pts:.1f} available"
        + ("" if abs(assigned_pts + unfilled_pts - available_pts) < 1e-6
           else " (MISMATCH — totals do not reconcile!)"),
    ]

    # Worst total-deviation first so outliers sit at the top of the report.
    for person in sorted(pts, key=lambda p: (-abs(_total_dev(p) or 0.0), p)):
        info = pts[person]
        role = "Senior" if person in data.seniors else "Junior"
        nf_days = int(info.get("night_float", 0.0))
        nf_note = f", NF duty {nf_days}d" if nf_days else ""
        line = f"{person} ({role}{nf_note}): total {info['total']:.1f}"
        total_flag = ""
        tgt = _person_total_target(person)
        if tgt is not None:
            dev = info['total'] - tgt
            line += f" (target {tgt:.1f}, dev {dev:+.1f})"
            if dev > 1.0:
                total_flag = " [OVER]"
            elif dev < -1.0:
                total_flag = " [UNDER]"
        line += f", weekend {info['weekend']:.1f}"
        if target_weekend and person in target_weekend:
            wt = target_weekend[person]
            line += f" (target {wt:.1f}, dev {info['weekend'] - wt:+.1f})"
        person_labels = set(info['labels'])
        person_labels.update(
            label for (target_person, label) in (target_label or {})
            if target_person == person
        )
        for label in sorted(person_labels):
            val = info['labels'].get(label, 0.0)
            line += f", {label} {val:.1f}"
            if target_label and (person, label) in target_label:
                ldev = val - target_label[(person, label)]
                line += f" (dev {ldev:+.1f})"
        penalty = (data.extra_points or {}).get(person, 0.0)
        penalty_note = f" [+{penalty:g} penalty applied]" if penalty > 0 else ""
        lines.append(line + penalty_note + _load_annotations(person, data) + total_flag)
    lines.extend(fairness_range_lines(pts))

    # Fold constraint checks in so a hand-edited schedule's violations surface here.
    from .validation import validate_schedule  # lazy: validation imports optimiser
    issues = validate_schedule(df, data)
    if issues:
        lines.append("Constraint violations:")
        lines.extend(f"  {issue}" for issue in issues)

    if unfilled:
        lines.append("Unfilled slots:")
        lines.extend(f"  {day} — {label}" for day, label in unfilled)
    return "\n".join(lines)


def schedule_quality(
    df: pd.DataFrame, data: InputData, points: Dict[str, ResidentPoints] | None = None
) -> Dict[str, float]:
    """Return a 0-100 schedule quality score and its components.

    Blends coverage (fraction of slots filled), total-target balance, and
    weekend-target balance. Balance is measured on each resident's deviation
    from the solver-resolved target, not on raw points. This matters when
    leave, caps, perks, penalties, availability, or carryover intentionally
    give residents different fair shares.

    Within each role, one role-eligible shift is treated as an unavoidable
    atomic rounding step. Anything beyond that step lowers the score. Thus a
    4-vs-0 weekend split around equal targets is still flagged even when total
    points happen to be equal, while a 1-vs-0 split caused by one indivisible
    one-point call is not.
    """
    pts = points if points is not None else calculate_points(df, data)
    labels = [s.label for s in data.shifts]
    records = df.to_dict("records")
    reserved = _nf_cell_keys(df) | _closed_cell_keys(df)

    def _key(day, label):
        return (day.isoformat() if hasattr(day, "isoformat") else day, label)

    # Coverage is over regular demand only: reserved cells (NF overlay + closed)
    # are neither demand nor a gap, so they drop out of both numerator and
    # denominator.
    total_slots = 0
    filled = 0
    for row in records:
        day = row.get("Date")
        for label in labels:
            if _key(day, label) in reserved:
                continue
            total_slots += 1
            if row.get(label) not in (None, "Unfilled"):
                filled += 1
    coverage = filled / total_slots if total_slots else 1.0

    # Juniors and seniors work disjoint pools, so every range and atomic
    # allowance is role-specific. The worst role determines the component.
    role_members = {
        "Junior": [p for p in data.juniors if p in pts],
        "Senior": [p for p in data.seniors if p in pts],
    }
    all_slots = [
        slot for slot in slot_points(data)
        if _key(slot.day, slot.shift.label) not in reserved
    ]

    target_total = _resolved_target(df, "target_total", data.target_total)
    target_total_map = _resolved_target(df, "target_total_map", data.target_total_map)
    target_weekend = _resolved_target(df, "target_weekend", data.target_weekend)

    def _dimension_value(person: str, dimension: str) -> float:
        return float(
            pts[person]["total"] if dimension == "total" else pts[person]["weekend"]
        )

    def _targets_for(members: List[str], dimension: str) -> Dict[str, float]:
        actual = {p: _dimension_value(p, dimension) for p in members}
        fallback = sum(actual.values()) / len(actual) if actual else 0.0
        targets: Dict[str, float] = {}
        for person in members:
            target = None
            if dimension == "total":
                target = (target_total_map or {}).get(person, target_total)
            elif target_weekend:
                target = target_weekend.get(person)
            targets[person] = fallback if target is None else float(target)
        return targets

    def _raw_role_range(dimension: str) -> float:
        ranges = []
        for members in role_members.values():
            values = [_dimension_value(p, dimension) for p in members]
            if len(values) > 1:
                ranges.append(max(values) - min(values))
        return max(ranges) if ranges else 0.0

    def _balance(dimension: str) -> tuple[float, float, float]:
        role_scores: List[float] = []
        deviation_ranges: List[float] = []
        allowances: List[float] = []
        for role, members in role_members.items():
            if len(members) < 2:
                continue
            targets = _targets_for(members, dimension)
            deviations = [_dimension_value(p, dimension) - targets[p] for p in members]
            dev_range = max(deviations) - min(deviations)
            eligible_slots = [s for s in all_slots if s.shift.role == role]
            if dimension == "weekend":
                eligible_slots = [s for s in eligible_slots if s.weekend]
            allowance = max((s.points for s in eligible_slots), default=0.0)
            excess = max(0.0, dev_range - allowance)
            mean_target = sum(abs(targets[p]) for p in members) / len(members)
            mean_actual = (
                sum(abs(_dimension_value(p, dimension)) for p in members) / len(members)
            )
            scale = max(mean_target, mean_actual, allowance)
            score = 1.0 if scale <= 0 else 1.0 - min(1.0, excess / scale)
            role_scores.append(score)
            deviation_ranges.append(dev_range)
            allowances.append(allowance)
        return (
            min(role_scores) if role_scores else 1.0,
            max(deviation_ranges) if deviation_ranges else 0.0,
            max(allowances) if allowances else 0.0,
        )

    total_range = _raw_role_range("total")
    weekend_range = _raw_role_range("weekend")
    balance_total, total_dev_range, total_allowance = _balance("total")
    balance_weekend, weekend_dev_range, weekend_allowance = _balance("weekend")

    score = 100.0 * (0.5 * coverage + 0.3 * balance_total + 0.2 * balance_weekend)
    return {
        "score": round(score, 1),
        "coverage": round(coverage, 3),
        "filled": filled,
        "total_slots": total_slots,
        "unfilled": total_slots - filled,
        "balance_total": round(balance_total, 3),
        "balance_weekend": round(balance_weekend, 3),
        "total_range": total_range,
        "weekend_range": weekend_range,
        "total_deviation_range": total_dev_range,
        "weekend_deviation_range": weekend_dev_range,
        "total_atomic_allowance": total_allowance,
        "weekend_atomic_allowance": weekend_allowance,
    }


def quality_diagnosis(df: pd.DataFrame, data: InputData, quality: Dict[str, float]) -> list:
    """Plain-language reasons a quality score is low, with what to change.

    Reads the solve metadata on ``df.attrs``, the score components, and the
    configuration's structural warnings, and turns them into actionable
    sentences ("the solver stopped early — raise the time limit", "min_gap
    caps each resident at N shifts", ...). Empty when nothing needs saying.
    """
    # Lazy import: validation imports the optimiser (and this module sits
    # below both), so importing it at module level would create a cycle.
    from .validation import config_warnings

    reasons: list = []
    attrs = getattr(df, "attrs", {}) or {}
    status = attrs.get("solver_status")
    wall = attrs.get("wall_time_sec")
    limit = attrs.get("time_limit_sec")
    if status == "FEASIBLE" and wall is not None and limit and wall >= 0.9 * limit:
        reasons.append(
            f"The solver used its whole {limit:.0f}s budget without proving the "
            "fairest schedule — the spread is very likely NOT the best achievable. "
            "Raise the solver time limit in ⑤ Review & run and regenerate; on "
            "shared hosting the same limit buys less search than on a fast machine."
        )

    structural = [
        w for w in config_warnings(data)
        if "min_gap" in w or "Structural workload" in w or "very tight" in w
        or "unfilled" in w.lower()
    ]
    if quality.get("unfilled"):
        reasons.append(
            f"{quality['unfilled']:.0f} slot(s) are unfilled, which costs coverage "
            "(half the score). "
            + ("The capacity advisories below explain why. "
               if structural else
               "Check caps, blackouts, exemptions, and roster size.")
        )
    if quality.get("balance_total", 1.0) < 0.9 and status == "OPTIMAL":
        reasons.append(
            "The total-point spread is proven unavoidable with the current "
            "rules — a hard rule (min_gap, caps, blackouts, availability) is "
            "forcing it, not the optimiser."
        )
    reasons.extend(structural)
    return reasons


def _eligible_pool(data: InputData, shift: ShiftTemplate) -> set:
    # Regular eligibility is role-based; NF pools no longer gate regular shifts
    # (a night-float-eligible shift on an uncovered date is an ordinary shift).
    return set(data.juniors) if shift.role == "Junior" else set(data.seniors)


def assignment_rationale(
    df: pd.DataFrame,
    data: InputData,
    day,
    label: str,
    points: Dict[str, ResidentPoints] | None = None,
) -> List[str]:
    """Return a heuristic explanation of why a slot holds its current value.

    This is a post-hoc rationale (eligibility + load standing), not a formal
    sensitivity analysis of the optimiser.
    """
    shift = next((s for s in data.shifts if s.label == label), None)
    if shift is None:
        return [f"No shift labelled '{label}'."]

    person = None
    for row in df.to_dict("records"):
        if row.get("Date") == day:
            person = row.get(label)
            break

    if person == "Closed":
        return [f"'{label}' is closed (stood down) on {day}: outside points and fairness."]

    if person in (None, "Unfilled"):
        nf_note = " night-float" if shift.night_float else ""
        if not _eligible_pool(data, shift):
            return [f"No resident is eligible for '{label}' ({shift.role}{nf_note})."]
        return [
            f"'{label}' is unfilled on {day}: every eligible resident was "
            "unavailable (leave, rotator window, night-float period or min-gap "
            "spacing) or assigning one would have worsened fairness."
        ]

    pts = points if points is not None else calculate_points(df, data)
    role = "Senior" if person in set(data.seniors) else "Junior"
    lines = [f"{person} is a {role} eligible for '{label}'."]

    totals = {p: v["total"] for p, v in pts.items()}
    if person in totals:
        fewer = sum(1 for v in totals.values() if v < totals[person])
        lines.append(
            f"{person} carries {totals[person]:.1f} total points; {fewer} of "
            f"{len(totals)} residents carry fewer — load balancing favoured this "
            "assignment."
        )
    if is_weekend(day, shift, data.weekend_days, weekend_holiday_dates(data)) and person in pts:
        lines.append(
            f"Weekend slot: {person} has {pts[person]['weekend']:.1f} weekend points."
        )
    return lines
