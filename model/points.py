"""Single source of truth for per-slot point classification and scaling.

Both the CP-SAT solver (which works in scaled integers) and the post-solve
fairness reporting (which works in floats) consume slots classified here, so
the "how many points is this slot worth / is it a weekend / is it night
float" logic can never drift between them.

This module must stay importable without pandas or OR-Tools installed (the
stub-only CI job runs with neither).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Sequence

from .data_models import InputData, ShiftTemplate
from .utils import effective_points, is_weekend, weekend_holiday_dates

__all__ = ["POINT_SCALE", "SlotPoints", "scaled", "classify_slot", "slot_points", "block_days"]

# The solver's integer scale: 1.0 points == 100 solver units. The only place
# float points are converted to solver integers is ``scaled`` below.
POINT_SCALE = 100


@dataclass(frozen=True)
class SlotPoints:
    """One (day, shift) slot with its resolved value and classification."""

    day: date
    shift: ShiftTemplate
    points: float  # effective points (weekday override replaced, holiday bonus added)
    weekend: bool
    night_float: bool


def scaled(points: float, scale: int = POINT_SCALE) -> int:
    """Convert float points to the solver's integer scale."""
    return int(round(points * scale))


def classify_slot(
    day: date,
    shift: ShiftTemplate,
    data: InputData,
    weekend_dates: set | None = None,
) -> SlotPoints:
    """Classify a single (day, shift) slot.

    Pass ``weekend_dates`` (from :func:`weekend_holiday_dates`) when calling in
    a loop so the holiday set is computed once.
    """
    if weekend_dates is None:
        weekend_dates = weekend_holiday_dates(data)
    return SlotPoints(
        day=day,
        shift=shift,
        points=effective_points(day, shift, data),
        weekend=is_weekend(day, shift, data.weekend_days, weekend_dates),
        night_float=shift.night_float,
    )


def block_days(data: InputData) -> List[date]:
    """Return every day in the schedule block, inclusive of both ends."""
    span = (data.end_date - data.start_date).days + 1
    return [data.start_date + timedelta(days=i) for i in range(span)]


def slot_points(data: InputData, days: Sequence[date] | None = None) -> List[SlotPoints]:
    """Classify every (day, shift) slot in the block (or the given days)."""
    if days is None:
        days = block_days(data)
    weekend_dates = weekend_holiday_dates(data)
    return [
        classify_slot(day, shift, data, weekend_dates)
        for day in days
        for shift in data.shifts
    ]
