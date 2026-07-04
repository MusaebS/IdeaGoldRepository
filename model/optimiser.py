from dataclasses import replace
from datetime import timedelta
import os
from typing import Any, Dict, List, Mapping, Sequence, Tuple

# CP-SAT variable handles are opaque (real ortools IntVar or the _Var stub
# below), so they are typed as Any throughout.
CpVar = Any

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

from .data_models import (
    InputData,
    blackout_night_before_dates,
    blackout_person_windows,
    is_night_call,
    normalized_leaves,
    normalized_rotators,
)
from .nf_blocks import respects_nf_blocks
from .points import POINT_SCALE, SlotPoints, block_days, classify_slot, scaled, slot_points
from .reductions import eligible_for_shift, reduction_caps
from .utils import weekend_holiday_dates
from .weights import availability_weights


def objective_weights(
    n_days: int, n_shifts: int, has_prefs: bool, max_slot_points_scaled: int = 1
) -> Tuple[int, int, int, int, int, int]:
    """The objective weights: coverage on top, then fairness, then preferences.

    **Coverage dominates fairness.** Leaving a *fillable* slot empty must never
    reduce the objective — an uncovered on-call is never an acceptable price
    for tidier point totals. One unfilled slot removes at most its own points
    from one resident, so it can improve the whole fairness objective by at
    most ``max_slot_points × (sum of the deviation weights)``; setting
    ``W_UNFILLED`` just above that makes filling any coverable slot always
    worth it. When a slot genuinely cannot be filled (caps, blackouts,
    eligibility — all hard constraints) ``Unfilled`` still absorbs it, so this
    never causes infeasibility.

    **Fairness ladder** (below coverage): overall spread (``max_dev``), then
    total, weekend, night-float, and per-label deviations.

    **Preferences** are the lowest tier (weight 1). Every fairness weight is
    multiplied by ``K = 2·D·S + 1`` so the preference reward (in
    ``[-2·D·S, 0]``) can never buy a single scaled point of any deviation;
    they only order exact fairness ties. ``K = 1`` when no preferences are set.

    Returns ``(W_MAXDEV, W_TOTAL, W_WEEKEND, W_NIGHTS, W_LABEL, W_UNFILLED)``.
    """
    scale = 2 * n_days * n_shifts + 1 if has_prefs else 1
    w_maxdev, w_total, w_weekend, w_nights, w_label = (
        10**9 * scale, 10**6 * scale, 10**3 * scale, 10**2 * scale, 10 * scale,
    )
    fair_sum = w_maxdev + w_total + w_weekend + w_nights + w_label
    w_unfilled = max(1, max_slot_points_scaled) * fair_sum + 1
    return (w_maxdev, w_total, w_weekend, w_nights, w_label, w_unfilled)


