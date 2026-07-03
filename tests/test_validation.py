import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.validation import validate_input, validate_schedule, config_warnings


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


def test_cap_for_unknown_resident_rejected():
    data = _data(max_total={"Nobody": 5.0})
    assert any("unknown resident" in i for i in validate_input(data))


def test_negative_cap_rejected():
    data = _data(max_nights={"A": -1.0})
    assert any("cannot be negative" in i for i in validate_input(data))


# --- leave / rotator window advisories -----------------------------------------

def test_warns_window_outside_schedule_dates():
    from model.validation import config_warnings
    data = _data(leaves=[("A", date(2022, 1, 1), date(2022, 1, 2))])  # prior year
    assert any("outside the schedule" in w for w in config_warnings(data))


def test_warns_rotator_with_no_active_days():
    from model.validation import config_warnings
    # rotator window entirely before the block -> no active days
    data = _data(rotators=[("A", date(2022, 12, 1), date(2022, 12, 31))])
    assert any("no active days" in w for w in config_warnings(data))


def test_warns_leave_covering_whole_block():
    from model.validation import config_warnings
    data = _data(leaves=[("A", date(2023, 1, 1), date(2023, 1, 4))])  # whole 4-day block
    assert any("whole block" in w for w in config_warnings(data))


def test_warns_redundant_leave_outside_rotator_window():
    from model.validation import config_warnings
    data = _data(
        rotators=[("A", date(2023, 1, 1), date(2023, 1, 2))],
        leaves=[("A", date(2023, 1, 3), date(2023, 1, 4))],  # outside A's rotator window
    )
    assert any("outside their rotator active window" in w for w in config_warnings(data))


def test_no_spurious_leave_rotator_warnings():
    from model.validation import config_warnings
    data = _data(leaves=[("A", date(2023, 1, 2), date(2023, 1, 2))])  # one normal day
    assert not any(
        kw in w
        for w in config_warnings(data)
        for kw in ("outside the schedule", "no active days", "whole block")
    )


def test_extra_points_for_unknown_resident_rejected():
    data = _data(extra_points={"Nobody": 3.0})
    assert any("unknown resident" in i for i in validate_input(data))


def test_negative_extra_points_rejected():
    data = _data(extra_points={"A": -1.0})
    assert any("cannot be negative" in i for i in validate_input(data))


def test_warns_extra_points_exceed_cap():
    from model.validation import config_warnings
    data = _data(extra_points={"A": 5.0}, max_total={"A": 3.0})
    assert any("can't fit under the cap" in w for w in config_warnings(data))


def test_weekday_override_unknown_shift_rejected():
    data = _data(weekday_points={("Nope", 1): 2.0})
    assert any("unknown shift" in i for i in validate_input(data))


def test_weekday_override_bad_weekday_rejected():
    data = _data(weekday_points={("D", 9): 2.0})  # 9 is not a valid weekday
    assert any("invalid weekday" in i for i in validate_input(data))


def test_warns_holiday_outside_schedule():
    from model.validation import config_warnings
    data = _data(holidays=[(date(2022, 12, 25), 1.0, False)])  # before the block
    assert any("Holiday" in w and "outside the schedule" in w for w in config_warnings(data))


def _grp_data(**kw):
    base = dict(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 5),
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    base.update(kw)
    return InputData(**base)


def test_validate_group_factor_out_of_range():
    issues = validate_input(_grp_data(group_factors={"R2": 2.5}))
    assert any("load factor must be > 0" in i for i in issues)
    issues = validate_input(_grp_data(group_factors={"R2": 0.0}))
    assert any("load factor must be > 0" in i for i in issues)


def test_validate_group_assignment_rules():
    issues = validate_input(
        _grp_data(group_factors={"R2": 0.9}, resident_groups={"X": "R2"})
    )
    assert any("unknown resident 'X'" in i for i in issues)
    issues = validate_input(_grp_data(resident_groups={"A": "R9"}))
    assert any("undefined group 'R9'" in i for i in issues)


def test_validate_perk_rules():
    from model.data_models import Perk

    issues = validate_input(_grp_data(perks=[Perk("X", 0.8)]))
    assert any("unknown resident 'X'" in i for i in issues)
    issues = validate_input(_grp_data(perks=[Perk("A", 0.0)]))
    assert any("must be > 0" in i for i in issues)
    issues = validate_input(
        _grp_data(perks=[Perk("A", 0.8, date(2023, 1, 5), date(2023, 1, 2))])
    )
    assert any("ends" in i and "before it" in i for i in issues)


def test_validate_exemption_rules():
    issues = validate_input(_grp_data(exempt_shifts={"X": ["S"]}))
    assert any("unknown resident 'X'" in i for i in issues)
    issues = validate_input(_grp_data(exempt_shifts={"A": ["Nope"]}))
    assert any("unknown shift 'Nope'" in i for i in issues)


def test_warning_when_everyone_exempt_from_shift():
    warnings = config_warnings(_grp_data(exempt_shifts={"A": ["S"], "B": ["S"]}))
    assert any("always be unfilled" in w for w in warnings)


def test_warning_when_exempt_from_all_role_shifts():
    warnings = config_warnings(_grp_data(exempt_shifts={"A": ["S"]}))
    assert any("exempt from every Junior shift" in w for w in warnings)


def test_warning_nf_eligible_but_exempt():
    data = _grp_data(
        shifts=[
            ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False),
            ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False),
        ],
        nf_juniors=["A", "B"],
        exempt_shifts={"A": ["NF"]},
    )
    warnings = config_warnings(data)
    assert any("night-float eligible but exempt" in w for w in warnings)


def test_warning_perk_outside_block():
    from model.data_models import Perk

    warnings = config_warnings(
        _grp_data(perks=[Perk("A", 0.8, date(2023, 2, 1), date(2023, 2, 5))])
    )
    assert any("outside" in w and "perk" in w.lower() for w in warnings)


def test_validate_schedule_flags_exempt_assignment():
    data = _grp_data(exempt_shifts={"A": ["S"]}, min_gap=0)
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
    ])
    issues = validate_schedule(df, data)
    assert any("exempt from this shift" in i for i in issues)
