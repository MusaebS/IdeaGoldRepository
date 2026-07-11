"""Headless UI tests via Streamlit's AppTest.

These run the real app.py in-process (no browser) and cover the wiring the
unit tests can't: session-state lifecycle, the Generate flow, and (in
test_manual_edit_*) the apply/revert manual-edit feature. The browser-level
smoke test lives in scripts/smoke_app.py and stays complementary.
"""
import os
from datetime import date

import pytest

pytest.importorskip("streamlit")
pd = pytest.importorskip("pandas")

from streamlit.testing.v1 import AppTest

from model.data_models import ShiftTemplate, InputData

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


def _at() -> AppTest:
    return AppTest.from_file(APP, default_timeout=60)


def _result_fixture():
    """A small solved schedule + config for seeding session state directly."""
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 3),
        shifts=shifts,
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 2), "Day": "Monday", "D": "Alice"},
        {"Date": date(2023, 1, 3), "Day": "Tuesday", "D": "Bob"},
    ])
    df.attrs["target_total"] = 1.0
    df.attrs["target_total_map"] = {"Alice": 1.0, "Bob": 1.0}
    return df, data


def _seed_result(at: AppTest, df, data) -> None:
    at.session_state["result_df"] = df
    at.session_state["solver_df"] = df
    at.session_state["result_data"] = data
    at.session_state["result_prior_ledger"] = None
    at.session_state["result_version"] = 1
    at.session_state["manually_edited"] = False


def test_app_loads_without_exception():
    at = _at().run()
    assert not at.exception
    assert any("Idea Gold Scheduler" in item.value for item in at.markdown)
    tab_labels = {tab.label for tab in at.tabs}
    assert {
        "① Setup",
        "② Coverage",
        "③ Policies",
        "④ History",
        "⑤ Review & run",
        "⑥ Results",
        "Diagnostics",
    } <= tab_labels
    assert at.session_state["benchmark_result"] is None
    assert at.button(key="benchmark_run").label == "Run benchmark"
    assert any("No schedule generated yet" in item.value for item in at.markdown)


def test_generate_with_empty_config_shows_validation_errors():
    at = _at().run()
    generate = [b for b in at.button if "Generate schedule" in b.label]
    assert generate, "Generate button not found"
    generate[0].click()
    at.run()
    assert not at.exception
    assert any("Fix the configuration" in e.value for e in at.error)


def test_test_mode_generate_produces_schedule(monkeypatch):
    # dev = 10s solver budget: the demo roster (10 shifts x 28 days) reliably
    # solves to FEASIBLE in that window; test's 1s budget hits UNKNOWN.
    monkeypatch.setenv("ENV", "dev")
    at = _at()
    at.run()
    at.checkbox(key="test_mode").set_value(True)
    at.run()
    generate = [b for b in at.button if "Generate schedule" in b.label]
    generate[0].click()
    at.run()
    assert not at.exception
    assert at.session_state["result_df"] is not None
    assert at.session_state["result_version"] == 1
    assert at.session_state["solver_df"] is not None
    # Metrics rendered
    assert any("Schedule quality" in m.label for m in at.metric)
    # The user is told the schedule is ready without hunting for the tab.
    assert any("Schedule generated" in s.value for s in at.success)


def test_seeded_results_survive_rerun():
    df, data = _result_fixture()
    at = _at()
    at.run()
    _seed_result(at, df, data)
    at.run()
    assert not at.exception
    assert any("Schedule quality" in m.label for m in at.metric)
    assert {"Overview", "Schedule", "Fairness", "Audit trail", "Export"} <= {
        tab.label for tab in at.tabs
    }
    # A cosmetic rerun (no new solve) keeps the result in place.
    at.run()
    assert at.session_state["result_df"] is not None
    assert at.session_state["result_version"] == 1


# --- manual-edit persistence -------------------------------------------------

def test_normalize_edited_schedule_restores_dates_attrs_and_blanks():
    from ui.state import normalize_edited_schedule

    base, _ = _result_fixture()
    # Simulate what st.data_editor returns: Timestamps, lost attrs, a cleared cell.
    edited = base.copy()
    edited["Date"] = pd.to_datetime(edited["Date"])
    edited.attrs = {}
    edited.loc[1, "D"] = None

    cleaned = normalize_edited_schedule(edited, base)
    assert list(cleaned["Date"]) == list(base["Date"])  # real date objects again
    assert cleaned["D"][1] == "Unfilled"                # cleared cell normalised
    assert cleaned.attrs["target_total_map"] == {"Alice": 1.0, "Bob": 1.0}
    assert base.attrs  # source untouched


