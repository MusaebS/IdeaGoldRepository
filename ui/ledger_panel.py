"""Tab ④ carryover-ledger panel: upload, reconcile, edit in a grid, download.

The ledger used to be an opaque upload; this panel turns it into an editable
table so real-world schedule changes (a swap after publishing, a correction)
can be maintained in the fairness history. Whatever the grid shows is the
ledger the next Generate uses, and it can be downloaded again without
generating at all.

Because ledger names and shift labels are matched by exact string everywhere,
real-world drift (a misspelling fixed on the roster, a renamed shift, people
or shifts coming and going) silently splits or orphans history. After an
upload the reconcile section compares the ledger against the current roster
and shift catalogue and asks what to do about each mismatch — merge, keep as
history, or remove — applying nothing until confirmed.
"""
from __future__ import annotations

import hashlib
import re

import pandas as pd
import streamlit as st

from model.ledger import (
    drop_label,
    drop_person,
    ledger_from_json,
    ledger_to_json,
    ledger_to_rows,
    reconcile_report,
    rename_label,
    rename_person,
    rows_to_ledger,
)
from ui.state import Keys, flash

_EDITOR_KEY = "ledgrid_editor"
_COLUMNS = ["Resident", "Total", "Weekend"]
_KEEP = "Keep as history"
_REMOVE = "Remove from ledger"
_MERGE_PREFIX = "Merge into "


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", str(text))


def _clear_reconcile_widgets() -> None:
    for key in [k for k in st.session_state if str(k).startswith("ledgrec_")]:
        st.session_state.pop(key, None)


def _mismatch_choices(unknown, suggestions, candidates, kind: str, key_prefix: str) -> dict:
    """One selectbox per unmatched ledger entry; suggested matches first."""
    choices = {}
    for name in unknown:
        suggested = list(suggestions.get(name, ()))
        ordered = suggested + [c for c in candidates if c not in suggested]
        options = [_KEEP] + [f"{_MERGE_PREFIX}{c}" for c in ordered] + [_REMOVE]
        choices[name] = st.selectbox(
            f"Ledger {kind} “{name}”",
            options,
            key=f"{key_prefix}{_slug(name)}",
            help=f"“{name}” is in the ledger but not in the current setup. "
            "Merging moves its history onto the current entry (a fixed "
            "misspelling or rename); keeping leaves it dormant; removing "
            "discards the history.",
        )
    return choices


