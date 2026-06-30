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
from model.nf_blocks import respects_nf_blocks


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
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 2),
        shifts=[ShiftTemplate(label="S1", role="Senior", night_float=True, thu_weekend=False)],
        juniors=["A"],
        seniors=["B"],
        nf_juniors=["A"],
        nf_seniors=[],  # B cannot cover night float
        leaves=[],
        rotators=[],
        min_gap=2,
        nf_block_length=1,
    )

    df = build_schedule(data)
    # Only unfilled is eligible due to NF restriction; also min_gap prevents A working both days
    assert set(df["S1"]) == {"Unfilled"}


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


def test_respects_nf_blocks_function():
    shifts = [ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)]
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},
        {"Date": date(2023, 1, 3), "NF": "A"},
    ])
    assert respects_nf_blocks(df, 3, shifts)

    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},
        {"Date": date(2023, 1, 3), "NF": "B"},
    ])
    assert not respects_nf_blocks(df, 3, shifts)


def test_nf_blocks_exact_assignment():
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),
        shifts=[ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)],
        juniors=["A"],
        seniors=[],
        nf_juniors=["A"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=2,
    )

    df = build_schedule(data, env="test")
    rows = df.to_dict("records")
    assert rows[2]["NF"] == "Unfilled"
    assert rows[0]["NF"] == rows[1]["NF"]
    assert respects_nf_blocks(df, 2, data.shifts)


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
    assert any(line.startswith("A (Junior, NF 0.0): total 2.0") for line in lines)
    assert any(line.startswith("B (Junior, NF 0.0): total 0.0") for line in lines)


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


def test_nf_trailing_partial_block_is_covered():
    pytest.importorskip("ortools")
    shifts = [ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),  # 3 days, block_length 2 -> 1-day trailing block
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=["A", "B"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=2,
    )
    rows = build_schedule(data, env="test").to_dict("records")
    assert rows[0]["NF"] == rows[1]["NF"]      # the full 2-day block
    assert rows[0]["NF"] != "Unfilled"
    assert rows[2]["NF"] != "Unfilled"         # trailing night now covered, not dropped


def test_nf_blocks_allows_short_trailing_run():
    shifts = [ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)]
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},
        {"Date": date(2023, 1, 3), "NF": "A"},  # A: run of 3 == block_length
        {"Date": date(2023, 1, 4), "NF": "B"},  # B: run of 1, ends on last day -> allowed
    ])
    assert respects_nf_blocks(df, 3, shifts)


def test_nf_blocks_rejects_short_non_trailing_run():
    shifts = [ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)]
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},  # A: run of 2, NOT on last day -> invalid
        {"Date": date(2023, 1, 3), "NF": "B"},
        {"Date": date(2023, 1, 4), "NF": "B"},
        {"Date": date(2023, 1, 5), "NF": "B"},  # B: run of 3 == block_length
    ])
    assert not respects_nf_blocks(df, 3, shifts)


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
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 3),
        shifts=[ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False)],
        juniors=["A"],
        seniors=[],
        nf_juniors=[],          # NF shift cannot be covered -> infeasible
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=2,
    )
    with pytest.raises(RuntimeError) as exc:
        build_schedule(data, env="test")
    message = str(exc.value).lower()
    assert "night-float" in message or "no eligible" in message


def test_nf_block_feasible_with_positive_min_gap():
    pytest.importorskip("ortools")
    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=1.0),
        ShiftTemplate(label="DayShift", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 10),  # 10 days, two NF blocks of 5
        shifts=shifts,
        juniors=["A", "B", "C", "D"],
        seniors=[],
        nf_juniors=["A", "B", "C", "D"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,            # previously forced NF blocks infeasible
        nf_block_length=5,
    )
    rows = build_schedule(data, env="dev").to_dict("records")  # must not raise
    # NF block integrity preserved (and no INFEASIBLE error -> the fix)
    assert rows[0]["NF"] == rows[1]["NF"] == rows[2]["NF"] == rows[3]["NF"] == rows[4]["NF"]


