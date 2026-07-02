from __future__ import annotations

from typing import Dict, List, TypedDict

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import ShiftTemplate, InputData, normalized_perks
from .points import classify_slot
from .utils import effective_points, is_weekend, weekend_holiday_dates

__all__ = [
    "ResidentPoints",
    "calculate_points",
    "format_fairness_log",
    "fairness_range_lines",
    "schedule_quality",
    "assignment_rationale",
]


class ResidentPoints(TypedDict):
    """Per-resident point summary produced by :func:`calculate_points`."""

    total: float
    weekend: float
    night_float: float
    labels: Dict[str, float]


def _empty_points() -> ResidentPoints:
    return {"total": 0.0, "weekend": 0.0, "labels": {}, "night_float": 0.0}


def calculate_points(df: pd.DataFrame, data: InputData) -> Dict[str, ResidentPoints]:
    """Return mapping of resident to total and weekend points per label."""
    summary: Dict[str, ResidentPoints] = {
        name: _empty_points() for name in data.juniors + data.seniors
    }
    weekend_dates = weekend_holiday_dates(data)
    for row in df.to_dict("records"):
        day = row.get("Date")
        for sh in data.shifts:
            person = row.get(sh.label)
            if person in (None, "Unfilled"):
                continue
            # Shared classification (model.points) — the same source the solver
            # optimises against, so reporting can never drift from it.
            slot = classify_slot(day, sh, data, weekend_dates)
            info = summary.setdefault(person, _empty_points())
            info["total"] += slot.points
            info["labels"][sh.label] = info["labels"].get(sh.label, 0.0) + slot.points
            if slot.night_float:
                info["night_float"] += slot.points
            if slot.weekend:
                info["weekend"] += slot.points
    return summary


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

    nf_totals = [v.get("night_float", 0.0) for v in points.values()]
    if any(nf_totals):  # only report when night-float shifts are in play
        nf_min = min(nf_totals)
        nf_max = max(nf_totals)
        lines.append(
            f"Night-float points min {nf_min:.1f}, max {nf_max:.1f}, range {nf_max - nf_min:.1f}"
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


def _load_annotations(person: str, data: InputData) -> str:
    """Group / perk / exemption notes for a resident's log line.

    The fairness targets already embed the group and perk factors (via the
    availability weights), so deviations stay honest against the reduced or
    raised share — these notes just make the *why* visible in the log.
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
    target_nf = _resolved_target(df, "target_night_float", data.target_night_float)

    records = df.to_dict("records")
    shift_by_label = {s.label: s for s in data.shifts}
    labels = list(shift_by_label)
    unfilled = [
        (row.get("Date"), label)
        for row in records
        for label in labels
        if row.get(label) in (None, "Unfilled")
    ]
    total_slots = len(records) * len(labels)
    filled = total_slots - len(unfilled)

    # Checksum: assigned + unfilled points must equal the points available. Uses
    # effective (weekday/holiday-adjusted) points so it still reconciles.
    assigned_pts = sum(info["total"] for info in pts.values())
    unfilled_pts = sum(
        effective_points(day, shift_by_label[label], data) for day, label in unfilled
    )
    available_pts = sum(
        effective_points(row.get("Date"), sh, data) for row in records for sh in data.shifts
    )

    def _person_total_target(person):
        return (target_total_map or {}).get(person, target_total)

    def _total_dev(person):
        tgt = _person_total_target(person)
        return None if tgt is None else pts[person]["total"] - tgt

    lines: List[str] = [
        f"Schedule health: {filled}/{total_slots} slots filled ({len(unfilled)} unfilled).",
        f"Points: {assigned_pts:.1f} assigned + {unfilled_pts:.1f} unfilled "
        f"= {available_pts:.1f} available"
        + ("" if abs(assigned_pts + unfilled_pts - available_pts) < 1e-6
           else " (MISMATCH — totals do not reconcile!)"),
    ]

    # Worst total-deviation first so outliers sit at the top of the report.
    for person in sorted(pts, key=lambda p: (-abs(_total_dev(p) or 0.0), p)):
        info = pts[person]
        role = "Senior" if person in data.seniors else "Junior"
        nf = info.get("night_float", 0.0)
        line = f"{person} ({role}, NF {nf:.1f}"
        if target_nf and person in target_nf:
            line += f" (target {target_nf[person]:.1f}, dev {nf - target_nf[person]:+.1f})"
        line += f"): total {info['total']:.1f}"
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
        for label in sorted(info['labels']):
            val = info['labels'][label]
            line += f", {label} {val:.1f}"
            if data.target_label and (person, label) in data.target_label:
                ldev = val - data.target_label[(person, label)]
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

    Blends coverage (fraction of slots filled), total-point balance, and
    weekend balance. 100 means every slot is filled and the load is perfectly
    even across residents; lower scores flag unfilled shifts or unfair spread.
    """
    pts = points if points is not None else calculate_points(df, data)
    labels = [s.label for s in data.shifts]
    records = df.to_dict("records")

    total_slots = len(records) * len(labels)
    filled = 0
    for row in records:
        for label in labels:
            if row.get(label) not in (None, "Unfilled"):
                filled += 1
    coverage = filled / total_slots if total_slots else 1.0

    totals = [v["total"] for v in pts.values()] or [0.0]
    weekends = [v["weekend"] for v in pts.values()] or [0.0]
    total_range = max(totals) - min(totals)
    weekend_range = max(weekends) - min(weekends)
    mean_total = sum(totals) / len(totals)
    mean_weekend = sum(weekends) / len(weekends)
    balance_total = 1.0 - min(1.0, total_range / mean_total) if mean_total > 0 else 1.0
    balance_weekend = (
        1.0 - min(1.0, weekend_range / mean_weekend) if mean_weekend > 0 else 1.0
    )

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
    }


def _eligible_pool(data: InputData, shift: ShiftTemplate) -> set:
    if shift.role == "Junior":
        pool = set(data.juniors)
        if shift.night_float:
            pool &= set(data.nf_juniors)
    else:
        pool = set(data.seniors)
        if shift.night_float:
            pool &= set(data.nf_seniors)
    return pool


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

    if person in (None, "Unfilled"):
        nf_note = " night-float" if shift.night_float else ""
        if not _eligible_pool(data, shift):
            return [f"No resident is eligible for '{label}' ({shift.role}{nf_note})."]
        return [
            f"'{label}' is unfilled on {day}: every eligible resident was "
            "unavailable (leave, rotator window, min-gap spacing or NF-block "
            "rules) or assigning one would have worsened fairness."
        ]

    pts = points if points is not None else calculate_points(df, data)
    role = "Senior" if person in set(data.seniors) else "Junior"
    nf_elig = " (night-float eligible)" if shift.night_float else ""
    lines = [f"{person} is a {role} eligible for '{label}'{nf_elig}."]

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
