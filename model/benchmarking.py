"""Structured, UI-neutral solve-time benchmarks for the scheduler.

The command-line adapter lives in :mod:`scripts.benchmark`; this module owns
the synthetic input construction and timing so a future UI can run the same
well-defined cases without importing a script or scraping printed output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Literal

from .data_models import InputData, ShiftTemplate
from .optimiser import ORTOOLS_AVAILABLE, build_schedule

DEFAULT_TARGET_SECONDS = 60.0

# A representative 10-shift mix (2 night-float-eligible + 8 regular). The
# benchmark factory deliberately rejects a larger shift count instead of
# silently building fewer shifts than the result claims to have measured.
_SHIFT_POOL: tuple[tuple[str, str, bool, bool], ...] = (
    ("Junior NF", "Junior", True, False),
    ("Senior NF", "Senior", True, False),
    ("ER night", "Junior", False, True),
    ("Ward night", "Junior", False, True),
    ("Senior night", "Senior", False, True),
    ("Evening", "Senior", False, False),
    ("Morning", "Senior", False, False),
    ("Ward morning", "Junior", False, False),
    ("ER zone 1", "Junior", False, False),
    ("ER zone 2", "Junior", False, False),
)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One synthetic scheduler workload and its performance target.

    At least two people are required because the representative shift pool has
    both junior and senior roles. ``shifts`` is capped by the fixed pool so the
    reported dimensions always match the generated problem.
    """

    people: int
    days: int
    shifts: int
    target_seconds: float = DEFAULT_TARGET_SECONDS

    def __post_init__(self) -> None:
        for field_name in ("people", "days", "shifts"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
        if self.people < 2:
            raise ValueError("people must be at least 2 (one junior and one senior)")
        if self.days < 1:
            raise ValueError("days must be at least 1")
        if not 1 <= self.shifts <= len(_SHIFT_POOL):
            raise ValueError(f"shifts must be between 1 and {len(_SHIFT_POOL)}")
        if isinstance(self.target_seconds, bool) or not isinstance(
            self.target_seconds, (int, float)
        ):
            raise TypeError("target_seconds must be a number")
        if self.target_seconds <= 0:
            raise ValueError("target_seconds must be greater than 0")

    @property
    def dimensions(self) -> str:
        """Human-readable dimensions used by CLI and UI adapters."""
        return f"{self.people} people x {self.days} days x {self.shifts} shifts"


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Structured outcome of a completed benchmark run."""

    case: BenchmarkCase
    elapsed_seconds: float
    solver_status: str | None

    @property
    def within_target(self) -> bool:
        return self.elapsed_seconds <= self.case.target_seconds

    @property
    def flag(self) -> Literal["OK", "SLOW"]:
        return "OK" if self.within_target else "SLOW"


# The historical CLI sweep, exposed as immutable typed cases. Keeping the
# suite small and bounded avoids an accidental multi-minute run when a UI
# merely renders its available presets.
SAFE_BENCHMARK_PRESETS: tuple[BenchmarkCase, ...] = (
    BenchmarkCase(10, 14, 5),
    BenchmarkCase(20, 28, 8),
    BenchmarkCase(45, 28, 10),
)


def benchmark_available() -> bool:
    """Whether timings use the real OR-Tools solver rather than the stub."""
    return ORTOOLS_AVAILABLE


def build_benchmark_input(case: BenchmarkCase) -> InputData:
    """Build the deterministic synthetic scheduler input for ``case``."""
    n_seniors = max(1, case.people // 3)
    n_juniors = case.people - n_seniors
    juniors = [f"J{i}" for i in range(n_juniors)]
    seniors = [f"S{i}" for i in range(n_seniors)]
    templates = [
        ShiftTemplate(
            label=label,
            role=role,
            night_float=night_float,
            thu_weekend=thu_weekend,
            points=1.0,
        )
        for label, role, night_float, thu_weekend in _SHIFT_POOL[: case.shifts]
    ]
    start = date(2025, 1, 1)
    return InputData(
        start_date=start,
        end_date=start + timedelta(days=case.days - 1),
        shifts=templates,
        juniors=juniors,
        seniors=seniors,
        nf_juniors=juniors[: max(1, n_juniors // 2)],
        nf_seniors=seniors[: max(1, n_seniors // 2)],
        leaves=[],
        rotators=[],
        min_gap=1,
        nf_block_length=5,
    )


def run_benchmark(case: BenchmarkCase, *, env: str = "prod") -> BenchmarkResult:
    """Build and time one case using the real solver.

    ``RuntimeError`` is raised when OR-Tools is unavailable. Treating a stub
    run as a real benchmark would return a fast but meaningless result.
    Solver/build errors otherwise propagate to the caller so CLI and UI
    adapters can present them in their native error style.
    """
    if not benchmark_available():
        raise RuntimeError("OR-Tools not installed; timings would be meaningless.")
    data = build_benchmark_input(case)
    started = time.perf_counter()
    frame = build_schedule(data, env=env)
    elapsed = time.perf_counter() - started
    raw_status = frame.attrs.get("solver_status")
    status = None if raw_status is None else str(raw_status)
    return BenchmarkResult(case=case, elapsed_seconds=elapsed, solver_status=status)


def run_benchmark_suite(
    cases: Iterable[BenchmarkCase] = SAFE_BENCHMARK_PRESETS,
    *,
    env: str = "prod",
) -> list[BenchmarkResult]:
    """Run ``cases`` sequentially and return results in the supplied order."""
    return [run_benchmark(case, env=env) for case in cases]


__all__ = [
    "BenchmarkCase",
    "BenchmarkResult",
    "DEFAULT_TARGET_SECONDS",
    "SAFE_BENCHMARK_PRESETS",
    "benchmark_available",
    "build_benchmark_input",
    "run_benchmark",
    "run_benchmark_suite",
]