class SchedulerSolver:
    def __init__(self, data: InputData):
        self.data = data
        self.model = cp_model.CpModel()
        self.SCALE = POINT_SCALE
        self.people = data.juniors + data.seniors + ["Unfilled"]
        self.days = block_days(data)
        self.shifts = data.shifts
        self.labels = sorted({s.label for s in data.shifts})
        # Every (day, shift) slot classified once — value, weekend, night-float —
        # so the solver and fairness reporting agree by construction.
        weekend_dates = weekend_holiday_dates(data)
        self.slots: Dict[Tuple[int, int], SlotPoints] = {
            (d_idx, s_idx): classify_slot(day, sh, data, weekend_dates)
            for d_idx, day in enumerate(self.days)
            for s_idx, sh in enumerate(self.shifts)
        }
        self.vars: Dict[Tuple[int, int, int], CpVar] = {}
        self.label_pts: Dict[Tuple[int, str], CpVar] = {}
        self.total_pts: Dict[int, CpVar] = {}
        self.weekend_pts: Dict[int, CpVar] = {}
        self.nf_pts: Dict[int, CpVar] = {}
        self.dev_label: Dict[Tuple[int, str], CpVar] = {}
        self.dev_total: Dict[int, CpVar] = {}
        self.dev_weekend: Dict[int, CpVar] = {}
        self.dev_night_float: Dict[int, CpVar] = {}
        self.max_dev: CpVar | None = None
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
            self.model.nf_pts = self.nf_pts
            self.model.dev_label = self.dev_label
            self.model.dev_total = self.dev_total
            self.model.dev_weekend = self.dev_weekend
            self.model.dev_night_float = self.dev_night_float
            self.model.max_dev = self.max_dev
        except AttributeError:
            # Real ortools model does not allow setting new attributes
            pass
        self.add_constraints()
        self.add_deviation_constraints()
        self.add_cap_constraints()
        self.add_extra_point_constraints()
        self.add_reduction_constraints()
        self.build_objective()

    def build_variables(self) -> None:
        for p_idx in range(len(self.people)):
            for d_idx in range(len(self.days)):
                for s_idx in range(len(self.shifts)):
                    self.vars[(p_idx, d_idx, s_idx)] = self.model.NewBoolVar(
                        f"x_{p_idx}_{d_idx}_{s_idx}")

    def _max_points(self) -> int:
        """Return scaled upper bound for point totals (uses effective points so
        weekday overrides / holiday bonuses never overflow the variable bounds)."""
        return max(1, scaled(sum(slot.points for slot in self.slots.values())))

    def compute_points(self) -> None:
        max_val = self._max_points()
        for p_idx, _ in enumerate(self.people[:-1]):
            # One pass over the classified slots accumulates the per-label,
            # weekend and night-float expressions together.
            label_parts: Dict[str, list] = {label: [] for label in self.labels}
            wk_parts: List[CpVar] = []
            nf_parts: List[CpVar] = []
            for (d_idx, s_idx), slot in self.slots.items():
                term = scaled(slot.points) * self.vars[(p_idx, d_idx, s_idx)]
                label_parts[slot.shift.label].append(term)
                if slot.weekend:
                    wk_parts.append(term)
                if slot.night_float:
                    nf_parts.append(term)

            for label in self.labels:
                parts = label_parts[label]
                var = self.model.NewIntVar(0, max_val, f"labelpts_{p_idx}_{label}")
                self.model.Add(var == (sum(parts) if parts else 0))
                self.label_pts[(p_idx, label)] = var

            tot_expr = sum(self.label_pts[(p_idx, lbl)] for lbl in self.labels)
            tvar = self.model.NewIntVar(0, max_val, f"totalpts_{p_idx}")
            self.model.Add(tvar == tot_expr)
            self.total_pts[p_idx] = tvar

            wvar = self.model.NewIntVar(0, max_val, f"weekendpts_{p_idx}")
            self.model.Add(wvar == (sum(wk_parts) if wk_parts else 0))
            self.weekend_pts[p_idx] = wvar

            nfvar = self.model.NewIntVar(0, max_val, f"nfpts_{p_idx}")
            self.model.Add(nfvar == (sum(nf_parts) if nf_parts else 0))
            self.nf_pts[p_idx] = nfvar

    def add_deviation_constraints(self) -> None:
        max_val = self._max_points()

        target_total_map = self.data.target_total_map
        if self.data.target_total is not None or target_total_map:
            for p_idx, person in enumerate(self.people[:-1]):
                person_target: float | None
                if target_total_map and person in target_total_map:
                    person_target = target_total_map[person]
                else:
                    person_target = self.data.target_total
                if person_target is None:
                    continue
                target = scaled(person_target)
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
                target = scaled(self.data.target_weekend[person])
                var = self.model.NewIntVar(0, max_val, f"dev_weekend_{p_idx}")
                self.model.Add(var >= self.weekend_pts[p_idx] - target)
                self.model.Add(var >= target - self.weekend_pts[p_idx])
                self.dev_weekend[p_idx] = var

        if self.data.target_night_float:
            for p_idx, person in enumerate(self.people[:-1]):
                if person not in self.data.target_night_float:
                    continue
                target = scaled(self.data.target_night_float[person])
                var = self.model.NewIntVar(0, max_val, f"dev_nf_{p_idx}")
                self.model.Add(var >= self.nf_pts[p_idx] - target)
                self.model.Add(var >= target - self.nf_pts[p_idx])
                self.dev_night_float[p_idx] = var

        if self.data.target_label:
            for p_idx, person in enumerate(self.people[:-1]):
                for label in self.labels:
                    key = (person, label)
                    if key not in self.data.target_label:
                        continue
                    target = scaled(self.data.target_label[key])
                    var = self.model.NewIntVar(0, max_val, f"dev_label_{p_idx}_{label}")
                    lp = self.label_pts[(p_idx, label)]
                    self.model.Add(var >= lp - target)
                    self.model.Add(var >= target - lp)
                    self.dev_label[(p_idx, label)] = var

    def add_cap_constraints(self) -> None:
        """Hard per-resident ceilings on total and night-float points.

        Capped residents simply work less; the slack falls to ``Unfilled`` (so a
        cap never makes the model infeasible). Caps override fairness — a capped
        resident's deviation from their fair share is accepted.
        """
        for p_idx, person in enumerate(self.people[:-1]):
            if self.data.max_total and person in self.data.max_total:
                self.model.Add(self.total_pts[p_idx] <= scaled(self.data.max_total[person]))
            if self.data.max_nights and person in self.data.max_nights:
                self.model.Add(self.nf_pts[p_idx] <= scaled(self.data.max_nights[person]))

    def add_extra_point_constraints(self) -> None:
        """Hard floor enforcing mandatory extra points on punished residents.

        Their total must reach the (already raised) target, so the extra is
        actually carried, not merely aimed at. If a resident cannot reach it
        (availability / min_gap), the model is infeasible and the diagnostics say
        so — i.e. you learn the penalty can't be applied rather than it silently
        being skipped.
        """
        extra = self.data.extra_points or {}
        tmap = self.data.target_total_map or {}
        for p_idx, person in enumerate(self.people[:-1]):
            if extra.get(person, 0.0) > 0 and person in tmap:
                self.model.Add(self.total_pts[p_idx] >= scaled(tmap[person]))

    def add_reduction_constraints(self) -> None:
        """Hard windowed caps from shift-type load reductions.

        Factor 0 pins the member's variables for those (label, window-day)
        slots to zero (stronger propagation than a ≤ 0 sum); a partial factor
        caps the scaled points. Like ``max_total``/``max_nights`` a reduction
        can never make the model infeasible — uncovered slots fall to
        ``Unfilled``.
        """
        caps = reduction_caps(self.data)
        if not caps:
            return
        person_idx = {p: i for i, p in enumerate(self.people[:-1])}
        for cap in caps:
            p_idx = person_idx.get(cap.person)
            if p_idx is None:
                continue
            slot_keys = [
                key for key, slot in self.slots.items()
                if slot.shift.label in cap.labels and cap.start <= slot.day <= cap.end
            ]
            if not slot_keys:
                continue
            if cap.factor <= 0:
                for key in slot_keys:
                    self.model.Add(self.vars[(p_idx,) + key] == 0)
            else:
                self.model.Add(
                    sum(
                        scaled(self.slots[key].points) * self.vars[(p_idx,) + key]
                        for key in slot_keys
                    )
                    <= scaled(cap.cap_points)
                )

    def add_constraints(self) -> None:
        self._add_slot_coverage_and_eligibility()
        self._add_one_shift_per_day()
        self._add_min_gap_windows()
        self._add_nf_block_constraints()
        self._add_nf_rest_constraints()
        self._add_avoid_pair_constraints()

    def _add_avoid_pair_constraints(self) -> None:
        """Avoid pairs: the two residents never work on the same day.

        At most one of the pair is assigned per day across all shifts. Like
        the caps this can never make the model infeasible on its own — the
        uncoverable surplus falls to ``Unfilled`` — and it involves no
        fairness targets.
        """
        pairs = self.data.avoid_pairs or []
        if not pairs:
            return
        person_idx = {p: i for i, p in enumerate(self.people[:-1])}
        n_shifts = len(self.shifts)
        for pair in pairs:
            a_idx = person_idx.get(pair[0])
            b_idx = person_idx.get(pair[1])
            if a_idx is None or b_idx is None or a_idx == b_idx:
                continue
            for d_idx in range(len(self.days)):
                self.model.Add(
                    sum(self.vars[(a_idx, d_idx, s)] for s in range(n_shifts))
                    + sum(self.vars[(b_idx, d_idx, s)] for s in range(n_shifts))
                    <= 1
                )

    def _blocked_day_indices(self) -> Dict[int, set]:
        """Person index -> day indices that person cannot work.

        A day is blocked by any leave covering it, or — for a rotator — by
        falling outside all of that resident's active windows. (Blackouts are
        shift-type-aware and live in ``_blocked_slot_indices``.) Precomputed
        once so the constraint loop does an O(1) membership check instead of
        re-scanning every leave per (day, shift, person) triple.
        """
        rotator_windows: Dict[str, list] = {}
        for res, start, end in normalized_rotators(self.data.rotators):
            rotator_windows.setdefault(res, []).append((start, end))
        leave_windows: Dict[str, list] = {}
        for res, start, end, _comp in normalized_leaves(self.data.leaves):
            leave_windows.setdefault(res, []).append((start, end))

        blocked: Dict[int, set] = {}
        for p_idx, person in enumerate(self.people[:-1]):  # exclude Unfilled
            windows = rotator_windows.get(person)
            leaves = leave_windows.get(person, [])
            days_blocked = set()
            for d_idx, day in enumerate(self.days):
                if windows and not any(s <= day <= e for s, e in windows):
                    days_blocked.add(d_idx)  # outside rotator active window
                elif any(s <= day <= e for s, e in leaves):
                    days_blocked.add(d_idx)  # on leave (compensated or not)
            if days_blocked:
                blocked[p_idx] = days_blocked
        return blocked

    def _blocked_slot_indices(self) -> Dict[int, set]:
        """Person index -> (day, shift) index pairs blocked by group blackouts.

        Blackouts are shift-type-aware, unlike leaves: the window blocks every
        non-night-float shift (night float is a separate rotation, never
        touched), and the night-before date blocks only the night on-calls
        (``is_night_call``: "Thu counts as weekend", non-NF) so the member is
        not post-call on their first off day. The compensated flag only
        affects the fairness share (model.weights), never the blocking.
        """
        windows = blackout_person_windows(self.data.blackouts, self.data.named_groups)
        night_dates = blackout_night_before_dates(
            self.data.blackouts, self.data.named_groups
        )
        if not windows and not night_dates:
            return {}
        blocked: Dict[int, set] = {}
        for p_idx, person in enumerate(self.people[:-1]):  # exclude Unfilled
            person_windows = windows.get(person, ())
            person_nights = night_dates.get(person, ())
            slots = set()
            for (d_idx, s_idx), slot in self.slots.items():
                if slot.shift.night_float:
                    continue
                if any(s <= slot.day <= e for s, e, _comp in person_windows):
                    slots.add((d_idx, s_idx))
                elif slot.day in person_nights and is_night_call(slot.shift):
                    slots.add((d_idx, s_idx))
            if slots:
                blocked[p_idx] = slots
        return blocked

    def _eligible_person_indices(self) -> Dict[int, set]:
        """Shift index -> person indices allowed on that shift (role + NF + exemptions)."""
        juniors, seniors = set(self.data.juniors), set(self.data.seniors)
        nf_juniors, nf_seniors = set(self.data.nf_juniors), set(self.data.nf_seniors)
        exempt = self.data.exempt_shifts or {}
        eligible: Dict[int, set] = {}
        for s_idx, shift in enumerate(self.shifts):
            pool = juniors if shift.role == "Junior" else seniors
            if shift.night_float:
                pool = pool & (nf_juniors if shift.role == "Junior" else nf_seniors)
            eligible[s_idx] = {
                p_idx
                for p_idx, person in enumerate(self.people[:-1])
                if person in pool and shift.label not in exempt.get(person, ())
            }
        return eligible

    def _add_slot_coverage_and_eligibility(self) -> None:
        blocked = self._blocked_day_indices()
        slot_blocked = self._blocked_slot_indices()
        eligible = self._eligible_person_indices()
        for d_idx in range(len(self.days)):
            for s_idx in range(len(self.shifts)):
                # exactly one assignment per slot
                self.model.Add(
                    sum(self.vars[(p_idx, d_idx, s_idx)]
                        for p_idx in range(len(self.people))) == 1
                )
                for p_idx in range(len(self.people) - 1):  # exclude Unfilled
                    if (
                        p_idx not in eligible[s_idx]
                        or d_idx in blocked.get(p_idx, ())
                        or (d_idx, s_idx) in slot_blocked.get(p_idx, ())
                    ):
                        self.model.Add(self.vars[(p_idx, d_idx, s_idx)] == 0)

    def _add_one_shift_per_day(self) -> None:
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

    def _add_min_gap_windows(self) -> None:
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

    def _add_nf_block_constraints(self) -> None:
        # night float blocks must have exact length
        block_len = self.data.nf_block_length
        if block_len > 1:
            nf_shift_idxs = [i for i, s in enumerate(self.shifts) if s.night_float]
            for s_idx in nf_shift_idxs:
                for block_start in range(0, len(self.days), block_len):
                    block_idxs = list(range(block_start, min(block_start + block_len, len(self.days))))
                    # A trailing partial block is still covered by a single
                    # resident for the remaining days instead of forcing the
                    # nights unfilled (which previously dropped coverage at the
                    # end of the horizon); the post-solve validator allows this
                    # short final run.
                    for p_idx in range(len(self.people)):
                        first_var = self.vars[(p_idx, block_idxs[0], s_idx)]
                        for d_idx in block_idxs[1:]:
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

    def _add_nf_rest_constraints(self) -> None:
        # Rest around night-float blocks. A resident who works the first/last
        # night of a block gets ``gap`` idle days before/after that block versus
        # *any* shift (NF or regular), matching the spec's "min_gap between any
        # two shifts" intent. Nights stay consecutive within a block (enforced in
        # _add_nf_block_constraints); this only spaces a block from adjacent
        # regular shifts and other blocks. Block boundaries are deterministic
        # from the day-0-aligned partition, so this also covers block_len == 1
        # (each NF night is its own block).
        gap = self.data.min_gap
        nf_shift_idxs = [i for i, s in enumerate(self.shifts) if s.night_float]
        n_days = len(self.days)
        block_span = max(1, self.data.nf_block_length)
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

    def _preference_rewards(self) -> Dict[Tuple[int, int, int], int]:
        """(person, day, shift) -> reward in {1, 2} for preference matches.

        One point when the slot's label is among the person's preferred shift
        types, one when the slot's day type (weekend as classified by
        ``classify_slot`` — including ``thu_weekend`` and weekend-flagged
        holidays) matches their preferred day type.
        """
        preferred = self.data.preferred_shifts or {}
        day_type = self.data.preferred_day_type or {}
        if not preferred and not day_type:
            return {}
        rewards: Dict[Tuple[int, int, int], int] = {}
        for p_idx, person in enumerate(self.people[:-1]):
            labels = set(preferred.get(person, ()))
            wants = day_type.get(person)
            if not labels and not wants:
                continue
            for (d_idx, s_idx), slot in self.slots.items():
                reward = 0
                if slot.shift.label in labels:
                    reward += 1
                if wants == "weekend" and slot.weekend:
                    reward += 1
                elif wants == "weekday" and not slot.weekend:
                    reward += 1
                if reward:
                    rewards[(p_idx, d_idx, s_idx)] = reward
        return rewards

    def build_objective(self) -> None:
        unfilled_vars = [
            self.vars[(len(self.people) - 1, d_idx, s_idx)]
            for d_idx in range(len(self.days))
            for s_idx in range(len(self.shifts))
        ]

        terms = []
        # Lexicographic-style weights: overall-points spread first, then total,
        # weekend, night-float, per-label deviations, and finally unfilled
        # slots. When soft preferences exist, objective_weights rescales the
        # whole ladder so the preference rewards (weight 1) sit strictly below
        # everything — they only order exact fairness ties.
        rewards = self._preference_rewards()
        self.pref_rewards = rewards
        max_slot = max((scaled(slot.points) for slot in self.slots.values()), default=1)
        W_MAXDEV, W_TOTAL, W_WEEKEND, W_NIGHTS, W_LABEL, W_UNFILLED = objective_weights(
            len(self.days), len(self.shifts), bool(rewards), max_slot
        )
        if self.max_dev is not None:
            terms.append(W_MAXDEV * self.max_dev)
        if self.dev_total:
            terms.append(W_TOTAL * sum(self.dev_total.values()))
        if self.dev_weekend:
            terms.append(W_WEEKEND * sum(self.dev_weekend.values()))
        if self.dev_night_float:
            terms.append(W_NIGHTS * sum(self.dev_night_float.values()))
        if self.dev_label:
            terms.append(W_LABEL * sum(self.dev_label.values()))
        terms.append(W_UNFILLED * sum(unfilled_vars))
        if rewards:
            terms.append(sum(-r * self.vars[key] for key, r in rewards.items()))

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


