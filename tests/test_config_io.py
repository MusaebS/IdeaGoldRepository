import sys, os
import json
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.data_models import ShiftTemplate, InputData
from model.config_io import input_data_to_json, input_data_from_json


def _sample_data():
    return InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 28),
        shifts=[
            ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=2.0),
            ShiftTemplate(label="Day", role="Senior", night_float=False, thu_weekend=True, points=1.0),
        ],
        juniors=["A", "B"],
        seniors=["C"],
        nf_juniors=["A"],
        nf_seniors=[],
        leaves=[("A", date(2023, 1, 5), date(2023, 1, 7), False)],  # uncompensated
        rotators=[("B", date(2023, 1, 1), date(2023, 1, 14))],
        min_gap=2,
        nf_block_length=5,
        seed=99,
        weekend_days=[4, 5],
        max_total={"A": 12.0},
        max_nights={"A": 4.0},
        extra_points={"B": 2.0},
        weekday_points={("NF", 1): 2.0},  # NF worth 2 on Tuesdays
        holidays=[(date(2023, 1, 20), 1.5, True)],
    )


def test_config_round_trip():
    data = _sample_data()
    restored = input_data_from_json(input_data_to_json(data))

    assert restored.start_date == data.start_date
    assert restored.end_date == data.end_date
    assert [s.__dict__ for s in restored.shifts] == [s.__dict__ for s in data.shifts]
    assert restored.juniors == data.juniors
    assert restored.seniors == data.seniors
    assert restored.nf_juniors == data.nf_juniors
    assert restored.nf_seniors == data.nf_seniors
    assert restored.leaves == data.leaves
    assert restored.rotators == data.rotators
    assert restored.min_gap == data.min_gap
    assert restored.nf_block_length == data.nf_block_length
    assert restored.seed == data.seed
    assert restored.weekend_days == data.weekend_days
    assert restored.max_total == data.max_total
    assert restored.max_nights == data.max_nights
    assert restored.extra_points == data.extra_points
    assert restored.weekday_points == data.weekday_points
    assert restored.holidays == data.holidays


def test_config_from_minimal_json():
    text = (
        '{"start_date": "2023-02-01", "end_date": "2023-02-02", "shifts": [],'
        ' "juniors": ["X"], "seniors": []}'
    )
    data = input_data_from_json(text)
    assert data.start_date == date(2023, 2, 1)
    assert data.juniors == ["X"]
    assert data.shifts == []
    assert data.leaves == []
    assert data.rotators == []
    assert data.min_gap == 1  # default
    assert data.nf_block_length == 5  # default


def test_legacy_three_tuple_leave_normalizes_to_compensated():
    text = (
        '{"start_date": "2023-02-01", "end_date": "2023-02-05", "shifts": [],'
        ' "juniors": ["A"], "seniors": [], "leaves": [["A", "2023-02-02", "2023-02-03"]]}'
    )
    data = input_data_from_json(text)
    assert data.leaves == [("A", date(2023, 2, 2), date(2023, 2, 3), True)]


def test_solver_derived_targets_are_not_serialized():
    data = _sample_data()
    data.target_total = 12.0
    data.target_total_map = {"A": 6.0, "B": 6.0}
    data.target_weekend = {"A": 2.0}
    data.target_night_float = {"A": 1.0}
    data.target_label = {("A", "NF"): 1.0}

    raw = json.loads(input_data_to_json(data))
    assert not any(key.startswith("target_") for key in raw)

    restored = input_data_from_json(input_data_to_json(data))
    assert restored.target_total is None
    assert restored.target_total_map is None
    assert restored.target_weekend is None
    assert restored.target_night_float is None
    assert restored.target_label is None


def test_malformed_json_raises_decode_error():
    with pytest.raises(json.JSONDecodeError):
        input_data_from_json("{not valid json")


def test_missing_required_date_key_raises_key_error():
    with pytest.raises(KeyError):
        input_data_from_json('{"end_date": "2023-02-02", "shifts": []}')


def test_invalid_date_string_raises_value_error():
    with pytest.raises(ValueError):
        input_data_from_json('{"start_date": "not-a-date", "end_date": "2023-02-02"}')


