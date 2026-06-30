import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

import pytest

from model.data_models import ShiftTemplate, InputData
from model.ledger import empty_ledger, update_ledger, ledger_to_json, ledger_from_json


def _data():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    return InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 6),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )


def test_update_ledger_accumulates():
    data = _data()
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S": "A"},
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "B"},
    ])
    prior = {"A": {"total": 5.0, "weekend": 0.0, "night_float": 0.0}}
    updated = update_ledger(prior, df, data)
    assert updated["A"]["total"] == 7.0  # 5 prior + 2 this block
    assert updated["B"]["total"] == 1.0  # new resident this block


def test_ledger_round_trip():
    ledger = {"A": {"total": 7.0, "weekend": 2.0, "night_float": 3.0}}
    restored = ledger_from_json(ledger_to_json(ledger))
    assert restored == ledger


def test_ledger_from_json_fills_missing_dimensions():
    restored = ledger_from_json('{"A": {"total": 4}}')
    assert restored == {"A": {"total": 4.0, "weekend": 0.0, "night_float": 0.0}}


def test_empty_ledger():
    assert empty_ledger() == {}


def test_carryover_shifts_load_to_underloaded_resident():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule
    from model.fairness import calculate_points

    ledger = {
        "A": {"total": 10.0, "weekend": 0.0, "night_float": 0.0},
        "B": {"total": 0.0, "weekend": 0.0, "night_float": 0.0},
    }
    df = build_schedule(_data(), env="test", ledger=ledger)
    pts = calculate_points(df, _data())
    # A was overloaded in prior blocks, so this block should favour B.
    assert pts["A"]["total"] < pts["B"]["total"]


def test_no_ledger_is_unchanged_behaviour():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule
    from model.fairness import calculate_points

    df = build_schedule(_data(), env="test")
    pts = calculate_points(df, _data())
    assert abs(pts["A"]["total"] - pts["B"]["total"]) <= 1  # even split, no carryover
