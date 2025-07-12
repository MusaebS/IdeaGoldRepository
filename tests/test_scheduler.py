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


def test_build_expectation_report():
    data = [
        {
            "Name": "A",
            "Shift1_assigned_total": 2,
            "Shift1_expected_total": 1,
            "Shift1_assigned_weekend": 1,
            "Shift1_expected_weekend": 0,
            "Assigned Points": 3,
            "Expected Points": 1,
        },
        {
            "Name": "B",
            "Shift1_assigned_total": 0,
            "Shift1_expected_total": 1,
            "Shift1_assigned_weekend": 0,
            "Shift1_expected_weekend": 1,
            "Assigned Points": 0,
            "Expected Points": 2,
        },
    ]
    df = scheduler.pd.DataFrame(data)
    report = scheduler.build_expectation_report(df)
    assert len(report) == 4


def test_weekend_filter_fallback():
    setup_state_simple()
    st.session_state.shifts[0]["thur_weekend"] = True
    st.session_state.start_date = date(2023, 1, 5)
    st.session_state.end_date = date(2023, 1, 5)
    st.session_state.leaves = [("A", date(2023, 1, 5), date(2023, 1, 5))]
    df, _, unf, _ = scheduler.build_schedule()
    assert unf.empty
    assert df._data[0]["Shift1"] == "B"


=
def test_fill_unassigned_shifts_prioritizes_deficit():
    cfg = {
        "label": "Shift1",
        "role": "Junior",
        "night_float": False,
        "thur_weekend": False,
        "points": 1.0,
    }

    schedule_rows = [{"Date": date(2023, 1, 1), "Day": "Sunday", "Shift1": "Unfilled"}]
    stats = {
        "A": {"Shift1": {"total": 0, "weekend": 0}},
        "B": {"Shift1": {"total": 0, "weekend": 0}},
    }
    unfilled = [(date(2023, 1, 1), "Shift1")]

    points_assigned = {"A": 0, "B": 0}
    expected_points_total = {"A": 0, "B": 0}
    juniors = ["A", "B"]
    seniors = []
    regular_pool = ["A", "B"]
    shift_labels = ["Shift1"]

    target_total = {"Shift1": {"A": 0, "B": 1}}
    target_weekend = {"Shift1": {"A": 0, "B": 1}}

    new_unfilled = scheduler.fill_unassigned_shifts(
        schedule_rows,
        stats,
        unfilled,
        {"Shift1": cfg},
        points_assigned,
        expected_points_total,
        juniors,
        seniors,
        regular_pool,
        shift_labels,

        target_total,
        target_weekend,
    )

    assert new_unfilled == []
    assert schedule_rows[0]["Shift1"] == "B"

