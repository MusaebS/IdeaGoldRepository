"""Idea Gold Scheduler — Streamlit entry point.

The UI lives in the ``ui`` package (state, editors, config tabs, results);
this script wires the pieces together. Run with ``streamlit run app.py``.
"""
import streamlit as st

from ui.config_tabs import load_demo_data_once, render_application
from ui.state import init_session_state, show_flash
from ui.theme import apply_app_theme, render_hero

st.set_page_config(page_title="Idea Gold Scheduler", page_icon="🗓️", layout="wide")
apply_app_theme()
init_session_state()
show_flash()

render_hero(
    "Idea Gold Scheduler",
    "Build a provably fair on-call schedule. Set up shifts and people, tweak the "
    "rules, then Generate — the optimiser balances the workload for you.",
    eyebrow="Clinical scheduling workspace",
    meta=("Constraint-aware", "Cumulative fairness", "Auditable exports"),
)

with st.sidebar:
    st.subheader("Workspace tools")
    st.caption(
        "Load the complete example setup to explore every stage without entering "
        "a real roster. Turn it off at any time; your current edits stay in place."
    )
    test_mode = st.checkbox(
        "Test mode (preload example data)",
        key="test_mode",
        help="Loads example shifts and residents once. It does not run the solver.",
    )
if test_mode:
    load_demo_data_once()

render_application()
