import sys, os
import io
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest

try:
    import pandas as pd
except Exception:  # pragma: no cover - fallback when pandas missing
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.fairness import calculate_points
from model.exporters import (
    build_fairness_frame,
    schedule_to_excel_bytes,
    schedule_to_pdf_bytes,
)


def _sample():
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Senior", night_float=True, thu_weekend=False, points=2.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 8),
        shifts=shifts,
        juniors=["Alice"],
        seniors=["Bob"],
        nf_juniors=[],
        nf_seniors=["Bob"],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 7), "Day": "Saturday", "D": "Alice", "N": "Bob"},
        {"Date": date(2023, 1, 8), "Day": "Sunday", "D": "Alice", "N": "Bob"},
    ])
    return df, data


def _df_and_data(**overrides):
    """Two-day, two-shift fixture; kwargs override the InputData fields.

    Passing ``seniors=["Bob"]`` moves Bob out of the junior list automatically
    so role-split tests don't have to restate the roster.
    """
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    base = dict(
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
    base.update(overrides)
    if overrides.get("seniors") and "juniors" not in overrides:
        base["juniors"] = [p for p in base["juniors"] if p not in base["seniors"]]
    data = InputData(**base)
    df = pd.DataFrame([
        {"Date": date(2023, 1, 7), "Day": "Saturday", "D": "Alice", "N": "Bob"},
        {"Date": date(2023, 1, 8), "Day": "Sunday", "D": "Bob", "N": "Alice"},
    ])
    return df, data


def test_build_fairness_frame():
    df, data = _sample()
    points = calculate_points(df, data)
    frame = build_fairness_frame(points, data)
    by_name = {row["Resident"]: row for row in frame.to_dict("records")}
    assert by_name["Alice"]["Role"] == "Junior"
    assert by_name["Bob"]["Role"] == "Senior"
    assert by_name["Alice"]["Total"] == 2.0      # D 1.0 x 2 days
    assert by_name["Bob"]["Total"] == 4.0        # N 2.0 x 2 days (NF-eligible but uncovered = regular)
    assert by_name["Bob"]["NF duty (days)"] == 0  # no NF coverage configured
    assert "D" in frame.columns and "N" in frame.columns


def test_schedule_to_excel_bytes():
    pytest.importorskip("openpyxl")
    df, data = _sample()
    blob = schedule_to_excel_bytes(df, data)
    assert isinstance(blob, (bytes, bytearray))
    assert blob[:2] == b"PK"  # .xlsx is a zip archive


def test_schedule_to_pdf_bytes():
    pytest.importorskip("reportlab")
    df, data = _sample()
    blob = schedule_to_pdf_bytes(df, data)
    assert isinstance(blob, (bytes, bytearray))
    assert blob[:4] == b"%PDF"  # PDF magic number


def test_exports_accept_cosmetic_columns_and_palette():
    pytest.importorskip("openpyxl")
    pytest.importorskip("reportlab")
    df, data = _sample()
    # A purely cosmetic column (like an on-call team label) plus a custom palette
    # must not break the exports; fairness is computed from points, not columns.
    df = df.copy()
    df["On-call team"] = ["Red", "Blue"]
    points = calculate_points(df, data)
    frame = build_fairness_frame(points, data, df)
    assert "On-call team" not in frame.columns  # cosmetic column stays cosmetic
    xl = schedule_to_excel_bytes(
        df, data, points=points, color_mode="auto", palette={"weekend": "#123456"}
    )
    pdf = schedule_to_pdf_bytes(
        df, data, points=points, color_mode="role", palette={"senior": "#654321"}
    )
    assert xl[:2] == b"PK" and pdf[:4] == b"%PDF"


def test_fairness_frame_with_df_adds_counts_targets_and_notes():
    df, data = _sample()
    df.attrs["target_total_map"] = {"Alice": 3.0, "Bob": 3.0}
    df.attrs["target_weekend"] = {"Alice": 3.0, "Bob": 3.0}
    data.exempt_shifts = {"Alice": ["N"]}
    points = calculate_points(df, data)
    frame = build_fairness_frame(points, data, df)
    by_name = {row["Resident"]: row for row in frame.to_dict("records")}
    assert by_name["Alice"]["D n"] == 2 and by_name["Alice"]["N n"] == 0
    assert by_name["Bob"]["N n"] == 2
    assert by_name["Alice"]["Total target"] == 3.0
    assert by_name["Alice"]["Total dev"] == -1.0
    assert by_name["Alice"]["Weekend target"] == 3.0
    assert "NF target" not in by_name["Bob"]  # NF is no longer a balanced dimension
    assert "[exempt: N]" in by_name["Alice"]["Notes"]


def test_fairness_frame_with_prior_ledger_adds_cumulative():
    df, data = _sample()
    points = calculate_points(df, data)
    prior = {
        "Alice": {"total": 10.0, "weekend": 4.0, "night_float": 0.0,
                  "label_counts": {"D": 8}},
    }
    frame = build_fairness_frame(points, data, df, prior)
    by_name = {row["Resident"]: row for row in frame.to_dict("records")}
    assert by_name["Alice"]["Prior total"] == 10.0
    assert by_name["Alice"]["Cumulative total"] == 12.0
    assert by_name["Alice"]["Cumulative weekend"] == 6.0
    assert by_name["Alice"]["D n cum"] == 10  # 8 prior + 2 this block
    assert by_name["Bob"]["Prior total"] == 0.0
    assert by_name["Bob"]["Cumulative total"] == 4.0


def test_fairness_frame_without_new_args_keeps_old_shape():
    df, data = _sample()
    points = calculate_points(df, data)
    frame = build_fairness_frame(points, data)
    for absent in ("Total target", "Prior total", "Cumulative total", "D n", "Notes"):
        assert absent not in frame.columns


def test_build_assignment_frame_lists_every_slot():
    from model.exporters import build_assignment_frame

    _, data = _sample()
    # Built directly (not via .loc) so the pandas-stub CI job can run this too.
    df = pd.DataFrame([
        {"Date": date(2023, 1, 7), "Day": "Saturday", "D": "Alice", "N": "Bob"},
        {"Date": date(2023, 1, 8), "Day": "Sunday", "D": "Alice", "N": "Unfilled"},
    ])
    frame = build_assignment_frame(df, data)
    records = frame.to_dict("records")
    assert len(records) == 4  # 2 days x 2 shifts, unfilled included
    sat_n = next(r for r in records if r["Shift"] == "N" and r["Date"] == date(2023, 1, 7))
    assert sat_n["Resident"] == "Bob" and sat_n["Points"] == 2.0
    assert sat_n["Weekend"] is True and sat_n["Night float"] is True
    sun_n = next(r for r in records if r["Shift"] == "N" and r["Date"] == date(2023, 1, 8))
    assert sun_n["Resident"] == "Unfilled"


# --- print views / report helpers ----------------------------------------------

def test_schedule_print_view_merges_dates_and_labels_unfilled():
    from model.exporters import schedule_print_view

    df, data = _df_and_data()
    records = df.to_dict("records")
    records[1]["D"] = None  # a genuine gap
    columns, rows, weekend_rows = schedule_print_view(pd.DataFrame(records), data)
    assert columns[0] == "Date" and "Day" not in columns
    assert rows[0]["Date"].startswith(("Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"))
    assert rows[1]["D"] == "Unfilled"  # explicit, not a blank cell


def test_schedule_print_view_flags_weekend_rows():
    from model.exporters import schedule_print_view

    shifts = [ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    data = InputData(
        start_date=date(2023, 1, 6), end_date=date(2023, 1, 8),  # Fri..Sun
        shifts=shifts, juniors=["A", "B"], seniors=[], nf_juniors=[], nf_seniors=[],
        leaves=[], rotators=[], min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 6), "Day": "Friday", "D": "A"},
        {"Date": date(2023, 1, 7), "Day": "Saturday", "D": "B"},
        {"Date": date(2023, 1, 8), "Day": "Sunday", "D": "A"},
    ])
    _, _, weekend_rows = schedule_print_view(df, data)
    assert weekend_rows == {1, 2}


