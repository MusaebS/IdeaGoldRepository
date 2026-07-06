"""Results rendering: metrics, styled grid, fairness summary, downloads."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from model.coloring import COLOR_MODES, DEFAULT_PALETTE, schedule_cell_colors, theme_palette
from model.exporters import (
    build_assignment_frame,
    build_fairness_frame,
    schedule_to_excel_bytes,
    schedule_to_pdf_bytes,
)
from model.fairness import (
    assignment_rationale,
    calculate_points,
    fairness_range_lines,
    format_fairness_log,
    preference_satisfaction,
    schedule_quality,
)
from model.ledger import LedgerPolicy, block_adjustments, ledger_to_json, update_ledger
from model.validation import validate_schedule

from ui.editors import custom_columns_editor
from ui.state import Keys, apply_manual_edits, normalize_edited_schedule, revert_manual_edits


def style_schedule(df, data, color_mode, palette=None):
    """Return a Styler shading the grid by ``color_mode`` (unfilled always flagged).

    Uses the same ``schedule_cell_colors`` map the Excel/PDF exports use, so the
    on-screen view and the downloads agree cell-for-cell. ``palette`` recolours
    the named roles. Cosmetic custom columns are simply left unshaded.
    """
    color_map = schedule_cell_colors(df, data, color_mode, palette)
    columns = list(df.columns)
    records = df.to_dict("records")

    def _apply(_):
        css = pd.DataFrame("", index=df.index, columns=df.columns)
        for (row_idx, label), hexcolor in color_map.items():
            if label not in columns:
                continue
            style = f"background-color: {hexcolor}"
            if records[row_idx].get(label) in (None, "Unfilled"):
                style += "; color: #b00000; font-weight: 600"
            css.iloc[row_idx, columns.index(label)] = style
        return css

    return df.style.apply(_apply, axis=None)


def final_schedule_df(base_df, extra_cols, extra_vals, order):
    """Return a *display/export* copy of the schedule with cosmetic columns.

    Custom columns are looked up per date and appended; ``order`` reorders and
    (by omission) hides columns. Purely cosmetic — the returned frame is never
    fed back into scheduling maths, and ``base_df.attrs`` (solver-resolved
    targets) are carried over so exports still show deviations.
    """
    out = base_df.copy()
    dates = list(base_df["Date"])
    for name in extra_cols:
        vals = extra_vals.get(name, {})
        out[name] = [vals.get(str(d), "") for d in dates]
    if order:
        # ``order`` is the explicit show list (from the multiselect); columns
        # left out are hidden. Callers pass the full set when nothing is hidden.
        keep = [c for c in order if c in out.columns]
        if keep:
            out = out[keep]
    try:
        out.attrs = dict(base_df.attrs)
    except (AttributeError, TypeError):  # pragma: no cover - stub frames
        pass
    return out


def reconcile_column_order(all_cols) -> None:
    """Keep ``col_order`` session state in sync with the live column set.

    New columns are appended (shown); removed columns drop out. A user's manual
    reorder / hide (deselecting in the multiselect) is otherwise preserved.
    """
    known = st.session_state[Keys.KNOWN_COLS]
    order = st.session_state[Keys.COL_ORDER]
    for c in all_cols:
        if c not in known:
            order.append(c)
    order[:] = [c for c in order if c in all_cols]
    st.session_state[Keys.KNOWN_COLS] = list(all_cols)
    st.session_state[Keys.COL_ORDER] = order


def cached_export(kind, signature, builder):
    """Memoise an expensive export so a download click doesn't rebuild it.

    Every Streamlit download click is a full rerun; regenerating the Excel/PDF
    (seconds on a large roster) each time blanks the results while it runs — the
    "download makes the schedule disappear" bug. Caching by ``signature`` (result
    version + colours + columns) keeps downloads instant and the view stable.
    """
    cache = st.session_state[Keys.EXPORT_CACHE]
    if cache.get("signature") != signature:
        cache.clear()
        cache["signature"] = signature
    if kind not in cache:
        cache[kind] = builder()
    return cache[kind]


def _reset_palette() -> None:
    """Restore default colours (an on_click callback so pickers re-init)."""
    st.session_state[Keys.PALETTE] = dict(DEFAULT_PALETTE)
    for key in DEFAULT_PALETTE:
        st.session_state.pop(f"{Keys.PAL_PREFIX}{key}", None)


def _apply_theme() -> None:
    """Derive all role colours from the theme colour (on_click callback)."""
    st.session_state[Keys.PALETTE] = theme_palette(
        st.session_state.get("pal_theme", DEFAULT_PALETTE["points"]),
        current=st.session_state[Keys.PALETTE],
    )
    for key in DEFAULT_PALETTE:
        st.session_state.pop(f"{Keys.PAL_PREFIX}{key}", None)


def _render_solver_caption(df, data) -> None:
    warning = df.attrs.get("solver_warning") if hasattr(df, "attrs") else None
    if warning:
        st.warning(warning)
    status = df.attrs.get("solver_status") if hasattr(df, "attrs") else None
    wall = df.attrs.get("wall_time_sec") if hasattr(df, "attrs") else None
    if status:
        detail = f"Solver status: {status} · seed {data.seed}"
        if wall is not None:
            detail += f" · {wall:.2f}s"
        st.caption(detail)


def _render_customisation(df) -> tuple:
    """The cosmetic expander: colour mode, palette pickers, custom columns."""
    with st.expander("🎨 Customise the schedule — colours & columns", expanded=False):
        color_label = st.selectbox(
            "Colour cells by",
            list(COLOR_MODES),
            index=0,
            help="Shade the grid; the same colours flow into the Excel and PDF downloads.",
        )
        tc = st.columns([2, 2, 4])
        with tc[0]:
            st.color_picker(
                "Theme colour", DEFAULT_PALETTE["points"], key="pal_theme",
                help="Pick one colour and apply — the role shades below are "
                "derived from it automatically (unfilled stays the warning red).",
            )
        with tc[1]:
            st.markdown("&nbsp;")
            st.button("Apply theme shades", key="pal_theme_apply", on_click=_apply_theme)
        st.caption("Colours for each role (used by the modes above):")
        pcols = st.columns(5)
        for col, (key, lbl) in zip(
            pcols,
            [("weekend", "Weekend"), ("points", "Points"), ("senior", "Senior"),
             ("junior", "Junior"), ("unfilled", "Unfilled")],
        ):
            st.session_state[Keys.PALETTE][key] = col.color_picker(
                lbl,
                st.session_state[Keys.PALETTE].get(key, DEFAULT_PALETTE[key]),
                key=f"{Keys.PAL_PREFIX}{key}",
            )
        # on_click runs before the next script pass, so the pickers genuinely
        # re-initialise from the defaults (a same-run pop left them stale).
        st.button("Reset colours", key="pal_reset", on_click=_reset_palette)
        st.divider()
        custom_columns_editor(df)
    return COLOR_MODES[color_label], st.session_state[Keys.PALETTE]


def _ledger_policy_notes(policy, prior_ledger, data) -> list:
    """Human-readable summary of the adjustments baked into the saved ledger."""
    if not (policy.no_refund_penalties or policy.no_catchup_excused):
        return []
    notes = []
    for person, adj in sorted(block_adjustments(prior_ledger, data).items()):
        if policy.no_refund_penalties and adj["penalty"]:
            notes.append(f"{person} +{adj['penalty']:g} penalty not carried")
        if policy.no_catchup_excused and abs(adj["excused_total"]) > 1e-9:
            notes.append(f"{person} {adj['excused_total']:+.1f} pts excused credit")
    return notes


def _render_downloads(final_df, df, data, points, color_mode, palette, prior_ledger) -> None:
    st.subheader("Downloads")
    log_text = format_fairness_log(df, data, points=points)
    export_sig = (
        st.session_state[Keys.RESULT_VERSION],
        color_mode,
        tuple(sorted(palette.items())),
        tuple(final_df.columns),
        tuple(
            (name, tuple(sorted(vals.items())))
            for name, vals in sorted(st.session_state[Keys.EXTRA_VALS].items())
        ),
    )
    dcols = st.columns(3)
    dcols[0].download_button(
        "Download CSV (schedule)",
        final_df.to_csv(index=False),
        file_name="schedule.csv",
        mime="text/csv",
        use_container_width=True,
    )
    try:
        excel_bytes = cached_export(
            "excel", export_sig,
            lambda: schedule_to_excel_bytes(
                final_df, data, points=points, color_mode=color_mode, palette=palette,
                prior_ledger=prior_ledger,
            ),
        )
        dcols[1].download_button(
            "Download Excel (schedule + fairness)",
            excel_bytes,
            file_name="schedule.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    except ImportError as exc:  # pragma: no cover - openpyxl not installed
        dcols[1].info(f"Excel export needs openpyxl: {exc}")
    except Exception as exc:
        dcols[1].error(f"Excel export failed: {exc}")
    try:
        pdf_bytes = cached_export(
            "pdf", export_sig,
            lambda: schedule_to_pdf_bytes(
                final_df, data, points=points, color_mode=color_mode, palette=palette,
                prior_ledger=prior_ledger,
            ),
        )
        dcols[2].download_button(
            "Download PDF (schedule + fairness)",
            pdf_bytes,
            file_name="schedule.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except ImportError as exc:  # pragma: no cover - reportlab not installed
        dcols[2].info(f"PDF export needs reportlab: {exc}")
    except Exception as exc:
        dcols[2].error(f"PDF export failed: {exc}")
    dcols2 = st.columns(2)
    dcols2[0].download_button(
        "Download Fairness Log",
        log_text,
        file_name="fairness_log.txt",
        use_container_width=True,
    )
    policy = LedgerPolicy(
        no_refund_penalties=st.session_state.get(Keys.LEDGER_NO_REFUND, True),
        no_catchup_excused=st.session_state.get(Keys.LEDGER_NO_CATCHUP, True),
    )
    dcols2[1].download_button(
        "Download updated ledger (for next block)",
        ledger_to_json(update_ledger(prior_ledger, df, data, policy=policy)),
        file_name=f"fairness_ledger_through_{data.end_date.isoformat()}.json",
        mime="application/json",
        use_container_width=True,
    )
    st.caption(
        "Keep the ledger — it's the cumulative fairness record. Streamlit Cloud "
        "doesn't store anything between sessions, so re-upload it under "
        "'Carryover fairness' next block to keep months fair."
    )
    notes = _ledger_policy_notes(policy, prior_ledger, data)
    if notes:
        st.caption("Ledger policy applied: " + "; ".join(notes) + ".")
    if st.checkbox("Show Fairness Log"):
        st.text(log_text)


def _shift_cell_options(data, shift, df=None) -> list:
    """Dropdown values for a shift column in the manual editor.

    Role-eligible residents (minus exemptions), plus any night-float coverer that
    appears in this column's overlay cells (so their value stays valid), plus the
    two non-resident markers every cell may be set to:

    * ``"Unfilled"`` — a coverage gap (someone should be here but isn't): counted
      against coverage, its points reported as unfilled, flagged red.
    * ``"Closed"`` — the slot is not staffed / unavailable (a resident shortage,
      a shift that does not run that day): removed from demand entirely, never
      counted as unfilled, and left out of points and fairness.

    Both are always offered so an editor can mark any slot either way; the
    recompute in ``normalize_edited_schedule`` makes the choice take effect.
    """
    from model.night_float import nf_cells_from_attr

    pool = data.juniors if shift.role == "Junior" else data.seniors
    exempt = data.exempt_shifts or {}
    options = [p for p in pool if shift.label not in exempt.get(p, ())]
    for (_day, lbl), name in nf_cells_from_attr(df).items():
        if lbl == shift.label and name not in options:
            options.append(name)
    return options + ["Unfilled", "Closed"]


def _render_manual_edit(df, result_data) -> None:
    with st.expander("Manual edit & revalidate", expanded=st.session_state[Keys.MANUALLY_EDITED]):
        st.caption(
            "Change assignments below, review the live preview, then click "
            "**Apply edits** to make them the schedule — fairness, the log, and "
            "every download will follow. Nothing changes until you apply. "
            "Set a cell to **Unfilled** for a coverage gap (counts against "
            "coverage) or **Closed** to stand the slot down as unavailable "
            "(removed from demand — not counted as unfilled, and outside points "
            "and fairness)."
        )
        # Dropdown cells restricted to role/NF-eligible residents stop typos at
        # the source; constraint issues (min-gap etc.) are still surfaced below.
        column_config = {
            sh.label: st.column_config.SelectboxColumn(
                sh.label, options=_shift_cell_options(result_data, sh, df), required=False
            )
            for sh in result_data.shifts
            if sh.label in df.columns
        }
        edited = st.data_editor(
            df,
            key=Keys.SCHEDULE_EDITOR,
            disabled=["Date", "Day"],
            column_config=column_config,
        )
        preview = normalize_edited_schedule(edited, df)
        issues = validate_schedule(preview, result_data)
        if issues:
            st.error(f"{len(issues)} constraint issue(s):")
            for issue in issues:
                st.write(f"- {issue}")
        else:
            st.success("No constraint violations.")
        edited_points = calculate_points(preview, result_data)
        edited_quality = schedule_quality(preview, result_data, points=edited_points)
        st.caption(f"Edited schedule quality: {edited_quality['score']} / 100")

        bcols = st.columns(2)
        if bcols[0].button("Apply edits", type="primary", key="apply_edits"):
            apply_manual_edits(edited)
            st.rerun()
        if st.session_state[Keys.MANUALLY_EDITED]:
            if bcols[1].button("Revert to solver result", key="revert_edits"):
                revert_manual_edits()
                st.rerun()


def _render_rationale(df, result_data) -> None:
    with st.expander("Why was a slot assigned?", expanded=False):
        labels = [s.label for s in result_data.shifts]
        dates = [row.get("Date") for row in df.to_dict("records")]
        if labels and dates:
            why_date = st.selectbox("Date", dates, key="why_date")
            why_label = st.selectbox("Shift", labels, key="why_label")
            for line in assignment_rationale(df, result_data, why_date, why_label):
                st.write(f"- {line}")
        else:
            st.caption("Generate a schedule with at least one shift to use this.")


def render_results() -> None:
    """Render everything below the Generate button, from session state.

    Results render from ``session_state`` so they survive reruns (e.g. changing
    the colour mode) without re-solving.
    """
    if st.session_state[Keys.RESULT_DF] is None:
        return
    df = st.session_state[Keys.RESULT_DF]
    data = st.session_state[Keys.RESULT_DATA]
    prior_ledger = st.session_state.get(Keys.RESULT_PRIOR_LEDGER)

    _render_solver_caption(df, data)

    if st.session_state[Keys.MANUALLY_EDITED]:
        st.warning(
            "Schedule manually edited — fairness, the log, and all downloads "
            "reflect your edits, not the raw solver output. Use 'Revert to "
            "solver result' in the manual-edit panel to undo."
        )
        edit_issues = validate_schedule(df, data)
        if edit_issues:
            st.error(
                f"The edited schedule violates {len(edit_issues)} constraint(s); "
                "details in the manual-edit panel and the fairness log."
            )

    points = calculate_points(df, data)
    quality = schedule_quality(df, data, points=points)
    mcols = st.columns(3)
    mcols[0].metric("Schedule quality", f"{quality['score']} / 100")
    mcols[1].metric("Slots filled", f"{quality['filled']}/{quality['total_slots']}")
    mcols[2].metric("Unfilled", quality["unfilled"])
    st.caption(
        f"Total-points range {quality['total_range']:.1f} · "
        f"weekend range {quality['weekend_range']:.1f} (smaller is fairer)"
    )
    pref_stats = preference_satisfaction(df, data)
    if pref_stats:
        st.caption(
            "Preference matches (soft, fairness untouched): "
            + " · ".join(
                f"{person} {matched}/{total}"
                for person, (matched, total) in sorted(pref_stats.items())
            )
        )

    color_mode, palette = _render_customisation(df)

    # Reorder / hide columns (cosmetic). Selection order = display order.
    all_cols = list(df.columns) + list(st.session_state[Keys.EXTRA_COLS])
    reconcile_column_order(all_cols)
    chosen = st.multiselect(
        "Columns to show (order = display order; unselect to hide)",
        all_cols,
        key=Keys.COL_ORDER,
    )
    order = chosen or all_cols
    final_df = final_schedule_df(
        df, st.session_state[Keys.EXTRA_COLS], st.session_state[Keys.EXTRA_VALS], order
    )

    try:
        st.dataframe(
            style_schedule(final_df, data, color_mode, palette), use_container_width=True
        )
    except Exception:
        # Colouring must never take down the results; fall back to plain.
        st.caption("Styled view unavailable; showing the plain table.")
        st.dataframe(final_df, use_container_width=True)

    ranges = fairness_range_lines(points)
    if ranges:
        st.subheader("Fairness summary")
        for line in ranges:
            st.write(line)
        fair_frame = build_fairness_frame(points, data, df, prior_ledger)
        if len(fair_frame):
            st.caption(
                "Per resident: calls and points per shift type, targets and "
                "deviations, cumulative history (with a ledger), and load notes."
            )
            st.dataframe(fair_frame, use_container_width=True)
            st.download_button(
                "Download fairness table (CSV)",
                fair_frame.to_csv(index=False),
                file_name="fairness_table.csv",
                mime="text/csv",
            )
            chart_df = fair_frame[
                ["Resident", "Total", "Weekend"]
            ].set_index("Resident")
            st.caption("Regular workload by resident (points)")
            st.bar_chart(chart_df, stack=False)
            nf_duty = {
                name: int(info.get("night_float", 0)) for name, info in points.items()
                if info.get("night_float", 0)
            }
            if nf_duty:
                st.caption(
                    "Night-float duty (days, outside regular fairness): "
                    + " · ".join(f"{p} {d}" for p, d in sorted(nf_duty.items()))
                )

    with st.expander("Per-call detail (audit)", expanded=False):
        st.caption(
            "Every (date, shift) slot with who took it and what it was worth — "
            "download and archive it for future reference."
        )
        call_frame = build_assignment_frame(df, data)
        st.dataframe(call_frame, use_container_width=True)
        st.download_button(
            "Download per-call CSV",
            call_frame.to_csv(index=False),
            file_name="per_call_detail.csv",
            mime="text/csv",
        )

    _render_downloads(final_df, df, data, points, color_mode, palette, prior_ledger)
    _render_manual_edit(df, data)
    _render_rationale(df, data)
