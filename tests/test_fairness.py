import sys, os
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    import pandas as pd
except Exception:
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.fairness import (
    calculate_points,
    format_fairness_log,
    schedule_quality,
    assignment_rationale,
)


def _resident_line(log: str, name: str) -> str:
    """Return the per-resident line for ``name`` from a fairness log.

    Keys on the first token only, so tests stay stable across cosmetic
    format changes to the rest of the line.
    """
    for line in log.splitlines():
        if line.split(" ")[0] == name:
            return line
    raise AssertionError(f"no line for resident {name!r} in log:\n{log}")


def _sample_df_and_shifts():
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    df = pd.DataFrame([
        {"Date": date(2023, 1, 7), "D": "Alice", "N": "Bob"},
        {"Date": date(2023, 1, 8), "D": "Bob", "N": "Alice"},
    ])
    return df, shifts


def test_calculate_points():
    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    pts = calculate_points(df, data)
    assert pts == {
        "Alice": {
            "total": 3.0,
            "weekend": 3.0,
            "labels": {"D": 1.0, "N": 2.0},
            "night_float": 0.0,
        },
        "Bob": {
            "total": 3.0,
            "weekend": 3.0,
            "labels": {"D": 1.0, "N": 2.0},
            "night_float": 0.0,
        },
    }


def test_format_fairness_log():
    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=3.0,
    )
    log = format_fairness_log(df, data)
    expected_lines = [
        "Schedule health: 4/4 slots filled (0 unfilled).",
        "Points: 6.0 assigned + 0.0 unfilled = 6.0 available",
        "Alice (Junior): total 3.0 (target 3.0, dev +0.0), weekend 3.0, D 1.0, N 2.0",
        "Bob (Junior): total 3.0 (target 3.0, dev +0.0), weekend 3.0, D 1.0, N 2.0",
        "Total points min 3.0, max 3.0, range 0.0",
        "Weekend points min 3.0, max 3.0, range 0.0",
    ]
    assert log.splitlines() == expected_lines


def test_schedule_quality_perfect():
    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    q = schedule_quality(df, data)
    assert q["coverage"] == 1.0
    assert q["unfilled"] == 0
    assert q["score"] == 100.0


def test_schedule_quality_penalizes_unfilled_and_imbalance():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "Unfilled"},
    ])
    q = schedule_quality(df, data)
    assert q["unfilled"] == 1
    assert q["coverage"] == 0.5
    assert q["score"] < 100.0


def test_schedule_quality_allows_one_shift_granularity():
    # 3 residents, 2 slots: someone must end a whole shift apart — the
    # unavoidable one-slot difference must not count against balance.
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B", "C"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "B"},
    ])
    q = schedule_quality(df, data)
    assert q["total_range"] == 1.0  # the raw spread is still reported
    assert q["balance_total"] == 1.0  # but not penalized: one shift is atomic
    assert q["score"] == 100.0


def test_quality_diagnosis_flags_timeout_and_structure():
    from model.fairness import quality_diagnosis

    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 29),  # 28 days
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=6,  # weekly lock + capacity squeeze
    )
    df = pd.DataFrame([{"Date": date(2023, 1, 2), "S": "A"}])
    df.attrs["solver_status"] = "FEASIBLE"
    df.attrs["wall_time_sec"] = 59.5
    df.attrs["time_limit_sec"] = 60
    quality = {"unfilled": 3, "balance_total": 0.5}
    reasons = quality_diagnosis(df, data, quality)
    text = " ".join(reasons)
    assert "budget" in text  # solver stopped at the limit
    assert "unfilled" in text.lower()
    assert "weekend fairness is impossible" in text  # min_gap 6 structural lock
    # An OPTIMAL solve doesn't blame the time limit.
    df.attrs["solver_status"] = "OPTIMAL"
    assert not any("budget" in r for r in quality_diagnosis(df, data, quality))


def test_assignment_rationale_for_assigned_person():
    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    text = " ".join(assignment_rationale(df, data, date(2023, 1, 7), "D"))
    assert "Alice" in text
    assert "Junior" in text


def test_assignment_rationale_for_unfilled():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
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
    )
    df = pd.DataFrame([{"Date": date(2023, 1, 1), "S": "Unfilled"}])
    text = " ".join(assignment_rationale(df, data, date(2023, 1, 1), "S")).lower()
    assert "unfilled" in text or "eligible" in text


def test_weekend_days_configures_is_weekend():
    from model.utils import is_weekend

    shift = ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)
    friday = date(2023, 1, 6)  # a Friday (Jan 1 2023 is a Sunday)
    assert not is_weekend(friday, shift)            # default Sat/Sun
    assert is_weekend(friday, shift, [4, 5])        # Fri/Sat weekend -> Friday counts


