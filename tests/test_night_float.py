"""Night float as a separate coverage overlay (not a regular shift type).

Night float is resolved *before* the regular scheduler: covered dates are
assigned to their NF coverer and removed from regular demand; uncovered dates
stay ordinary regular shifts; a covered date with no coverer falls back to
regular; and each NF assignment blocks its coverer from regular work (plus rest
days) exactly like an *uncompensated* leave — reduced regular target, no future
catch-up. NF never carries regular points.
"""
import sys, os
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover - fallback when pandas missing
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import (
    Blackout,
    InputData,
    Leave,
    NightFloatAssignment,
    NightFloatCoverage,
    ShiftTemplate,
    is_regular_night_call,
    nf_covered,
)
from model.fairness import calculate_label_counts, calculate_points
from model.ledger import update_ledger
from model.night_float import (
    nf_cells_from_attr,
    nf_cells_to_attr,
    nf_duty_days,
    nf_leave_windows,
    resolve_night_float,
)
from model.validation import config_warnings, validate_input, validate_schedule
from model.weights import availability_weights


def _nf_shift(**over):
    base = dict(label="NF", role="Junior", night_float=True, thu_weekend=False, points=2.0)
    base.update(over)
    return ShiftTemplate(**base)


def _reg_shift(label="D", **over):
    base = dict(label=label, role="Junior", night_float=False, thu_weekend=False, points=1.0)
    base.update(over)
    return ShiftTemplate(**base)


def _data(**over):
    base = dict(
        start_date=date(2023, 1, 2),   # Monday
        end_date=date(2023, 1, 5),     # Thursday (4 days)
        shifts=[_nf_shift()],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=["A"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    base.update(over)
    return InputData(**base)


def _all_week():
    return NightFloatCoverage("NF", weekdays=(0, 1, 2, 3, 4, 5, 6))


# --- coverage predicate + date-aware night-call ------------------------------

def test_nf_covered_weekday_include_exclude():
    shift = _nf_shift()
    data = _data(
        nf_coverage={"NF": NightFloatCoverage(
            "NF", weekdays=(0,), include_dates=(date(2023, 1, 3),),
            exclude_dates=(date(2023, 1, 9),),
        )}
    )
    assert nf_covered(date(2023, 1, 2), shift, data)      # Monday: weekday match
    assert not nf_covered(date(2023, 1, 4), shift, data)  # Wednesday: no match
    assert nf_covered(date(2023, 1, 3), shift, data)      # explicit include date
    assert not nf_covered(date(2023, 1, 9), shift, data)  # Monday but excluded

    # A non-NF-eligible shift is never NF-covered, and an eligible shift with no
    # coverage configured is never covered (scheduled entirely as regular).
    assert not nf_covered(date(2023, 1, 2), _reg_shift(), data)
    assert not nf_covered(date(2023, 1, 2), shift, _data())


def test_is_regular_night_call_is_date_aware():
    night = _nf_shift(thu_weekend=True)  # NF-eligible night on-call
    # Covered on Mondays only.
    data = _data(nf_coverage={"NF": NightFloatCoverage("NF", weekdays=(0,))})
    # On a covered date the overlay owns it — not a regular night call.
    assert not is_regular_night_call(date(2023, 1, 2), night, data)
    # On an uncovered date it is an ordinary regular night on-call.
    assert is_regular_night_call(date(2023, 1, 3), night, data)
    # A day shift is never a night call.
    assert not is_regular_night_call(date(2023, 1, 3), _reg_shift(), data)


# --- overlay resolver --------------------------------------------------------

def test_coverer_only_covers_own_role():
    # A junior with a blanket assignment (empty labels) covers only the junior
    # NF shift — never the senior one. The senior NF shift, left uncovered, is a
    # coverage gap that falls back to regular scheduling.
    data = _data(
        shifts=[
            _nf_shift(label="JrNF", role="Junior"),
            _nf_shift(label="SrNF", role="Senior"),
        ],
        juniors=["A"], seniors=["S"], nf_juniors=["A"], nf_seniors=["S"],
        nf_coverage={
            "JrNF": NightFloatCoverage("JrNF", weekdays=tuple(range(7))),
            "SrNF": NightFloatCoverage("SrNF", weekdays=tuple(range(7))),
        },
        # A covers the whole block (empty labels = all of A's own-role NF shifts).
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 5), (), 0)],
    )
    nf_cells, gap_slots, _ = resolve_night_float(data)
    assert set(nf_cells.values()) == {"A"}
    assert all(lbl == "JrNF" for _d, lbl in nf_cells)   # never the senior shift
    assert all(lbl == "SrNF" for _d, lbl in gap_slots)  # senior NF is a gap (no senior coverer)
    assert gap_slots  # the senior shift really is uncovered


