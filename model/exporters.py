from __future__ import annotations

import io
from typing import Dict

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import InputData
from .fairness import calculate_points

__all__ = ["build_fairness_frame", "schedule_to_excel_bytes"]


def build_fairness_frame(
    points: Dict[str, Dict[str, float]], data: InputData
) -> "pd.DataFrame":
    """Return a per-resident fairness table (total, weekend, NF, per-label)."""
    labels = sorted(
        {label for info in points.values() for label in info.get("labels", {})}
    )
    rows = []
    for name in sorted(points):
        info = points[name]
        role = "Senior" if name in data.seniors else "Junior"
        row = {
            "Resident": name,
            "Role": role,
            "Total": info.get("total", 0.0),
            "Weekend": info.get("weekend", 0.0),
            "Night Float": info.get("night_float", 0.0),
        }
        for label in labels:
            row[label] = info.get("labels", {}).get(label, 0.0)
        rows.append(row)
    return pd.DataFrame(rows)


def schedule_to_excel_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, Dict[str, float]] | None = None,
) -> bytes:
    """Serialise the schedule and fairness summary to an .xlsx workbook.

    Sheet "Schedule" is the calendar grid (rows = dates, columns = shift
    labels); sheet "Fairness" is the per-resident point summary. Requires
    ``openpyxl`` to be installed.
    """
    points = points if points is not None else calculate_points(df, data)
    fairness = build_fairness_frame(points, data)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Schedule", index=False)
        fairness.to_excel(writer, sheet_name="Fairness", index=False)
    return buffer.getvalue()
