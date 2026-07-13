import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.benchmark import BenchmarkResult, run_host_benchmark, suggested_time_limit


def test_suggestion_scales_inversely_with_host_speed():
    # A host at half speed should get roughly double the starting budget.
    fast = suggested_time_limit(45, 28, 10, speed_ratio=2.0)
    par = suggested_time_limit(45, 28, 10, speed_ratio=1.0)
    slow = suggested_time_limit(45, 28, 10, speed_ratio=0.5)
    assert fast < par < slow


def test_suggestion_without_a_ratio_uses_the_base_curve():
    # No measured ratio -> treat as an average host (ratio 1.0).
    assert suggested_time_limit(45, 28, 10) == suggested_time_limit(45, 28, 10, 1.0)


def test_suggestion_is_clamped_and_tidy():
    tiny = suggested_time_limit(3, 3, 1, speed_ratio=100.0)
    huge = suggested_time_limit(90, 60, 12, speed_ratio=0.01)
    assert tiny >= 30                     # floor
    assert huge <= 1800                   # ceiling
    assert tiny % 30 == 0 and huge % 30 == 0


def test_bigger_rosters_suggest_at_least_as_much_time():
    small = suggested_time_limit(6, 7, 2, speed_ratio=1.0)
    large = suggested_time_limit(60, 28, 10, speed_ratio=1.0)
    assert large >= small


def test_run_host_benchmark_returns_none_or_a_valid_result():
    # Under the no-ortools stub the reference solve records no wall time and
    # this returns None; with a real solver it returns a positive ratio.
    result = run_host_benchmark(trials=1)
    assert result is None or (
        isinstance(result, BenchmarkResult)
        and result.wall_time_sec > 0
        and result.speed_ratio > 0
    )
