import sys, os
from datetime import date
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    import pandas as pd
except Exception:
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.fairness import calculate_points, format_fairness_log


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
    pts = calculate_points(df, shifts)
    assert pts == {
        "Alice": {"total": 3.0, "weekend": 3.0, "labels": {"D": 1.0, "N": 2.0}},
        "Bob": {"total": 3.0, "weekend": 3.0, "labels": {"D": 1.0, "N": 2.0}},
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
        min_gap=1,
        target_total=3.0,
    )
    log = format_fairness_log(df, data)
    expected_lines = [
        "Alice: total 3.0 (dev +0.0), weekend 3.0, D 1.0, N 2.0",
        "Bob: total 3.0 (dev +0.0), weekend 3.0, D 1.0, N 2.0",
        "Total point range: 0.0",
        "Weekend point range: 0.0",
    ]
    assert log.splitlines() == expected_lines
