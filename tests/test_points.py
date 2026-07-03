"""Tests for model/points.py — the shared slot-classification/scaling source
consumed by both the solver (scaled ints) and fairness reporting (floats)."""
from datetime import date

from model.data_models import ShiftTemplate, InputData
from model.points import POINT_SCALE, classify_slot, scaled, slot_points, block_days
from model.utils import effective_points, is_weekend, weekend_holiday_dates

MON = date(2023, 1, 2)
TUE = date(2023, 1, 3)
SAT = date(2023, 1, 7)
SUN = date(2023, 1, 8)


def _data(**kw) -> InputData:
    base = dict(
        start_date=MON,
        end_date=SUN,
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=True, points=2.0),
        ],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=["A"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
        weekday_points={("NF", TUE.weekday()): 3.0},
        holidays=[(TUE, 0.5, False), (SAT, 1.0, True)],
    )
    base.update(kw)
    return InputData(**base)


def test_scaled_is_the_solver_conversion():
    assert scaled(1.0) == POINT_SCALE
    assert scaled(2.5) == 250
    assert scaled(0.333) == 33  # rounds, never truncates
    assert scaled(0.335) == 34


def test_classify_slot_agrees_with_utils():
    data = _data()
    weekend_dates = weekend_holiday_dates(data)
    for day in block_days(data):
        for sh in data.shifts:
            slot = classify_slot(day, sh, data, weekend_dates)
            assert slot.points == effective_points(day, sh, data)
            assert slot.weekend == is_weekend(day, sh, data.weekend_days, weekend_dates)
            assert slot.night_float == sh.night_float
            assert slot.day == day and slot.shift is sh


def test_classify_slot_override_plus_holiday():
    data = _data()
    nf = data.shifts[1]
    # Tuesday: override (3.0) + holiday bonus (0.5)
    assert classify_slot(TUE, nf, data).points == 3.5
    # Saturday: base (2.0) + weekend-counting holiday bonus (1.0), and weekend.
    sat_slot = classify_slot(SAT, nf, data)
    assert sat_slot.points == 3.0
    assert sat_slot.weekend


def test_slot_points_covers_every_slot_once():
    data = _data()
    slots = slot_points(data)
    days = block_days(data)
    assert len(slots) == len(days) * len(data.shifts)
    assert {(s.day, s.shift.label) for s in slots} == {
        (day, sh.label) for day in days for sh in data.shifts
    }


def test_scaled_total_matches_float_total_within_rounding():
    data = _data()
    slots = slot_points(data)
    float_total = sum(s.points for s in slots)
    scaled_sum = sum(scaled(s.points) for s in slots)
    # Per-slot rounding error is at most 0.5 units per slot.
    assert abs(scaled_sum - float_total * POINT_SCALE) <= 0.5 * len(slots)


def test_block_days_inclusive():
    data = _data()
    days = block_days(data)
    assert days[0] == data.start_date
    assert days[-1] == data.end_date
    assert len(days) == 7
