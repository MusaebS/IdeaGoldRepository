from __future__ import annotations

from typing import Dict, List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import ShiftTemplate, InputData
from .utils import is_weekend

__all__ = [
    "calculate_points",
    "format_fairness_log",
    "fairness_range_lines",
    "schedule_quality",
]


def calculate_points(df: pd.DataFrame, data: InputData) -> Dict[str, Dict[str, float]]:
    """Return mapping of resident to total and weekend points per label."""
    summary: Dict[str, Dict[str, float]] = {
        name: {"total": 0.0, "weekend": 0.0, "labels": {}, "night_float": 0.0}
        for name in data.juniors + data.seniors
    }
    for row in df.to_dict("records"):
        day = row.get("Date")
        for sh in data.shifts:
            person = row.get(sh.label)
            if person in (None, "Unfilled"):
                continue
            info = summary.setdefault(person, {"total": 0.0, "weekend": 0.0, "labels": {}, "night_float": 0.0})
            info["total"] += sh.points
            info["labels"][sh.label] = info["labels"].get(sh.label, 0.0) + sh.points
            if sh.night_float:
                info["night_float"] += sh.points
            if is_weekend(day, sh):
                info["weekend"] += sh.points
    return summary


def fairness_range_lines(points: Dict[str, Dict[str, float]]) -> List[str]:
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

    return lines


def format_fairness_log(
    df: pd.DataFrame, data: InputData, points: Dict[str, Dict[str, float]] | None = None
) -> str:
    """Generate a human-readable fairness log."""
    pts = points or calculate_points(df, data)
    lines: List[str] = []
    for person in sorted(pts):
        info = pts[person]
        role = "Senior" if person in data.seniors else "Junior"
        nf = info.get("night_float", 0.0)
        line = f"{person} ({role}, NF {nf:.1f}): total {info['total']:.1f}"
        if data.target_total is not None:
            dev = info['total'] - data.target_total
            line += f" (dev {dev:+.1f})"
        line += f", weekend {info['weekend']:.1f}"
        if data.target_weekend and person in data.target_weekend:
            wdev = info['weekend'] - data.target_weekend[person]
            line += f" (dev {wdev:+.1f})"
        for label in sorted(info['labels']):
            val = info['labels'][label]
            line += f", {label} {val:.1f}"
            if data.target_label and (person, label) in data.target_label:
                ldev = val - data.target_label[(person, label)]
                line += f" (dev {ldev:+.1f})"
        lines.append(line)
    lines.extend(fairness_range_lines(pts))
    return "\n".join(lines)


def schedule_quality(
    df: pd.DataFrame, data: InputData, points: Dict[str, Dict[str, float]] | None = None
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
