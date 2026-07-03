"""Direct tests for model/utils.py — the single source of points truth.

These functions are used by the solver, fairness reporting, and colouring, so
their edge cases (weekday overrides vs holiday bonuses, custom weekends) are
locked in here before anything builds on top of them.
"""
from datetime import date

from model.data_models import ShiftTemplate, InputData
from model.utils import is_weekend, effective_points, weekend_holiday_dates

SAT = date(2023, 1, 7)
SUN = date(2023, 1, 8)
MON = date(2023, 1, 2)
TUE = date(2023, 1, 3)
THU = date(2023, 1, 5)
FRI = date(2023, 1, 6)


def _shift(**kw) -> ShiftTemplate:
    base = dict(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)
    base.update(kw)
    return ShiftTemplate(**base)


def _data(**kw) -> InputData:
    base = dict(
        start_date=MON,
        end_date=SUN,
        shifts=[_shift()],
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    base.update(kw)
    return InputData(**base)


# --- is_weekend -------------------------------------------------------------

def test_is_weekend_defaults_to_sat_sun():
    sh = _shift()
    assert is_weekend(SAT, sh)
    assert is_weekend(SUN, sh)
    assert not is_weekend(FRI, sh)
    assert not is_weekend(MON, sh)


def test_is_weekend_custom_weekend_days():
    sh = _shift()
    assert is_weekend(FRI, sh, weekend_days=[4, 5])
    assert is_weekend(SAT, sh, weekend_days=[4, 5])
    assert not is_weekend(SUN, sh, weekend_days=[4, 5])


def test_is_weekend_thu_flag_only_affects_that_shift():
    flagged = _shift(thu_weekend=True)
    plain = _shift()
    assert is_weekend(THU, flagged)
    assert not is_weekend(THU, plain)
    # The flag adds Thursday on top of a custom weekend, not instead of it.
    assert is_weekend(THU, flagged, weekend_days=[4, 5])


def test_is_weekend_holiday_dates_count():
    sh = _shift()
    assert not is_weekend(TUE, sh)
    assert is_weekend(TUE, sh, weekend_dates={TUE})
    assert not is_weekend(MON, sh, weekend_dates={TUE})


# --- effective_points -------------------------------------------------------

def test_effective_points_base_value():
    assert effective_points(MON, _shift(points=1.5), _data()) == 1.5


def test_weekday_override_replaces_base_points():
    data = _data(weekday_points={("S", TUE.weekday()): 3.0})
    sh = _shift(points=1.0)
    assert effective_points(TUE, sh, data) == 3.0  # replaced, not added
    assert effective_points(MON, sh, data) == 1.0  # other weekdays untouched


def test_override_is_keyed_by_label():
    data = _data(weekday_points={("Other", TUE.weekday()): 3.0})
    assert effective_points(TUE, _shift(label="S"), data) == 1.0


def test_holiday_bonus_adds_to_base():
    data = _data(holidays=[(TUE, 1.5, False)])
    assert effective_points(TUE, _shift(points=1.0), data) == 2.5
    assert effective_points(MON, _shift(points=1.0), data) == 1.0


def test_holiday_bonus_adds_on_top_of_weekday_override():
    data = _data(
        weekday_points={("S", TUE.weekday()): 3.0},
        holidays=[(TUE, 1.5, False)],
    )
    assert effective_points(TUE, _shift(points=1.0), data) == 4.5


def test_multiple_holidays_on_same_date_all_add():
    data = _data(holidays=[(TUE, 1.0, False), (TUE, 0.5, True)])
    assert effective_points(TUE, _shift(points=1.0), data) == 2.5


def test_effective_points_with_no_override_maps():
    data = _data(weekday_points=None, holidays=None)
    assert effective_points(MON, _shift(points=2.0), data) == 2.0


# --- weekend_holiday_dates ---------------------------------------------------

def test_weekend_holiday_dates_only_flagged():
    data = _data(holidays=[(TUE, 1.0, True), (THU, 1.0, False)])
    assert weekend_holiday_dates(data) == {TUE}


def test_weekend_holiday_dates_empty_cases():
    assert weekend_holiday_dates(_data(holidays=None)) == set()
    assert weekend_holiday_dates(_data(holidays=[])) == set()