Ledger = Mapping[str, Mapping[str, float]]


def _carryover_targets(
    prior: Mapping[str, float],
    block_points: float,
    members: Sequence[str],
    weights: Mapping[str, float],
    weight_sum: float,
    clamp_max: float,
) -> Dict[str, float]:
    """Per-member this-block target that balances *cumulative* load.

    Each member's cumulative fair share (prior + this block, weighted) minus what
    they already carry, clamped to ``[0, clamp_max]`` so the value stays within a
    single block's range (an extreme prior imbalance is corrected over several
    blocks rather than all at once).
    """
    if weight_sum <= 0:
        return {m: 0.0 for m in members}
    cumulative = block_points + sum(prior.get(m, 0.0) for m in members)
    return {
        m: min(clamp_max, max(0.0, cumulative * weights[m] / weight_sum - prior.get(m, 0.0)))
        for m in members
    }


# Availability/load weighting lives in model.weights (shared with the ledger's
# no-catch-up policy); the old private name is kept as an alias.
_availability_weights = availability_weights


def _shift_reduced_targets(
    target_map: Mapping[str, float],
    deltas: Mapping[str, float],
    pool: Sequence[str],
) -> Dict[str, float]:
    """Lower reduced members' targets and raise the others' to reconcile.

    The inverse of :func:`_apply_extra_points`: the load taken off the reduced
    members still has to be carried, so it moves onto the non-reduced pool
    members in proportion to their targets (equally when those are all zero).
    If everyone in the pool is reduced there is nobody to receive the load and
    it simply falls to ``Unfilled``.
    """
    out = {p: float(target_map.get(p, 0.0)) for p in pool}
    moved = 0.0
    for person, delta in deltas.items():
        if person not in out or delta <= 0:
            continue
        take = min(delta, out[person])
        out[person] -= take
        moved += take
    receivers = [p for p in pool if p not in deltas]
    if moved > 0 and receivers:
        receiver_sum = sum(out[p] for p in receivers)
        if receiver_sum > 0:
            for p in receivers:
                out[p] += moved * out[p] / receiver_sum
        else:
            for p in receivers:
                out[p] += moved / len(receivers)
    return out


