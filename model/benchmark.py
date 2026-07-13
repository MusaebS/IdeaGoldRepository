"""One-off host speed benchmark → a rough solver time-limit suggestion.

Scheduling difficulty is *not* reliably predictable from problem size, so this
never promises an exact time. What it can do: measure how fast this particular
host solves a fixed reference roster, and scale a size-based starting budget by
that speed. Slower hosting yields a larger starting limit. The definitive
"was that enough?" answer is the post-solve verdict
(``solve_report.convergence_verdict``), not this estimate.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple, Optional

# The reference roster: small and loosely constrained enough that CP-SAT proves
# OPTIMAL every time, so its wall time reflects host CPU speed rather than the
# (open-ended) cost of proving optimality on a hard instance.
_REF_PEOPLE_J = 6
_REF_PEOPLE_S = 4
_REF_DAYS = 10
_REF_GAP = 1

# Wall time (seconds) the reference roster takes on the calibration host. Speed
# ratios are taken against this: a host that measures ~2x this is treated as
# roughly half speed and gets ~2x the suggested starting limit.
REFERENCE_WALL_TIME = 0.33


class BenchmarkResult(NamedTuple):
    wall_time_sec: float
    speed_ratio: float  # >1 = faster than the calibration host, <1 = slower


def _reference_input():
    from .data_models import InputData, ShiftTemplate

    shifts = [
        ShiftTemplate(label="BenchJnr", role="Junior", night_float=False,
                      thu_weekend=False, points=1.0),
        ShiftTemplate(label="BenchSnr", role="Senior", night_float=False,
                      thu_weekend=False, points=2.0),
    ]
    return InputData(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1) + timedelta(days=_REF_DAYS - 1),
        shifts=shifts,
        juniors=[f"BJ{i}" for i in range(_REF_PEOPLE_J)],
        seniors=[f"BS{i}" for i in range(_REF_PEOPLE_S)],
        nf_juniors=[], nf_seniors=[], leaves=[], rotators=[],
        min_gap=_REF_GAP, seed=0,
    )


def run_host_benchmark(trials: int = 2, cap_sec: float = 8.0) -> Optional[BenchmarkResult]:
    """Solve the reference roster a few times and return the best (minimum) wall
    time plus the derived speed ratio.

    Returns ``None`` when solve timing is unavailable — e.g. the no-ortools stub,
    where solving returns instantly and no wall time is recorded — so callers can
    fall back to an un-scaled, size-only suggestion.
    """
    from .optimiser import build_schedule

    best: Optional[float] = None
    for _ in range(max(1, trials)):
        df = build_schedule(_reference_input(), env="prod", time_limit_sec=cap_sec)
        try:
            wall = df.attrs.get("wall_time_sec")
        except (AttributeError, TypeError):  # stub frame without .attrs
            wall = None
        if wall is None or wall <= 0:
            return None
        best = wall if best is None else min(best, wall)
    if best is None or best <= 0:
        return None
    ratio = max(0.05, min(10.0, REFERENCE_WALL_TIME / best))
    return BenchmarkResult(wall_time_sec=best, speed_ratio=ratio)


def suggested_time_limit(
    num_people: int, num_days: int, num_shifts: int, speed_ratio: Optional[float] = None
) -> int:
    """A rough starting time limit (seconds) for a problem of this size.

    Uses the same size-tuned base budget as the solver (``compute_time_limit``
    in prod) and scales it by host speed: a host at half speed
    (``speed_ratio = 0.5``) gets double the starting budget. Clamped to a sane
    range and rounded to a tidy value. Always a *starting point*, never a promise.
    """
    from .optimiser import compute_time_limit

    base = compute_time_limit("prod", num_people, num_days, num_shifts)
    ratio = speed_ratio if (speed_ratio and speed_ratio > 0) else 1.0
    suggestion = base / ratio
    suggestion = max(30.0, min(1800.0, suggestion))
    step = 30 if suggestion < 300 else 60
    return int(round(suggestion / step) * step)
