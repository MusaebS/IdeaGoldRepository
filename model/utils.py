from __future__ import annotations

from datetime import date
from typing import Iterable

from .data_models import ShiftTemplate

__all__ = ["is_weekend", "effective_points", "weekend_holiday_dates"]

DEFAULT_WEEKEND_DAYS = (5, 6)  # Saturday, Sunday


def is_weekend(
    day: date,
    shift: ShiftTemplate,
    weekend_days: Iterable[int] | None = None,
    weekend_dates: Iterable[date] | None = None,
) -> bool:
    """Return True if the given day/shift counts as a weekend.

    ``weekend_days`` is a set of weekday numbers (Mon=0 .. Sun=6); when omitted
    it defaults to Saturday/Sunday. The per-shift ``thu_weekend`` flag adds
    Thursday for that shift. ``weekend_dates`` is a set of specific dates that also
    count as weekend (holidays flagged to count toward weekend balance).
    """
    days = DEFAULT_WEEKEND_DAYS if weekend_days is None else tuple(weekend_days)
    if day.weekday() in days or (shift.thu_weekend and day.weekday() == 3):
        return True
    return weekend_dates is not None and day in weekend_dates


def effective_points(day: date, shift: ShiftTemplate, data) -> float:
    """Return the points a shift is worth on a given day.

    Starts from the shift's default, replaces it with a weekday override if one
    exists (e.g. night = 2 on Tuesdays), then adds any holiday bonus for that
    date, and finally multiplies weekend slots by ``data.weekend_multiplier``
    (e.g. 2.0 makes every weekend shift count double — one weekend ≈ two
    weekdays in the total balance). The multiplier lives here, in the single
    source of per-slot value, so the solver, targets, fairness reports,
    exports, and ledger all agree on it automatically. A holiday flagged as
    weekend gets both its bonus and the multiplier.
    """
    pts = shift.points
    weekday_points = getattr(data, "weekday_points", None)
    if weekday_points:
        pts = weekday_points.get((shift.label, day.weekday()), pts)
    holidays = getattr(data, "holidays", None)
    if holidays:
        for h_date, bonus, _weekend in holidays:
            if h_date == day:
                pts += bonus
    multiplier = getattr(data, "weekend_multiplier", 1.0) or 1.0
    if multiplier != 1.0 and is_weekend(
        day, shift, getattr(data, "weekend_days", None), weekend_holiday_dates(data)
    ):
        pts *= multiplier
    return pts


def weekend_holiday_dates(data) -> set:
    """Return the set of holiday dates that should count toward weekend balance."""
    holidays = getattr(data, "holidays", None)
    return {h_date for h_date, _bonus, weekend in (holidays or []) if weekend}
