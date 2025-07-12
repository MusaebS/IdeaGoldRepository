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
