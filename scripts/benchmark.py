"""Rough solve-time benchmark for the optimiser.

The spec targets a solve time of <= 60s for 40 residents x 28 days x 10 shift
labels on Streamlit Cloud. This script times ``build_schedule`` across a few
sizes so regressions in model size / solve time are easy to spot.

Usage::

    python scripts/benchmark.py            # default size sweep
    python scripts/benchmark.py 40 28 10   # one custom run: juniors+seniors, days, shifts

Requires OR-Tools (``pip install -r requirements.txt``); without it the stub
solver returns instantly and the timings are meaningless.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from model.benchmarking import (  # noqa: E402
    SAFE_BENCHMARK_PRESETS,
    BenchmarkCase,
    benchmark_available,
    build_benchmark_input,
    run_benchmark,
)


def _make(people: int, days: int, shifts: int):
    """Backward-compatible wrapper around the structured input builder."""
    return build_benchmark_input(BenchmarkCase(people, days, shifts))


def _run(people: int, days: int, shifts: int) -> None:
    result = run_benchmark(BenchmarkCase(people, days, shifts), env="prod")
    print(
        f"{people:>3} people x {days:>2} days x {shifts:>2} shifts: "
        f"{result.elapsed_seconds:6.2f}s  status={result.solver_status}  [{result.flag}]"
    )


def main() -> None:
    if not benchmark_available():
        print("OR-Tools not installed; timings would be meaningless. Aborting.")
        return
    args = sys.argv[1:]
    if len(args) == 3:
        _run(int(args[0]), int(args[1]), int(args[2]))
        return
    print("Solve-time sweep (target: <= 60s for 40 x 28 x 10):")
    for case in SAFE_BENCHMARK_PRESETS:
        _run(case.people, case.days, case.shifts)


if __name__ == "__main__":
    main()
