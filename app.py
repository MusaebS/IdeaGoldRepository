"""Idea Gold Scheduler — Streamlit entry point.

The UI lives in the ``ui`` package (state, editors, config tabs, results);
this script wires the pieces together. Run with ``streamlit run app.py``.
"""
import streamlit as st

from ui.config_tabs import load_demo_data_once, render_config_tabs, render_generate_and_solve
from ui.results import render_results
from ui.state import init_session_state

st.set_page_config(page_title="Idea Gold Scheduler", page_icon="🗓️", layout="wide")
st.title("🗓️ Idea Gold Scheduler")
st.caption(
    "Build a provably fair on-call schedule. Set up shifts and people, tweak the "
    "rules, then Generate — the optimiser balances the workload for you."
)

init_session_state()

# optional sample data for quick testing
test_mode = st.checkbox("Test mode (preload example data)")
if test_mode:
    load_demo_data_once()

session_config, carryover_ledger = render_config_tabs()
render_generate_and_solve(session_config, carryover_ledger)
render_results()