def test_resolve_night_float_covered_gap_and_leaves():
    data = _data(
        end_date=date(2023, 1, 5),
        nf_coverage={"NF": _all_week()},
        # A covers only the first two days; the last two are covered-but-unassigned.
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 3), (), 0)],
    )
    nf_cells, gap_slots, leaves = resolve_night_float(data)
    assert nf_cells == {
        (date(2023, 1, 2), "NF"): "A",
        (date(2023, 1, 3), "NF"): "A",
    }
    assert gap_slots == {(date(2023, 1, 4), "NF"), (date(2023, 1, 5), "NF")}
    # The assignment becomes one uncompensated leave over its window (+0 rest).
    assert leaves == [Leave("A", date(2023, 1, 2), date(2023, 1, 3), False)]
    assert nf_duty_days(nf_cells) == {"A": 2}


def test_nf_cells_attr_round_trip_is_serializable():
    # df.attrs is serialized by pandas/Streamlit, which rejects tuple keys, so
    # the overlay stores {date-iso: {label: name}} and reads it back to tuples.
    cells = {(date(2023, 1, 2), "NF"): "A", (date(2023, 1, 3), "NF"): "B"}
    attr = nf_cells_to_attr(cells)
    assert attr == {"2023-01-02": {"NF": "A"}, "2023-01-03": {"NF": "B"}}
    assert all(isinstance(k, str) for k in attr)  # JSON/Arrow-serializable keys

    class _Frame:
        attrs = {"nf_cells": attr}

    assert nf_cells_from_attr(_Frame()) == {
        ("2023-01-02", "NF"): "A", ("2023-01-03", "NF"): "B",
    }


def test_nf_leave_windows_add_rest_and_are_uncompensated():
    data = _data(
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 3), (), 2)],
    )
    windows = nf_leave_windows(data)
    # window end extended by the 2 rest days; compensated flag is always False.
    assert windows == [Leave("A", date(2023, 1, 2), date(2023, 1, 5), False)]


def test_nf_assignment_reduces_availability_like_uncompensated_leave():
    # The linchpin: an NF window is fed to the regular scheduler as an
    # uncompensated leave, so it scales the coverer's fair share down exactly
    # as an equivalent uncompensated leave would (no special NF weighting).
    nf = _data(nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 4), (), 0)])
    leave = _data(leaves=[("A", date(2023, 1, 2), date(2023, 1, 4), False)])
    assert availability_weights(nf) == availability_weights(leave)


# --- fairness / points: NF cells excluded unless counted ---------------------

def _covered_df(labels_by_day):
    """Build a df and mark its NF cells in attrs the way the overlay does."""
    rows = [{"Date": d, **cols} for d, cols in labels_by_day]
    df = pd.DataFrame(rows)
    return df


def test_calculate_points_excludes_nf_cells_and_counts_duty_days():
    data = _data(
        shifts=[_reg_shift(), _nf_shift()],
        nf_coverage={"NF": _all_week()},
    )
    df = _covered_df([
        (date(2023, 1, 2), {"D": "B", "NF": "A"}),
        (date(2023, 1, 3), {"D": "B", "NF": "A"}),
    ])
    df.attrs["nf_cells"] = nf_cells_to_attr({
        (date(2023, 1, 2), "NF"): "A",
        (date(2023, 1, 3), "NF"): "A",
    })
    pts = calculate_points(df, data)
    # A's NF work carries no regular points; it is recorded as duty days.
    assert pts["A"]["total"] == 0.0
    assert pts["A"]["night_float"] == 2       # two duty days
    assert pts["B"]["total"] == 2.0           # two D calls @ 1.0
    # Label counts skip NF cells too.
    counts = calculate_label_counts(df, data)
    assert counts["A"].get("NF", 0) == 0
    assert counts["B"]["D"] == 2


# --- ledger: NF window is excused, never caught up ---------------------------

