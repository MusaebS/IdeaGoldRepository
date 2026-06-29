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
