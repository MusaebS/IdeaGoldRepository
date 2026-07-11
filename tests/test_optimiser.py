import sys, os
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    import pandas as pd
except Exception:
    from model import optimiser as opt
    pd = opt.pd

import pytest

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule, respects_min_gap


def test_simple_schedule():
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="Shift1", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    df = build_schedule(data)
    assert len(df) == 2
    assert set(df["Shift1"]) <= {"A", "B", "Unfilled"}
    assert list(df["Day"]) == ["Sunday", "Monday"]


def test_schedule_with_strict_cpmodel(strict_cp):
    """Scheduler should not fail if CpModel disallows new attributes."""

    from model import optimiser as opt

    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 1),
        shifts=[ShiftTemplate(label="S1", role="Junior", night_float=False, thu_weekend=False)],
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )

    df = opt.build_schedule(data)
    assert len(df) == 1


def test_role_and_gap_constraints():
    pytest.importorskip("ortools")  # needs the real solver to assign B
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="S1", role="Senior", night_float=False, thu_weekend=False)],
        juniors=["A"],  # a junior can't cover a senior shift
        seniors=["B"],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=2,
        nf_block_length=1,
    )

    df = build_schedule(data)
    # Only B (a Senior) is eligible; min_gap=2 prevents B working both days, so
    # one day is B and the other falls to Unfilled.
    assert set(df["S1"]) == {"B", "Unfilled"}


def test_respects_min_gap_function():
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S1": "A"},
        {"Date": date(2023, 1, 2), "S1": "A"},
    ])
    assert not respects_min_gap(df, 2)

    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S1": "A"},
        {"Date": date(2023, 1, 3), "S1": "A"},
    ])
    assert not respects_min_gap(df, 2)

    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S1": "A"},
        {"Date": date(2023, 1, 4), "S1": "A"},
    ])
    assert respects_min_gap(df, 2)


def test_respects_min_gap_with_day_column():
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "Day": "Sunday", "S1": "A"},
        {"Date": date(2023, 1, 4), "Day": "Wednesday", "S1": "A"},
    ])
    assert respects_min_gap(df, 2)


def _points_by_resident(df: pd.DataFrame, shifts: list[ShiftTemplate]) -> dict:
    pts: dict[str, float] = {}
    for row in df.to_dict("records"):
        for s in shifts:
            p = row.get(s.label)
            if p and p != "Unfilled":
                pts[p] = pts.get(p, 0.0) + s.points
    return pts


def test_total_points_balanced(balanced_cp):
    from model import optimiser as opt

    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=1.0,
    )

    df = opt.build_schedule(data, env="test")
    pts = _points_by_resident(df, shifts)
    assert abs(pts.get("A", 0) - pts.get("B", 0)) <= 1


def test_total_points_min_deviation(balanced_cp):
    from model import optimiser as opt

    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=1.5,
    )

    df = opt.build_schedule(data, env="test")
    pts = _points_by_resident(df, shifts)
    diff = abs(pts.get("A", 0) - pts.get("B", 0))
    assert diff == 1


def test_intvar_upper_bound_multiple_shifts(recording_cp):
    from model import optimiser as opt

    bounds = recording_cp

    shifts = [
        ShiftTemplate(label="S1", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="S2", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 1),
        shifts=shifts,
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=3.0,
    )

    opt.build_schedule(data, env="test")
    days = (data.end_date - data.start_date).days + 1
    expected = days * int(100 * sum(s.points for s in shifts))
    assert expected in bounds


def test_total_points_balanced_multiple_shifts(balanced_cp):
    from model import optimiser as opt

    shifts = [
        ShiftTemplate(label="D1", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="D2", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )

    df = opt.build_schedule(data, env="test")
    pts = _points_by_resident(df, shifts)
    assert abs(pts.get("A", 0) - pts.get("B", 0)) <= 1


def test_default_targets_balance_points(balanced_cp):
    from model import optimiser as opt
    from model.fairness import calculate_points

    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 6),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )

    df = opt.build_schedule(data, env="test")
    pts = calculate_points(df, data)
    totals = [v["total"] for v in pts.values()]
    weekends = [v["weekend"] for v in pts.values()]

    assert max(totals) - min(totals) <= 1
    assert max(weekends) - min(weekends) <= 1