def test_nf_assignment_shortfall_is_excused_not_repaid():
    # A covers NF for the whole 4-day block, so A does no regular work. The NF
    # window is an uncompensated leave, so the no-catch-up policy credits A's
    # excused regular share: A and B finish level and A is not loaded extra next
    # block. NF duty days are tracked separately (outside the balance).
    data = _data(
        shifts=[_reg_shift(), _nf_shift()],
        nf_coverage={"NF": _all_week()},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 5), (), 0)],
    )
    days = [date(2023, 1, 2 + i) for i in range(4)]
    df = _covered_df([(d, {"D": "B", "NF": "A"}) for d in days])
    df.attrs["nf_cells"] = nf_cells_to_attr({(d, "NF"): "A" for d in days})

    ledger = update_ledger({}, df, data)
    # Regular pool is the D shift only (4 points); B earns them all, A earns
    # none but is credited the excused shortfall -> equal standing.
    assert ledger["A"]["total"] == pytest.approx(ledger["B"]["total"])
    assert ledger["A"]["total"] == pytest.approx(2.0)
    assert ledger["A"]["adjustments"]["excused_credit"]["total"] == pytest.approx(2.0)
    assert ledger["A"]["nf_days"] == 4        # informational duty record
    assert "night_float" not in ledger["A"]   # not a carryover dimension


# --- validation: input errors + advisory warnings ----------------------------

def test_validate_input_night_float_rules():
    issues = validate_input(_data(
        shifts=[_reg_shift(), _nf_shift()],
        nf_coverage={
            "Ghost": NightFloatCoverage("Ghost", weekdays=(0,)),   # unknown shift
            "D": NightFloatCoverage("D", weekdays=(9,)),           # not NF-eligible + bad weekday
        },
    ))
    assert any("unknown shift 'Ghost'" in i for i in issues)
    assert any("'D', which is not marked night-float-eligible" in i for i in issues)
    assert any("invalid weekday 9" in i for i in issues)

    issues = validate_input(_data(
        nf_coverage={"NF": _all_week()},
        nf_assignments=[
            NightFloatAssignment("Zed", date(2023, 1, 2), date(2023, 1, 3), (), 1),   # unknown
            NightFloatAssignment("B", date(2023, 1, 3), date(2023, 1, 2), (), -1),    # B not NF, backwards, neg rest
        ],
    ))
    assert any("unknown resident 'Zed'" in i for i in issues)
    assert any("'B' has a night-float assignment but is not marked" in i for i in issues)
    assert any("ends (2023-01-02) before it starts" in i for i in issues)
    assert any("rest days for 'B' cannot be negative" in i for i in issues)

    # A coverer cannot be assigned a night-float shift of a different role.
    issues = validate_input(_data(
        shifts=[_nf_shift(label="JrNF", role="Junior"),
                _nf_shift(label="SrNF", role="Senior")],
        juniors=["A"], seniors=["S"], nf_juniors=["A"], nf_seniors=["S"],
        nf_coverage={"SrNF": NightFloatCoverage("SrNF", weekdays=(0,))},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 3), ("SrNF",), 0)],
    ))
    assert any("'A' (Junior) names 'SrNF', a Senior night-float shift" in i for i in issues)

    # A well-formed overlay validates clean.
    ok = _data(
        nf_coverage={"NF": _all_week()},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 3), ("NF",), 1)],
    )
    assert validate_input(ok) == []


def test_night_float_warnings():
    # NF-eligible shift with no coverage pattern -> scheduled as regular.
    warnings = config_warnings(_data(shifts=[_nf_shift()]))
    assert any("night-float-eligible but has no coverage" in w for w in warnings)

    # Covered dates with no assigned coverer -> regular fallback advisory.
    warnings = config_warnings(_data(
        nf_coverage={"NF": _all_week()},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 2), (), 0)],
    ))
    assert any("no assigned coverer and fall back to regular" in w for w in warnings)


def test_validate_schedule_flags_regular_night_before_on_uncovered_nf_night():
    # An NF-eligible night on-call with no coverage on the relevant date is an
    # ordinary regular night call, so the blackout "night before" rule still
    # protects the regular resident from being post-call on their off day.
    data = _data(
        shifts=[_nf_shift(thu_weekend=True)],
        juniors=["A", "B"],
        nf_juniors=["A", "B"],
        blackouts=[Blackout(None, ("A",), date(2023, 1, 6), date(2023, 1, 6))],
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 5), "NF": "A"},  # night before A's blackout
        {"Date": date(2023, 1, 6), "NF": "B"},
    ])
    issues = validate_schedule(df, data)
    assert any("night call 'NF'" in i and "post-call" in i for i in issues)


