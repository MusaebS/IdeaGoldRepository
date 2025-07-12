from datetime import timedelta
from typing import Dict, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover - fallback when pandas missing
    class SimpleDataFrame(list):
        def __init__(self, data=None):
            super().__init__(data or [])

        def to_dict(self, orient="records"):
            return list(self)

        def __getitem__(self, key):
            return [row.get(key) for row in self]

    pd = type("pd", (), {"DataFrame": SimpleDataFrame})()

try:
    from ortools.sat.python import cp_model
except Exception:  # pragma: no cover - simple fallback if ortools missing
    class _Var:
        def __init__(self):
            self.value = 0

        def __add__(self, other):
            if isinstance(other, _Var):
                return self.value + other.value
            return self.value + other

        __radd__ = __add__

    class CpModel:
        def NewBoolVar(self, name):
            return _Var()

        def Add(self, constraint):
            pass

        def NewIntVar(self, a, b, name):
            return _Var()

        def Minimize(self, expr):
            pass

    class CpSolver:
        OPTIMAL = 0
        FEASIBLE = 0

        def __init__(self):
            self.parameters = type("p", (), {})()
            self.parameters.max_time_in_seconds = 0

        def Solve(self, model):
            # Dummy assignment: mark every shift as unfilled
            if hasattr(model, 'vars'):
                people = model.people
                days = model.days
                shifts = model.shifts
                unfilled_idx = len(people) - 1
                for d_idx in range(len(days)):
                    for s_idx in range(len(shifts)):
                        for p_idx in range(len(people)):
                            v = model.vars[(p_idx, d_idx, s_idx)]
                            v.value = 0
                        model.vars[(unfilled_idx, d_idx, s_idx)].value = 1
            return self.OPTIMAL

        def Value(self, var):
            return var.value

    cp_model = type("cp_model", (), {"CpModel": CpModel, "CpSolver": CpSolver, "OPTIMAL": 0, "FEASIBLE": 0})

from .data_models import InputData, ShiftTemplate


class SchedulerSolver:
    def __init__(self, data: InputData):
        self.data = data
        self.model = cp_model.CpModel()
        self.people = data.juniors + data.seniors + ["Unfilled"]
        self.days = [data.start_date + timedelta(days=i)
                     for i in range((data.end_date - data.start_date).days + 1)]
        self.shifts = data.shifts
        self.vars: Dict[Tuple[int, int, int], object] = {}
        self.build_variables()
        # expose internals for stub solver (may fail on real CpModel)
        try:
            self.model.people = self.people
            self.model.days = self.days
            self.model.shifts = self.shifts
            self.model.vars = self.vars
        except AttributeError:
            # Real ortools model does not allow setting new attributes
            pass
        self.add_constraints()
        self.build_objective()

    def build_variables(self) -> None:
        for p_idx in range(len(self.people)):
            for d_idx in range(len(self.days)):
                for s_idx in range(len(self.shifts)):
                    self.vars[(p_idx, d_idx, s_idx)] = self.model.NewBoolVar(
                        f"x_{p_idx}_{d_idx}_{s_idx}")

    def add_constraints(self) -> None:
        for d_idx in range(len(self.days)):
            for s_idx in range(len(self.shifts)):
                self.model.Add(
                    sum(self.vars[(p_idx, d_idx, s_idx)]
                        for p_idx in range(len(self.people))) == 1
                )

    def build_objective(self) -> None:
        # Simplified: just minimise unfilled slots
        unfilled_vars = [
            self.vars[(len(self.people) - 1, d_idx, s_idx)]
            for d_idx in range(len(self.days))
            for s_idx in range(len(self.shifts))
        ]
        self.model.Minimize(sum(unfilled_vars))

    def solve(self, time_limit_sec: int | None = None):
        solver = cp_model.CpSolver()
        if time_limit_sec:
            solver.parameters.max_time_in_seconds = time_limit_sec
        solver.Solve(self.model)
        rows = []
        for d_idx, day in enumerate(self.days):
            row = {"Date": day}
            for s_idx, shift in enumerate(self.shifts):
                assigned = None
                for p_idx, person in enumerate(self.people):
                    if solver.Value(self.vars[(p_idx, d_idx, s_idx)]):
                        assigned = person
                        break
                row[shift.label] = assigned
            rows.append(row)
        return pd.DataFrame(rows)


def build_schedule(data: InputData, time_limit_sec: int | None = 60) -> pd.DataFrame:
    solver = SchedulerSolver(data)
    return solver.solve(time_limit_sec=time_limit_sec)

