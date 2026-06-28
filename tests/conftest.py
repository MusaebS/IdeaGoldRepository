import pytest


class _PermVar:
    """Permissive CP-SAT variable stand-in.

    Supports the arithmetic used while building the model and exposes a writable
    ``value`` that the fake solvers below assign. Real OR-Tools ``IntVar``
    objects do not allow setting ``.value``, so fixtures that simulate a solver
    must use this stub regardless of whether OR-Tools is installed.
    """

    def __init__(self):
        self.value = 0

    def __add__(self, other):
        return self.value + (other.value if isinstance(other, _PermVar) else other)

    __radd__ = __add__

    def __mul__(self, other):
        return self.value * (other.value if isinstance(other, _PermVar) else other)

    __rmul__ = __mul__

    def __sub__(self, other):
        return self.value - (other.value if isinstance(other, _PermVar) else other)

    def __rsub__(self, other):
        return (other.value if isinstance(other, _PermVar) else other) - self.value

    def __ge__(self, other):
        return self.value >= (other.value if isinstance(other, _PermVar) else other)

    def __le__(self, other):
        return self.value <= (other.value if isinstance(other, _PermVar) else other)

    def __gt__(self, other):
        return self.value > (other.value if isinstance(other, _PermVar) else other)

    def __lt__(self, other):
        return self.value < (other.value if isinstance(other, _PermVar) else other)


class _PermModel:
    """Permissive stub model: allows attribute assignment (so the solver's
    introspection hooks succeed) and hands out writable variables."""

    def NewBoolVar(self, name):
        return _PermVar()

    def NewIntVar(self, a, b, name):
        return _PermVar()

    def Add(self, constraint):
        pass

    def Minimize(self, expr):
        pass


def _stub_cp(model_cls, solver_cls):
    return type(
        "cp_model",
        (),
        {"CpModel": model_cls, "CpSolver": solver_cls, "OPTIMAL": 0, "FEASIBLE": 0},
    )


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
    """Patch optimiser.cp_model with a solver that evenly rotates residents.

    Self-contained (uses :class:`_PermModel`) and forces the OR-Tools-absent
    code path, so it behaves identically whether or not OR-Tools is installed.
    """
    from model import optimiser as opt

    class BalancedSolver:
        OPTIMAL = 0
        FEASIBLE = 0

        def __init__(self):
            self.parameters = type("p", (), {})()
            self.parameters.max_time_in_seconds = 0

        def Solve(self, model):
            num_res = len(model.people) - 1
            for d_idx in range(len(model.days)):
                for s_idx in range(len(model.shifts)):
                    p_idx = (d_idx + s_idx) % num_res
                    for idx in range(len(model.people)):
                        model.vars[(idx, d_idx, s_idx)].value = int(idx == p_idx)
            return self.OPTIMAL

        def StatusName(self, status):
            return "OPTIMAL"

        def Value(self, var):
            return getattr(var, "value", 0)

    monkeypatch.setattr(opt, "ORTOOLS_AVAILABLE", False)
    stub_cp = _stub_cp(_PermModel, BalancedSolver)
    monkeypatch.setattr(opt, "cp_model", stub_cp)
    return stub_cp


@pytest.fixture
def recording_cp(monkeypatch):
    """Patch optimiser.cp_model with a permissive stub that records the upper
    bounds passed to ``NewIntVar``. Returns the list of recorded bounds."""
    from model import optimiser as opt

    bounds = []

    class RecordingModel(_PermModel):
        def NewIntVar(self, a, b, name):
            bounds.append(b)
            return super().NewIntVar(a, b, name)

    class _Solver:
        OPTIMAL = 0
        FEASIBLE = 0

        def __init__(self):
            self.parameters = type("p", (), {})()
            self.parameters.max_time_in_seconds = 0

        def Solve(self, model):
            return self.OPTIMAL

        def StatusName(self, status):
            return "OPTIMAL"

        def Value(self, var):
            return getattr(var, "value", 0)

    monkeypatch.setattr(opt, "ORTOOLS_AVAILABLE", False)
    stub_cp = _stub_cp(RecordingModel, _Solver)
    monkeypatch.setattr(opt, "cp_model", stub_cp)
    return bounds
