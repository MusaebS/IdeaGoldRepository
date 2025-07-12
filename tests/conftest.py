import importlib
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


class SessionState(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


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


def simple_date_range(start, end):
    if not isinstance(start, datetime):
        start = datetime.combine(start, datetime.min.time())
    if not isinstance(end, datetime):
        end = datetime.combine(end, datetime.min.time())
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(days)]


class SimplePandasModule(types.SimpleNamespace):
    def DataFrame(self, data=None, columns=None):
        return SimpleDataFrame(data, columns)

    def date_range(self, start, end):
        return simple_date_range(start, end)


@pytest.fixture(autouse=True)
def stub_modules(monkeypatch):
    st = types.SimpleNamespace(session_state=SessionState(), error=lambda *a, **k: None)
    pd = SimplePandasModule()
    monkeypatch.setitem(sys.modules, 'streamlit', st)
    monkeypatch.setitem(sys.modules, 'pandas', pd)
    root = Path(__file__).resolve().parent.parent
    monkeypatch.syspath_prepend(str(root))
    import scheduler
    importlib.reload(scheduler)
    yield st, scheduler
    importlib.reload(scheduler)


@pytest.fixture
def simple_state(stub_modules):
    st, _ = stub_modules
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
    st.session_state.quota_method = "hare"
    return st


@pytest.fixture
def sched(stub_modules):
    _, scheduler = stub_modules
    return scheduler