def test_calculate_points_uses_configured_weekend():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    df = pd.DataFrame([
        {"Date": date(2023, 1, 6), "S": "A"},  # Friday
        {"Date": date(2023, 1, 7), "S": "A"},  # Saturday
    ])
    common = dict(
        start_date=date(2023, 1, 6), end_date=date(2023, 1, 7), shifts=shifts,
        juniors=["A"], seniors=[], nf_juniors=[], nf_seniors=[], leaves=[], rotators=[], min_gap=0,
    )
    default = InputData(**common)
    assert calculate_points(df, default)["A"]["weekend"] == 1.0  # only Saturday

    fri_sat = InputData(weekend_days=[4, 5], **common)
    assert calculate_points(df, fri_sat)["A"]["weekend"] == 2.0  # Friday + Saturday


def test_fairness_range_lines_report_nightfloat_duty_days():
    # night_float now holds an informational NF duty-day count, not points.
    from model.fairness import fairness_range_lines
    points = {
        "A": {"total": 5.0, "weekend": 1.0, "labels": {}, "night_float": 3},
        "B": {"total": 5.0, "weekend": 1.0, "labels": {}, "night_float": 1},
    }
    lines = fairness_range_lines(points)
    assert any("Night-float duty days" in line and "outside regular fairness" in line
               for line in lines)


def test_fairness_range_lines_omits_nightfloat_when_zero():
    from model.fairness import fairness_range_lines
    points = {"A": {"total": 1.0, "weekend": 0.0, "labels": {}, "night_float": 0}}
    assert not any("Night-float" in line for line in fairness_range_lines(points))


def test_fairness_log_lists_unfilled_slots_and_health():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "Unfilled"},
    ])
    log = format_fairness_log(df, data)
    assert "Schedule health: 1/2 slots filled (1 unfilled)." in log
    assert "Unfilled slots:" in log
    assert any("2023-01-03 — S" in line for line in log.splitlines())


def test_fairness_log_flags_overloaded_resident():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 6),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        target_total=2.0,  # A ends well over, B well under
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "A"},
        {"Date": date(2023, 1, 4), "S": "A"},
        {"Date": date(2023, 1, 5), "S": "A"},  # A = 4 (dev +2 -> OVER); B = 0 (dev -2 -> UNDER)
    ])
    log = format_fairness_log(df, data)
    assert "[OVER]" in _resident_line(log, "A")
    assert "[UNDER]" in _resident_line(log, "B")


def test_fairness_log_surfaces_constraint_violations():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
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
        min_gap=2,  # A on consecutive days violates this
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S": "A"},
        {"Date": date(2023, 1, 2), "S": "A"},
    ])
    log = format_fairness_log(df, data)
    assert "Constraint violations:" in log
    assert any("Minimum gap" in line for line in log.splitlines())


def test_fairness_log_checksum_reconciles():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "Unfilled"},
    ])
    log = format_fairness_log(df, data)
    assert "Points: 1.0 assigned + 1.0 unfilled = 2.0 available" in log
    assert "MISMATCH" not in log


def test_fairness_log_annotates_penalty():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 5),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        extra_points={"A": 2.0},
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "A"},
        {"Date": date(2023, 1, 4), "S": "A"},
        {"Date": date(2023, 1, 5), "S": "B"},
    ])
    log = format_fairness_log(df, data)
    assert "[+2 penalty applied]" in _resident_line(log, "A")
    assert "penalty applied" not in _resident_line(log, "B")


def test_fairness_log_annotates_group_perk_exemption():
    from model.data_models import Perk

    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        group_factors={"R2": 0.9},
        resident_groups={"A": "R2"},
        perks=[Perk("A", 0.8, None, date(2023, 1, 31))],
        exempt_shifts={"B": ["S"]},
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "B"},
    ])
    log = format_fairness_log(df, data)
    a_line = _resident_line(log, "A")
    assert "[R2 ×0.90]" in a_line
    assert "[perk ×0.80 →2023-01-31]" in a_line
    b_line = _resident_line(log, "B")
    assert "[exempt: S]" in b_line
    assert "[R2" not in b_line


def test_fairness_log_line_unchanged_without_load_features():
    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    log = format_fairness_log(df, data)
    assert "[" not in _resident_line(log, "Alice")  # no annotations rendered


def test_load_annotation_notes_summarise_leaves():
    from model.fairness import load_annotation_notes

    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 28),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        # The second window starts before the block: only in-block days count.
        leaves=[
            ("Alice", date(2023, 1, 5), date(2023, 1, 7), True),
            ("Alice", date(2022, 12, 30), date(2023, 1, 2), False),
        ],
        rotators=[],
        min_gap=0,
    )
    assert load_annotation_notes("Alice", data) == ["[leave 3d comp]", "[leave 2d uncomp]"]
    assert load_annotation_notes("Bob", data) == []
    log = format_fairness_log(df, data)
    assert "[leave 3d comp]" in _resident_line(log, "Alice")


def test_calculate_label_counts():
    from model.fairness import calculate_label_counts

    df, shifts = _sample_df_and_shifts()
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice", "Bob", "Cara"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    counts = calculate_label_counts(df, data)
    assert counts["Alice"] == {"D": 1, "N": 1}
    assert counts["Bob"] == {"D": 1, "N": 1}
    assert counts["Cara"] == {}  # rostered but never assigned