def test_fairness_log_includes_unused_resident():
    from model.fairness import format_fairness_log

    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S": "A"},
        {"Date": date(2023, 1, 2), "S": "A"},
    ])
    log = format_fairness_log(df, data)
    lines = log.splitlines()
    assert any(line.startswith("A (Junior): total 2.0") for line in lines)
    assert any(line.startswith("B (Junior): total 0.0") for line in lines)


def test_auto_target_computation():
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    assert data.target_total is None
    assert data.target_weekend is None
    df = build_schedule(data, env="test")
    # build_schedule must not mutate the caller's InputData; the resolved
    # auto-targets are exposed on df.attrs instead.
    assert data.target_total is None
    assert data.target_weekend is None
    assert df.attrs["target_total"] == 0.5
    assert df.attrs["target_weekend"] == {"A": 0.0, "B": 0.0}


def test_rotator_targets_scaled_by_availability():
    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 4),  # 4-day block
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[("A", date(2023, 1, 1), date(2023, 1, 2))],  # A present 2 of 4 days
        min_gap=0,
    )
    df = build_schedule(data, env="test")
    # total points = 4 days * 1.0; weights A=2, B=4 -> A carries 1/3, B carries 2/3
    total_map = df.attrs["target_total_map"]
    target_weekend = df.attrs["target_weekend"]
    assert total_map is not None
    assert abs(total_map["A"] - 4 * 2 / 6) < 1e-6
    assert abs(total_map["B"] - 4 * 4 / 6) < 1e-6
    # weekend targets follow the same availability ratio (2 : 4)
    assert abs(target_weekend["A"] * 4 - target_weekend["B"] * 2) < 1e-6
    # caller's InputData is left untouched
    assert data.target_total_map is None
    assert data.target_weekend is None


def test_rotator_excluded_outside_window():
    pytest.importorskip("ortools")
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=shifts,
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[("A", date(2023, 1, 1), date(2023, 1, 1))],  # only day 0
        min_gap=0,
        nf_block_length=1,
    )
    rows = build_schedule(data, env="test").to_dict("records")
    assert rows[0]["S"] == "A"          # available and only eligible resident
    assert rows[1]["S"] == "Unfilled"   # outside rotator window -> cannot work


def test_leave_excluded_inside_window():
    pytest.importorskip("ortools")
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=shifts,
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[("A", date(2023, 1, 1), date(2023, 1, 1))],  # leave on day 0
        rotators=[],
        min_gap=0,
        nf_block_length=1,
    )
    rows = build_schedule(data, env="test").to_dict("records")
    assert rows[0]["S"] == "Unfilled"   # on leave -> cannot work
    assert rows[1]["S"] == "A"          # available the next day


def test_diagnose_infeasibility_lists_nf_pool():
    from model.optimiser import diagnose_infeasibility

    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),
        shifts=[ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)],
        juniors=["A"],
        seniors=[],
        nf_juniors=[],          # no eligible NF residents
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=2,
    )
    text = " ".join(diagnose_infeasibility(data)).lower()
    assert "no eligible" in text


def test_infeasible_schedule_raises_with_hints():
    pytest.importorskip("ortools")
    # A mandatory extra-points floor that cannot fit (only one 1-point slot to
    # earn 5) makes the model genuinely infeasible.
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 1),
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        extra_points={"A": 5.0},
    )
    with pytest.raises(RuntimeError):
        build_schedule(data, env="test")


def test_known_optimal_block_is_perfectly_balanced():
    """Two residents over an even number of identical single-point days: the
    optimal schedule splits the load exactly, so the total range must be 0."""
    pytest.importorskip("ortools")
    from model.fairness import calculate_points

    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),  # Monday
        end_date=date(2023, 1, 5),    # 4 days
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = build_schedule(data, env="prod")
    pts = calculate_points(df, data)
    totals = [v["total"] for v in pts.values()]
    assert max(totals) - min(totals) == 0  # 2 points each, perfectly balanced
    assert all(row["S"] != "Unfilled" for row in df.to_dict("records"))


