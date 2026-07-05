"""Shift closures: (date, label) cells the regular scheduler does not staff.

A closure stands a shift down for a stretch of dates (a resident shortage, a
holiday the department drops the shift, …). Like night-float-covered cells,
closed cells are **removed from regular demand**: never assigned, never counted
as *unfilled*, and outside the point/fairness system. They are written into the
schedule as ``"Closed"``.

Reserved cells (the shared extension point)
-------------------------------------------

Both the night-float overlay and closures produce cells the regular solver must
skip. Everything that asks "is this an ordinary regular assignment?" — point
totals, the unfilled count, min-gap spacing, schedule validation — goes through
:func:`reserved_cell_keys`, the union of every non-regular cell tagged on the
frame. A future feature that reserves cells only needs to (1) tag them on
``df.attrs`` and (2) add them here; the consumers pick it up with no further
changes.

Pure and stub-safe (no pandas / OR-Tools / Streamlit).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Set, Tuple

from .data_models import InputData, shift_closed
from .night_float import nf_cells_from_attr
from .points import block_days

__all__ = [
    "resolve_closures",
    "closed_cells_to_attr",
    "closed_cells_from_attr",
    "reserved_cell_keys",
]

Slot = Tuple[date, str]


def resolve_closures(data: InputData) -> Set[Slot]:
    """Return the set of ``(date, label)`` cells closed for this block."""
    closed: Set[Slot] = set()
    if not getattr(data, "closures", None):
        return closed
    for day in block_days(data):
        for shift in data.shifts:
            if shift_closed(day, shift, data):
                closed.add((day, shift.label))
    return closed


def closed_cells_to_attr(closed: Set[Slot]) -> Dict[str, List[str]]:
    """``{(date, label)}`` → a JSON/Arrow-serializable ``{date-iso: [labels]}``."""
    out: Dict[str, List[str]] = {}
    for day, label in closed:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        out.setdefault(key, []).append(label)
    return out


def closed_cells_from_attr(df) -> Set[Tuple[str, str]]:
    """Read ``df.attrs['closed_cells']`` back into ``{(date-iso, label)}``."""
    attrs = getattr(df, "attrs", {}) or {}
    raw = attrs.get("closed_cells", {}) or {}
    out: Set[Tuple[str, str]] = set()
    for iso, labels in raw.items():
        for label in labels or ():
            out.add((str(iso), str(label)))
    return out


def reserved_cell_keys(df) -> Set[Tuple[str, str]]:
    """Every non-regular ``(date-iso, label)`` cell tagged on the frame.

    The union of night-float overlay cells and closed cells — the single set
    every "is this a regular assignment?" check consults, so new reserved-cell
    features extend one place instead of every consumer.
    """
    return set(nf_cells_from_attr(df)) | closed_cells_from_attr(df)