def test_groups_perks_exemptions_round_trip():
    from model.data_models import Perk

    data = _sample_data()
    data.group_factors = {"R1": 1.0, "R2": 0.9}
    data.resident_groups = {"A": "R2", "B": "R1"}
    data.perks = [
        Perk("A", 0.8, date(2023, 1, 5), date(2023, 1, 20)),
        Perk("B", 0.9),  # forever
    ]
    data.exempt_shifts = {"A": ["NF"]}
    data.named_groups = {"Team A": ["A", "B"], "Team B": ["C"]}

    restored = input_data_from_json(input_data_to_json(data))
    assert restored.group_factors == data.group_factors
    assert restored.resident_groups == data.resident_groups
    assert restored.perks == data.perks  # Perk is a NamedTuple: tuple equality
    assert restored.exempt_shifts == data.exempt_shifts
    assert restored.named_groups == data.named_groups


def test_legacy_config_without_new_fields_loads_none():
    restored = input_data_from_json(input_data_to_json(_sample_data()))
    assert restored.group_factors is None
    assert restored.resident_groups is None
    assert restored.perks is None
    assert restored.exempt_shifts is None
    assert restored.named_groups is None


def test_config_json_has_no_display_key_when_unused():
    raw = json.loads(input_data_to_json(_sample_data()))
    assert "display" not in raw


def test_display_section_round_trips():
    from model.config_io import display_from_json

    display = {
        "palette": {"points": "#4a90d9", "unfilled": "#123456"},
        "extra_cols": ["Consultant"],
        "extra_vals": {"Consultant": {"2023-01-01": "Dr X"}},
        "col_order": ["Date", "Consultant"],
    }
    text = input_data_to_json(_sample_data(), display=display)
    assert display_from_json(text) == display
    # The solver config part still loads normally from a display-bearing file.
    restored = input_data_from_json(text)
    assert restored.juniors == ["A", "B"]


def test_display_from_json_defensive():
    from model.config_io import display_from_json

    assert display_from_json(input_data_to_json(_sample_data())) is None  # absent
    assert display_from_json("{not json") is None
    assert display_from_json('{"display": "nope"}') is None
    # Unknown palette roles and non-hex values are dropped.
    text = input_data_to_json(
        _sample_data(),
        display={"palette": {"points": "#4a90d9", "bogus": "#111111", "senior": "red"}},
    )
    assert display_from_json(text) == {"palette": {"points": "#4a90d9"}}


def test_blackouts_round_trip_and_legacy_none():
    from model.data_models import Blackout

    data = _sample_data()
    data.named_groups = {"Team A": ["A", "B"]}
    data.blackouts = [
        Blackout("Team A", (), date(2023, 1, 10), date(2023, 1, 12)),
        Blackout(None, ("C",), date(2023, 1, 20), date(2023, 1, 20), False, False),
    ]
    restored = input_data_from_json(input_data_to_json(data))
    assert restored.blackouts == data.blackouts

    assert input_data_from_json(input_data_to_json(_sample_data())).blackouts is None


def test_reductions_round_trip_and_legacy_none():
    from model.data_models import LoadReduction

    data = _sample_data()
    data.named_groups = {"Team A": ["A", "B"]}
    data.reductions = [
        LoadReduction("Team A", (), ("NF",), 0.25, date(2023, 1, 8), date(2023, 1, 21)),
        LoadReduction(None, ("C",), ("Day",), 0.0, date(2023, 1, 1), date(2023, 1, 28), True),
    ]
    restored = input_data_from_json(input_data_to_json(data))
    assert restored.reductions == data.reductions

    assert input_data_from_json(input_data_to_json(_sample_data())).reductions is None


def test_preferences_round_trip_and_legacy_none():
    data = _sample_data()
    data.preferred_shifts = {"A": ["NF"]}
    data.preferred_day_type = {"B": "weekday", "C": "weekend"}
    restored = input_data_from_json(input_data_to_json(data))
    assert restored.preferred_shifts == data.preferred_shifts
    assert restored.preferred_day_type == data.preferred_day_type

    legacy = input_data_from_json(input_data_to_json(_sample_data()))
    assert legacy.preferred_shifts is None
    assert legacy.preferred_day_type is None
