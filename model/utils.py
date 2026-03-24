from __future__ import annotations

from datetime import date

from .data_models import ShiftTemplate

__all__ = ["is_weekend"]


def is_weekend(day: date, shift: ShiftTemplate) -> bool:
    """Return True if the given day/shift counts as a weekend."""
    return day.weekday() >= 5 or (shift.thu_weekend and day.weekday() == 3)
