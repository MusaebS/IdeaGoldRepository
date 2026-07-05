"""Shift-type load reductions: caps, target modes, and ledger repayment."""
import sys, os
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import InputData, LoadReduction, ShiftTemplate, normalized_reductions
from model.ledger import update_ledger
from model.optimiser import resolve_targets
from model.reductions import reduction_caps
from model.validation import config_warnings, validate_input, validate_schedule


def _data(**over):
    base = dict(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 7),
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


def _nf_data(**over):
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=True, thu_weekend=False, points=2.0),
    ]
    return _data(shifts=shifts, nf_juniors=["A", "B"], **over)


def test_normalized_reductions_tolerates_short_tuples():
    entries = [
        ("T", None, ["N"], 0.5, date(2023, 1, 2), date(2023, 1, 4)),
        (None, ("A",), ("N", "D"), 0.0, date(2023, 1, 2), date(2023, 1, 4), True),
    ]
    out = list(normalized_reductions(entries))
    assert out[0] == LoadReduction("T", (), ("N",), 0.5, date(2023, 1, 2), date(2023, 1, 4), False)
    assert out[1].keep_total is True and out[1].labels == ("N", "D")


def test_reduction_caps_equal_weights():
    data = _nf_data(
        reductions=[LoadReduction(None, ("A",), ("N",), 0.5, date(2023, 1, 2), date(2023, 1, 4))]
    )
    caps = reduction_caps(data)
    assert len(caps) == 1
    cap = caps[0]
    # Window holds 3 N-slots worth 2 points each; A's equal share is 3.0, so
    # a 0.5 factor caps A at 1.5 points and takes the other 1.5 away.
    assert cap.person == "A" and cap.labels == frozenset({"N"})
    assert cap.cap_points == pytest.approx(1.5)
    assert cap.reduce_total == pytest.approx(1.5)
    assert cap.reduce_nf == pytest.approx(1.5)


def test_reduction_caps_availability_weighted_and_group_resolved():
    data = _nf_data(
        rotators=[("B", date(2023, 1, 2), date(2023, 1, 4))],  # B active 3 of 6 days
        named_groups={"T": ["A"]},
        reductions=[LoadReduction("T", (), ("N",), 0.5, date(2023, 1, 2), date(2023, 1, 4))],
    )
    caps = reduction_caps(data)
    # Weights A=6, B=3 → A's share of the 6 window points is 4; half is capped.
    assert caps[0].cap_points == pytest.approx(2.0)
    assert caps[0].reduce_total == pytest.approx(2.0)
    assert caps[0].person == "A" and len(caps) == 1


def test_reduction_caps_skip_ineligible_members():
    data = _nf_data(
        exempt_shifts={"A": ["N"]},
        reductions=[LoadReduction(None, ("A",), ("N",), 0.0, date(2023, 1, 2), date(2023, 1, 4))],
    )
    assert reduction_caps(data) == []  # A can never work N anyway


def test_resolve_targets_work_less_now_shifts_totals():
    reduction = LoadReduction(None, ("A",), ("D",), 0.0, date(2023, 1, 2), date(2023, 1, 7))
    resolved = resolve_targets(_data(reductions=[reduction]))
    assert resolved.target_total_map["A"] == pytest.approx(0.0)
    assert resolved.target_total_map["B"] == pytest.approx(6.0)

    keep = reduction._replace(keep_total=True)
    resolved = resolve_targets(_data(reductions=[keep]))
    assert resolved.target_total_map["A"] == pytest.approx(3.0)
    assert resolved.target_total_map["B"] == pytest.approx(3.0)


def test_resolve_targets_reduction_on_uncovered_nf_label():
    # "N" is night-float-eligible but has no coverage, so it is an ordinary
    # regular shift; a f=0 reduction on it lowers A's total share, B absorbs it.
    reduction = LoadReduction(None, ("A",), ("N",), 0.0, date(2023, 1, 2), date(2023, 1, 7))
    resolved = resolve_targets(_nf_data(reductions=[reduction]))
    assert resolved.target_night_float is None  # NF is no longer a balanced dimension
    assert resolved.target_total_map["A"] == pytest.approx(3.0)
    assert resolved.target_total_map["B"] == pytest.approx(15.0)


