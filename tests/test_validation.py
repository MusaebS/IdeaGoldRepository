import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.validation import validate_input, validate_schedule


def _data(**over):
    base = dict(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 4),
        shifts=[ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=["C"],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=1,
    )
    base.update(over)
    return InputData(**base)


def test_valid_schedule_has_no_issues():
    data = _data()
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "D": "A"},
        {"Date": date(2023, 1, 2), "D": "B"},
        {"Date": date(2023, 1, 3), "D": "A"},
        {"Date": date(2023, 1, 4), "D": "B"},
    ])
    assert validate_schedule(df, data) == []


def test_role_violation_detected():
    data = _data()
    df = pd.DataFrame([{"Date": date(2023, 1, 1), "D": "C"}])  # senior on a Junior shift
    assert any("not a Junior" in i for i in validate_schedule(df, data))


def test_leave_violation_detected():
    data = _data(leaves=[("A", date(2023, 1, 1), date(2023, 1, 1))])
    df = pd.DataFrame([{"Date": date(2023, 1, 1), "D": "A"}])
    assert any("on leave" in i for i in validate_schedule(df, data))


def test_rotator_window_violation_detected():
    data = _data(rotators=[("A", date(2023, 1, 2), date(2023, 1, 4))])
    df = pd.DataFrame([{"Date": date(2023, 1, 1), "D": "A"}])  # A works outside window
    assert any("rotator window" in i for i in validate_schedule(df, data))


def test_min_gap_violation_detected():
    data = _data(min_gap=2)
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "D": "A"},
        {"Date": date(2023, 1, 2), "D": "A"},  # A twice within the gap
    ])
    assert any("Minimum gap" in i for i in validate_schedule(df, data))


def test_double_booking_detected():
    data = _data(
        shifts=[
            ShiftTemplate(label="D1", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="D2", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ]
    )
    df = pd.DataFrame([{"Date": date(2023, 1, 1), "D1": "A", "D2": "A"}])  # A twice in one day
    assert any("more than one shift" in i for i in validate_schedule(df, data))


# --- validate_input (pre-solve configuration checks) ---------------------------

def test_valid_config_has_no_input_issues():
    assert validate_input(_data()) == []


def test_backwards_date_range_rejected():
    data = _data(start_date=date(2023, 1, 10), end_date=date(2023, 1, 1))
    assert any("before start date" in i for i in validate_input(data))


def test_no_shifts_rejected():
    assert any("at least one shift" in i for i in validate_input(_data(shifts=[])))


def test_duplicate_shift_label_rejected():
    data = _data(
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="D", role="Senior", night_float=False, thu_weekend=False, points=1.0),
        ]
    )
    assert any("Duplicate shift label" in i for i in validate_input(data))


def test_reserved_shift_label_rejected():
    data = _data(
        shifts=[ShiftTemplate(label="Day", role="Junior", night_float=False, thu_weekend=False)]
    )
    assert any("reserved" in i for i in validate_input(data))


def test_nf_eligible_not_in_roster_rejected():
    data = _data(nf_juniors=["Z"])  # Z is not a listed junior
    assert any("not in the Juniors list" in i for i in validate_input(data))


def test_person_in_both_roles_rejected():
    data = _data(juniors=["A"], seniors=["A"])
    assert any("both a Junior and a Senior" in i for i in validate_input(data))


def test_leave_for_unknown_resident_rejected():
    data = _data(leaves=[("Nobody", date(2023, 1, 1), date(2023, 1, 2))])
    assert any("unknown resident" in i for i in validate_input(data))


def test_build_schedule_rejects_invalid_config():
    import pytest
    from model.optimiser import build_schedule

    with pytest.raises(ValueError):
        build_schedule(_data(shifts=[]), env="test")


# --- config_warnings (non-blocking advisories) ---------------------------------

def test_config_warnings_empty_for_healthy_config():
    from model.validation import config_warnings
    assert config_warnings(_data()) == []


def test_config_warnings_flags_uncoverable_nf_shift():
    from model.validation import config_warnings
    data = _data(
        shifts=[ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)],
        nf_juniors=[],  # no eligible NF residents
    )
    assert any("no" in w and "eligible" in w for w in config_warnings(data))


def test_config_warnings_flags_more_shifts_than_residents():
    from model.validation import config_warnings
    data = _data(
        juniors=["A"],  # one junior...
        shifts=[
            ShiftTemplate(label="D1", role="Junior", night_float=False, thu_weekend=False),
            ShiftTemplate(label="D2", role="Junior", night_float=False, thu_weekend=False),
        ],  # ...but two Junior shifts per day
    )
    assert any("unfilled each day" in w for w in config_warnings(data))
