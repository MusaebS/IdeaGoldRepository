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
    assert at.title[0].value == "🗓️ Idea Gold Scheduler"


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
    at.checkbox[0].set_value(True)  # Test mode
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


def test_seeded_results_survive_rerun():
    df, data = _result_fixture()
    at = _at()
    at.run()
    _seed_result(at, df, data)
    at.run()
    assert not at.exception
    assert any("Schedule quality" in m.label for m in at.metric)
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
    assert options == ["Bob", "Unfilled"]


def test_ledger_policy_toggles_default_on():
    at = _at()
    at.run()
    boxes = {c.label: c for c in at.checkbox}
    refund = next(v for k, v in boxes.items() if "Penalties don't earn" in k)
    catchup = next(v for k, v in boxes.items() if "Excused shortfalls" in k)
    assert refund.value is True and catchup.value is True
    refund.set_value(False)
    at.run()
    assert at.session_state["ledger_no_refund"] is False
    assert at.session_state["ledger_no_catchup"] is True


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
    assert blackouts[0].day_before is True and blackouts[0].compensated is True
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
