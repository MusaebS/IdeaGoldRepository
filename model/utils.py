from __future__ import annotations

from datetime import date
from typing import Iterable

from .data_models import ShiftTemplate

__all__ = ["is_weekend"]

DEFAULT_WEEKEND_DAYS = (5, 6)  # Saturday, Sunday


def is_weekend(
    day: date, shift: ShiftTemplate, weekend_days: Iterable[int] | None = None
) -> bool:
    """Return True if the given day/shift counts as a weekend.

    ``weekend_days`` is a set of weekday numbers (Mon=0 .. Sun=6); when omitted
    it defaults to Saturday/Sunday. The per-shift ``thu_weekend`` flag adds
    Thursday for that shift regardless of ``weekend_days``.
    """
    days = DEFAULT_WEEKEND_DAYS if weekend_days is None else tuple(weekend_days)
    return day.weekday() in days or (shift.thu_weekend and day.weekday() == 3)
