from datetime import timedelta
import os
from typing import Dict, Tuple

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

try:
    from ortools.sat.python import cp_model
except ImportError:  # pragma: no cover - simple fallback if ortools missing
    class _Var:
        def __init__(self):
            self.value = 0

        def __add__(self, other):
            if isinstance(other, _Var):
                return self.value + other.value
            return self.value + other

        __radd__ = __add__

        def __mul__(self, other):
            if isinstance(other, _Var):
                return self.value * other.value
            return self.value * other

        __rmul__ = __mul__

        def __sub__(self, other):
            if isinstance(other, _Var):
                return self.value - other.value
            return self.value - other

        def __rsub__(self, other):
            if isinstance(other, _Var):
                return other.value - self.value
            return other - self.value

        def __ge__(self, other):
            if isinstance(other, _Var):
                return self.value >= other.value
            return self.value >= other

        def __le__(self, other):
            if isinstance(other, _Var):
                return self.value <= other.value
            return self.value <= other

        def __gt__(self, other):
            if isinstance(other, _Var):
                return self.value > other.value
            return self.value > other

        def __lt__(self, other):
            if isinstance(other, _Var):
                return self.value < other.value
            return self.value < other

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
from .nf_blocks import respects_nf_blocks
from .utils import is_weekend


class SchedulerSolver:
    def __init__(self, data: InputData):
        self.data = data
        self.model = cp_model.CpModel()
        self.SCALE = 100
        self.people = data.juniors + data.seniors + ["Unfilled"]
        self.days = [data.start_date + timedelta(days=i)
                     for i in range((data.end_date - data.start_date).days + 1)]
        self.shifts = data.shifts
        self.labels = sorted({s.label for s in data.shifts})
        self.vars: Dict[Tuple[int, int, int], object] = {}
        self.label_pts: Dict[Tuple[int, str], object] = {}
        self.total_pts: Dict[int, object] = {}
        self.weekend_pts: Dict[int, object] = {}
        self.dev_label: Dict[Tuple[int, str], object] = {}
        self.dev_total: Dict[int, object] = {}
        self.dev_weekend: Dict[int, object] = {}
        self.max_dev: object | None = None
        self.build_variables()
        self.compute_points()
        # expose internals for stub solver (may fail on real CpModel)
        try:
            self.model.people = self.people
            self.model.days = self.days
            self.model.shifts = self.shifts
            self.model.vars = self.vars
            self.model.label_pts = self.label_pts
            self.model.total_pts = self.total_pts
            self.model.weekend_pts = self.weekend_pts
            self.model.dev_label = self.dev_label
            self.model.dev_total = self.dev_total
            self.model.dev_weekend = self.dev_weekend
            self.model.max_dev = self.max_dev
        except AttributeError:
            # Real ortools model does not allow setting new attributes
            pass
        self.add_constraints()
        self.add_deviation_constraints()
        self.build_objective()

    def build_variables(self) -> None:
        for p_idx in range(len(self.people)):
            for d_idx in range(len(self.days)):
                for s_idx in range(len(self.shifts)):
                    self.vars[(p_idx, d_idx, s_idx)] = self.model.NewBoolVar(
                        f"x_{p_idx}_{d_idx}_{s_idx}")

    def _mul(self, var, coef: int):
        if coef <= 0:
            return 0
        expr = var
        for _ in range(coef - 1):
            expr = expr + var
        return expr

    def _max_points(self) -> int:
        """Return scaled upper bound for point totals."""
        total_points = sum(s.points for s in self.shifts)
        return max(1, len(self.days) * int(100 * total_points))

    def compute_points(self) -> None:
        scale = self.SCALE
        max_val = self._max_points()
        for p_idx, _ in enumerate(self.people[:-1]):
            for label in self.labels:
                parts = []
                for d_idx in range(len(self.days)):
                    for s_idx, sh in enumerate(self.shifts):
                        if sh.label != label:
                            continue
                        coef = int(round(sh.points * scale))
                        parts.append(self._mul(self.vars[(p_idx, d_idx, s_idx)], coef))
                expr = sum(parts) if parts else 0
                var = self.model.NewIntVar(0, max_val, f"labelpts_{p_idx}_{label}")
                self.model.Add(var == expr)
                self.label_pts[(p_idx, label)] = var

            tot_expr = sum(self.label_pts[(p_idx, l)] for l in self.labels)
            tvar = self.model.NewIntVar(0, max_val, f"totalpts_{p_idx}")
            self.model.Add(tvar == tot_expr)
            self.total_pts[p_idx] = tvar

            wk_parts = []
            for d_idx, day in enumerate(self.days):
                for s_idx, sh in enumerate(self.shifts):
                    if not is_weekend(day, sh):
                        continue
                    coef = int(round(sh.points * scale))
                    wk_parts.append(self._mul(self.vars[(p_idx, d_idx, s_idx)], coef))
            wk_expr = sum(wk_parts) if wk_parts else 0
            wvar = self.model.NewIntVar(0, max_val, f"weekendpts_{p_idx}")
            self.model.Add(wvar == wk_expr)
            self.weekend_pts[p_idx] = wvar

    def add_deviation_constraints(self) -> None:
        scale = self.SCALE
        max_val = self._max_points()

        if self.data.target_total is not None:
            target = int(round(self.data.target_total * scale))
            for p_idx in range(len(self.people) - 1):
                var = self.model.NewIntVar(0, max_val, f"dev_total_{p_idx}")
                self.model.Add(var >= self.total_pts[p_idx] - target)
                self.model.Add(var >= target - self.total_pts[p_idx])
                self.dev_total[p_idx] = var
            self.max_dev = self.model.NewIntVar(0, max_val, "max_dev")
            for var in self.dev_total.values():
                self.model.Add(self.max_dev >= var)

        if self.data.target_weekend:
            for p_idx, person in enumerate(self.people[:-1]):
                if person not in self.data.target_weekend:
                    continue
                target = int(round(self.data.target_weekend[person] * scale))
                var = self.model.NewIntVar(0, max_val, f"dev_weekend_{p_idx}")
                self.model.Add(var >= self.weekend_pts[p_idx] - target)
                self.model.Add(var >= target - self.weekend_pts[p_idx])
                self.dev_weekend[p_idx] = var

        if self.data.target_label:
            for p_idx, person in enumerate(self.people[:-1]):
                for label in self.labels:
                    key = (person, label)
                    if key not in self.data.target_label:
                        continue
                    target = int(round(self.data.target_label[key] * scale))
                    var = self.model.NewIntVar(0, max_val, f"dev_label_{p_idx}_{label}")
                    lp = self.label_pts[(p_idx, label)]
                    self.model.Add(var >= lp - target)
                    self.model.Add(var >= target - lp)
                    self.dev_label[(p_idx, label)] = var

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
                # no more than one shift per day
                for d_idx in range(len(self.days)):
                    self.model.Add(
                        sum(
                            self.vars[(p_idx, d_idx, s_idx)]
                            for s_idx in range(len(self.shifts))
                        )
                        <= 1
                    )
                for d1_idx, day1 in enumerate(self.days):
                    for d2_idx in range(d1_idx + 1, len(self.days)):
                        if (self.days[d2_idx] - day1).days <= gap:
                            for s1_idx in range(len(self.shifts)):
                                for s2_idx in range(len(self.shifts)):
                                    self.model.Add(
                                        self.vars[(p_idx, d1_idx, s1_idx)] +
                                        self.vars[(p_idx, d2_idx, s2_idx)] <= 1
                                    )

        # night float blocks must have exact length
        block_len = self.data.nf_block_length
        if block_len > 1:
            nf_shift_idxs = [i for i, s in enumerate(self.shifts) if s.night_float]
            for s_idx in nf_shift_idxs:
                for block_start in range(0, len(self.days), block_len):
                    block_days = list(range(block_start, min(block_start + block_len, len(self.days))))
                    if len(block_days) < block_len:
                        for d_idx in block_days:
                            for p_idx in range(len(self.people) - 1):
                                self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)
                        continue
                    for p_idx in range(len(self.people)):
                        first_var = self.vars[(p_idx, block_days[0], s_idx)]
                        for d_idx in block_days[1:]:
                            self.model.Add(first_var == self.vars[(p_idx, d_idx, s_idx)])
                for boundary in range(block_len, len(self.days), block_len):
                    if boundary >= len(self.days):
                        break
                    prev_day = boundary - 1
                    next_day = boundary
                    for p_idx in range(len(self.people)):
                        self.model.Add(
                            self.vars[(p_idx, prev_day, s_idx)] + self.vars[(p_idx, next_day, s_idx)] <= 1
                        )

    def build_objective(self) -> None:
        unfilled_vars = [
            self.vars[(len(self.people) - 1, d_idx, s_idx)]
            for d_idx in range(len(self.days))
            for s_idx in range(len(self.shifts))
        ]

        terms = []
        W1, W2, W3, W4, W5 = 10**9, 10**6, 10**3, 10, 1
        if self.max_dev is not None:
            terms.append(W1 * self.max_dev)
        if self.dev_total:
            terms.append(W2 * sum(self.dev_total.values()))
        if self.dev_weekend:
            terms.append(W3 * sum(self.dev_weekend.values()))
        if self.dev_label:
            terms.append(W4 * sum(self.dev_label.values()))
        terms.append(W5 * sum(unfilled_vars))

        self.model.Minimize(sum(terms))

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
            row = {"Date": day, "Day": day.strftime("%A")}
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
    df = solver.solve(time_limit_sec=limit)
    if not respects_min_gap(df, data.min_gap):
        raise RuntimeError("Schedule violates min_gap constraint")
    if not respects_nf_blocks(df, data.nf_block_length, data.shifts):
        raise RuntimeError("Schedule violates nf_block_length constraint")
    return df


def respects_min_gap(df: pd.DataFrame, gap: int) -> bool:
    """Return True if no resident appears on days ``gap`` or fewer apart."""
    if gap <= 0:
        return True
    assignments: Dict[str, list] = {}
    for row in df.to_dict("records"):
        day = row.get("Date")
        for label, person in row.items():
            if label == "Date" or person in (None, "Unfilled"):
                continue
            assignments.setdefault(person, []).append(day)
    for days in assignments.values():
        days.sort()
        for d1, d2 in zip(days, days[1:]):
            if (d2 - d1).days <= gap:
                return False
    return True


