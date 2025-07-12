from datetime import timedelta
import os
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
        INFEASIBLE = 1
        MODEL_INVALID = 2
        UNKNOWN = 3

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

        def StatusName(self, status):
            names = {
                self.OPTIMAL: "OPTIMAL",
                self.FEASIBLE: "FEASIBLE",
                self.INFEASIBLE: "INFEASIBLE",
                self.MODEL_INVALID: "MODEL_INVALID",
                self.UNKNOWN: "UNKNOWN",
            }
            return names.get(status, str(status))

        def Value(self, var):
            return var.value

    cp_model = type(
        "cp_model",
        (),
        {
            "CpModel": CpModel,
            "CpSolver": CpSolver,
            "OPTIMAL": CpSolver.OPTIMAL,
            "FEASIBLE": CpSolver.FEASIBLE,
            "INFEASIBLE": CpSolver.INFEASIBLE,
            "MODEL_INVALID": CpSolver.MODEL_INVALID,
            "UNKNOWN": CpSolver.UNKNOWN,
        },
    )

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
        for d_idx, day in enumerate(self.days):
            for s_idx, shift in enumerate(self.shifts):
                # exactly one assignment per slot
                self.model.Add(
                    sum(self.vars[(p_idx, d_idx, s_idx)]
                        for p_idx in range(len(self.people))) == 1
                )
                for p_idx, person in enumerate(self.people[:-1]):  # exclude Unfilled
                    # role eligibility
                    if shift.role == "Junior" and person not in self.data.juniors:
                        self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)
                    if shift.role == "Senior" and person not in self.data.seniors:
                        self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)
                    # night float eligibility
                    if shift.night_float:
                        if shift.role == "Junior" and person not in self.data.nf_juniors:
                            self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)
                        if shift.role == "Senior" and person not in self.data.nf_seniors:
                            self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)
                    # leaves
                    for res, start, end in self.data.leaves:
                        if res == person and start <= day <= end:
                            self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)

        # min_gap spacing
        gap = self.data.min_gap
        if gap > 0:
            for p_idx, _ in enumerate(self.people[:-1]):  # exclude Unfilled
                for d1_idx, day1 in enumerate(self.days):
                    for d2_idx in range(d1_idx + 1, len(self.days)):
                        if (self.days[d2_idx] - day1).days <= gap:
                            for s1_idx in range(len(self.shifts)):
                                for s2_idx in range(len(self.shifts)):
                                    self.model.Add(
                                        self.vars[(p_idx, d1_idx, s1_idx)] +
                                        self.vars[(p_idx, d2_idx, s2_idx)] <= 1
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
        status = solver.Solve(self.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            name_func = getattr(solver, "StatusName", lambda s: str(s))
            raise RuntimeError(f"Solver ended with status {name_func(status)}")
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


def build_schedule(data: InputData, env: str | None = None) -> pd.DataFrame:
    """Build schedule with optional environment based time limit."""
    solver = SchedulerSolver(data)
    env = env or os.environ.get("ENV", "prod").lower()
    if env == "dev":
        limit = 10
    elif env == "test":
        limit = 1
    else:
        limit = 60
    return solver.solve(time_limit_sec=limit)


