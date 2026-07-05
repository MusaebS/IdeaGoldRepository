"""Tab ④ carryover-ledger panel: upload, edit in a grid, download.

The ledger used to be an opaque upload; this panel turns it into an editable
table so real-world schedule changes (a swap after publishing, a correction)
can be maintained in the fairness history. Whatever the grid shows is the
ledger the next Generate uses, and it can be downloaded again without
generating at all.
"""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from model.ledger import ledger_from_json, ledger_to_json, ledger_to_rows, rows_to_ledger
from ui.state import Keys

_EDITOR_KEY = "ledgrid_editor"
_COLUMNS = ["Resident", "Total", "Weekend"]


def render_ledger_panel(roster: list) -> dict | None:
    """Render the ledger uploader + editable grid; return the active ledger.

    Returns ``None`` when no ledger is loaded (a standalone one-off block).
    The returned — possibly hand-edited — ledger feeds ``build_schedule`` and,
    through ``set_result``, the results page's updated-ledger download.
    """
    uploaded = st.file_uploader(
        "Load fairness ledger (JSON)", type="json", key="ledger_upload"
    )
    if uploaded is not None:
        # Import once per file: the uploader returns the same file on every
        # rerun and re-parsing it would clobber grid edits.
        sig = hashlib.md5(uploaded.getvalue()).hexdigest()
        if st.session_state.get(Keys.LEDGER_SIG) != sig:
            st.session_state[Keys.LEDGER_SIG] = sig  # set first: a bad file never loops
            try:
                loaded = ledger_from_json(uploaded.getvalue().decode("utf-8"))
            except Exception as exc:
                st.error(f"Could not read ledger: {exc}")
            else:
                st.session_state[Keys.LEDGER_BASE] = loaded
                st.session_state[Keys.LEDGER_ROWS] = ledger_to_rows(loaded)
                st.session_state.pop(_EDITOR_KEY, None)
                st.rerun()

    rows = st.session_state.get(Keys.LEDGER_ROWS)
    if rows is None:
        if st.button("Start an empty ledger", key="ledger_start"):
            st.session_state[Keys.LEDGER_ROWS] = []
            st.session_state[Keys.LEDGER_BASE] = {}
            st.session_state.pop(_EDITOR_KEY, None)
            st.rerun()
        return None

    st.caption(
        "Edit the cumulative points below (e.g. after a real-world swap) — "
        "add or remove residents with the grid's +/– controls. The edited "
        "ledger is what the next Generate balances against."
    )
    grid = pd.DataFrame(rows, columns=_COLUMNS) if rows else pd.DataFrame(columns=_COLUMNS)
    edited = st.data_editor(
        grid,
        key=_EDITOR_KEY,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Resident": st.column_config.TextColumn("Resident", required=True),
            "Total": st.column_config.NumberColumn("Total", format="%.1f"),
            "Weekend": st.column_config.NumberColumn("Weekend", format="%.1f"),
        },
    )
    edited_rows = edited.to_dict("records")
    st.session_state[Keys.LEDGER_ROWS] = edited_rows

    ledger, problems = rows_to_ledger(
        edited_rows, base=st.session_state.get(Keys.LEDGER_BASE)
    )
    for problem in problems:
        st.warning(problem)
    if roster:
        strangers = sorted(name for name in ledger if name not in set(roster))
        if strangers:
            st.caption(
                "In the ledger but not on this block's roster (history kept): "
                + ", ".join(strangers)
            )

    bcols = st.columns(2)
    bcols[0].download_button(
        "Download edited ledger (JSON)",
        ledger_to_json(ledger),
        file_name="fairness_ledger_edited.json",
        mime="application/json",
        use_container_width=True,
    )
    if bcols[1].button("Clear ledger (standalone block)", key="ledger_clear"):
        # LEDGER_SIG is kept on purpose: the file still sitting in the
        # uploader must not silently re-import on the next rerun.
        st.session_state[Keys.LEDGER_ROWS] = None
        st.session_state[Keys.LEDGER_BASE] = None
        st.session_state.pop(_EDITOR_KEY, None)
        st.rerun()
    return ledger or None
