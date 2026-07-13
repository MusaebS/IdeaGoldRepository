"""Regression tests for the fairness-audit fixes: coverage-first + per-label."""
import sys, os
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.data_models import InputData, ShiftTemplate
from model.optimiser import LABEL_TARGET_MAX_CELLS, resolve_targets
from model.fairness import calculate_label_counts, calculate_points

MON = date(2026, 1, 5)


def _data(shifts, juniors, days=12, **kw):
    base = dict(
        start_date=MON, end_date=MON + timedelta(days=days - 1), shifts=shifts,
        juniors=list(juniors), seniors=[], nf_juniors=[], nf_seniors=[],
        leaves=[], rotators=[], min_gap=0, nf_block_length=1,
    )
    base.update(kw)
    return InputData(**base)


def _sh(label, points=1.0):
    return ShiftTemplate(label=label, role="Junior", night_float=False, thu_weekend=False, points=points)


def test_coverage_never_left_unfilled_when_fillable():
    # 3 residents, 7 one-shift days, everyone always free: the solver used to
    # leave a slot empty to make points look equal. Coverage must win now.
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data([_sh("D")], ["A", "B", "C"], days=7)
    df = build_schedule(data, env="test")
    assert "Unfilled" not in list(df["D"])


def test_per_label_distribution_is_balanced():
    # Equal total points must not hide an unequal shift-type mix: with D=1 and
    # N=2 over 6 residents / 12 days, every resident should get ~2 nights, not
    # 1..3. The per-label targets enforce this.
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data([_sh("D"), _sh("N", 2.0)], [f"J{i}" for i in range(6)], days=12)
    df = build_schedule(data, env="test")
    counts = calculate_label_counts(df, data)
    nights = [counts.get(p, {}).get("N", 0) for p in data.juniors]
    days_worked = [counts.get(p, {}).get("D", 0) for p in data.juniors]
    assert max(nights) - min(nights) <= 1
    assert max(days_worked) - min(days_worked) <= 1


def test_auto_label_targets_set_for_small_problems():
    data = _data([_sh("D"), _sh("N", 2.0)], [f"J{i}" for i in range(4)], days=10)
    resolved = resolve_targets(data)
    assert resolved.target_label  # populated
    # Each label's targets sum to that label's total points (fair shares).
    n_target = sum(v for (p, lbl), v in resolved.target_label.items() if lbl == "N")
    assert n_target == pytest.approx(20.0)  # 10 nights x 2 points


def test_label_targets_gated_off_when_too_large():
    # A roster past the cell threshold skips auto per-label targets so the
    # extra variables don't starve the primary balance under a time limit.
    n = LABEL_TARGET_MAX_CELLS // (28 * 10) + 5
    data = _data([_sh(f"S{i}") for i in range(10)], [f"J{i}" for i in range(n)], days=28)
    assert len(data.juniors) * 28 * 10 > LABEL_TARGET_MAX_CELLS
    assert resolve_targets(data).target_label is None


def test_explicit_label_targets_are_respected_over_the_gate():
    data = _data([_sh(f"S{i}") for i in range(10)], [f"J{i}" for i in range(30)], days=28)
    data.target_label = {("J0", "S0"): 3.0}
    assert resolve_targets(data).target_label == {("J0", "S0"): 3.0}


def test_preference_holders_keep_free_label_mix():
    data = _data([_sh("D"), _sh("N", 2.0)], ["A", "B", "C", "E"], days=10,
                 preferred_shifts={"A": ["N"]})
    resolved = resolve_targets(data)
    # A opted into a preference, so no per-label target pins their mix.
    assert not any(p == "A" for (p, _lbl) in resolved.target_label)
    assert any(p == "B" for (p, _lbl) in resolved.target_label)


def test_weekend_guardrail_prevents_avoidable_concentration():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        [_sh("D")], ["A", "B", "C", "D"], days=28,
        weekend_multiplier=2.0,
    )
    df = build_schedule(data, env="test")
    points = calculate_points(df, data)
    assert "Unfilled" not in list(df["D"])
    assert {points[p]["total"] for p in data.juniors} == {9.0}
    # Eight weekend calls x2: every resident carries exactly two calls / 4 pts.
    assert {points[p]["weekend"] for p in data.juniors} == {4.0}


def test_weekend_guardrail_never_sacrifices_coverage_when_spread_unavoidable():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        [_sh("D")], ["A", "B"], days=7,
        rotators=[("A", MON + timedelta(days=5), MON + timedelta(days=6))],
    )
    df = build_schedule(data, env="test")
    points = calculate_points(df, data)
    assert "Unfilled" not in list(df["D"])
    assert points["A"]["weekend"] == 2.0
    assert points["B"]["weekend"] == 0.0