def test_solver_respects_zero_and_partial_caps():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        reductions=[LoadReduction(None, ("A",), ("D",), 0.0,
                                  date(2023, 1, 4), date(2023, 1, 5), True)]
    )
    df = build_schedule(data, env="test")
    by_date = {row["Date"]: row["D"] for row in df.to_dict("records")}
    assert by_date[date(2023, 1, 4)] == "B"
    assert by_date[date(2023, 1, 5)] == "B"
    assert "Unfilled" not in by_date.values()

    half = _data(
        reductions=[LoadReduction(None, ("A",), ("D",), 0.5,
                                  date(2023, 1, 2), date(2023, 1, 7), True)]
    )
    df = build_schedule(half, env="test")
    a_days = sum(1 for row in df.to_dict("records") if row["D"] == "A")
    assert a_days <= 1  # cap is 1.5 points; two 1-point days would exceed it


def test_reduction_shortfall_is_repaid_next_block():
    # The flagship invariant: a reduction is never credited by the ledger's
    # no-catch-up policy, so the whole shortfall carries as debt and the next
    # block's carryover target is higher for the reduced member.
    data = _data(
        reductions=[LoadReduction(None, ("A",), ("D",), 0.0,
                                  date(2023, 1, 2), date(2023, 1, 7))]
    )
    df = pd.DataFrame(
        [{"Date": d, "D": "B"} for d in
         (date(2023, 1, 2 + i) for i in range(6))]
    )
    ledger = update_ledger({}, df, data)
    assert ledger["A"]["total"] == 0.0
    assert "adjustments" not in ledger["A"]  # no excused credit
    assert ledger["B"]["total"] == 6.0

    resolved = resolve_targets(_data(), ledger)
    assert resolved.target_total_map["A"] == pytest.approx(6.0)
    assert resolved.target_total_map["B"] == pytest.approx(0.0)


def test_validate_input_reduction_rules():
    issues = validate_input(_data(
        reductions=[LoadReduction("Ghost", (), ("D",), 1.5,
                                  date(2023, 1, 4), date(2023, 1, 3))]
    ))
    assert any("must be between 0 and 1" in i for i in issues)
    assert any("undefined group 'Ghost'" in i for i in issues)
    assert any("ends (2023-01-03) before it starts" in i for i in issues)

    issues = validate_input(_data(
        reductions=[LoadReduction(None, ("Zed",), (), 0.5,
                                  date(2023, 1, 2), date(2023, 1, 4))]
    ))
    assert any("names no shift types" in i for i in issues)
    assert any("unknown resident 'Zed'" in i for i in issues)

    issues = validate_input(_data(
        reductions=[LoadReduction(None, ("A",), ("Ghost shift",), 0.5,
                                  date(2023, 1, 2), date(2023, 1, 4))]
    ))
    assert any("unknown shift 'Ghost shift'" in i for i in issues)

    ok = _data(
        named_groups={"T": ["A"]},
        reductions=[LoadReduction("T", (), ("D",), 0.5, date(2023, 1, 2), date(2023, 1, 4))],
    )
    assert validate_input(ok) == []


def test_reduction_warnings():
    warnings = config_warnings(_data(
        named_groups={"T": []},
        reductions=[
            LoadReduction("T", (), ("D",), 1.0, date(2023, 2, 1), date(2023, 2, 2)),
        ],
    ))
    assert any("100% load factor" in w for w in warnings)
    assert any("empty group 'T'" in w for w in warnings)
    assert any("outside the schedule dates" in w for w in warnings)

    # Both juniors fully reduced on D: the label cannot be assigned at all.
    warnings = config_warnings(_data(
        reductions=[LoadReduction(None, ("A", "B"), ("D",), 0.0,
                                  date(2023, 1, 4), date(2023, 1, 4))],
    ))
    assert any("fully reduced" in w for w in warnings)


def test_validate_schedule_flags_cap_violation():
    data = _data(
        reductions=[LoadReduction(None, ("A",), ("D",), 0.0,
                                  date(2023, 1, 4), date(2023, 1, 5))]
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 4), "D": "A"},
        {"Date": date(2023, 1, 5), "D": "B"},
    ])
    issues = validate_schedule(df, data)
    assert any("reduced shift(s) D" in i for i in issues)