def test_max_total_cap_is_enforced():
    pytest.importorskip("ortools")
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 6),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        max_total={"A": 2.0},  # A may work at most 2 points total
    )
    df = build_schedule(data, env="test")
    from model.fairness import calculate_points

    assert calculate_points(df, data)["A"]["total"] <= 2.0


def test_uncompensated_leave_scales_quota_down():
    # B is on uncompensated leave half the block, so B's fair-share target is
    # scaled down and A's up (whereas a compensated leave keeps full quotas).
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 4),  # 4-day block
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[("B", date(2023, 1, 1), date(2023, 1, 2), False)],  # uncompensated, 2 of 4 days
        rotators=[],
        min_gap=0,
    )
    df = build_schedule(data, env="test")
    tmap = df.attrs["target_total_map"]
    # total points = 4; weights A=4, B=2 -> A target 4*4/6, B target 4*2/6
    assert abs(tmap["A"] - 4 * 4 / 6) < 1e-6
    assert abs(tmap["B"] - 4 * 2 / 6) < 1e-6


def test_compensated_leave_keeps_full_quota():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 4),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[("B", date(2023, 1, 1), date(2023, 1, 2), True)],  # compensated
        rotators=[],
        min_gap=0,
    )
    df = build_schedule(data, env="test")
    tmap = df.attrs["target_total_map"]
    # compensated -> equal weights -> equal targets (2.0 each)
    assert abs(tmap["A"] - 2.0) < 1e-6
    assert abs(tmap["B"] - 2.0) < 1e-6


def test_extra_points_are_enforced():
    pytest.importorskip("ortools")
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 30),  # 30 points across 3 residents
        shifts=shifts,
        juniors=["A", "B", "C"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        extra_points={"A": 3.0},
    )
    df = build_schedule(data, env="test")
    from model.fairness import calculate_points

    pts = calculate_points(df, data)
    # A must carry ~3 points above peers, and at least the raised target.
    assert pts["A"]["total"] >= pts["B"]["total"] + 2
    assert pts["A"]["total"] >= pts["C"]["total"] + 2
    assert df.attrs["target_total_map"]["A"] > df.attrs["target_total_map"]["B"]


