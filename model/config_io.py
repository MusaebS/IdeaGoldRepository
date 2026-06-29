from __future__ import annotations

import json
from datetime import date
from typing import List, Tuple

from .data_models import ShiftTemplate, InputData

__all__ = ["input_data_to_json", "input_data_from_json"]


def _windows_to_json(windows: List[Tuple[str, date, date]]) -> List[list]:
    return [[name, start.isoformat(), end.isoformat()] for name, start, end in windows]


def _windows_from_json(items) -> List[Tuple[str, date, date]]:
    out: List[Tuple[str, date, date]] = []
    for name, start, end in items or []:
        out.append((name, date.fromisoformat(start), date.fromisoformat(end)))
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
        "leaves": _windows_to_json(data.leaves),
        "rotators": _windows_to_json(data.rotators),
        "min_gap": data.min_gap,
        "nf_block_length": data.nf_block_length,
        "seed": data.seed,
        "weekend_days": data.weekend_days,
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
        leaves=_windows_from_json(raw.get("leaves")),
        rotators=_windows_from_json(raw.get("rotators")),
        min_gap=int(raw.get("min_gap", 1)),
        nf_block_length=int(raw.get("nf_block_length", 5)),
        seed=int(raw.get("seed", 0)),
        weekend_days=(
            [int(d) for d in raw["weekend_days"]]
            if raw.get("weekend_days") is not None
            else None
        ),
    )
