import sys, os
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.data_models import InputData, ShiftTemplate
from model.optimiser import SolveProgress, build_schedule
from model.fairness import calculate_points


def _data(days=14, min_gap=2):
    shifts = [
        ShiftTemplate(label="JCall", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="SCall", role="Senior", night_float=False, thu_weekend=False, points=2.0),
    ]
    from datetime import timedelta
    return InputData(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1) + timedelta(days=days - 1),
        shifts=shifts,
        juniors=[f"J{i}" for i in range(8)],
        seniors=[f"S{i}" for i in range(5)],
        nf_juniors=[], nf_seniors=[], leaves=[], rotators=[], min_gap=min_gap, seed=0,
    )


def test_solve_progress_default_state():
    p = SolveProgress()
    assert p.solution_count == 0
    assert p.last_improvement_sec is None
    assert p.done is False


def test_build_schedule_accepts_progress_and_warm_start_without_ortools():
    # Even under the stub (no AddHint / no callback) these params must be inert,
    # never raising, so the code path is safe everywhere.
    data = _data()
    prog = SolveProgress()
    df = build_schedule(data, env="test", progress=prog)
    # A warm start from the produced frame must also be accepted.
    df2 = build_schedule(data, env="test", warm_start_df=df, progress=SolveProgress())
    assert df2 is not None


def test_progress_sink_is_populated_during_a_real_solve():
    pytest.importorskip("ortools")
    data = _data()
    prog = SolveProgress()
    build_schedule(data, env="prod", time_limit_sec=4, progress=prog)
    assert prog.solution_count >= 1
    assert prog.last_improvement_sec is not None
    assert prog.last_improvement_sec >= 0


def _total_range(df, data):
    pts = calculate_points(df, data)
    totals = [v["total"] for v in pts.values()]
    return max(totals) - min(totals)


def test_warm_start_continuation_never_regresses_fairness():
    pytest.importorskip("ortools")
    data = _data()
    first = build_schedule(data, env="prod", time_limit_sec=4)
    before = _total_range(first, data)
    # Continue from the first schedule: the hint seeds the search, so the result
    # can only stay the same or get fairer — never worse.
    cont = build_schedule(data, env="prod", time_limit_sec=6, warm_start_df=first)
    after = _total_range(cont, data)
    assert after <= before + 1e-9