# --- end-to-end overlay through build_schedule -------------------------------

def test_overlay_assigns_coverer_and_frees_regular():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        shifts=[_reg_shift(), _nf_shift()],
        juniors=["A", "B", "C"],
        nf_juniors=["A"],
        nf_coverage={"NF": _all_week()},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 5), (), 0)],
    )
    df = build_schedule(data, env="test")
    rows = df.to_dict("records")
    # Every NF cell is A (written by the overlay) and marked in attrs.
    assert all(row["NF"] == "A" for row in rows)
    assert len(df.attrs["nf_cells"]) == 4
    # A is on NF the whole block -> blocked from regular D; B/C cover it.
    assert "A" not in [row["D"] for row in rows]
    assert "Unfilled" not in [row["D"] for row in rows]


def test_uncovered_dates_are_regular_shifts():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    # NF covered on weekends only; weekdays are ordinary regular shifts.
    data = _data(
        start_date=date(2023, 1, 2),   # Monday
        end_date=date(2023, 1, 8),     # Sunday (7 days)
        shifts=[_nf_shift(points=1.0)],
        juniors=["A", "B"],
        nf_juniors=["A"],
        nf_coverage={"NF": NightFloatCoverage("NF", weekdays=(5, 6))},  # Sat/Sun
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 7), date(2023, 1, 8), (), 0)],
    )
    df = build_schedule(data, env="test")
    nf_cells = nf_cells_from_attr(df)
    assert set(nf_cells) == {
        (date(2023, 1, 7).isoformat(), "NF"),
        (date(2023, 1, 8).isoformat(), "NF"),
    }
    for row in df.to_dict("records"):
        key = (row["Date"].isoformat(), "NF")
        if row["Date"].weekday() in (5, 6):
            assert nf_cells[key] == "A"            # overlay cell
        else:
            assert key not in nf_cells             # regular assignment
            assert row["NF"] in ("A", "B")
    pts = calculate_points(df, data)
    # Weekend NF cells are A's duty days (excluded from totals); the 5 weekday
    # NF-eligible shifts are ordinary regular points shared by A and B.
    assert pts["A"]["night_float"] == 2
    assert pts["A"]["total"] + pts["B"]["total"] == pytest.approx(5.0)


def test_covered_gap_falls_back_to_regular():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(
        shifts=[_nf_shift(points=1.0)],
        juniors=["A", "B"],
        nf_juniors=["A"],
        nf_coverage={"NF": _all_week()},
        # A covers only the first two days; the last two are gaps -> regular.
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 3), (), 0)],
    )
    df = build_schedule(data, env="test")
    nf_cells = nf_cells_from_attr(df)
    assert set(nf_cells) == {
        (date(2023, 1, 2).isoformat(), "NF"),
        (date(2023, 1, 3).isoformat(), "NF"),
    }
    for row in df.to_dict("records"):
        key = (row["Date"].isoformat(), "NF")
        if row["Date"] in (date(2023, 1, 4), date(2023, 1, 5)):
            assert key not in nf_cells             # gap filled as a regular shift
            assert row["NF"] in ("A", "B")


def test_coverer_is_blocked_from_regular_during_window_and_rest():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    # A covers NF Jan 2-3 with one rest day -> A is off regular D Jan 2, 3 and 4.
    data = _data(
        shifts=[_reg_shift(), _nf_shift()],
        juniors=["A", "B", "C"],
        nf_juniors=["A"],
        nf_coverage={"NF": _all_week()},
        nf_assignments=[NightFloatAssignment("A", date(2023, 1, 2), date(2023, 1, 3), (), 1)],
    )
    df = build_schedule(data, env="test")
    by_date = {row["Date"]: row for row in df.to_dict("records")}
    for blocked in (date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4)):
        assert by_date[blocked]["D"] != "A"        # window + rest day
    # NF Jan 2-3 covered by A; Jan 4-5 are gaps that fall back to regular.
    assert by_date[date(2023, 1, 2)]["NF"] == "A"
    assert by_date[date(2023, 1, 3)]["NF"] == "A"
