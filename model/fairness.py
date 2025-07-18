from __future__ import annotations

from datetime import date
from typing import Dict, List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import ShiftTemplate, InputData

__all__ = ["calculate_points", "format_fairness_log"]


def _is_weekend(day: date, shift: ShiftTemplate) -> bool:
    return day.weekday() >= 5 or (shift.thu_weekend and day.weekday() == 3)


def calculate_points(df: pd.DataFrame, data: InputData) -> Dict[str, Dict[str, float]]:
    """Return mapping of resident to total and weekend points per label."""
    summary: Dict[str, Dict[str, float]] = {
        name: {"total": 0.0, "weekend": 0.0, "labels": {}}
        for name in data.juniors + data.seniors
    }
    for row in df.to_dict("records"):
        day = row.get("Date")
        for sh in data.shifts:
            person = row.get(sh.label)
            if person in (None, "Unfilled"):
                continue
            info = summary.setdefault(person, {"total": 0.0, "weekend": 0.0, "labels": {}})
            info["total"] += sh.points
            info["labels"][sh.label] = info["labels"].get(sh.label, 0.0) + sh.points
            if _is_weekend(day, sh):
                info["weekend"] += sh.points
    return summary


def format_fairness_log(df: pd.DataFrame, data: InputData) -> str:
    """Generate a human-readable fairness log."""
    pts = calculate_points(df, data)
    lines: List[str] = []
    for person in sorted(pts):
        info = pts[person]
        line = f"{person}: total {info['total']:.1f}"
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
    totals = [v['total'] for v in pts.values()]
    if totals:
        lines.append(f"Total point range: {max(totals) - min(totals):.1f}")
    wk_totals = [v['weekend'] for v in pts.values()]
    if wk_totals:
        lines.append(f"Weekend point range: {max(wk_totals) - min(wk_totals):.1f}")
    return "\n".join(lines)
