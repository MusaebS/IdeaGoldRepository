import sys, os
from datetime import date

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
