from datetime import date


def setup_state_simple():
    import streamlit as st
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


def test_allocate_integer_quotas_basic(sched):
    quotas = {"A": 1.2, "B": 0.8}
    result = sched.allocate_integer_quotas(quotas, 2)
    assert result == {"A": 1, "B": 1}


def test_build_schedule_simple(sched, simple_state):
    df, wide, unf, compact = sched.build_schedule()
    assert len(df) == 2
    assert unf.empty
    assert not wide.empty
    assert not compact.empty


def test_build_expectation_report(sched):
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
    df = sched.pd.DataFrame(data)
    report = sched.build_expectation_report(df)
    assert len(report) == 4


def test_weekend_filter_fallback(sched, simple_state):
    simple_state.session_state.shifts[0]["thur_weekend"] = True
    simple_state.session_state.start_date = date(2023, 1, 5)
    simple_state.session_state.end_date = date(2023, 1, 5)
    simple_state.session_state.leaves = [("A", date(2023, 1, 5), date(2023, 1, 5))]
    df, _, unf, _ = sched.build_schedule()
    assert unf.empty
    assert df._data[0]["Shift1"] == "B"


def test_fill_unassigned_shifts_prioritizes_deficit(sched, simple_state):

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

    new_unfilled = sched.fill_unassigned_shifts(
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


def test_balance_points_basic(sched):
    import streamlit as st
    setup_state_simple()
    cfg = st.session_state.shifts[0]
    shift_cfg_map = {"Shift1": cfg}
    schedule_rows = [
        {"Date": date(2023, 1, 1), "Day": "Mon", "Shift1": "A"},
        {"Date": date(2023, 1, 2), "Day": "Tue", "Shift1": "A"},
    ]
    stats = {
        "A": {"Shift1": {"total": 2, "weekend": 0}},
        "B": {"Shift1": {"total": 0, "weekend": 0}},
    }
    points_assigned = {"A": 2, "B": 0}
    expected_points_total = {"A": 1, "B": 1}
    last_assigned = {"A": date(2023, 1, 2), "B": None}

    sched.balance_points(
        schedule_rows,
        stats,
        shift_cfg_map,
        expected_points_total,
        points_assigned,
        st.session_state.min_gap,
        ["Shift1"],
        last_assigned,
    )

    assigned = [row["Shift1"] for row in schedule_rows]
    assert set(assigned) == {"A", "B"}
    assert points_assigned == {"A": 1, "B": 1}


def test_balance_weekends_updates_points_and_last_assigned(sched, simple_state):
    cfg = simple_state.session_state.shifts[0]
    shift_cfg_map = {"Shift1": cfg}
    schedule_rows = [
        {"Date": date(2023, 1, 6), "Day": "Fri", "Shift1": "A"},
        {"Date": date(2023, 1, 2), "Day": "Mon", "Shift1": "B"},
    ]
    stats = {
        "A": {"Shift1": {"total": 1, "weekend": 1}},
        "B": {"Shift1": {"total": 1, "weekend": 0}},
    }
    last_assigned = {"A": date(2023, 1, 6), "B": date(2023, 1, 2)}
    points_assigned = {"A": 2, "B": 1}
    target_weekend = {"Shift1": {"A": 0, "B": 1}}

    sched.balance_weekends(
        schedule_rows,
        stats,
        target_weekend,
        shift_cfg_map,
        simple_state.session_state.min_gap,
        ["Shift1"],
        last_assigned,
        points_assigned,
    )

    assert schedule_rows[0]["Shift1"] == "B"
    assert schedule_rows[1]["Shift1"] == "A"
    assert points_assigned == {"A": 1, "B": 2}
    assert stats["A"]["Shift1"]["weekend"] == 0
    assert stats["B"]["Shift1"]["weekend"] == 1
    assert last_assigned == {"A": date(2023, 1, 2), "B": date(2023, 1, 6)}


def test_balance_totals_adjusts_weekend_stats(sched, simple_state):
    cfg = simple_state.session_state.shifts[0]
    shift_cfg_map = {"Shift1": cfg}
    schedule_rows = [
        {"Date": date(2023, 1, 6), "Day": "Fri", "Shift1": "A"},
        {"Date": date(2023, 1, 2), "Day": "Mon", "Shift1": "A"},
    ]
    stats = {
        "A": {"Shift1": {"total": 2, "weekend": 1}},
        "B": {"Shift1": {"total": 0, "weekend": 0}},
    }
    target_total = {"Shift1": {"A": 1, "B": 1}}
    points_assigned = {"A": 3, "B": 0}
    last_assigned = {"A": date(2023, 1, 6), "B": None}

    sched.balance_totals(
        schedule_rows,
        stats,
        target_total,
        simple_state.session_state.min_gap,
        ["Shift1"],
        last_assigned,
        shift_cfg_map,
        points_assigned,
    )

    assert schedule_rows[0]["Shift1"] == "B"
    assert stats["A"]["Shift1"] == {"total": 1, "weekend": 0}
    assert stats["B"]["Shift1"] == {"total": 1, "weekend": 1}
    assert points_assigned == {"A": 1, "B": 2}
    assert last_assigned == {"A": date(2023, 1, 2), "B": date(2023, 1, 6)}

