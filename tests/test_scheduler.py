from datetime import date


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

