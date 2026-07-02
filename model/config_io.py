from __future__ import annotations

import json
from datetime import date
from typing import List, Tuple

from .data_models import Leave, RotatorWindow, ShiftTemplate, InputData, normalized_leaves

__all__ = ["input_data_to_json", "input_data_from_json"]


def _windows_to_json(windows: List[Tuple[str, date, date]]) -> List[list]:
    return [[name, start.isoformat(), end.isoformat()] for name, start, end in windows]


def _windows_from_json(items) -> List[RotatorWindow]:
    out: List[RotatorWindow] = []
    for name, start, end in items or []:
        out.append(RotatorWindow(name, date.fromisoformat(start), date.fromisoformat(end)))
    return out


def _leaves_to_json(leaves) -> List[list]:
    return [
        [name, start.isoformat(), end.isoformat(), compensated]
        for name, start, end, compensated in normalized_leaves(leaves)
    ]


def _leaves_from_json(items) -> List[Leave]:
    out: List[Leave] = []
    for entry in items or []:
        name, start, end = entry[0], entry[1], entry[2]
        compensated = bool(entry[3]) if len(entry) > 3 else True
        out.append(Leave(name, date.fromisoformat(start), date.fromisoformat(end), compensated))
    return out


def input_data_to_json(data: InputData) -> str:
    """Serialise an :class:`InputData` configuration to a JSON string.

    Solver-derived fields (the ``target_*`` values) are intentionally omitted;
    only user-entered configuration is saved.
    """
    payload = {
        "start_date": data.start_date.isoformat(),
        "end_date": data.end_date.isoformat(),
        "shifts": [
            {
                "label": s.label,
                "role": s.role,
                "night_float": s.night_float,
                "thu_weekend": s.thu_weekend,
                "points": s.points,
            }
            for s in data.shifts
        ],
        "juniors": list(data.juniors),
        "seniors": list(data.seniors),
        "nf_juniors": list(data.nf_juniors),
        "nf_seniors": list(data.nf_seniors),
        "leaves": _leaves_to_json(data.leaves),
        "rotators": _windows_to_json(data.rotators),
        "min_gap": data.min_gap,
        "nf_block_length": data.nf_block_length,
        "seed": data.seed,
        "weekend_days": data.weekend_days,
        "max_total": data.max_total,
        "max_nights": data.max_nights,
        "extra_points": data.extra_points,
        "weekday_points": (
            [[label, wd, pts] for (label, wd), pts in data.weekday_points.items()]
            if data.weekday_points
            else None
        ),
        "holidays": (
            [[d.isoformat(), bonus, weekend] for d, bonus, weekend in data.holidays]
            if data.holidays
            else None
        ),
    }
    return json.dumps(payload, indent=2)


def input_data_from_json(text: str) -> InputData:
    """Rebuild an :class:`InputData` from a JSON string produced by
    :func:`input_data_to_json`."""
    raw = json.loads(text)
    shifts = [
        ShiftTemplate(
            label=s["label"],
            role=s["role"],
            night_float=bool(s["night_float"]),
            thu_weekend=bool(s["thu_weekend"]),
            points=float(s.get("points", 1.0)),
        )
        for s in raw.get("shifts", [])
    ]
    return InputData(
        start_date=date.fromisoformat(raw["start_date"]),
        end_date=date.fromisoformat(raw["end_date"]),
        shifts=shifts,
        juniors=list(raw.get("juniors", [])),
        seniors=list(raw.get("seniors", [])),
        nf_juniors=list(raw.get("nf_juniors", [])),
        nf_seniors=list(raw.get("nf_seniors", [])),
        leaves=_leaves_from_json(raw.get("leaves")),
        rotators=_windows_from_json(raw.get("rotators")),
        min_gap=int(raw.get("min_gap", 1)),
        nf_block_length=int(raw.get("nf_block_length", 5)),
        seed=int(raw.get("seed", 0)),
        weekend_days=(
            [int(d) for d in raw["weekend_days"]]
            if raw.get("weekend_days") is not None
            else None
        ),
        max_total=(
            {str(k): float(v) for k, v in raw["max_total"].items()}
            if raw.get("max_total")
            else None
        ),
        max_nights=(
            {str(k): float(v) for k, v in raw["max_nights"].items()}
            if raw.get("max_nights")
            else None
        ),
        extra_points=(
            {str(k): float(v) for k, v in raw["extra_points"].items()}
            if raw.get("extra_points")
            else None
        ),
        weekday_points=(
            {(str(label), int(wd)): float(pts) for label, wd, pts in raw["weekday_points"]}
            if raw.get("weekday_points")
            else None
        ),
        holidays=(
            [
                (date.fromisoformat(d), float(bonus), bool(weekend))
                for d, bonus, weekend in raw["holidays"]
            ]
            if raw.get("holidays")
            else None
        ),
    )
