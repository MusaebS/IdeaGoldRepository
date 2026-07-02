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


def with_attrs(new_df, source_df):
    """Copy ``source_df.attrs`` onto ``new_df`` and return it.

    ``st.data_editor`` returns a fresh Arrow-roundtripped frame without
    ``attrs`` — but the solver-resolved fairness targets live there, and the
    fairness log / exports read them. Losing them silently would blank every
    target/deviation column.
    """
    try:
        new_df.attrs = dict(source_df.attrs)
    except (AttributeError, TypeError):  # pragma: no cover - stub frames
        pass
    return new_df


def _normalize_cell(value) -> str:
    """An empty/cleared editor cell means the slot is unfilled."""
    if value is None:
        return "Unfilled"
    if isinstance(value, float) and value != value:  # NaN from a cleared cell
        return "Unfilled"
    text = str(value).strip()
    return text if text else "Unfilled"


def normalize_edited_schedule(edited, base):
    """Return an edited schedule frame cleaned up for use as the live result.

    - ``Date`` / ``Day`` are restored from ``base``: they are disabled in the
      editor so their values are unchanged by construction, but the Arrow
      round-trip turns dates into Timestamps, which would break holiday /
      weekend matching downstream.
    - Blank or cleared shift cells become ``"Unfilled"``.
    - ``base.attrs`` (solver targets) are carried over.
    """
    out = edited.copy()
    for col in ("Date", "Day"):
        if col in out.columns and col in base.columns:
            out[col] = list(base[col])
    for col in out.columns:
        if col in ("Date", "Day"):
            continue
        out[col] = [_normalize_cell(v) for v in out[col]]
    return with_attrs(out, base)


def apply_manual_edits(edited) -> None:
    """Make an edited schedule the live result (fairness/exports follow it)."""
    base = st.session_state[Keys.RESULT_DF]
    cleaned = normalize_edited_schedule(edited, base)
    cleaned.attrs["manually_edited"] = True
    st.session_state[Keys.RESULT_DF] = cleaned
    st.session_state[Keys.MANUALLY_EDITED] = True
    bump_result_version()  # invalidates the cached Excel/PDF exports
    st.session_state.pop(Keys.SCHEDULE_EDITOR, None)


def revert_manual_edits() -> None:
    """Restore the pristine solver result."""
    st.session_state[Keys.RESULT_DF] = st.session_state[Keys.SOLVER_DF]
    st.session_state[Keys.MANUALLY_EDITED] = False
    bump_result_version()
    st.session_state.pop(Keys.SCHEDULE_EDITOR, None)
