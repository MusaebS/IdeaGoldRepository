from __future__ import annotations

from typing import Dict, List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import ShiftTemplate, InputData
from .utils import is_weekend

__all__ = ["calculate_points", "format_fairness_log", "fairness_range_lines"]


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