def test_apply_edits_persists_and_revert_restores():
    df, data = _result_fixture()
    at = _at()
    at.run()
    _seed_result(at, df, data)
    at.run()

    apply_btn = [b for b in at.button if b.key == "apply_edits"]
    assert apply_btn, "Apply edits button not rendered"
    apply_btn[0].click()
    at.run()
    assert not at.exception
    assert at.session_state["manually_edited"] is True
    assert at.session_state["result_version"] == 2  # export cache invalidated
    # Solver targets survive the apply (attrs restored onto the edited frame).
    assert at.session_state["result_df"].attrs["target_total_map"] == {
        "Alice": 1.0, "Bob": 1.0,
    }
    assert any("manually edited" in w.value for w in at.warning)

    revert_btn = [b for b in at.button if b.key == "revert_edits"]
    assert revert_btn, "Revert button not rendered after apply"
    revert_btn[0].click()
    at.run()
    assert not at.exception
    assert at.session_state["manually_edited"] is False
    assert at.session_state["result_version"] == 3
    assert not any("manually edited" in w.value for w in at.warning)


def test_edited_schedule_flows_into_fairness_log():
    from model.fairness import format_fairness_log
    from ui.state import normalize_edited_schedule

    base, data = _result_fixture()
    edited = base.copy()
    edited.attrs = {}
    edited.loc[1, "D"] = "Alice"  # Alice now works both days

    cleaned = normalize_edited_schedule(edited, base)
    log = format_fairness_log(cleaned, data)
    alice = next(line for line in log.splitlines() if line.split(" ")[0] == "Alice")
    assert "total 2.0" in alice  # reflects the edit, against preserved targets
    assert "(target 1.0" in alice


def test_manual_edit_closed_cell_is_stood_down_not_unfilled():
    from model.fairness import calculate_points, format_fairness_log
    from ui.state import normalize_edited_schedule

    base, data = _result_fixture()
    edited = base.copy()
    edited.attrs = {}
    edited.loc[0, "D"] = "Closed"  # manually stand the first slot down

    cleaned = normalize_edited_schedule(edited, base)
    # Recorded on attrs so every calculation excludes it (not a phantom resident).
    assert cleaned.attrs["closed_cells"] == {"2023-01-02": ["D"]}
    pts = calculate_points(cleaned, data)
    assert "Closed" not in pts
    assert pts["Alice"]["total"] == 0.0  # Alice's slot is now closed
    assert pts["Bob"]["total"] == 1.0
    health = format_fairness_log(cleaned, data).splitlines()[0]
    assert "1 closed" in health
    assert "0 unfilled" in health  # a closure is not a coverage gap


def test_manual_edit_can_reopen_a_closed_cell():
    from dataclasses import replace
    from model.data_models import ShiftClosure
    from model.fairness import calculate_points
    from ui.state import normalize_edited_schedule

    base, data = _result_fixture()
    data = replace(data, closures=[ShiftClosure("D", date(2023, 1, 2), date(2023, 1, 2))])
    base = base.copy()
    base.loc[0, "D"] = "Closed"
    base.attrs["closed_cells"] = {"2023-01-02": ["D"]}
    # The editor reassigns Alice to the previously-closed cell.
    edited = base.copy()
    edited.attrs = dict(base.attrs)
    edited.loc[0, "D"] = "Alice"

    cleaned = normalize_edited_schedule(edited, base)
    assert cleaned.attrs["closed_cells"] == {}  # re-opened
    assert calculate_points(cleaned, data)["Alice"]["total"] == 1.0


# --- seniority groups / perks / exemptions -----------------------------------

