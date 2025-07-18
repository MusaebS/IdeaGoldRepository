import pytest


@pytest.fixture
def strict_cp(monkeypatch):
    """Patch optimiser.cp_model with a model that forbids new attributes."""
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
        __mul__ = __add__
        __rmul__ = __mul__
        __sub__ = __add__
        __rsub__ = __add__
        __ge__ = lambda self, other: True
        __le__ = __ge__

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

    stub_cp = type(
        "cp_model",
        (),
        {"CpModel": StrictModel, "CpSolver": StubSolver, "OPTIMAL": 0, "FEASIBLE": 0},
    )
    monkeypatch.setattr(opt, "cp_model", stub_cp)
    return stub_cp


@pytest.fixture
def balanced_cp(monkeypatch):
    """Patch optimiser.cp_model with a solver that evenly rotates residents."""
    from model import optimiser as opt

    class BalancedSolver(opt.cp_model.CpSolver):
        def Solve(self, model):
            num_res = len(model.people) - 1
            for d_idx in range(len(model.days)):
                for s_idx in range(len(model.shifts)):
                    p_idx = (d_idx + s_idx) % num_res
                    for idx in range(len(model.people)):
                        model.vars[(idx, d_idx, s_idx)].value = int(idx == p_idx)
            return self.OPTIMAL

    stub_cp = type(
        "cp_model",
        (),
        {
            "CpModel": opt.cp_model.CpModel,
            "CpSolver": BalancedSolver,
            "OPTIMAL": 0,
            "FEASIBLE": 0,
        },
    )
    monkeypatch.setattr(opt, "cp_model", stub_cp)
    return stub_cp