def _reconcile_section(ledger: dict, roster: list, shift_labels: list) -> None:
    """Post-upload confirmation step for names/shifts that don't match."""
    report = reconcile_report(ledger, roster, shift_labels)
    # An empty roster / shift list just means the config isn't set up yet —
    # nothing sensible to reconcile against.
    unknown_people = report.unknown_people if roster else ()
    unknown_labels = report.unknown_labels if shift_labels else ()

    if roster and report.new_people:
        st.caption(
            "On the roster with no ledger history yet (start at 0): "
            + ", ".join(report.new_people)
        )
    if shift_labels and report.new_labels:
        st.caption(
            "Current shifts with no ledger history yet: " + ", ".join(report.new_labels)
        )
    if not (unknown_people or unknown_labels):
        return

    sig = st.session_state.get(Keys.LEDGER_SIG) or "manual"
    if st.session_state.get(Keys.LEDGER_RECONCILE_DISMISSED) == sig:
        # Reviewed and kept as-is: stay quiet, but keep the facts visible.
        if unknown_people:
            st.caption(
                "In the ledger but not on this block's roster (history kept): "
                + ", ".join(unknown_people)
            )
        if unknown_labels:
            st.caption(
                "Ledger shift types not in the current shifts (history kept): "
                + ", ".join(unknown_labels)
            )
        return

    count = len(unknown_people) + len(unknown_labels)
    with st.expander(f"⚠️ Reconcile ledger with current setup ({count} to review)", expanded=True):
        st.caption(
            "Names and shift types are matched exactly, so a spelling fix or "
            "a renamed shift shows up here as unmatched. Choose what to do "
            "with each — nothing changes until you apply. Likely matches are "
            "listed first."
        )
        person_choices = _mismatch_choices(
            unknown_people, report.person_suggestions, roster, "name", "ledgrec_p_"
        )
        label_choices = _mismatch_choices(
            unknown_labels, report.label_suggestions, shift_labels, "shift", "ledgrec_l_"
        )
        acols = st.columns(2)
        if acols[0].button(
            "Apply choices", key="ledgrec_apply", type="primary", use_container_width=True
        ):
            merges = removals = 0
            for name, choice in person_choices.items():
                if choice.startswith(_MERGE_PREFIX):
                    ledger = rename_person(ledger, name, choice[len(_MERGE_PREFIX):])
                    merges += 1
                elif choice == _REMOVE:
                    ledger = drop_person(ledger, name)
                    removals += 1
            for label, choice in label_choices.items():
                if choice.startswith(_MERGE_PREFIX):
                    ledger = rename_label(ledger, label, choice[len(_MERGE_PREFIX):])
                    merges += 1
                elif choice == _REMOVE:
                    ledger = drop_label(ledger, label)
                    removals += 1
            kept = count - merges - removals
            # Replace the base wholesale: it is what re-attaches label history
            # on every grid edit, so a stale base would resurrect old names.
            st.session_state[Keys.LEDGER_BASE] = ledger
            st.session_state[Keys.LEDGER_ROWS] = ledger_to_rows(ledger)
            st.session_state[Keys.LEDGER_RECONCILE_DISMISSED] = sig
            st.session_state.pop(_EDITOR_KEY, None)
            _clear_reconcile_widgets()
            flash(
                f"Ledger reconciled: {merges} merged, {removals} removed, "
                f"{kept} kept as history."
            )
            st.rerun()
        if acols[1].button(
            "Keep everything as history", key="ledgrec_dismiss", use_container_width=True
        ):
            st.session_state[Keys.LEDGER_RECONCILE_DISMISSED] = sig
            _clear_reconcile_widgets()
            st.rerun()


def render_ledger_panel(roster: list, shift_labels: list | None = None) -> dict | None:
    """Render the ledger uploader + reconcile step + editable grid.

    Returns ``None`` when no ledger is loaded (a standalone one-off block).
    The returned — possibly hand-edited — ledger feeds ``build_schedule`` and,
    through ``set_result``, the results page's updated-ledger download.
    """
    shift_labels = shift_labels or []
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
                st.session_state[Keys.LEDGER_RECONCILE_DISMISSED] = None
                st.session_state.pop(_EDITOR_KEY, None)
                _clear_reconcile_widgets()
                labels = {
                    lbl
                    for entry in loaded.values()
                    for key in ("labels", "label_counts")
                    for lbl in entry.get(key) or {}
                }
                flash(
                    f"Loaded ledger: {len(loaded)} resident(s), "
                    f"{len(labels)} shift type(s) in history."
                )
                st.rerun()

    rows = st.session_state.get(Keys.LEDGER_ROWS)
    if rows is None:
        if st.button("Start an empty ledger", key="ledger_start"):
            st.session_state[Keys.LEDGER_ROWS] = []
            st.session_state[Keys.LEDGER_BASE] = {}
            st.session_state[Keys.LEDGER_RECONCILE_DISMISSED] = None
            st.session_state.pop(_EDITOR_KEY, None)
            flash("Started an empty ledger — this block seeds the fairness history.")
            st.rerun()
        return None

    st.caption(
        "Edit the cumulative points below (e.g. after a real-world swap) — "
        "add or remove residents with the grid's +/– controls. The edited "
        "ledger is what the next Generate balances against. To fix a "
        "misspelled name without losing its shift-type history, use the "
        "reconcile step below instead of retyping it here."
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
    _reconcile_section(ledger, list(roster or []), list(shift_labels))

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
        st.session_state[Keys.LEDGER_RECONCILE_DISMISSED] = None
        st.session_state.pop(_EDITOR_KEY, None)
        _clear_reconcile_widgets()
        flash("Ledger cleared — this block is standalone, with no fairness history.")
        st.rerun()
    return ledger or None