def test_respects_min_gap_rejects_shift_right_after_nf_block():
    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False),
        ShiftTemplate(label="Reg", role="Junior", night_float=False, thu_weekend=False),
    ]
    # A finishes a 2-night NF block (Jan 1-2) and works a regular shift Jan 3 -> no rest
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "Day": "Sun", "NF": "A", "Reg": "B"},
        {"Date": date(2023, 1, 2), "Day": "Mon", "NF": "A", "Reg": "B"},
        {"Date": date(2023, 1, 3), "Day": "Tue", "NF": "B", "Reg": "A"},
    ])
    assert not respects_min_gap(df, 1, shifts)


def test_respects_min_gap_allows_rest_around_nf_block():
    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False),
        ShiftTemplate(label="Reg", role="Junior", night_float=False, thu_weekend=False),
    ]
    # A: NF block Jan 1-2, rests Jan 3, regular Jan 4 (2 days after block end -> ok)
    # E: NF block Jan 3-4 (Jan 2 idle -> pre-rest ok); C/D regulars spaced
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "Day": "Sun", "NF": "A", "Reg": "C"},
        {"Date": date(2023, 1, 2), "Day": "Mon", "NF": "A", "Reg": "D"},
        {"Date": date(2023, 1, 3), "Day": "Tue", "NF": "E", "Reg": "C"},
        {"Date": date(2023, 1, 4), "Day": "Wed", "NF": "E", "Reg": "A"},
    ])
    assert respects_min_gap(df, 1, shifts)


def test_solver_enforces_rest_after_nf_block():
    pytest.importorskip("ortools")
    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=1.0),
        ShiftTemplate(label="Reg", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 10),
        shifts=shifts,
        juniors=["A", "B", "C", "D", "E"],
        seniors=[],
        nf_juniors=["A", "B", "C", "D", "E"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
        nf_block_length=5,
    )
    df = build_schedule(data, env="dev")  # build_schedule raises if rest is violated
    assert respects_min_gap(df, data.min_gap, data.shifts)


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


def test_night_float_target_computed_per_eligible_pool():
    # Target computation runs even on the stub path (no ortools needed).
    shifts = [ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 4),  # 4 days -> 4 night-float points
        shifts=shifts,
        juniors=["A", "B", "C"],
        seniors=[],
        nf_juniors=["A", "B"],  # C is a junior but not NF-eligible
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=1,
    )
    df = build_schedule(data, env="test")
    tnf = df.attrs["target_night_float"]
    assert tnf == {"A": 2.0, "B": 2.0}  # split among the eligible pool only
    assert "C" not in tnf


def test_night_float_load_is_balanced():
    pytest.importorskip("ortools")
    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=1.0),
        ShiftTemplate(label="DayShift", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    # Totals can be balanced by dumping all nights on one resident; the night-float
    # objective should instead split the nights evenly.
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 4),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=["A", "B"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=1,
    )
    df = build_schedule(data, env="test")
    from model.fairness import calculate_points

    pts = calculate_points(df, data)
    nf = [pts["A"]["night_float"], pts["B"]["night_float"]]
    assert max(nf) - min(nf) <= 1  # nights shared, not dumped on one resident


def test_max_nights_cap_is_enforced():
    pytest.importorskip("ortools")
    shifts = [
        ShiftTemplate(label="NF", role="Junior", night_float=True, thu_weekend=False, points=1.0),
        ShiftTemplate(label="DayShift", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 6),  # 6 nights available
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=["A", "B"],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=1,
        max_nights={"A": 1.0},  # A may work at most 1 night
    )
    df = build_schedule(data, env="test")
    from model.fairness import calculate_points

    assert calculate_points(df, data)["A"]["night_float"] <= 1.0


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
