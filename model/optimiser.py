from dataclasses import replace
from datetime import timedelta
import os
from typing import Dict, Tuple

ORTOOLS_AVAILABLE = True
try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

try:
    from ortools.sat.python import cp_model
except ImportError:  # pragma: no cover - simple fallback if ortools missing
    ORTOOLS_AVAILABLE = False
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

from .data_models import InputData
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
                        parts.append(coef * self.vars[(p_idx, d_idx, s_idx)])
                expr = sum(parts) if parts else 0
                var = self.model.NewIntVar(0, max_val, f"labelpts_{p_idx}_{label}")
                self.model.Add(var == expr)
                self.label_pts[(p_idx, label)] = var

            tot_expr = sum(self.label_pts[(p_idx, lbl)] for lbl in self.labels)
            tvar = self.model.NewIntVar(0, max_val, f"totalpts_{p_idx}")
            self.model.Add(tvar == tot_expr)
            self.total_pts[p_idx] = tvar

            wk_parts = []
            for d_idx, day in enumerate(self.days):
                for s_idx, sh in enumerate(self.shifts):
                    if not is_weekend(day, sh, self.data.weekend_days):
                        continue
                    coef = int(round(sh.points * scale))
                    wk_parts.append(coef * self.vars[(p_idx, d_idx, s_idx)])
            wk_expr = sum(wk_parts) if wk_parts else 0
            wvar = self.model.NewIntVar(0, max_val, f"weekendpts_{p_idx}")
            self.model.Add(wvar == wk_expr)
            self.weekend_pts[p_idx] = wvar

    def add_deviation_constraints(self) -> None:
        scale = self.SCALE
        max_val = self._max_points()

        target_total_map = self.data.target_total_map
        if self.data.target_total is not None or target_total_map:
            for p_idx, person in enumerate(self.people[:-1]):
                if target_total_map and person in target_total_map:
                    person_target = target_total_map[person]
                else:
                    person_target = self.data.target_total
                if person_target is None:
                    continue
                target = int(round(person_target * scale))
                var = self.model.NewIntVar(0, max_val, f"dev_total_{p_idx}")
                self.model.Add(var >= self.total_pts[p_idx] - target)
                self.model.Add(var >= target - self.total_pts[p_idx])
                self.dev_total[p_idx] = var
            if self.dev_total:
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
        rotator_windows: Dict[str, list] = {}
        for res, start, end in self.data.rotators:
            rotator_windows.setdefault(res, []).append((start, end))

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
                    # rotators: only available within their active window(s);
                    # this is the inverse of a leave (force 0 *outside* the span)
                    if person in rotator_windows and not any(
                        start <= day <= end for start, end in rotator_windows[person]
                    ):
                        self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)

        # At most one shift per resident per day (night-float or regular).
        for p_idx in range(len(self.people) - 1):  # exclude Unfilled
            for d_idx in range(len(self.days)):
                self.model.Add(
                    sum(
                        self.vars[(p_idx, d_idx, s_idx)]
                        for s_idx in range(len(self.shifts))
                    )
                    <= 1
                )

        # Regular-shift spacing: in any window of (gap + 1) consecutive days a
        # resident works at most one non-night-float shift. Night-float shifts
        # are excluded *here* because nights within a block are intentionally
        # consecutive; rest *around* NF blocks is enforced separately below so a
        # block is still spaced from adjacent regular shifts and other blocks.
        # O(residents x days).
        gap = self.data.min_gap
        non_nf_idxs = [i for i, sh in enumerate(self.shifts) if not sh.night_float]
        if gap > 0 and non_nf_idxs:
            for p_idx in range(len(self.people) - 1):  # exclude Unfilled
                for d_idx in range(len(self.days)):
                    window = range(d_idx, min(d_idx + gap + 1, len(self.days)))
                    self.model.Add(
                        sum(
                            self.vars[(p_idx, dd, s_idx)]
                            for dd in window
                            for s_idx in non_nf_idxs
                        )
                        <= 1
                    )

        # night float blocks must have exact length
        block_len = self.data.nf_block_length
        if block_len > 1:
            nf_shift_idxs = [i for i, s in enumerate(self.shifts) if s.night_float]
            for s_idx in nf_shift_idxs:
                for block_start in range(0, len(self.days), block_len):
                    block_days = list(range(block_start, min(block_start + block_len, len(self.days))))
                    if len(block_days) < block_len:
                        # Trailing partial block: cover it with a single resident
                        # for the remaining days instead of forcing the nights
                        # unfilled (which previously dropped coverage at the end
                        # of the horizon). The post-solve validator allows this
                        # short final run.
                        for p_idx in range(len(self.people)):
                            first_var = self.vars[(p_idx, block_days[0], s_idx)]
                            for d_idx in block_days[1:]:
                                self.model.Add(first_var == self.vars[(p_idx, d_idx, s_idx)])
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

        # Rest around night-float blocks. A resident who works the first/last
        # night of a block gets ``gap`` idle days before/after that block versus
        # *any* shift (NF or regular), matching the spec's "min_gap between any
        # two shifts" intent. Nights stay consecutive within a block (above);
        # this only spaces a block from adjacent regular shifts and other blocks.
        # Block boundaries are deterministic from the day-0-aligned partition, so
        # this also covers block_len == 1 (each NF night is its own block).
        nf_shift_idxs = [i for i, s in enumerate(self.shifts) if s.night_float]
        n_days = len(self.days)
        block_span = max(1, block_len)
        if gap > 0 and nf_shift_idxs:
            for s_idx in nf_shift_idxs:
                for bs in range(0, n_days, block_span):
                    be = min(bs + block_span, n_days) - 1  # block-end day index
                    for p_idx in range(len(self.people) - 1):  # exclude Unfilled
                        start_var = self.vars[(p_idx, bs, s_idx)]
                        end_var = self.vars[(p_idx, be, s_idx)]
                        for k in range(1, gap + 1):
                            after = be + k
                            if after < n_days:
                                for ss in range(len(self.shifts)):
                                    self.model.Add(
                                        end_var + self.vars[(p_idx, after, ss)] <= 1
                                    )
                            before = bs - k
                            if before >= 0:
                                for ss in range(len(self.shifts)):
                                    self.model.Add(
                                        start_var + self.vars[(p_idx, before, ss)] <= 1
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
        if not hasattr(solver, "OPTIMAL"):
            solver.OPTIMAL = getattr(cp_model, "OPTIMAL", 0)
        if not hasattr(solver, "FEASIBLE"):
            solver.FEASIBLE = getattr(cp_model, "FEASIBLE", solver.OPTIMAL)
        if not hasattr(solver, "Value"):
            solver.Value = lambda var: getattr(var, "value", 0)
        if time_limit_sec:
            solver.parameters.max_time_in_seconds = time_limit_sec
        # Seed the search. With the default multi-worker search a fixed seed
        # makes runs reproducible whenever the solver proves optimality; under a
        # wall-time limit the parallel workers may still differ, so this is
        # best-effort rather than a guarantee (kept multi-worker because a single
        # deterministic worker is too slow to stay feasible under tight limits).
        # Guarded so the lightweight test stubs (whose ``parameters`` is a bare
        # object) don't fail.
        try:
            solver.parameters.random_seed = int(getattr(self.data, "seed", 0))
        except (AttributeError, ValueError, TypeError):
            pass
        solved_with_response = True
        try:
            status = solver.Solve(self.model)
        except AttributeError:
            solved_with_response = False
            status = solver.OPTIMAL
            vars_dict = getattr(self.model, "vars", self.vars)
            unfilled_idx = len(self.people) - 1
            for (p_idx, _, _), var in vars_dict.items():
                setattr(var, "value", int(p_idx == unfilled_idx))
        ok_statuses = {
            getattr(cp_model, "OPTIMAL", None),
            getattr(cp_model, "FEASIBLE", None),
            getattr(solver, "OPTIMAL", None),
            getattr(solver, "FEASIBLE", None),
        }
        ok_statuses = {s for s in ok_statuses if s is not None}
        if status not in ok_statuses:
            name_func = getattr(solver, "StatusName", lambda s: str(s))
            status_name = name_func(status)
            if status_name not in {"OPTIMAL", "FEASIBLE"}:
                if status_name == "UNKNOWN":
                    # The solver hit the time limit before finding any feasible
                    # schedule; this is a budget problem, not proven infeasibility.
                    raise RuntimeError(
                        "The solver ran out of time before finding a schedule "
                        "(status: UNKNOWN). Allow more time (set ENV=prod), or "
                        "reduce the problem size / constraints, then try again."
                    )
                hints = diagnose_infeasibility(self.data)
                detail = "\n".join(f"- {h}" for h in hints)
                raise RuntimeError(
                    "No schedule satisfies the current constraints "
                    f"(solver status: {status_name}).\n{detail}"
                )
        rows = []
        for d_idx, day in enumerate(self.days):
            row = {"Date": day, "Day": day.strftime("%A")}
            for s_idx, shift in enumerate(self.shifts):
                assigned = None
                for p_idx, person in enumerate(self.people):
                    var = self.vars[(p_idx, d_idx, s_idx)]
                    val = getattr(var, "value", None)
                    if val is None and solved_with_response:
                        val = solver.Value(var)
                    if val:
                        assigned = person
                        break
                row[shift.label] = assigned
            rows.append(row)
        df = pd.DataFrame(rows)
        # Only a real solver response carries a meaningful status / wall time;
        # the stub fallback above sets variable values by hand.
        status_name = None
        wall_time = None
        if solved_with_response:
            try:
                status_name = getattr(solver, "StatusName", lambda s: str(s))(status)
                wall_time = getattr(solver, "WallTime", lambda: None)()
            except (AttributeError, TypeError, ValueError):  # pragma: no cover
                status_name = None
        try:
            df.attrs["solver_status"] = status_name
            df.attrs["wall_time_sec"] = wall_time
        except (AttributeError, TypeError):  # pragma: no cover - stub frames
            pass
        return df


def build_schedule(data: InputData, env: str | None = None) -> pd.DataFrame:
    """Build schedule with optional environment based time limit."""
    # Lazy import avoids a module-level cycle (validation imports this module).
    from .validation import validate_input

    problems = validate_input(data)
    if problems:
        detail = "\n".join(f"- {p}" for p in problems)
        raise ValueError(f"Invalid configuration:\n{detail}")

    day_count = (data.end_date - data.start_date).days + 1
    participants = data.juniors + data.seniors
    target_total = data.target_total
    target_total_map = data.target_total_map
    target_weekend = data.target_weekend
    if participants:
        total_points = day_count * sum(s.points for s in data.shifts)
        weekend_points = 0.0
        for i in range(day_count):
            day = data.start_date + timedelta(days=i)
            for s in data.shifts:
                if is_weekend(day, s, data.weekend_days):
                    weekend_points += s.points
        # Availability weights: a rotator is only present within their active
        # window(s), so they fairly carry a proportionally smaller share of the
        # workload while the other residents absorb the rest. With no rotators
        # every weight equals ``day_count`` and the targets reduce to an equal
        # split (preserving previous behaviour).
        rotator_windows: Dict[str, list] = {}
        for res, start, end in data.rotators:
            rotator_windows.setdefault(res, []).append((start, end))

        def _active_days(person: str) -> int:
            windows = rotator_windows.get(person)
            if not windows:
                return day_count
            return sum(
                1
                for i in range(day_count)
                if any(s <= data.start_date + timedelta(days=i) <= e for s, e in windows)
            )

        availability = {p: _active_days(p) for p in participants}
        weight_sum = sum(availability.values())

        if target_total is None:
            target_total = total_points / len(participants)
            if weight_sum > 0:
                target_total_map = {
                    p: total_points * availability[p] / weight_sum for p in participants
                }
        if target_weekend is None:
            if weight_sum > 0:
                target_weekend = {
                    p: weekend_points * availability[p] / weight_sum for p in participants
                }
            else:
                target_weekend = {p: 0.0 for p in participants}

    # Solve against a copy carrying the resolved targets so the caller's
    # ``InputData`` is never mutated (re-running with changed dates previously
    # reused stale auto-targets). The resolved targets are exposed on ``df.attrs``.
    solve_data = replace(
        data,
        target_total=target_total,
        target_total_map=target_total_map,
        target_weekend=target_weekend,
    )
    using_stub = not ORTOOLS_AVAILABLE
    solver = SchedulerSolver(solve_data)
    env = (env or os.environ.get("ENV", "prod")).lower()
    limit = compute_time_limit(env, len(participants) or 1, day_count, len(data.shifts) or 1)
    df = solver.solve(time_limit_sec=limit)
    df.attrs["time_limit_sec"] = limit
    df.attrs["solver_warning"] = None
    df.attrs["target_total"] = target_total
    df.attrs["target_total_map"] = target_total_map
    df.attrs["target_weekend"] = target_weekend
    if using_stub:
        df.attrs["solver_warning"] = (
            "OR-Tools not installed; using fallback output with unfilled shifts."
        )
        return df
    if not respects_min_gap(df, data.min_gap, data.shifts):
        raise RuntimeError("Schedule violates min_gap constraint")
    if not respects_nf_blocks(df, data.nf_block_length, data.shifts):
        raise RuntimeError("Schedule violates nf_block_length constraint")
    return df


def respects_min_gap(df: pd.DataFrame, gap: int, shifts=None) -> bool:
    """Return True if the schedule respects ``gap`` days of rest between shifts.

    Two rules, mirroring the solver:

    * Regular (non-night-float) shifts for a resident must be more than ``gap``
      days apart.
    * A night-float block (a maximal run of consecutive NF days for a resident)
      must have at least ``gap`` idle days before it starts and after it ends,
      versus *any* shift. Nights within a block stay consecutive.

    When ``shifts`` is omitted, every column is treated as a regular shift
    (backwards-compatible behaviour).
    """
    if gap <= 0:
        return True
    nf_labels = {s.label for s in shifts if s.night_float} if shifts else set()
    records = df.to_dict("records")
    if hasattr(df, "columns"):
        # Every column except the date/day labels holds resident names. Do not
        # filter by dtype: pandas >= 3.0 stores string columns as the "str"
        # dtype rather than "object", which previously caused these columns to
        # be skipped so that violations went undetected. The per-cell isinstance
        # check below already ignores non-name values.
        shift_cols = [c for c in df.columns if c not in {"Date", "Day"}]
    else:
        first = records[0] if records else {}
        shift_cols = [k for k in first.keys() if k not in {"Date", "Day"}]

    regular_days: Dict[str, list] = {}
    nf_days: Dict[str, set] = {}
    all_days: Dict[str, set] = {}
    for row in records:
        day = row.get("Date")
        if day is None:
            continue
        for label in shift_cols:
            person = row.get(label)
            if person in (None, "Unfilled") or not isinstance(person, str):
                continue
            all_days.setdefault(person, set()).add(day)
            if label in nf_labels:
                nf_days.setdefault(person, set()).add(day)
            else:
                regular_days.setdefault(person, []).append(day)

    # Rule 1: regular-to-regular spacing.
    for days in regular_days.values():
        days.sort()
        for d1, d2 in zip(days, days[1:]):
            if (d2 - d1).days <= gap:
                return False

    # Rule 2: rest around each night-float block.
    for person, days_set in nf_days.items():
        days = sorted(days_set)
        worked = all_days.get(person, set())
        run_start = run_prev = days[0]
        runs = []
        for d in days[1:]:
            if (d - run_prev).days == 1:
                run_prev = d
            else:
                runs.append((run_start, run_prev))
                run_start = run_prev = d
        runs.append((run_start, run_prev))
        for start, end in runs:
            for k in range(1, gap + 1):
                if (end + timedelta(days=k)) in worked:
                    return False
                if (start - timedelta(days=k)) in worked:
                    return False
    return True


def compute_time_limit(env: str, num_people: int, num_days: int, num_shifts: int) -> int:
    """Scale time limits by environment and rough problem size."""
    env = env.lower()
    base_map = {"dev": 10, "test": 1, "prod": 60}
    base = base_map.get(env, base_map["prod"])
    size = max(1, num_people) * max(1, num_days) * max(1, num_shifts)
    if size <= 500:
        return max(1, int(round(base * 0.5)))
    if size >= 4000:
        return base
    scale = 0.5 + 0.5 * (size / 4000)
    return max(1, min(base, int(round(base * scale))))


def diagnose_infeasibility(data: InputData) -> list:
    """Return human-readable, actionable hints for why no feasible schedule
    exists.

    Uses cheap configuration checks (no solving) and focuses on the constraints
    that can actually make the model infeasible: night-float eligibility and the
    min_gap / NF-block conflict. Coverage of ordinary shifts is always absorbed
    by the implicit ``Unfilled`` resident, so it is never a hard failure.
    """
    hints = []
    nf_shifts = [s for s in data.shifts if s.night_float]
    for s in nf_shifts:
        pool = data.nf_juniors if s.role == "Junior" else data.nf_seniors
        if not pool:
            hints.append(
                f"Night-float shift '{s.label}' ({s.role}) has no eligible "
                f"residents; add NF-eligible {s.role.lower()}s or turn off Night "
                f"Float for it."
            )
    if not hints:
        hints.append(
            "Constraints are jointly unsatisfiable. Try shortening NF Block "
            "Length, reducing overlapping leaves/rotators, or adding more "
            "eligible residents."
        )
    return hints
