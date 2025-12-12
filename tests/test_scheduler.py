"""
Minimal self-test for Idea Gold Scheduler.

✓  checks "total-points spread ≤ 1"
✓  measures solve time (pytest-benchmark)
"""

import pytest
from hypothesis import given, strategies as st
from model.optimiser import build_schedule

# ---- helpers ---------------------------------------------------------

def tiny_instance():
    """Return the simplest valid InputData (4 juniors, 4 seniors, 2 labels, 3 days)."""
    from model.data_models import InputData, ShiftTemplate
    from datetime import date
    return InputData(
        juniors=["J1", "J2", "J3", "J4"],
        seniors=["S1", "S2", "S3", "S4"],
        nf_juniors=[],
        nf_seniors=[],
        shifts=[
            ShiftTemplate(label="Day", role="Junior", night_float=False, thu_weekend=False, points=1),
            ShiftTemplate(label="Night", role="Senior", night_float=False, thu_weekend=False, points=1),
        ],
        start_date=date(2025, 8, 1),
        end_date=date(2025, 8, 3),
        nf_block_length=5,
        min_gap=1,
        leaves=[],
        rotators=[],
    )

# ---- property-based fairness test -----------------------------------

@given(seed=st.integers(0, 2**32 - 1))
def test_total_point_spread(seed):
    data = tiny_instance()  # in real life: random_instance(seed)
    df, meta = build_schedule(data)
    assert meta["max_total_dev"] <= 1

# ---- performance benchmark ------------------------------------------

def test_solve_speed(benchmark):
    data = tiny_instance()
    benchmark(lambda: build_schedule(data))
