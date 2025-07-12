import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule


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