def _apply_reduction_targets(
    data: InputData,
    target_total_map: Dict[str, float] | None,
    target_night_float: Dict[str, float] | None,
    participants: Sequence[str],
) -> Tuple[Dict[str, float] | None, Dict[str, float] | None]:
    """Fold "work less now" reductions into the total / night-float targets.

    Only ``keep_total=False`` caps lower targets (their members genuinely work
    less this block; the ledger repays the shortfall later). ``keep_total=True``
    caps leave targets alone so the member is compensated with other shift
    types in-block. Weights are never touched either way, so the ledger's
    no-catch-up policy cannot credit (excuse) the reduction.
    """
    caps = [c for c in reduction_caps(data) if not c.keep_total]
    if not caps:
        return target_total_map, target_night_float

    if target_total_map:
        totals_delta: Dict[str, float] = {}
        for cap in caps:
            totals_delta[cap.person] = totals_delta.get(cap.person, 0.0) + cap.reduce_total
        target_total_map = _shift_reduced_targets(
            target_total_map, totals_delta, participants
        )

    nf_delta: Dict[str, float] = {}
    for cap in caps:
        if cap.reduce_nf > 0:
            nf_delta[cap.person] = nf_delta.get(cap.person, 0.0) + cap.reduce_nf
    if nf_delta and target_night_float:
        target_night_float = dict(target_night_float)
        for role_pool in (data.nf_juniors, data.nf_seniors):
            members = [p for p in role_pool if p in target_night_float]
            sub = {p: nf_delta[p] for p in members if p in nf_delta}
            if members and sub:
                target_night_float.update(
                    _shift_reduced_targets(
                        {p: target_night_float[p] for p in members}, sub, members
                    )
                )
    return target_total_map, target_night_float