def test_seniority_editors_store_to_session():
    at = _at()
    at.run()
    # Seed a roster so the editors render their inputs.
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.run()
    at.text_input(key="grp_name").set_value("R2")
    at.number_input(key="grp_pct").set_value(90)
    grp_add = [b for b in at.button if b.key == "grp_add"]
    grp_add[0].click()
    at.run()
    assert at.session_state["group_factors"] == {"R2": 0.9}
    # Assign Alice to R2.
    at.multiselect(key="grp_who").set_value(["Alice"])
    assign = [b for b in at.button if b.key == "grp_assign"]
    assign[0].click()
    at.run()
    assert at.session_state["resident_groups"] == {"Alice": "R2"}
    assert not at.exception


def test_shift_cell_options_exclude_exempt():
    from ui.results import _shift_cell_options

    _, data = _result_fixture()
    from dataclasses import replace
    data = replace(data, exempt_shifts={"Alice": ["D"]})
    shift = data.shifts[0]
    options = _shift_cell_options(data, shift)
    assert "Alice" not in options
    # Both non-resident markers are always offered so any cell can be set unfilled
    # (a coverage gap) or closed (unavailable / not staffed).
    assert options == ["Bob", "Unfilled", "Closed"]


def test_ledger_policy_toggles_default_on():
    at = _at()
    at.run()
    boxes = {c.label: c for c in at.checkbox}
    refund = next(v for k, v in boxes.items() if "Penalties don't earn" in k)
    catchup = next(v for k, v in boxes.items() if "Excused shortfalls" in k)
    same_type = next(v for k, v in boxes.items() if "same shift type" in k)
    assert refund.value is True and catchup.value is True and same_type.value is True
    refund.set_value(False)
    at.run()
    assert at.session_state["ledger_no_refund"] is False
    assert at.session_state["ledger_no_catchup"] is True


def test_optional_name_matching_deduplicates_without_rewriting_display_name():
    at = _at().run()
    at.toggle(key="normalize_names").set_value(True)
    juniors = next(area for area in at.text_area if area.label.startswith("Juniors"))
    juniors.set_value(" Alice   Smith \nALICE SMITH\nBob\nBOB")
    at.run()

    assert at.session_state["juniors"] == ["Alice   Smith", "Bob"]
    assert not at.exception


def test_fill_column_button_populates_extra_vals():
    df, data = _result_fixture()
    at = _at()
    at.run()
    _seed_result(at, df, data)
    at.session_state["extra_cols"] = ["Consultant"]
    at.run()
    at.text_area(key="fill_names").set_value("Dr X, Dr Y")
    fill = [b for b in at.button if b.key == "fill_apply"]
    assert fill, "Fill button not rendered"
    fill[0].click()
    at.run()
    assert not at.exception
    vals = at.session_state["extra_vals"]["Consultant"]
    assert vals == {"2023-01-02": "Dr X", "2023-01-03": "Dr Y"}  # daily cycle


# --- theme shades + display persistence ---------------------------------------

def test_apply_theme_shades_recolours_palette():
    from model.coloring import theme_palette, DEFAULT_PALETTE

    df, data = _result_fixture()
    at = _at()
    at.run()
    _seed_result(at, df, data)
    at.run()
    theme_btn = [b for b in at.button if b.key == "pal_theme_apply"]
    assert theme_btn, "Apply theme shades button not rendered"
    theme_btn[0].click()
    at.run()
    assert not at.exception
    expected = theme_palette(DEFAULT_PALETTE["points"], current=DEFAULT_PALETTE)
    assert at.session_state["palette"] == expected


def test_restore_display_state_applies_and_pops_widget_keys():
    from model.coloring import DEFAULT_PALETTE
    from ui.state import restore_display_state

    state = {
        "palette": dict(DEFAULT_PALETTE),
        "pal_points": "#000000",       # stale widget state
        "pal_theme": "#000000",
        "extra_cols": ["Old"],
        "extra_vals": {"Old": {}},
        "extra_cols_editor": {"deltas": 1},
        "col_order": ["Date"],
        "known_cols": ["Date"],
    }
    display = {
        "palette": {"points": "#4a90d9"},
        "extra_cols": ["Consultant"],
        "extra_vals": {"Consultant": {"2023-01-01": "Dr X"}},
        "col_order": ["Date", "Consultant"],
    }
    restore_display_state(display, state=state)
    assert state["palette"]["points"] == "#4a90d9"
    assert state["palette"]["unfilled"] == DEFAULT_PALETTE["unfilled"]  # merged
    assert "pal_points" not in state and "pal_theme" not in state
    assert state["extra_cols"] == ["Consultant"]
    assert state["extra_vals"] == {"Consultant": {"2023-01-01": "Dr X"}}
    assert "extra_cols_editor" not in state
    assert state["col_order"] == ["Date", "Consultant"]
    assert state["known_cols"] == ["Date", "Consultant"]  # hides stay hidden