def test_fairness_print_sections_split_by_role_and_curated():
    from model.exporters import fairness_print_sections

    df, data = _df_and_data(seniors=["Bob"])
    points = calculate_points(df, data)
    fairness = build_fairness_frame(points, data, df)
    sections = fairness_print_sections(fairness, data)
    titles = [t for t, _, _ in sections]
    assert titles == ["Juniors", "Seniors"]
    junior_cols = sections[0][1]
    assert "Notes" not in junior_cols and "Role" not in junior_cols
    assert "Resident" in junior_cols and "Total" in junior_cols
    # Per-label COUNT columns print; raw per-label point columns stay in CSV.
    assert "D n" in junior_cols and "D" not in junior_cols
    # NF duty is dropped when nobody has any.
    assert "NF duty (days)" not in junior_cols


def test_annotation_footnotes_number_only_noted_residents():
    from model.exporters import annotation_footnotes

    df, data = _df_and_data(exempt_shifts={"Alice": ["N"]})
    points = calculate_points(df, data)
    fairness = build_fairness_frame(points, data, df)
    markers, lines = annotation_footnotes(fairness)
    assert markers == {"Alice": 1}
    alice_notes = next(
        r["Notes"] for r in fairness.to_dict("records") if r["Resident"] == "Alice"
    )
    assert lines == [f"1. Alice — {alice_notes}"]


