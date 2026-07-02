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
