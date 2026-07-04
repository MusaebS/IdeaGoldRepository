"""Soft shift preferences: weight rescaling, tie-breaking, fairness neutrality."""
import sys, os
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import InputData, ShiftTemplate
from model.fairness import preference_satisfaction
from model.optimiser import objective_weights, resolve_targets
from model.validation import config_warnings, validate_input


def _data(**over):
    base = dict(
        start_date=date(2023, 1, 6),   # Friday
        end_date=date(2023, 1, 7),     # Saturday
        shifts=[ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=1,
    )
    base.update(over)
    return InputData(**base)


def test_objective_weights_k_gating_and_domination():
    # Without preferences the legacy ladder is byte-identical.
    assert objective_weights(28, 10, False) == (10**9, 10**6, 10**3, 10**2, 10, 1)
    # With preferences everything is rescaled by K = 2·D·S + 1, which strictly
    # dominates the whole preference range [-2·D·S, 0].
    weights = objective_weights(28, 10, True)
    k = 2 * 28 * 10 + 1
    assert weights == (10**9 * k, 10**6 * k, 10**3 * k, 10**2 * k, 10 * k, k)
    assert min(weights) == k > 2 * 28 * 10


def test_preferences_break_ties_without_touching_fairness():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(preferred_day_type={"A": "weekend"})
    df = build_schedule(data, env="test")
    by_date = {row["Date"]: row["D"] for row in df.to_dict("records")}
    # One Friday + one Saturday, equal shares either way: A's weekend
    # preference decides the otherwise-tied split.
    assert by_date[date(2023, 1, 7)] == "A"
    assert by_date[date(2023, 1, 6)] == "B"


def test_preference_never_leaves_a_slot_unfilled():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        start_date=date(2023, 1, 7), end_date=date(2023, 1, 7),  # Saturday only
        juniors=["A"], preferred_day_type={"A": "weekday"},
    )
    df = build_schedule(data, env="test")
    assert df.to_dict("records")[0]["D"] == "A"  # despite the mismatch


def test_preference_never_buys_a_deviation():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    # A prefers weekends; both days are weekend days. Taking both would
    # satisfy the preference twice but cost total-deviation — fairness wins.
    data = _data(
        start_date=date(2023, 1, 7), end_date=date(2023, 1, 8),  # Sat + Sun
        preferred_day_type={"A": "weekend"},
    )
    df = build_schedule(data, env="test")
    workers = {row["D"] for row in df.to_dict("records")}
    assert workers == {"A", "B"}


def test_resolve_targets_identical_with_and_without_preferences():
    plain = resolve_targets(_data())
    with_prefs = resolve_targets(_data(
        preferred_shifts={"A": ["D"]}, preferred_day_type={"B": "weekday"}
    ))
    assert with_prefs.target_total_map == plain.target_total_map
    assert with_prefs.target_weekend == plain.target_weekend
    assert with_prefs.target_night_float == plain.target_night_float


def test_preference_satisfaction_counts_per_axis():
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    data = _data(
        shifts=shifts,
        preferred_shifts={"A": ["N"]},
        preferred_day_type={"A": "weekend", "B": "weekday"},
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 6), "D": "B", "N": "A"},   # Friday
        {"Date": date(2023, 1, 7), "D": "A", "N": "B"},   # Saturday
    ])
    stats = preference_satisfaction(df, data)
    # A: label axis 1/2 (N on Fri yes, D on Sat no); day axis 1/2 (Sat yes).
    assert stats["A"] == (2, 4)
    # B: day axis only — Friday yes, Saturday no.
    assert stats["B"] == (1, 2)


def test_validate_input_preference_rules():
    issues = validate_input(_data(
        preferred_shifts={"Zed": ["D"], "A": ["Ghost"]},
        preferred_day_type={"B": "nights"},
    ))
    assert any("unknown resident 'Zed'" in i for i in issues)
    assert any("prefers unknown shift 'Ghost'" in i for i in issues)
    assert any("must be 'weekend' or 'weekday'" in i for i in issues)
    ok = _data(preferred_shifts={"A": ["D"]}, preferred_day_type={"B": "weekend"})
    assert validate_input(ok) == []


def test_warning_for_never_workable_preferred_shift():
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="SN", role="Senior", night_float=False, thu_weekend=False, points=1.0),
    ]
    warnings = config_warnings(_data(
        shifts=shifts,
        seniors=["C"],
        preferred_shifts={"A": ["SN"]},  # a junior preferring a senior shift
    ))
    assert any("can never work it" in w for w in warnings)
