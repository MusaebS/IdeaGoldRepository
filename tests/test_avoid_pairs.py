"""Avoid pairs: same-day separation, validation, fairness neutrality."""
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
from model.optimiser import resolve_targets
from model.validation import config_warnings, validate_input, validate_schedule


def _data(**over):
    base = dict(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 7),
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="E", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ],
        juniors=["A", "B", "C"],
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


def test_solver_never_puts_pair_on_same_day():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(avoid_pairs=[("A", "B")])
    df = build_schedule(data, env="test")
    for row in df.to_dict("records"):
        workers = {row["D"], row["E"]}
        assert not {"A", "B"} <= workers, f"pair together on {row['Date']}"


def test_pair_of_sole_role_members_degrades_to_unfilled_not_infeasible():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule

    data = _data(juniors=["A", "B"], avoid_pairs=[("A", "B")])
    df = build_schedule(data, env="test")  # must not raise
    for row in df.to_dict("records"):
        workers = {row["D"], row["E"]}
        assert not {"A", "B"} <= workers
        assert "Unfilled" in workers  # one slot per day cannot be covered
    assert any("expect unfilled" in w for w in config_warnings(data))


def test_avoid_pairs_leave_fairness_targets_untouched():
    plain = resolve_targets(_data())
    paired = resolve_targets(_data(avoid_pairs=[("A", "B")]))
    assert paired.target_total_map == plain.target_total_map
    assert paired.target_weekend == plain.target_weekend


def test_validate_input_avoid_pair_rules():
    issues = validate_input(_data(avoid_pairs=[("A", "Zed"), ("B", "B")]))
    assert any("unknown resident 'Zed'" in i for i in issues)
    assert any("'B' with themselves" in i for i in issues)
    assert validate_input(_data(avoid_pairs=[("A", "B")])) == []


def test_validate_schedule_flags_pair_on_same_day():
    data = _data(avoid_pairs=[("A", "B")])
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "D": "A", "E": "B"},
        {"Date": date(2023, 1, 3), "D": "A", "E": "C"},
    ])
    issues = validate_schedule(df, data)
    assert any("both on call (avoid pair)" in i and str(date(2023, 1, 2)) in i
               for i in issues)
    assert not any(str(date(2023, 1, 3)) in i and "avoid pair" in i for i in issues)


def test_avoid_pair_annotated_in_log():
    from model.fairness import load_annotation_notes

    data = _data(avoid_pairs=[("A", "B")])
    assert load_annotation_notes("A", data) == ["[avoids: B]"]
    assert load_annotation_notes("B", data) == ["[avoids: A]"]
    assert load_annotation_notes("C", data) == []


def test_avoid_pair_both_covering_night_float_is_feasible():
    # Two avoid-pair residents each covering a DIFFERENT night-float shift on
    # the same dates is valid (each NF cell has exactly one coverer). Their
    # co-presence on NF duty is fixed by the overlay and can't be scheduled
    # away, so the avoid-pair constraint must not make the whole model
    # infeasible — it clamps at zero regular shifts instead of a negative RHS.
    pytest.importorskip("ortools")
    from datetime import timedelta

    from model.data_models import NightFloatAssignment, NightFloatCoverage
    from model.optimiser import build_schedule

    start = date(2023, 1, 2)
    data = _data(
        start_date=start,
        end_date=start + timedelta(days=13),
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="NF1", role="Junior", night_float=True, thu_weekend=False, points=2.0),
            ShiftTemplate(label="NF2", role="Junior", night_float=True, thu_weekend=False, points=2.0),
        ],
        juniors=["A", "B", "C", "D", "E"],
        nf_juniors=["A", "B"],
        avoid_pairs=[("A", "B")],
        nf_coverage={
            "NF1": NightFloatCoverage("NF1", weekdays=(0, 1, 2, 3, 4, 5, 6)),
            "NF2": NightFloatCoverage("NF2", weekdays=(0, 1, 2, 3, 4, 5, 6)),
        },
        nf_assignments=[
            NightFloatAssignment("A", start, start + timedelta(days=13), ("NF1",), 1),
            NightFloatAssignment("B", start, start + timedelta(days=13), ("NF2",), 1),
        ],
    )
    df = build_schedule(data, env="test")  # must not raise INFEASIBLE
    assert df.attrs.get("solver_status") in {"OPTIMAL", "FEASIBLE"}
    # A and B still never share a regular shift on any day.
    for row in df.to_dict("records"):
        regular = [row.get(s.label) for s in data.shifts if not s.night_float]
        assert not ({"A", "B"} <= set(regular))
