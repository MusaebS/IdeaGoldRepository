import types
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# create minimal streamlit stub
class SessionState(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

st = types.SimpleNamespace(session_state=SessionState(), error=lambda *a, **k: None)
sys.modules['streamlit'] = st

# minimal pandas stub for tests
class SimpleDataFrame:
    def __init__(self, data=None, columns=None):
        self._data = list(data) if data is not None else []
        self.columns = columns or []

    def __len__(self):
        return len(self._data)

    @property
    def empty(self):
        return len(self._data) == 0

    def to_dict(self, orient="records"):
        if orient != "records":
            raise NotImplementedError
        return list(self._data)


class SimplePandasModule(types.SimpleNamespace):
    def DataFrame(self, data=None, columns=None):
        return SimpleDataFrame(data, columns)

    def date_range(self, start, end):
        from datetime import datetime, timedelta
        if isinstance(start, datetime):
            start_dt = start
        else:
            start_dt = datetime.combine(start, datetime.min.time())
        if isinstance(end, datetime):
            end_dt = end
        else:
            end_dt = datetime.combine(end, datetime.min.time())
        days = (end_dt - start_dt).days + 1
        return [start_dt + timedelta(days=i) for i in range(days)]


sys.modules['pandas'] = SimplePandasModule()

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
    df, wide, unf, compact = scheduler.build_schedule()
    assert len(df) == 2
    assert unf.empty
    assert not wide.empty
    assert not compact.empty
