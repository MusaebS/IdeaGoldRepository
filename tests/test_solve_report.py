import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.solve_report import convergence_verdict


def test_optimal_needs_no_more_time():
    v = convergence_verdict("OPTIMAL", 3.0, 300, 3.0)
    assert v.level == "optimal"
    assert v.suggested_limit is None
    assert "optimal" in v.headline.lower()


def test_still_improving_suggests_a_larger_limit():
    # Last improvement in the final quarter of a run that used its whole budget.
    v = convergence_verdict("FEASIBLE", 400.0, 400, 385.0)
    assert v.level == "improving"
    assert v.suggested_limit == 600            # 400 * 1.5, tidy 30s step
    assert "600s" in v.detail


def test_converged_when_improvements_went_quiet_early():
    v = convergence_verdict("FEASIBLE", 400.0, 400, 120.0)
    assert v.level == "converged"
    assert v.suggested_limit is None
    assert "unlikely to help" in v.detail


def test_finished_before_limit_is_converged():
    v = convergence_verdict("FEASIBLE", 40.0, 400, 30.0)
    assert v.level == "converged"
    assert v.suggested_limit is None


def test_hit_limit_without_callback_info_advises_more_time():
    v = convergence_verdict("FEASIBLE", 400.0, 400, None)
    assert v.level == "improving"
    assert v.suggested_limit == 600


def test_unknown_status_is_neutral():
    v = convergence_verdict(None, None, None, None)
    assert v.level == "unknown"
    assert v.suggested_limit is None


def test_suggested_limit_scales_with_the_run_length():
    small = convergence_verdict("FEASIBLE", 60.0, 60, 58.0)
    big = convergence_verdict("FEASIBLE", 600.0, 600, 590.0)
    assert small.suggested_limit == 90        # 60 * 1.5
    assert big.suggested_limit == 900         # 600 * 1.5