def test_named_groups_editor_stores_to_session():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob", "Cara"]
    at.run()
    at.text_input(key="team_name").set_value("Team A")
    add = [b for b in at.button if b.key == "team_add"]
    add[0].click()
    at.run()
    assert at.session_state["named_groups"] == {"Team A": []}
    at.multiselect(key="team_who").set_value(["Alice", "Bob"])
    assign = [b for b in at.button if b.key == "team_assign"]
    assign[0].click()
    at.run()
    assert at.session_state["named_groups"] == {"Team A": ["Alice", "Bob"]}
    assert not at.exception


def test_blackouts_editor_stores_to_session():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.session_state["named_groups"] = {"Team A": ["Alice", "Bob"]}
    at.run()
    add = [b for b in at.button if b.key == "bo_add"]
    add[0].click()
    at.run()
    blackouts = at.session_state["blackouts"]
    assert len(blackouts) == 1
    assert blackouts[0].group == "Team A"
    assert blackouts[0].night_before is True and blackouts[0].compensated is True
    assert not at.exception


def test_blackouts_editor_adhoc_names():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.run()
    at.selectbox(key="bo_who").set_value("(ad-hoc names…)")
    at.run()
    at.multiselect(key="bo_adhoc").set_value(["Bob"])
    add = [b for b in at.button if b.key == "bo_add"]
    add[0].click()
    at.run()
    blackouts = at.session_state["blackouts"]
    assert len(blackouts) == 1
    assert blackouts[0].group is None and blackouts[0].members == ("Bob",)
    assert not at.exception


