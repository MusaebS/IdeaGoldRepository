"""Post-solve convergence verdict.

Turns the raw solver status and timings recorded in ``df.attrs`` (see
``optimiser.build_schedule``) into a plain-language verdict the UI can show:
whether allowing the solver more time is likely to yield a fairer schedule, and
if so a concrete limit to try next. The point is to replace guess-and-check
("try 400s, then 500s") with a definite signal.

Pure and dependency-free so it also runs under the no-pandas/no-ortools stub CI
job.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Optional


class SolveVerdict(NamedTuple):
    """A verdict about whether a finished solve could improve with more time.

    ``level`` is one of ``"optimal"``, ``"converged"``, ``"improving"`` or
    ``"unknown"``. ``suggested_limit`` is a time-limit (seconds) worth trying
    next, or ``None`` when more time would not help.
    """

    level: str
    headline: str
    detail: str
    suggested_limit: Optional[int]


def _round_up_to(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def convergence_verdict(
    solver_status: Optional[str],
    wall_time_sec: Optional[float],
    time_limit_sec: Optional[float],
    last_improvement_sec: Optional[float] = None,
) -> SolveVerdict:
    """Classify a finished solve from its status and timings.

    * ``OPTIMAL`` — proven best; more time cannot help.
    * ``FEASIBLE`` and the last improving schedule landed in the final quarter
      of a run that used its whole time budget — *still improving*: raising the
      limit is likely to help, and a concrete next value is suggested.
    * ``FEASIBLE`` and improvements went quiet well before the limit (or the run
      finished under it) — *converged*: more time is unlikely to help.
    """
    status = (solver_status or "").upper()
    if status == "OPTIMAL":
        return SolveVerdict(
            "optimal",
            "Proven optimal",
            "This is the fairest schedule possible under the current "
            "constraints — allowing more time cannot improve it.",
            None,
        )
    if status != "FEASIBLE":
        return SolveVerdict(
            "unknown",
            "No convergence data",
            "This run recorded no timing information, so there is nothing to "
            "advise about the time limit.",
            None,
        )

    limit = float(time_limit_sec or 0.0)
    wall = float(wall_time_sec or 0.0)
    hit_limit = limit > 0 and wall >= 0.9 * limit

    # "Still improving" = the last better schedule appeared in the final quarter
    # of the run, so the solver had not run out of ideas when time ran out.
    still_improving = (
        hit_limit
        and last_improvement_sec is not None
        and wall > 0
        and (wall - float(last_improvement_sec)) <= 0.25 * wall
    )

    if still_improving:
        nxt = _round_up_to(max(limit, wall) * 1.5, 30)
        last = float(last_improvement_sec or wall)
        return SolveVerdict(
            "improving",
            "Still improving when time ran out",
            f"The solver was still finding fairer schedules as late as {last:.0f}s "
            f"into the {limit:.0f}s limit, so it had not settled. Raising the limit "
            f"is likely to help — try about {nxt}s.",
            nxt,
        )

    if not hit_limit:
        return SolveVerdict(
            "converged",
            "Converged",
            "The solver finished before the time limit — it had run out of fairer "
            "schedules to find, so more time will not help. To change the result, "
            "adjust the constraints (min_gap, caps, weekend multiplier).",
            None,
        )

    if last_improvement_sec is None:
        nxt = _round_up_to(max(limit, wall) * 1.5, 30)
        return SolveVerdict(
            "improving",
            "Used the whole time limit",
            f"The solver used the full {limit:.0f}s without proving optimality. If "
            f"the schedule still looks uneven, raising the limit may help — try "
            f"about {nxt}s.",
            nxt,
        )

    return SolveVerdict(
        "converged",
        "Converged",
        f"The solver stopped finding fairer schedules at {float(last_improvement_sec):.0f}s "
        f"and could not improve for the rest of the {limit:.0f}s limit. More time is "
        f"unlikely to help — this is effectively as fair as these constraints allow.",
        None,
    )
