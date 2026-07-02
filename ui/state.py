"""Session-state management: key registry, defaults, and result lifecycle."""
from __future__ import annotations

import streamlit as st

from model.coloring import DEFAULT_PALETTE


class Keys:
    """Session-state / widget key registry.

    Every key that is read or written from more than one place lives here so
    a rename can't silently orphan state. One-off widget keys stay local to
    their editor.
    """

    # Configuration state
    SHIFTS = "shifts"
    JUNIORS = "juniors"
    SENIORS = "seniors"
    NF_JUNIORS = "nf_juniors"
    NF_SENIORS = "nf_seniors"
    LEAVES = "leaves"
    ROTATORS = "rotators"
    CAPS = "caps"
    EXTRA_POINTS = "extra_points"
    WEEKDAY_POINTS = "weekday_points"
    HOLIDAYS = "holidays"
    DEMO_LOADED = "demo_loaded"
    RETRY_CONFIG = "retry_config"

    # Result state
    RESULT_DF = "result_df"          # the live schedule (may carry manual edits)
    SOLVER_DF = "solver_df"          # pristine solver output (for revert)
    RESULT_DATA = "result_data"
    RESULT_PRIOR_LEDGER = "result_prior_ledger"
    RESULT_VERSION = "result_version"
    MANUALLY_EDITED = "manually_edited"
    SCHEDULE_EDITOR = "schedule_editor"

    # Display state
    EXTRA_COLS = "extra_cols"
    EXTRA_VALS = "extra_vals"
    PALETTE = "palette"
    COL_ORDER = "col_order"
    KNOWN_COLS = "known_cols"
    EXPORT_CACHE = "export_cache"
    PAL_PREFIX = "pal_"


def _defaults() -> dict:
    return {
        Keys.SHIFTS: [],
        Keys.JUNIORS: [],
        Keys.SENIORS: [],
        Keys.NF_JUNIORS: [],
        Keys.NF_SENIORS: [],
        Keys.LEAVES: [],
        Keys.ROTATORS: [],
        Keys.CAPS: {},
        Keys.EXTRA_POINTS: {},
        Keys.WEEKDAY_POINTS: {},
        Keys.HOLIDAYS: [],
        Keys.RESULT_DF: None,
        Keys.SOLVER_DF: None,
        Keys.RESULT_DATA: None,
        Keys.RESULT_PRIOR_LEDGER: None,
        Keys.RESULT_VERSION: 0,
        Keys.MANUALLY_EDITED: False,
        Keys.EXTRA_COLS: [],
        Keys.EXTRA_VALS: {},
        Keys.PALETTE: dict(DEFAULT_PALETTE),
        Keys.COL_ORDER: [],
        Keys.KNOWN_COLS: [],
        Keys.EXPORT_CACHE: {},
        Keys.DEMO_LOADED: False,
    }


def init_session_state() -> None:
    """Populate any missing session-state keys with their defaults."""
    for key, value in _defaults().items():
        if key not in st.session_state:
            st.session_state[key] = value


def bump_result_version() -> None:
    """Invalidate result-derived caches (the export cache keys off this)."""
    st.session_state[Keys.RESULT_VERSION] += 1


def set_result(df, data, prior_ledger) -> None:
    """Store a fresh solver result and reset the manual-edit state."""
    st.session_state[Keys.RESULT_DF] = df
    st.session_state[Keys.SOLVER_DF] = df
    st.session_state[Keys.RESULT_DATA] = data
    st.session_state[Keys.RESULT_PRIOR_LEDGER] = prior_ledger
    st.session_state[Keys.MANUALLY_EDITED] = False
    bump_result_version()
