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
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from model.data_models import InputData, ShiftTemplate  # noqa: E402
from model.optimiser import ORTOOLS_AVAILABLE, build_schedule  # noqa: E402

# A representative 10-shift mix (2 night-float + 8 regular), reused/truncated.
_SHIFT_POOL = [
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
]


def _make(people: int, days: int, shifts: int) -> InputData:
    n_seniors = max(1, people // 3)
    n_juniors = max(1, people - n_seniors)
    juniors = [f"J{i}" for i in range(n_juniors)]
    seniors = [f"S{i}" for i in range(n_seniors)]
    pool = _SHIFT_POOL[:shifts] if shifts <= len(_SHIFT_POOL) else _SHIFT_POOL
    templates = [
        ShiftTemplate(label=lbl, role=role, night_float=nf, thu_weekend=thu, points=1.0)
        for lbl, role, nf, thu in pool
    ]
    return InputData(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 1) + timedelta(days=days - 1),
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


def _run(people: int, days: int, shifts: int) -> None:
    data = _make(people, days, shifts)
    start = time.perf_counter()
    df = build_schedule(data, env="prod")
    elapsed = time.perf_counter() - start
    status = df.attrs.get("solver_status")
    flag = "OK" if elapsed <= 60 else "SLOW"
    print(f"{people:>3} people x {days:>2} days x {shifts:>2} shifts: "
          f"{elapsed:6.2f}s  status={status}  [{flag}]")


def main() -> None:
    if not ORTOOLS_AVAILABLE:
        print("OR-Tools not installed; timings would be meaningless. Aborting.")
        return
    args = sys.argv[1:]
    if len(args) == 3:
        _run(int(args[0]), int(args[1]), int(args[2]))
        return
    print("Solve-time sweep (target: <= 60s for 40 x 28 x 10):")
    for people, days, shifts in [(10, 14, 5), (20, 28, 8), (45, 28, 10)]:
        _run(people, days, shifts)


if __name__ == "__main__":
    main()