def test_weekday_point_override_changes_load():
    from model.fairness import calculate_points
    # "night" is worth 2 on Tuesdays; a Tuesday-only schedule should score double.
    shifts = [ShiftTemplate(label="night", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    df = pd.DataFrame([{"Date": date(2023, 1, 3), "night": "A"}])  # Jan 3 2023 = Tuesday
    data = InputData(
        start_date=date(2023, 1, 3), end_date=date(2023, 1, 3), shifts=shifts,
        juniors=["A"], seniors=[], nf_juniors=[], nf_seniors=[], leaves=[], rotators=[],
        min_gap=0, weekday_points={("night", 1): 2.0},
    )
    assert calculate_points(df, data)["A"]["total"] == 2.0


def test_holiday_bonus_and_weekend_flag():
    from model.fairness import calculate_points
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    df = pd.DataFrame([{"Date": date(2023, 1, 4), "S": "A"}])  # Jan 4 2023 = Wednesday
    # Holiday: +1.5 bonus, counts as weekend
    data = InputData(
        start_date=date(2023, 1, 4), end_date=date(2023, 1, 4), shifts=shifts,
        juniors=["A"], seniors=[], nf_juniors=[], nf_seniors=[], leaves=[], rotators=[],
        min_gap=0, holidays=[(date(2023, 1, 4), 1.5, True)],
    )
    pts = calculate_points(df, data)
    assert pts["A"]["total"] == 2.5       # 1 + 1.5 bonus
    assert pts["A"]["weekend"] == 2.5     # holiday flagged as weekend

    # Same holiday but NOT counted as weekend
    data2 = InputData(
        start_date=date(2023, 1, 4), end_date=date(2023, 1, 4), shifts=shifts,
        juniors=["A"], seniors=[], nf_juniors=[], nf_seniors=[], leaves=[], rotators=[],
        min_gap=0, holidays=[(date(2023, 1, 4), 1.5, False)],
    )
    pts2 = calculate_points(df, data2)
    assert pts2["A"]["total"] == 2.5
    assert pts2["A"]["weekend"] == 0.0    # weekday, not counted


def test_weekday_override_raises_target():
    # A single higher-value day should not overflow bounds; targets reflect it.
    pytest.importorskip("ortools")
    shifts = [ShiftTemplate(label="night", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1), end_date=date(2023, 1, 7), shifts=shifts,
        juniors=["A", "B"], seniors=[], nf_juniors=[], nf_seniors=[], leaves=[], rotators=[],
        min_gap=0, weekday_points={("night", 1): 5.0},  # Tuesday worth 5
    )
    df = build_schedule(data, env="test")  # must not raise / overflow
    # total available = 6 normal days*1 + 1 Tuesday*5 = 11 points, split fairly
    assert abs(df.attrs["target_total_map"]["A"] - 11 / 2) < 1e-6


# --- resolve_targets (unit tests; previously only reachable via df.attrs) ----

def _rt_data(**kw):
    from model.data_models import InputData, ShiftTemplate
    base = dict(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 5),  # 4 days
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    base.update(kw)
    return InputData(**base)


def test_resolve_targets_auto_equal_split():
    from model.optimiser import resolve_targets

    data = _rt_data()
    resolved = resolve_targets(data)
    assert resolved.target_total == 2.0  # 4 points / 2 residents
    assert resolved.target_total_map == {"A": 2.0, "B": 2.0}
    assert data.target_total is None  # caller's InputData untouched


def test_resolve_targets_respects_explicit_target():
    from model.optimiser import resolve_targets

    data = _rt_data(target_total=3.0)
    resolved = resolve_targets(data)
    assert resolved.target_total == 3.0
    assert resolved.target_total_map is None  # not auto-built when total given


def test_resolve_targets_rotator_scales_share():
    from model.optimiser import resolve_targets

    # B active only 2 of 4 days -> weights A=4, B=2 -> shares 4*(4/6), 4*(2/6).
    data = _rt_data(rotators=[("B", date(2023, 1, 2), date(2023, 1, 3))])
    resolved = resolve_targets(data)
    assert resolved.target_total_map["A"] == pytest.approx(4 * 4 / 6)
    assert resolved.target_total_map["B"] == pytest.approx(4 * 2 / 6)


def test_resolve_targets_uncompensated_leave_scales_share():
    from model.optimiser import resolve_targets

    data = _rt_data(leaves=[("B", date(2023, 1, 2), date(2023, 1, 3), False)])
    resolved = resolve_targets(data)
    assert resolved.target_total_map["A"] == pytest.approx(4 * 4 / 6)
    assert resolved.target_total_map["B"] == pytest.approx(4 * 2 / 6)
    # A compensated leave keeps the full share.
    comp = _rt_data(leaves=[("B", date(2023, 1, 2), date(2023, 1, 3), True)])
    assert resolve_targets(comp).target_total_map == {"A": 2.0, "B": 2.0}


def test_resolve_targets_ledger_carryover_lightens_loaded_resident():
    from model.optimiser import resolve_targets

    ledger = {"A": {"total": 4.0, "weekend": 0.0, "night_float": 0.0},
              "B": {"total": 0.0, "weekend": 0.0, "night_float": 0.0}}
    resolved = resolve_targets(_rt_data(), ledger=ledger)
    # Cumulative fair share is 4 (prior) + 4 (block) = 8 -> 4 each; A already
    # carries 4 so gets 0 this block, B gets the full 4.
    assert resolved.target_total_map["A"] == pytest.approx(0.0)
    assert resolved.target_total_map["B"] == pytest.approx(4.0)


def test_resolve_targets_extra_points_reconcile():
    from model.optimiser import resolve_targets

    resolved = resolve_targets(_rt_data(extra_points={"A": 2.0}))
    tmap = resolved.target_total_map
    # A's target is raised by the penalty, B's lowered, totals still sum to 4.
    assert tmap["A"] > tmap["B"]
    assert tmap["A"] - 2.0 == pytest.approx(tmap["B"])
    assert sum(tmap.values()) == pytest.approx(4.0)


def test_resolve_targets_group_factor_scales_share():
    from model.optimiser import resolve_targets

    # B in a 0.5 group -> weights A=4, B=2 over the 4-day block.
    data = _rt_data(group_factors={"half": 0.5}, resident_groups={"B": "half"})
    resolved = resolve_targets(data)
    assert resolved.target_total_map["A"] == pytest.approx(4 * 4 / 6)
    assert resolved.target_total_map["B"] == pytest.approx(4 * 2 / 6)


def test_resolve_targets_perk_window_scales_share():
    from model.data_models import Perk
    from model.optimiser import resolve_targets

    # Perk 0.5 covers 2 of B's 4 days -> weights A=4, B=3.
    data = _rt_data(perks=[Perk("B", 0.5, date(2023, 1, 2), date(2023, 1, 3))])
    resolved = resolve_targets(data)
    assert resolved.target_total_map["A"] == pytest.approx(4 * 4 / 7)
    assert resolved.target_total_map["B"] == pytest.approx(4 * 3 / 7)


def test_exemption_leaves_targets_unchanged():
    from model.optimiser import resolve_targets

    resolved = resolve_targets(_rt_data(exempt_shifts={"B": ["S"]}))
    assert resolved.target_total_map == {"A": 2.0, "B": 2.0}


def _two_label_data(**kw):
    from model.data_models import ShiftTemplate
    return _rt_data(
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ],
        **kw,
    )


def test_resolve_targets_label_carryover_lightens_label_loaded_resident():
    from model.optimiser import resolve_targets

    # Equal totals (the total dimension is neutral); A's history is all N,
    # B's all D — repayment in kind swaps their label targets this block.
    ledger = {
        "A": {"total": 4.0, "weekend": 0.0, "labels": {"N": 4.0}},
        "B": {"total": 4.0, "weekend": 0.0, "labels": {"D": 4.0}},
    }
    resolved = resolve_targets(_two_label_data(), ledger=ledger)
    tl = resolved.target_label
    # Cumulative N pool = 4 block + 4 prior -> 4 each; A already carries 4 so
    # gets 0 now, B gets the whole block's N (clamped to the block pool).
    assert tl[("A", "N")] == pytest.approx(0.0)
    assert tl[("B", "N")] == pytest.approx(4.0)
    assert tl[("A", "D")] == pytest.approx(4.0)
    assert tl[("B", "D")] == pytest.approx(0.0)


def test_resolve_targets_label_carryover_toggle_off_matches_per_block():
    from model.optimiser import resolve_targets

    ledger = {
        "A": {"total": 4.0, "weekend": 0.0, "labels": {"N": 4.0}},
        "B": {"total": 4.0, "weekend": 0.0, "labels": {"D": 4.0}},
    }
    off = resolve_targets(_two_label_data(), ledger=ledger, label_carryover=False)
    plain = resolve_targets(_two_label_data())
    assert off.target_label == plain.target_label


def test_resolve_targets_label_carryover_ignores_stale_and_new_labels():
    from model.optimiser import resolve_targets

    # "Old" survives only in the history (a renamed/removed shift): no target
    # is built for it. The current labels carry no history, so they fall back
    # to the plain per-block fair share. A legacy ledger without any label
    # history behaves the same way.
    ledger = {
        "A": {"total": 2.0, "weekend": 0.0, "labels": {"Old": 2.0}},
        "B": {"total": 2.0, "weekend": 0.0},
    }
    resolved = resolve_targets(_two_label_data(), ledger=ledger)
    tl = resolved.target_label
    assert set(lbl for _, lbl in tl) == {"D", "N"}
    for person in ("A", "B"):
        assert tl[(person, "N")] == pytest.approx(2.0)  # 4 N points / 2 residents
        assert tl[(person, "D")] == pytest.approx(2.0)


def test_solver_never_assigns_exempt_resident():
    pytest.importorskip("ortools")
    data = _rt_data(exempt_shifts={"B": ["S"]}, min_gap=0)
    df = build_schedule(data, env="test")
    assert "B" not in set(df["S"])
    assert set(df["S"]) <= {"A", "Unfilled"}
