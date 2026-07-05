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
    blackout_night_before_dates,
    blackout_person_windows,
    is_night_call,
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
    assert out[1].night_before is False and out[1].compensated is True
    assert out[2].members == ("A", "B") and out[2].compensated is False


def test_blackout_person_windows_and_night_before_dates():
    blackouts = [
        Blackout("T", (), date(2023, 1, 5), date(2023, 1, 6)),
        Blackout(None, ("C",), date(2023, 1, 3), date(2023, 1, 3), night_before=False,
                 compensated=False),
    ]
    windows = blackout_person_windows(blackouts, {"T": ["A", "B"]})
    # Group members resolved at call time; the window is NOT extended — the
    # night-before rule is a separate, night-calls-only partial block.
    assert windows["A"] == [(date(2023, 1, 5), date(2023, 1, 6), True)]
    assert windows["B"] == [(date(2023, 1, 5), date(2023, 1, 6), True)]
    assert windows["C"] == [(date(2023, 1, 3), date(2023, 1, 3), False)]
    nights = blackout_night_before_dates(blackouts, {"T": ["A", "B"]})
    assert nights == {"A": {date(2023, 1, 4)}, "B": {date(2023, 1, 4)}}
    # An undefined group covers nobody.
    assert blackout_person_windows(blackouts[:1], None) == {}


def test_is_night_call_marker():
    night = ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=True, points=2.0)
    day = ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)
    nf = ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=True, points=2.0)
    assert is_night_call(night)
    assert not is_night_call(day)
    assert not is_night_call(nf)  # night float is a separate rotation


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


def test_solver_blocks_window_and_night_call_before():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=True, points=2.0),
    ]
    data = _data(
        shifts=shifts,
        juniors=["A", "B", "C"],
        blackouts=[Blackout(None, ("A",), date(2023, 1, 5), date(2023, 1, 5))],
    )
    df = build_schedule(data, env="test")
    rows = {row["Date"]: row for row in df.to_dict("records")}
    # In the window every non-NF shift is off-limits for A.
    assert rows[date(2023, 1, 5)]["D"] != "A"
    assert rows[date(2023, 1, 5)]["N"] != "A"
    # The day before, only the night call is blocked — the day shift is fine.
    assert rows[date(2023, 1, 4)]["N"] != "A"
    for row in rows.values():
        assert "Unfilled" not in (row["D"], row["N"])


def test_blackout_never_touches_night_float():
    pytest.importorskip("ortools")
    from model.data_models import NightFloatAssignment, NightFloatCoverage
    from model.optimiser import build_schedule

    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=2.0),
    ]
    # NF is covered every day by A via the overlay; A is also blacked out for
    # the block. Blackouts only touch regular slots, so A still covers NF.
    data = _data(
        shifts=shifts,
        juniors=["A"],
        nf_juniors=["A"],
        nf_coverage={"NF": NightFloatCoverage("NF", weekdays=(0, 1, 2, 3, 4, 5, 6))},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 7), (), 0)],
        blackouts=[Blackout(None, ("A",), date(2023, 1, 2), date(2023, 1, 7))],
    )
    df = build_schedule(data, env="test")
    assert all(row["NF"] == "A" for row in df.to_dict("records"))


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


def test_validate_schedule_flags_blackout_and_night_before():
    from model.data_models import NightFloatAssignment, NightFloatCoverage

    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=True, points=2.0),
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=2.0),
    ]
    # "NF" is covered by the overlay (B on the 4th, A on the 5th), so those
    # cells are not regular assignments and the blackout must not touch them.
    data = _data(
        shifts=shifts,
        nf_juniors=["A", "B"],
        nf_coverage={"NF": NightFloatCoverage("NF", weekdays=(0, 1, 2, 3, 4, 5, 6))},
        nf_assignments=[
            NightFloatAssignment("B", date(2023, 1, 4), date(2023, 1, 4), (), 0),
            NightFloatAssignment("A", date(2023, 1, 5), date(2023, 1, 5), (), 0),
        ],
        blackouts=[Blackout(None, ("A",), date(2023, 1, 5), date(2023, 1, 5))],
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 4), "D": "A", "N": "A", "NF": "B"},
        {"Date": date(2023, 1, 5), "D": "A", "N": "B", "NF": "A"},
    ])
    # The overlay marks NF-covered cells so validation skips them as regular.
    df.attrs["nf_cells"] = {
        (date(2023, 1, 4).isoformat(), "NF"): "B",
        (date(2023, 1, 5).isoformat(), "NF"): "A",
    }
    issues = validate_schedule(df, data)
    # Window: the day shift on Jan 5 is flagged; NF the same day is NOT.
    assert any("group blackout" in i and "'D'" in i for i in issues)
    assert not any("'NF'" in i and "blackout" in i for i in issues)
    # Night before: the night call on Jan 4 is flagged; the day shift is not.
    assert any("night call 'N'" in i and "post-call" in i for i in issues)
    assert not any("'D'" in i and str(date(2023, 1, 4)) in i and "blackout" in i for i in issues)


def test_blackout_applies_to_rotator_group_member():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    # B is a rotator (active Jan 2-5) and in a blacked-out group Jan 3-4:
    # outside the window AND during the blackout, B must not appear.
    data = _data(
        rotators=[("B", date(2023, 1, 2), date(2023, 1, 5))],
        named_groups={"T": ["B"]},
        blackouts=[Blackout("T", (), date(2023, 1, 3), date(2023, 1, 4))],
    )
    df = build_schedule(data, env="test")
    by_date = {row["Date"]: row["D"] for row in df.to_dict("records")}
    for blocked_day in (date(2023, 1, 3), date(2023, 1, 4),
                        date(2023, 1, 6), date(2023, 1, 7)):
        assert by_date[blocked_day] == "A"
