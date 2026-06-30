"""Cumulative fairness ledger.

A ledger records each resident's accumulated points across past scheduling
blocks (``total``, ``weekend``, ``night_float``). Feeding it into the next block
lets the optimiser balance *cumulative* load — a resident who carried extra last
block is given a lighter target this block — and the updated ledger is saved for
the block after that.

The ledger only influences the fairness *targets* (computed in
``build_schedule``); the solver itself is unchanged.
"""
from __future__ import annotations

import json
from typing import Dict

from .data_models import InputData
from .fairness import calculate_points

__all__ = [
    "DIMENSIONS",
    "empty_ledger",
    "update_ledger",
    "ledger_to_json",
    "ledger_from_json",
]

DIMENSIONS = ("total", "weekend", "night_float")


def empty_ledger() -> Dict[str, Dict[str, float]]:
    return {}


def update_ledger(prior, df, data: InputData) -> Dict[str, Dict[str, float]]:
    """Return ``prior`` plus the points each resident earned in ``df``.

    Residents new this block are added; residents only in ``prior`` are kept so
    history is never lost.
    """
    points = calculate_points(df, data)
    updated: Dict[str, Dict[str, float]] = {
        person: {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        for person, vals in (prior or {}).items()
    }
    for person, info in points.items():
        entry = updated.setdefault(person, {dim: 0.0 for dim in DIMENSIONS})
        entry["total"] += info.get("total", 0.0)
        entry["weekend"] += info.get("weekend", 0.0)
        entry["night_float"] += info.get("night_float", 0.0)
    return updated


def ledger_to_json(ledger) -> str:
    return json.dumps(ledger or {}, indent=2, sort_keys=True)


def ledger_from_json(text: str) -> Dict[str, Dict[str, float]]:
    raw = json.loads(text)
    return {
        str(person): {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        for person, vals in raw.items()
    }
