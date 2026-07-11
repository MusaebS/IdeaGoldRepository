from datetime import date

import pytest

import model.benchmarking as benchmarking
from model.benchmarking import (
    SAFE_BENCHMARK_PRESETS,
    BenchmarkCase,
    build_benchmark_input,
    run_benchmark,
    run_benchmark_suite,
)


def test_benchmark_case_validates_dimensions_and_target():
    with pytest.raises(ValueError, match="people must be at least 2"):
        BenchmarkCase(1, 28, 10)
    with pytest.raises(ValueError, match="days must be at least 1"):
        BenchmarkCase(10, 0, 5)
    with pytest.raises(ValueError, match="shifts must be between 1 and 10"):
        BenchmarkCase(10, 28, 11)
    with pytest.raises(ValueError, match="target_seconds must be greater than 0"):
        BenchmarkCase(10, 28, 5, target_seconds=0)
    with pytest.raises(TypeError, match="people must be an integer"):
        BenchmarkCase(True, 28, 5)


def test_safe_presets_preserve_the_historical_cli_sweep():
    assert [(c.people, c.days, c.shifts) for c in SAFE_BENCHMARK_PRESETS] == [
        (10, 14, 5),
        (20, 28, 8),
        (45, 28, 10),
    ]
    assert all(c.target_seconds == 60 for c in SAFE_BENCHMARK_PRESETS)


def test_build_benchmark_input_matches_the_case_dimensions():
    case = BenchmarkCase(20, 28, 8)

    data = build_benchmark_input(case)

    assert data.start_date == date(2025, 1, 1)
    assert (data.end_date - data.start_date).days + 1 == case.days
    assert len(data.juniors) + len(data.seniors) == case.people
    assert len(data.shifts) == case.shifts
    assert data.min_gap == 1
    assert set(data.nf_juniors) <= set(data.juniors)
    assert set(data.nf_seniors) <= set(data.seniors)


class _Frame:
    def __init__(self, status):
        self.attrs = {"solver_status": status}


def test_run_benchmark_returns_structured_timing(monkeypatch):
    calls = []
    ticks = iter((100.0, 102.5))
    monkeypatch.setattr(benchmarking, "ORTOOLS_AVAILABLE", True)
    monkeypatch.setattr(benchmarking.time, "perf_counter", lambda: next(ticks))

    def fake_build(data, env):
        calls.append((data, env))
        return _Frame("FEASIBLE")

    monkeypatch.setattr(benchmarking, "build_schedule", fake_build)
    case = BenchmarkCase(10, 14, 5, target_seconds=3)

    result = run_benchmark(case, env="test")

    assert result.case is case
    assert result.elapsed_seconds == 2.5
    assert result.solver_status == "FEASIBLE"
    assert result.within_target is True
    assert result.flag == "OK"
    assert calls[0][1] == "test"


def test_run_benchmark_marks_slow_and_preserves_missing_status(monkeypatch):
    ticks = iter((10.0, 12.0))
    monkeypatch.setattr(benchmarking, "ORTOOLS_AVAILABLE", True)
    monkeypatch.setattr(benchmarking.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(benchmarking, "build_schedule", lambda data, env: _Frame(None))

    result = run_benchmark(BenchmarkCase(10, 14, 5, target_seconds=1))

    assert result.solver_status is None
    assert result.within_target is False
    assert result.flag == "SLOW"


def test_run_benchmark_rejects_stub_timings(monkeypatch):
    monkeypatch.setattr(benchmarking, "ORTOOLS_AVAILABLE", False)

    with pytest.raises(RuntimeError, match="timings would be meaningless"):
        run_benchmark(BenchmarkCase(10, 14, 5))


def test_run_benchmark_suite_keeps_order_and_environment(monkeypatch):
    seen = []

    def fake_run(case, *, env):
        seen.append((case, env))
        return benchmarking.BenchmarkResult(case, 0.1, "OPTIMAL")

    monkeypatch.setattr(benchmarking, "run_benchmark", fake_run)
    cases = [BenchmarkCase(10, 14, 5), BenchmarkCase(20, 28, 8)]

    results = run_benchmark_suite(cases, env="dev")

    assert [result.case for result in results] == cases
    assert seen == [(cases[0], "dev"), (cases[1], "dev")]
