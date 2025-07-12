import types
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# create minimal streamlit stub
st = types.SimpleNamespace(session_state={}, error=lambda *a, **k: None)
sys.modules['streamlit'] = st

from datetime import date
import scheduler


def setup_state_simple():
    st.session_state.clear()
    st.session_state.shifts = [
        {
            "label": "Shift1",
            "role": "Junior",
            "night_float": False,
            "thur_weekend": False,
            "points": 1.0,
        }
    ]
    st.session_state.juniors = ["A", "B"]
    st.session_state.seniors = []
    st.session_state.nf_juniors = []
    st.session_state.nf_seniors = []
    st.session_state.leaves = []
    st.session_state.rotators = []
    st.session_state.extra_oncalls = {}
    st.session_state.weights = {}
    st.session_state.start_date = date(2023, 1, 1)
    st.session_state.end_date = date(2023, 1, 2)
    st.session_state.min_gap = 1
    st.session_state.nf_block_length = 5
    st.session_state.seed = 0


def test_allocate_integer_quotas_basic():
    quotas = {"A": 1.2, "B": 0.8}
    result = scheduler.allocate_integer_quotas(quotas, 2)
    assert result == {"A": 1, "B": 1}


def test_build_schedule_simple():
    setup_state_simple()
    df, summary, unf = scheduler.build_schedule()
    assert len(df) == 2
    assert unf.empty


def test_weekend_balancing_affects_summary():
    setup_state_simple()
    # span includes Thu-Fri-Sat to test weekend handling
    st.session_state.start_date = date(2023, 1, 5)
    st.session_state.end_date = date(2023, 1, 7)
    st.session_state.min_gap = 0

    import random
    random.seed(0)
    df, summary, unf = scheduler.build_schedule()
    assert unf.empty

    wk_cols = [c for c in summary.columns if c.endswith("_assigned_weekend")]
    for col in wk_cols:
        lbl = col.replace("_assigned_weekend", "")
        for _, row in summary.iterrows():
            assert row[col] == row[f"{lbl}_expected_weekend"]


def test_build_median_report_simple():
    data = [
        {
            "Name": "A",
            "Assigned Points": 6,
            "Shift1_assigned_total": 3,
            "Shift1_assigned_weekend": 2,
        },
        {
            "Name": "B",
            "Assigned Points": 2,
            "Shift1_assigned_total": 1,
            "Shift1_assigned_weekend": 0,
        },
        {
            "Name": "C",
            "Assigned Points": 4,
            "Shift1_assigned_total": 2,
            "Shift1_assigned_weekend": 1,
        },
    ]
    import pandas as pd
    df = pd.DataFrame(data)
    report = scheduler.build_median_report(df)
    # expect four rows describing deviation from median
    assert len(report) == 4
    pts = report[report["Label"] == "Points"].set_index("Name")["Δ Points vs median"].to_dict()
    assert pts == {"A": 2, "B": -2}
    shift = report[report["Label"] == "Shift1"].set_index("Name")["Δ Total vs median"].to_dict()
    assert shift == {"A": 1, "B": -1}
