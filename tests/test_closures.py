"""Shift closures: cells stood down, outside demand / points / fairness."""
import sys, os
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from model.data_models import InputData, ShiftClosure, ShiftTemplate, normalized_closures
from model.closures import resolve_closures, reserved_cell_keys
from model.validation import validate_input

MON = date(2026, 1, 5)  # a Monday


def _sh(label, points=1.0, role="Junior"):
    return ShiftTemplate(label=label, role=role, night_float=False, thu_weekend=False, points=points)


def _data(shifts, juniors, days=14, **kw):
    base = dict(
        start_date=MON, end_date=MON + timedelta(days=days - 1), shifts=shifts,
        juniors=list(juniors), seniors=[], nf_juniors=[], nf_seniors=[],
        leaves=[], rotators=[], min_gap=0, nf_block_length=1,
    )
    base.update(kw)
    return InputData(**base)


def test_normalized_closures_accepts_tuples_and_typed():
    entries = [
        ("Clinic", MON, MON + timedelta(days=2)),
        ("Clinic", MON, MON + timedelta(days=6), (5, 6)),
        ShiftClosure("Ward", MON, MON, ()),
    ]
    out = list(normalized_closures(entries))
    assert out[0] == ShiftClosure("Clinic", MON, MON + timedelta(days=2), ())
    assert out[1].weekdays == (5, 6)
    assert out[2].label == "Ward"


def test_resolve_closures_range_and_weekday_filter():
    data = _data([_sh("Clinic")], ["A", "B"], days=14, closures=[
        ShiftClosure("Clinic", MON, MON + timedelta(days=6), (5, 6)),  # weekends only
    ])
    closed = resolve_closures(data)
    # Only Sat (MON+5) and Sun (MON+6) within the window are closed.
    assert closed == {
        (MON + timedelta(days=5), "Clinic"),
        (MON + timedelta(days=6), "Clinic"),
    }


def test_validate_input_rejects_unknown_label_and_bad_range():
    data = _data([_sh("Ward")], ["A"], closures=[
        ShiftClosure("Ghost", MON, MON + timedelta(days=1)),
        ShiftClosure("Ward", MON + timedelta(days=3), MON),  # end before start
        ShiftClosure("Ward", MON, MON, (9,)),  # bad weekday
    ])
    problems = validate_input(data)
    assert any("not a configured shift" in p for p in problems)
    assert any("ends" in p and "before it starts" in p for p in problems)
    assert any("invalid weekday" in p for p in problems)


def test_closed_cells_are_stood_down_not_unfilled():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule
    from model.fairness import calculate_points, format_fairness_log

    data = _data([_sh("Ward"), _sh("Clinic")], [f"J{i}" for i in range(4)], days=14,
                 closures=[ShiftClosure("Clinic", MON, MON + timedelta(days=13))])
    df = build_schedule(data, env="test")
    clinic = list(df["Clinic"])
    assert clinic == ["Closed"] * 14  # every Clinic cell stood down
    assert "Unfilled" not in clinic   # a closure is not a coverage gap

    pts = calculate_points(df, data)
    assert "Closed" not in pts  # no phantom resident
    # Clinic carried no points; only the 14 Ward points are shared out.
    assert sum(pts[p]["total"] for p in data.juniors) == pytest.approx(14.0)

    log = format_fairness_log(df, data)
    assert "0 unfilled, 14 closed" in log
    assert "MISMATCH" not in log  # the points checksum still reconciles


def test_closed_cells_excluded_from_targets_and_reserved_set():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data([_sh("Ward"), _sh("Clinic")], [f"J{i}" for i in range(4)], days=14,
                 closures=[ShiftClosure("Clinic", MON, MON + timedelta(days=13))])
    df = build_schedule(data, env="test")
    # Targets reflect the open demand only (14 Ward points / 4 residents = 3.5).
    tmap = df.attrs.get("target_total_map") or {}
    assert all(abs(tmap[p] - 3.5) < 1e-9 for p in data.juniors)
    # Every Clinic cell is in the reserved set tagged on the frame.
    reserved = reserved_cell_keys(df)
    assert all((d.isoformat(), "Clinic") in reserved for d in
               (MON + timedelta(days=i) for i in range(14)))


def test_closures_do_not_disturb_ledger():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule
    from model.ledger import update_ledger

    # Recurring closure of one shift across blocks: the open shift stays fairly
    # balanced and the cumulative ledger converges (closures are not an excusal).
    juniors = ["A", "B", "C", "D"]
    ledger: dict = {}
    ranges = []
    for b in range(3):
        start = MON + timedelta(days=14 * b)
        data = InputData(
            start_date=start, end_date=start + timedelta(days=13),
            shifts=[_sh("Ward"), _sh("Clinic")], juniors=juniors, seniors=[],
            nf_juniors=[], nf_seniors=[], leaves=[], rotators=[], min_gap=0,
            closures=[ShiftClosure("Clinic", start, start + timedelta(days=13), (5, 6))],
        )
        df = build_schedule(data, env="test", ledger=ledger or None)
        ledger = update_ledger(ledger, df, data)
        totals = [ledger[p]["total"] for p in juniors]
        ranges.append(max(totals) - min(totals))
    assert ranges[-1] <= 1.0  # converged, no divergence


def test_config_round_trip_preserves_closures():
    from model.config_io import input_data_to_json, input_data_from_json

    data = _data([_sh("Ward"), _sh("Clinic")], ["A", "B"], closures=[
        ShiftClosure("Clinic", MON, MON + timedelta(days=6), (5, 6)),
        ShiftClosure("Ward", MON, MON),
    ])
    restored = input_data_from_json(input_data_to_json(data))
    assert list(normalized_closures(restored.closures)) == list(normalized_closures(data.closures))


def test_old_configs_without_closures_load():
    from model.config_io import input_data_from_json, input_data_to_json

    data = _data([_sh("Ward")], ["A", "B"])
    restored = input_data_from_json(input_data_to_json(data))
    assert restored.closures is None