def test_report_header_and_legend():
    from model.exporters import legend_entries, report_header_lines

    df, data = _df_and_data()
    df.attrs["solver_status"] = "OPTIMAL"
    df.attrs["wall_time_sec"] = 3.0
    lines = report_header_lines(data, df, {"score": 100.0, "unfilled": 0})
    text = " ".join(lines)
    assert "juniors" in text and "Generated" in text
    assert "Solver OPTIMAL in 3s" in text and "Quality 100.0/100" in text
    legend = legend_entries("auto")
    labels = [label for _, label in legend]
    assert any("Unfilled" in label for label in labels)
    assert any("Weekend" in label for label in labels)


def test_build_cumulative_frame_segments():
    from model.exporters import build_cumulative_frame

    df, data = _df_and_data()
    points = calculate_points(df, data)
    prior = {"Alice": {"total": 5.0, "weekend": 2.0}}
    rows = build_cumulative_frame(points, prior, data).to_dict("records")
    alice = [r for r in rows if r["Resident"] == "Alice"]
    assert {r["Segment"] for r in alice} == {"Prior blocks", "This block"}
    prior_row = next(r for r in alice if r["Segment"] == "Prior blocks")
    assert prior_row["Points"] == 5.0
    assert prior_row["Cumulative"] == 5.0 + points["Alice"]["total"]
    # Bob has no history: prior segment is zero, not missing.
    bob_prior = [
        r for r in rows
        if r["Resident"] == "Bob" and r["Segment"] == "Prior blocks"
    ]
    assert len(bob_prior) == 1 and bob_prior[0]["Points"] == 0.0


def _with_gap(df):
    """A frame whose first D cell is a genuine solver-style None gap."""
    records = df.to_dict("records")
    records[0]["D"] = None
    return pd.DataFrame(records)


def test_pdf_with_notes_ledger_and_gaps_still_renders():
    pytest.importorskip("reportlab")
    from model.data_models import Blackout

    df, data = _df_and_data(
        seniors=["Bob"],
        blackouts=[Blackout(None, ("Alice",), date(2023, 1, 7), date(2023, 1, 8))],
    )
    prior = {"Alice": {"total": 5.0, "weekend": 2.0}}
    out = schedule_to_pdf_bytes(_with_gap(df), data, color_mode="auto", prior_ledger=prior)
    assert out[:4] == b"%PDF"


def test_excel_gains_per_call_sheet_and_unfilled_labels():
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    df, data = _df_and_data()
    df = _with_gap(df)
    out = schedule_to_excel_bytes(df, data)
    book = load_workbook(io.BytesIO(out))
    assert set(book.sheetnames) >= {"Schedule", "Fairness", "Per-call"}
    schedule = book["Schedule"]
    header = [c.value for c in schedule[1]]
    values = [c.value for c in schedule[2]]
    assert values[header.index("D")] == "Unfilled"
    assert schedule.freeze_panes == "B2"
