"""Group blackout periods: blocking, day-before, compensation, ledger repayment."""
import sys, os
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import (
    Blackout,
    InputData,
    ShiftTemplate,
    blackout_person_windows,
    normalized_blackouts,
)
from model.ledger import update_ledger
from model.optimiser import resolve_targets
from model.validation import config_warnings, validate_input, validate_schedule
from model.weights import availability_weights


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


def test_normalized_blackouts_tolerates_short_tuples():
    entries = [
        ("T", None, date(2023, 1, 5), date(2023, 1, 6)),
        (None, ["A"], date(2023, 1, 5), date(2023, 1, 6), False),
        (None, ("A", "B"), date(2023, 1, 5), date(2023, 1, 6), True, False),
    ]
    out = list(normalized_blackouts(entries))
    assert out[0] == Blackout("T", (), date(2023, 1, 5), date(2023, 1, 6), True, True)
    assert out[1].day_before is False and out[1].compensated is True
    assert out[2].members == ("A", "B") and out[2].compensated is False


def test_blackout_person_windows_resolves_groups_and_day_before():
    blackouts = [
        Blackout("T", (), date(2023, 1, 5), date(2023, 1, 6)),
        Blackout(None, ("C",), date(2023, 1, 3), date(2023, 1, 3), day_before=False,
                 compensated=False),
    ]
    windows = blackout_person_windows(blackouts, {"T": ["A", "B"]})
    # Group members resolved at call time; day_before extends one day earlier.
    assert windows["A"] == [(date(2023, 1, 4), date(2023, 1, 6), True)]
    assert windows["B"] == [(date(2023, 1, 4), date(2023, 1, 6), True)]
    assert windows["C"] == [(date(2023, 1, 3), date(2023, 1, 3), False)]
    # An undefined group covers nobody.
    assert blackout_person_windows(blackouts[:1], None) == {}


def test_compensated_blackout_keeps_weights_uncompensated_scales():
    comp = _data(blackouts=[Blackout(None, ("A",), date(2023, 1, 2), date(2023, 1, 7))])
    plain = _data()
    assert availability_weights(comp) == availability_weights(plain)

    uncomp = _data(
        blackouts=[Blackout(None, ("A",), date(2023, 1, 2), date(2023, 1, 7),
                            compensated=False)]
    )
    weights = availability_weights(uncomp)
    assert weights["A"] == 0.0
    assert weights["B"] == 6.0


def test_solver_blocks_blackout_window_and_day_before():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        blackouts=[Blackout(None, ("A",), date(2023, 1, 5), date(2023, 1, 5))]
    )
    df = build_schedule(data, env="test")
    by_date = {row["Date"]: row["D"] for row in df.to_dict("records")}
    assert by_date[date(2023, 1, 4)] == "B"  # the day before is blocked too
    assert by_date[date(2023, 1, 5)] == "B"
    assert "Unfilled" not in by_date.values()


def test_compensated_blackout_shortfall_is_repaid_next_block():
    # A misses the whole block behind a compensated blackout: no excused
    # credit is issued, so the deficit carries and the next block's carryover
    # target is higher for A than for B — the debt is repaid, not forgiven.
    data = _data(blackouts=[Blackout(None, ("A",), date(2023, 1, 2), date(2023, 1, 7))])
    df = pd.DataFrame(
        [{"Date": d, "D": "B"} for d in
         (date(2023, 1, 2 + i) for i in range(6))]
    )
    ledger = update_ledger({}, df, data)
    assert ledger["A"]["total"] == 0.0
    assert "adjustments" not in ledger["A"]  # nothing excused, nothing credited
    assert ledger["B"]["total"] == 6.0

    next_block = _data()
    resolved = resolve_targets(next_block, ledger)
    assert resolved.target_total_map["A"] == pytest.approx(6.0)
    assert resolved.target_total_map["B"] == pytest.approx(0.0)


def test_uncompensated_blackout_shortfall_is_excused_not_repaid():
    data = _data(
        blackouts=[Blackout(None, ("A",), date(2023, 1, 2), date(2023, 1, 7),
                            compensated=False)]
    )
    df = pd.DataFrame(
        [{"Date": d, "D": "B"} for d in
         (date(2023, 1, 2 + i) for i in range(6))]
    )
    ledger = update_ledger({}, df, data)
    # The default no-catch-up policy credits the excused share, exactly like
    # uncompensated leave, so both residents end the block on equal standing.
    assert ledger["A"]["total"] == pytest.approx(3.0)
    assert ledger["B"]["total"] == pytest.approx(3.0)
    assert ledger["A"]["adjustments"]["excused_credit"]["total"] == pytest.approx(3.0)


def test_validate_input_blackout_rules():
    issues = validate_input(_data(
        blackouts=[Blackout("Ghost", (), date(2023, 1, 5), date(2023, 1, 6))]
    ))
    assert any("undefined group 'Ghost'" in i for i in issues)

    issues = validate_input(_data(
        blackouts=[Blackout(None, (), date(2023, 1, 5), date(2023, 1, 6))]
    ))
    assert any("no group and no members" in i for i in issues)

    issues = validate_input(_data(
        blackouts=[Blackout(None, ("Zed",), date(2023, 1, 6), date(2023, 1, 5))]
    ))
    assert any("unknown resident 'Zed'" in i for i in issues)
    assert any("ends (2023-01-05) before it starts" in i for i in issues)

    ok = _data(
        named_groups={"T": ["A", "B"]},
        blackouts=[Blackout("T", (), date(2023, 1, 5), date(2023, 1, 6))],
    )
    assert validate_input(ok) == []


def test_blackout_warnings():
    # Empty group and out-of-block window are both no-effect advisories.
    warnings = config_warnings(_data(
        named_groups={"T": []},
        blackouts=[Blackout("T", (), date(2023, 2, 5), date(2023, 2, 6))],
    ))
    assert any("empty group 'T'" in w for w in warnings)
    assert any("outside the schedule dates" in w for w in warnings)

    # Both juniors blacked out on a day with a junior shift: coverage risk.
    warnings = config_warnings(_data(
        named_groups={"T": ["A", "B"]},
        blackouts=[Blackout("T", (), date(2023, 1, 5), date(2023, 1, 5))],
    ))
    assert any("expect unfilled slots" in w for w in warnings)

    # Whole-block compensated blackout keeps an unearnable full share.
    warnings = config_warnings(_data(
        blackouts=[Blackout(None, ("A",), date(2023, 1, 2), date(2023, 1, 7))],
        extra_points={"A": 2.0},
    ))
    assert any("whole block" in w for w in warnings)
    assert any("penalty cannot fit" in w for w in warnings)


def test_validate_schedule_flags_blackout_assignment():
    data = _data(blackouts=[Blackout(None, ("A",), date(2023, 1, 5), date(2023, 1, 5))])
    df = pd.DataFrame([
        {"Date": date(2023, 1, 4), "D": "A"},  # day before — blocked too
        {"Date": date(2023, 1, 5), "D": "B"},
    ])
    issues = validate_schedule(df, data)
    assert any("group blackout" in i for i in issues)