def _apply_extra_points(
    target_total_map: Dict[str, float],
    extra_points: Mapping[str, float],
    participants: Sequence[str],
) -> Dict[str, float]:
    """Fold mandatory extra points (e.g. a penalty) into the total targets.

    Raises the punished residents' targets by their extra and lowers everyone
    else's proportionally so the targets still sum to the work available. A
    hard floor in the solver then enforces it.
    """
    extra = {p: extra_points.get(p, 0.0) for p in participants}
    extra_sum = sum(extra.values())
    base_sum = sum(target_total_map.values())
    if base_sum > 0 and extra_sum < base_sum:
        factor = (base_sum - extra_sum) / base_sum
        return {p: target_total_map[p] * factor + extra[p] for p in participants}
    # extreme: the extras alone exceed the available work
    return {p: extra[p] for p in participants}


# Per-label fairness adds ~residents×labels deviation variables. On small and
# mid-size rosters (a typical department) that is free — the solver reaches
# optimality and the low-priority label tier is satisfied only after total,
# weekend, and night-float balance. On a very large, time-limited problem the
# extra variables instead starve the primary balance without achieving label
# balance, so per-label targets are only auto-set below this size (measured:
# safe to ~24×28×8 ≈ 5.4k cells; harmful at 45×28×10 ≈ 12.6k). Set
# ``target_label`` explicitly to override the gate.
LABEL_TARGET_MAX_CELLS = 6000


