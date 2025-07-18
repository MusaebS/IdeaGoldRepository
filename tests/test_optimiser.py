import sys, os
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    import pandas as pd
except Exception:
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule, respects_min_gap
from model.nf_blocks import respects_nf_blocks


def test_simple_schedule():
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="Shift1", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    df = build_schedule(data)
    assert len(df) == 2
    assert set(df["Shift1"]) <= {"A", "B", "Unfilled"}
    assert list(df["Day"]) == ["Sunday", "Monday"]


def test_schedule_with_strict_cpmodel(strict_cp):
    """Scheduler should not fail if CpModel disallows new attributes."""

    from model import optimiser as opt

    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 1),
        shifts=[ShiftTemplate(label="S1", role="Junior", night_float=False, thu_weekend=False)],
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )

    df = opt.build_schedule(data)
    assert len(df) == 1


def test_role_and_gap_constraints():
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="S1", role="Senior", night_float=True, thu_weekend=False)],
        juniors=["A"],
        seniors=["B"],
        nf_juniors=["A"],
        nf_seniors=[],  # B cannot cover night float
        leaves=[],
        rotators=[],
        min_gap=2,
        nf_block_length=1,
    )

    df = build_schedule(data)
    # Only unfilled is eligible due to NF restriction; also min_gap prevents A working both days
    assert set(df["S1"]) == {"Unfilled"}


def test_respects_min_gap_function():
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S1": "A"},
        {"Date": date(2023, 1, 2), "S1": "A"},
    ])
    assert not respects_min_gap(df, 2)

    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S1": "A"},
        {"Date": date(2023, 1, 3), "S1": "A"},
    ])
    assert not respects_min_gap(df, 2)

    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S1": "A"},
        {"Date": date(2023, 1, 4), "S1": "A"},
    ])
    assert respects_min_gap(df, 2)


def test_respects_nf_blocks_function():
    shifts = [ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)]
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},
        {"Date": date(2023, 1, 3), "NF": "A"},
    ])
    assert respects_nf_blocks(df, 3, shifts)

    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},
        {"Date": date(2023, 1, 3), "NF": "B"},
    ])
    assert not respects_nf_blocks(df, 3, shifts)


def test_nf_blocks_exact_assignment():
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),
        shifts=[ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)],
        juniors=["A"],
        seniors=[],
        nf_juniors=["A"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=2,
    )

    df = build_schedule(data, env="test")
    rows = df.to_dict("records")
    assert rows[2]["NF"] == "Unfilled"
    assert rows[0]["NF"] == rows[1]["NF"]
    assert respects_nf_blocks(df, 2, data.shifts)


def _points_by_resident(df: pd.DataFrame, shifts: list[ShiftTemplate]) -> dict:
    pts: dict[str, float] = {}
    for row in df.to_dict("records"):
        for s in shifts:
            p = row.get(s.label)
            if p and p != "Unfilled":
                pts[p] = pts.get(p, 0.0) + s.points
    return pts


def test_total_points_balanced(balanced_cp):
    from model import optimiser as opt

    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=1.0,
    )

    df = opt.build_schedule(data, env="test")
    pts = _points_by_resident(df, shifts)
    assert abs(pts.get("A", 0) - pts.get("B", 0)) <= 1


def test_total_points_min_deviation(balanced_cp):
    from model import optimiser as opt

    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=1.5,
    )

    df = opt.build_schedule(data, env="test")
    pts = _points_by_resident(df, shifts)
    diff = abs(pts.get("A", 0) - pts.get("B", 0))
    assert diff == 1


def test_intvar_upper_bound_multiple_shifts(monkeypatch):
    from model import optimiser as opt

    bounds = []

    class RecordingModel(opt.cp_model.CpModel):
        def NewIntVar(self, a, b, name):
            bounds.append(b)
            return super().NewIntVar(a, b, name)

    stub_cp = type(
        "cp_model",
        (),
        {
            "CpModel": RecordingModel,
            "CpSolver": opt.cp_model.CpSolver,
            "OPTIMAL": 0,
            "FEASIBLE": 0,
        },
    )
    monkeypatch.setattr(opt, "cp_model", stub_cp)

    shifts = [
        ShiftTemplate(label="S1", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="S2", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 1),
        shifts=shifts,
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=3.0,
    )

    opt.build_schedule(data, env="test")
    days = (data.end_date - data.start_date).days + 1
    expected = days * int(100 * sum(s.points for s in shifts))
    assert expected in bounds


def test_total_points_balanced_multiple_shifts(balanced_cp):
    from model import optimiser as opt

    shifts = [
        ShiftTemplate(label="D1", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="D2", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )

    df = opt.build_schedule(data, env="test")
    pts = _points_by_resident(df, shifts)
    assert abs(pts.get("A", 0) - pts.get("B", 0)) <= 1


def test_fairness_log_includes_unused_resident():
    from model.fairness import format_fairness_log

    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S": "A"},
        {"Date": date(2023, 1, 2), "S": "A"},
    ])
    log = format_fairness_log(df, data)
    lines = log.splitlines()
    assert any(line.startswith("A (Junior, NF 0.0): total 2.0") for line in lines)
    assert any(line.startswith("B (Junior, NF 0.0): total 0.0") for line in lines)
