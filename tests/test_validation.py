import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.validation import validate_schedule


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