def _auto_label_targets(
    data: InputData, availability: Mapping[str, float]
) -> Dict[Tuple[str, str], float]:
    """Per-(resident, label) fair share of each shift type's points.

    Fulfils the spec's per-label balance ("every resident's points on each
    label equal their fractional share"): equal total points are not enough
    if one resident works all the heavy nights and another only day shifts.
    Each label's points are split availability-weighted across the residents
    eligible for it, feeding the existing ``dev_label`` tier (the lowest
    deviation weight, so it never overrides total/weekend/night-float
    balance). Deliberately skipped so it doesn't fight other features:

    * night-float shifts — already balanced by the night-float dimension;
    * (resident, label) pairs under a load reduction — the cap governs the mix;
    * residents with any shift/day-type preference — their mix is intentionally
      free (two people preferring opposite shifts swap, which is fair).
    """
    participants = list(data.juniors) + list(data.seniors)
    pref_people = set(data.preferred_shifts or {}) | set(data.preferred_day_type or {})
    capped = {(cap.person, lbl) for cap in reduction_caps(data) for lbl in cap.labels}

    label_points: Dict[str, float] = {}
    for slot in slot_points(data):
        if slot.shift.night_float:
            continue
        label_points[slot.shift.label] = label_points.get(slot.shift.label, 0.0) + slot.points

    targets: Dict[Tuple[str, str], float] = {}
    for shift in data.shifts:
        if shift.night_float or shift.label not in label_points:
            continue
        pool = [p for p in participants if eligible_for_shift(p, shift, data)]
        pool_weight = sum(availability.get(p, 0.0) for p in pool)
        if pool_weight <= 0:
            continue
        for person in pool:
            if person in pref_people or (person, shift.label) in capped:
                continue
            targets[(person, shift.label)] = (
                label_points[shift.label] * availability.get(person, 0.0) / pool_weight
            )
    return targets