def test_reductions_editor_stores_to_session():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.session_state["shifts"] = [
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    at.session_state["named_groups"] = {"Team A": ["Alice"]}
    at.run()
    at.multiselect(key="red_labels").set_value(["N"])
    at.run()
    add = [b for b in at.button if b.key == "red_add"]
    add[0].click()
    at.run()
    reductions = at.session_state["reductions"]
    assert len(reductions) == 1
    assert reductions[0].group == "Team A"
    assert reductions[0].labels == ("N",)
    assert reductions[0].factor == 0.0
    assert reductions[0].keep_total is False  # "work less now" is the default mode
    assert not at.exception


def test_ledger_panel_start_empty_and_rows_survive_rerun():
    at = _at()
    at.run()
    start = [b for b in at.button if b.key == "ledger_start"]
    assert start, "Start an empty ledger button not found"
    start[0].click()
    at.run()
    assert at.session_state["ledger_rows"] == []
    # Seed a row as if edited in the grid; it must survive a cosmetic rerun
    # and flow into the carryover ledger for the next Generate.
    at.session_state["ledger_rows"] = [
        {"Resident": "Alice", "Total": 5.0, "Weekend": 2.0, "Night float": 0.0}
    ]
    at.run()
    assert at.session_state["ledger_rows"][0]["Resident"] == "Alice"
    assert not at.exception


def test_ledger_panel_clear_returns_to_standalone():
    at = _at()
    at.run()
    at.session_state["ledger_rows"] = [
        {"Resident": "Alice", "Total": 5.0, "Weekend": 2.0, "Night float": 0.0}
    ]
    at.run()
    clear = [b for b in at.button if b.key == "ledger_clear"]
    assert clear, "Clear ledger button not found"
    clear[0].click()
    at.run()
    assert at.session_state["ledger_rows"] is None
    assert not at.exception


def test_ledger_reconcile_apply_merges_and_updates_base():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.session_state["shifts"] = [
        ShiftTemplate(label="Day", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    # As if a ledger was uploaded with a misspelled name and a renamed shift.
    at.session_state["ledger_base"] = {
        "Alicia": {"total": 5.0, "weekend": 2.0,
                   "labels": {"D": 5.0}, "label_counts": {"D": 5}},
        "Bob": {"total": 3.0, "weekend": 1.0},
    }
    at.session_state["ledger_rows"] = [
        {"Resident": "Alicia", "Total": 5.0, "Weekend": 2.0},
        {"Resident": "Bob", "Total": 3.0, "Weekend": 1.0},
    ]
    at.run()
    at.selectbox(key="ledgrec_p_Alicia").set_value("Merge into Alice")
    at.selectbox(key="ledgrec_l_D").set_value("Merge into Day")
    apply = [b for b in at.button if b.key == "ledgrec_apply"]
    assert apply, "Reconcile apply button not found"
    apply[0].click()
    at.run()
    base = at.session_state["ledger_base"]
    assert "Alicia" not in base
    assert base["Alice"]["total"] == 5.0
    assert base["Alice"]["labels"] == {"Day": 5.0}  # rename + label merge kept history
    assert base["Alice"]["label_counts"] == {"Day": 5}
    assert {r["Resident"] for r in at.session_state["ledger_rows"]} == {"Alice", "Bob"}
    assert not at.exception


def test_ledger_reconcile_dismiss_keeps_history_and_hides_panel():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice"]
    at.session_state["ledger_base"] = {"Zed": {"total": 2.0, "weekend": 0.0}}
    at.session_state["ledger_rows"] = [{"Resident": "Zed", "Total": 2.0, "Weekend": 0.0}]
    at.run()
    assert [s for s in at.selectbox if str(s.key).startswith("ledgrec_")]
    dismiss = [b for b in at.button if b.key == "ledgrec_dismiss"]
    assert dismiss, "Reconcile dismiss button not found"
    dismiss[0].click()
    at.run()
    # The reconcile widgets are gone; the unmatched entry is kept as history.
    assert not [s for s in at.selectbox if str(s.key).startswith("ledgrec_")]
    assert at.session_state["ledger_base"] == {"Zed": {"total": 2.0, "weekend": 0.0}}
    assert not at.exception


def test_populate_editors_from_config_round_trips_into_session():
    from datetime import date as _date

    from model.data_models import LoadReduction
    from ui.config_tabs import populate_editors_from_config

    data = InputData(
        start_date=_date(2026, 3, 2),
        end_date=_date(2026, 3, 15),
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
            ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=2.0),
        ],
        juniors=["Alice", "Bob"],
        seniors=["Sam"],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=2,
        seed=7,
        weekend_days=[4, 5],  # Fri/Sat
        max_total={"Alice": 4.0},
        max_total_excused={"Alice": True},
        extra_points={"Bob": 1.0},
        reductions=[LoadReduction(None, ("Bob",), ("N",), 0.0, _date(2026, 3, 2), _date(2026, 3, 8))],
    )
    state: dict = {}
    populate_editors_from_config(data, state=state)
    assert state["shifts"] == data.shifts
    assert state["juniors"] == ["Alice", "Bob"] and state["seniors"] == ["Sam"]
    assert state["start_date"] == _date(2026, 3, 2)
    assert state["min_gap"] == 2 and state["seed"] == 7
    assert state["weekend_labels"] == ["Fri", "Sat"]
    assert state["caps"] == {"Alice": {"total": 4.0, "excused": True}}
    assert state["extra_points"] == {"Bob": 1.0}
    assert len(state["reductions"]) == 1


def test_pending_config_applies_before_widgets_render():
    # The real upload flow: the History handler stashes the parsed config and
    # reruns; apply_pending_updates() must fill the ALREADY-INSTANTIATED Setup
    # widgets on the next run without a StreamlitAPIException. (Calling the
    # importer mid-run used to crash exactly there.)
    data = InputData(
        start_date=date(2026, 4, 6),
        end_date=date(2026, 4, 12),
        shifts=[
            ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ],
        juniors=["Alice", "Bob"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=3,
        seed=9,
    )
    at = _at()
    at.run()  # first run instantiates every keyed Setup widget
    at.session_state["pending_config"] = (data, None)
    at.run()
    assert not at.exception
    assert at.session_state["pending_config"] is None  # queue drained
    assert at.session_state["shifts"] == data.shifts
    assert at.session_state["juniors"] == ["Alice", "Bob"]
    # The keyed widgets themselves show the loaded values.
    assert at.date_input(key="start_date").value == date(2026, 4, 6)
    assert at.slider(key="min_gap").value == 3
    assert at.number_input(key="seed").value == 9


def test_pending_state_updates_min_gap_slider():
    # The infeasible-retry path queues a min_gap write-back so the slider
    # always shows the gap the schedule was actually built with.
    at = _at()
    at.run()
    assert at.slider(key="min_gap").value == 1  # default
    at.session_state["pending_widget_state"] = {"min_gap": 0}
    at.run()
    assert not at.exception
    assert at.session_state["pending_widget_state"] is None
    assert at.slider(key="min_gap").value == 0


def test_availability_apply_adds_compensated_leaves_with_dedupe():
    from model.availability import AvailabilityRow

    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    # One request already entered by hand, one new, one invalid.
    at.session_state["leaves"] = [("Alice", date(2026, 8, 5), date(2026, 8, 6), True)]
    at.session_state["avail_preview"] = [
        AvailabilityRow(2, "Alice", "Alice", date(2026, 8, 5), date(2026, 8, 6), None),
        AvailabilityRow(3, "Bob", "Bob", date(2026, 8, 10), date(2026, 8, 10), None),
        AvailabilityRow(4, "Nobody", None, None, None, "'Nobody' is not on the roster."),
    ]
    at.run()
    apply = [b for b in at.button if b.key == "avail_apply"]
    assert apply and "1 request" in apply[0].label  # duplicate + invalid excluded
    apply[0].click()
    at.run()
    leaves = at.session_state["leaves"]
    assert len(leaves) == 2
    assert tuple(leaves[1]) == ("Bob", date(2026, 8, 10), date(2026, 8, 10), True)
    assert at.session_state["avail_preview"] is None
    assert not at.exception


def test_preferences_editor_stores_to_session():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.session_state["shifts"] = [
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=False, points=2.0),
    ]
    at.run()
    at.multiselect(key="pref_labels").set_value(["N"])
    at.selectbox(key="pref_day").set_value("Weekends")
    at.run()
    set_btn = [b for b in at.button if b.key == "pref_set"]
    set_btn[0].click()
    at.run()
    assert at.session_state["preferred_shifts"] == {"Alice": ["N"]}
    assert at.session_state["preferred_day_type"] == {"Alice": "weekend"}
    assert not at.exception


def test_rotator_coverage_feeds_exemptions():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.session_state["shifts"] = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Junior", night_float=False, thu_weekend=True, points=2.0),
    ]
    at.session_state["exempt_shifts"] = {"Alice": ["D"]}  # pre-existing entry merges
    at.run()
    # The leaves editor must NOT grow a coverage multiselect.
    assert not [m for m in at.multiselect if m.key == "leaves_cover"]
    at.multiselect(key="rotators_cover").set_value(["D"])  # covers D only
    at.run()
    add = [b for b in at.button if b.key == "rotators_add"]
    add[0].click()
    at.run()
    assert len(at.session_state["rotators"]) == 1
    assert at.session_state["exempt_shifts"]["Alice"] == ["D", "N"]
    assert not at.exception


def test_avoid_pairs_locked_until_correct_code():
    at = _at()
    at.run()
    at.session_state["juniors"] = ["Alice", "Bob"]
    at.run()
    # Locked by default: no pair selectors rendered.
    assert not [s for s in at.selectbox if s.key == "avoid_a"]
    at.text_input(key="avoid_code").set_value("0000")
    unlock = [b for b in at.button if b.key == "avoid_unlock"]
    unlock[0].click()
    at.run()
    assert at.session_state["avoid_unlocked"] is False
    assert any("Wrong code" in w.value for w in at.warning)
    at.text_input(key="avoid_code").set_value("1221")
    unlock = [b for b in at.button if b.key == "avoid_unlock"]
    unlock[0].click()
    at.run()
    assert at.session_state["avoid_unlocked"] is True
    # Now the editor is available; add a pair.
    at.selectbox(key="avoid_a").set_value("Alice")
    at.selectbox(key="avoid_b").set_value("Bob")
    add = [b for b in at.button if b.key == "avoid_add"]
    add[0].click()
    at.run()
    assert at.session_state["avoid_pairs"] == [("Alice", "Bob")]
    assert not at.exception
