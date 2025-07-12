import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule


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


def test_schedule_with_strict_cpmodel(monkeypatch):
    """Scheduler should not fail if CpModel disallows new attributes."""

    from model import optimiser as opt

    class _Var:
        __slots__ = ("value",)

        def __init__(self):
            self.value = 0

        def __add__(self, other):
            if isinstance(other, _Var):
                return self.value + other.value
            return self.value + other

        __radd__ = __add__

    class StrictModel:
        __slots__ = ()

        def NewBoolVar(self, name):
            return _Var()

        def Add(self, constraint):
            pass

        def NewIntVar(self, a, b, name):
            return _Var()

        def Minimize(self, expr):
            pass

    class StubSolver(opt.cp_model.CpSolver):
        pass

    stub_cp_model = type(
        "cp_model",
        (),
        {"CpModel": StrictModel, "CpSolver": StubSolver, "OPTIMAL": 0, "FEASIBLE": 0},
    )

    monkeypatch.setattr(opt, "cp_model", stub_cp_model)

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
    )

    df = build_schedule(data)
    # Only unfilled is eligible due to NF restriction; also min_gap prevents A working both days
    assert set(df["S1"]) == {"Unfilled"}