def resolve_targets(data: InputData, ledger: Ledger | None = None) -> InputData:
    """Return a copy of ``data`` with all fairness targets resolved.

    Targets the caller left unset are auto-computed (equal shares weighted by
    availability); a ``ledger`` of prior-block points switches the totals to
    cumulative carryover balancing; ``extra_points`` penalties are folded into
    the total map. The caller's ``InputData`` is never mutated.
    """
    participants = data.juniors + data.seniors
    target_total = data.target_total
    target_total_map = data.target_total_map
    target_weekend = data.target_weekend
    target_night_float = data.target_night_float
    target_label = data.target_label

    if participants:
        total_points = 0.0
        weekend_points = 0.0
        nf_points_by_role: Dict[str, float] = {"Junior": 0.0, "Senior": 0.0}
        for slot in slot_points(data):
            total_points += slot.points
            if slot.weekend:
                weekend_points += slot.points
            if slot.night_float:
                role = slot.shift.role
                nf_points_by_role[role] = nf_points_by_role.get(role, 0.0) + slot.points

        availability = _availability_weights(data)
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

        if target_night_float is None:
            # Night-float load is balanced per role among the eligible pool only
            # (non-eligible residents never work nights), availability-weighted so
            # rotators carry a proportionally smaller share.
            target_night_float = {}
            for role, pool in (("Junior", data.nf_juniors), ("Senior", data.nf_seniors)):
                role_points = nf_points_by_role.get(role, 0.0)
                pool_weight = sum(availability.get(p, 0) for p in pool)
                if pool_weight > 0:
                    for p in pool:
                        target_night_float[p] = role_points * availability[p] / pool_weight

        if ledger:
            # Carryover fairness: targets balance cumulative load (prior + this
            # block) rather than this block alone. Overrides the per-block maps.
            prior_total = {p: ledger.get(p, {}).get("total", 0.0) for p in participants}
            prior_wk = {p: ledger.get(p, {}).get("weekend", 0.0) for p in participants}
            target_total_map = _carryover_targets(
                prior_total, total_points, participants, availability, weight_sum, total_points
            )
            target_weekend = _carryover_targets(
                prior_wk, weekend_points, participants, availability, weight_sum, weekend_points
            )
            target_night_float = {}
            for role, pool in (("Junior", data.nf_juniors), ("Senior", data.nf_seniors)):
                role_points = nf_points_by_role.get(role, 0.0)
                pool_weight = sum(availability.get(p, 0) for p in pool)
                prior_nf = {p: ledger.get(p, {}).get("night_float", 0.0) for p in pool}
                target_night_float.update(
                    _carryover_targets(prior_nf, role_points, pool, availability, pool_weight, role_points)
                )

        if data.extra_points and target_total_map:
            target_total_map = _apply_extra_points(
                target_total_map, data.extra_points, participants
            )

        if data.reductions:
            target_total_map, target_night_float = _apply_reduction_targets(
                data, target_total_map, target_night_float, participants
            )

        if target_label is None:
            day_count = (data.end_date - data.start_date).days + 1
            cells = len(participants) * max(1, day_count) * max(1, len(data.shifts))
            if cells <= LABEL_TARGET_MAX_CELLS:
                target_label = _auto_label_targets(data, availability) or None

    # A copy so the caller's ``InputData`` is never mutated (re-running with
    # changed dates previously reused stale auto-targets).
    return replace(
        data,
        target_total=target_total,
        target_total_map=target_total_map,
        target_weekend=target_weekend,
        target_night_float=target_night_float,
        target_label=target_label,
    )


def build_schedule(data: InputData, env: str | None = None, ledger: Ledger | None = None) -> pd.DataFrame:
    """Build schedule with optional environment based time limit.

    ``ledger`` (resident -> accumulated total/weekend/night_float points from
    prior blocks) switches fairness from per-block to cumulative: residents who
    carried extra previously get lighter targets this block.
    """
    # Lazy import avoids a module-level cycle (validation imports this module).
    from .validation import validate_input

    problems = validate_input(data)
    if problems:
        detail = "\n".join(f"- {p}" for p in problems)
        raise ValueError(f"Invalid configuration:\n{detail}")

    day_count = (data.end_date - data.start_date).days + 1
    participants = data.juniors + data.seniors
    # The resolved targets are exposed on ``df.attrs`` below.
    solve_data = resolve_targets(data, ledger)
    target_total = solve_data.target_total
    target_total_map = solve_data.target_total_map
    target_weekend = solve_data.target_weekend
    target_night_float = solve_data.target_night_float
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
    df.attrs["target_night_float"] = target_night_float
    df.attrs["target_label"] = solve_data.target_label
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
