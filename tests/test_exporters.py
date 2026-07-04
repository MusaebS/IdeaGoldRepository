import sys, os
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


def test_build_fairness_frame():
    df, data = _sample()
    points = calculate_points(df, data)
    frame = build_fairness_frame(points, data)
    by_name = {row["Resident"]: row for row in frame.to_dict("records")}
    assert by_name["Alice"]["Role"] == "Junior"
    assert by_name["Bob"]["Role"] == "Senior"
    assert by_name["Alice"]["Total"] == 2.0      # D 1.0 x 2 days
    assert by_name["Bob"]["Total"] == 4.0        # N 2.0 x 2 days
    assert by_name["Bob"]["Night Float"] == 4.0
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
    df.attrs["target_night_float"] = {"Bob": 4.0}
    data.exempt_shifts = {"Alice": ["N"]}
    points = calculate_points(df, data)
    frame = build_fairness_frame(points, data, df)
    by_name = {row["Resident"]: row for row in frame.to_dict("records")}
    assert by_name["Alice"]["D n"] == 2 and by_name["Alice"]["N n"] == 0
    assert by_name["Bob"]["N n"] == 2
    assert by_name["Alice"]["Total target"] == 3.0
    assert by_name["Alice"]["Total dev"] == -1.0
    assert by_name["Alice"]["Weekend target"] == 3.0
    assert by_name["Bob"]["NF target"] == 4.0 and by_name["Bob"]["NF dev"] == 0.0
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
