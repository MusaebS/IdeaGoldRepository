"""Results rendering: metrics, styled grid, fairness summary, downloads."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from model.coloring import COLOR_MODES, DEFAULT_PALETTE, schedule_cell_colors, theme_palette
from model.exporters import (
    build_assignment_frame,
    build_cumulative_frame,
    build_fairness_frame,
    schedule_to_excel_bytes,
    schedule_to_pdf_bytes,
    spreadsheet_safe_frame,
)
from model.fairness import (
    assignment_rationale,
    calculate_points,
    fairness_range_lines,
    format_fairness_log,
    preference_satisfaction,
    schedule_quality,
    quality_diagnosis,
)
from model.ledger import LedgerPolicy, block_adjustments, ledger_to_json, update_ledger
from model.solve_report import convergence_verdict
from model.validation import validate_schedule

from ui.editors import custom_columns_editor
from ui.state import Keys, apply_manual_edits, normalize_edited_schedule, revert_manual_edits
from ui.theme import render_card, render_section_header, render_status


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
    limit = df.attrs.get("time_limit_sec") if hasattr(df, "attrs") else None
    last_impr = df.attrs.get("last_improvement_sec") if hasattr(df, "attrs") else None
    # Turn the raw status/timings into a plain verdict on whether more solver
    # time would help. This replaces guess-and-check ("try 400s, then 500s"):
    # "still improving" says raise the limit (and to what); "converged" / proven
    # optimal says more time won't help, so stop re-running.
    verdict = convergence_verdict(status, wall, limit, last_impr)
    if verdict.level == "improving":
        st.warning(
            f"⏱️ **{verdict.headline}.** {verdict.detail} You can set it in the "
            "⑤ Review & run tab, then regenerate."
        )
    elif verdict.level == "optimal":
        st.success(f"✅ **{verdict.headline}.** {verdict.detail}")
    elif verdict.level == "converged":
        st.info(f"✅ **{verdict.headline}.** {verdict.detail}")
    if status:
        # The applied min_gap is shown so a relax-and-retry solve can never
        # silently differ from what the user thinks was used.
        detail = f"Solver status: {status} · seed {data.seed} · min_gap {data.min_gap}"
        if wall is not None:
            detail += f" · {wall:.2f}s"
        if limit:
            detail += f" (limit {limit:.0f}s)"
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
        tc = st.columns([2, 2, 4], vertical_alignment="bottom")
        with tc[0]:
            st.color_picker(
                "Theme colour", DEFAULT_PALETTE["points"], key="pal_theme",
                help="Pick one colour and apply — the role shades below are "
                "derived from it automatically (unfilled stays the warning red).",
            )
        with tc[1]:
            st.button(
                "Apply theme shades",
                key="pal_theme_apply",
                on_click=_apply_theme,
                width="stretch",
            )
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


def _current_ledger_policy() -> LedgerPolicy:
    """Return the ledger policy selected for this result/export session."""
    return LedgerPolicy(
        no_refund_penalties=st.session_state.get(Keys.LEDGER_NO_REFUND, True),
        no_catchup_excused=st.session_state.get(Keys.LEDGER_NO_CATCHUP, True),
    )


def _render_downloads(final_df, df, data, points, color_mode, palette, prior_ledger) -> None:
    st.subheader("Downloads")
    log_text = format_fairness_log(df, data, points=points)
    policy = _current_ledger_policy()
    export_sig = (
        st.session_state[Keys.RESULT_VERSION],
        color_mode,
        tuple(sorted(palette.items())),
        tuple(final_df.columns),
        policy.no_refund_penalties,
        policy.no_catchup_excused,
        tuple(
            (name, tuple(sorted(vals.items())))
            for name, vals in sorted(st.session_state[Keys.EXTRA_VALS].items())
        ),
    )
    dcols = st.columns(3)
    dcols[0].download_button(
        "Download CSV (schedule)",
        spreadsheet_safe_frame(final_df).to_csv(index=False),
        file_name="schedule.csv",
        mime="text/csv",
        width="stretch",
    )
    try:
        excel_bytes = cached_export(
            "excel", export_sig,
            lambda: schedule_to_excel_bytes(
                final_df, data, points=points, color_mode=color_mode, palette=palette,
                prior_ledger=prior_ledger,
                authoritative_df=df,
                ledger_policy=policy,
            ),
        )
        dcols[1].download_button(
            "Download Excel (schedule + fairness)",
            excel_bytes,
            file_name="schedule.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
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
                authoritative_df=df,
                ledger_policy=policy,
            ),
        )
        dcols[2].download_button(
            "Download PDF (schedule + fairness)",
            pdf_bytes,
            file_name="schedule.pdf",
            mime="application/pdf",
            width="stretch",
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
        width="stretch",
    )
    dcols2[1].download_button(
        "Download updated ledger (for next block)",
        ledger_to_json(update_ledger(prior_ledger, df, data, policy=policy)),
        file_name=f"fairness_ledger_through_{data.end_date.isoformat()}.json",
        mime="application/json",
        width="stretch",
    )
    st.caption(
        "Keep the ledger — it's the cumulative fairness record. The app does "
        "not durably store it between sessions, so re-upload it under "
        "History → Fairness ledger next block to keep months fair."
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
    non-resident marker a regular open-demand cell may be set to:

    * ``"Unfilled"`` — a coverage gap (someone should be here but isn't): counted
      against coverage, its points reported as unfilled, flagged red.
    Configured closure and NF-overlay cells are protected; demand-changing
    closures must be configured before running the optimiser.
    """
    from model.night_float import nf_cells_from_attr

    pool = data.juniors if shift.role == "Junior" else data.seniors
    exempt = data.exempt_shifts or {}
    options = [p for p in pool if shift.label not in exempt.get(p, ())]
    for (_day, lbl), name in nf_cells_from_attr(df).items():
        if lbl == shift.label and name not in options:
            options.append(name)
    return options + ["Unfilled"]


def _render_manual_edit(df, result_data) -> None:
    with st.expander("Manual edit & revalidate", expanded=st.session_state[Keys.MANUALLY_EDITED]):
        st.caption(
            "Change assignments below, review the live preview, then click "
            "**Apply edits** to make them the schedule — fairness, the log, and "
            "every download will follow. Nothing changes until you apply. "
            "Set a regular cell to **Unfilled** for a coverage gap. Configured "
            "closures and night-float overlay cells are protected because they "
            "define demand before optimisation; change those policies and "
            "regenerate instead."
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
        if bcols[0].button(
            "Apply edits",
            type="primary",
            key="apply_edits",
            disabled=bool(issues),
            help=(
                "Fix every listed constraint issue before applying."
                if issues else "Apply this validated schedule."
            ),
        ):
            try:
                apply_manual_edits(edited)
            except ValueError as exc:  # defensive: revalidate inside the mutation too
                st.error(str(exc))
            else:
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


def _render_overview(df, data, points, quality) -> None:
    """Render solver health, schedule quality, and preference outcomes."""
    render_section_header(
        "At-a-glance health",
        "Confirm coverage and fairness before reviewing individual assignments.",
        eyebrow="Overview",
        level=3,
    )
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

    mcols = st.columns(3)
    mcols[0].metric("Schedule quality", f"{quality['score']} / 100")
    mcols[1].metric("Slots filled", f"{quality['filled']}/{quality['total_slots']}")
    mcols[2].metric("Unfilled", quality["unfilled"])
    st.caption(
        f"Total-points range {quality['total_range']:.1f} · "
        f"weekend range {quality['weekend_range']:.1f} (smaller is fairer) · "
        f"score = 50% coverage ({quality['coverage']:.0%}) + 30% total balance "
        f"({quality['balance_total']:.0%}) + 20% weekend balance "
        f"({quality['balance_weekend']:.0%})"
    )
    if quality["score"] < 90:
        reasons = quality_diagnosis(df, data, quality)
        if reasons:
            with st.expander("Why isn't the quality higher?", expanded=True):
                for reason in reasons:
                    st.write(f"- {reason}")
    pref_stats = preference_satisfaction(df, data)
    if pref_stats:
        st.caption(
            "Preference matches (soft, fairness untouched): "
            + " · ".join(
                f"{person} {matched}/{total}"
                for person, (matched, total) in sorted(pref_stats.items())
            )
        )


def _render_schedule_workspace(df, data) -> tuple:
    """Render the schedule grid, cosmetic controls, and manual-edit workflow."""
    render_section_header(
        "Schedule workspace",
        "Shape the presentation, inspect every day, or make a controlled manual correction.",
        eyebrow="Schedule",
        level=3,
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
            style_schedule(final_df, data, color_mode, palette), width="stretch"
        )
    except Exception:
        # Colouring must never take down the results; fall back to plain.
        st.caption("Styled view unavailable; showing the plain table.")
        st.dataframe(final_df, width="stretch")

    _render_manual_edit(df, data)
    return final_df, color_mode, palette


_ROLE_HUES = {"Junior": "#5ab478", "Senior": "#966edc"}  # coloring.DEFAULT_PALETTE


def _workload_chart(role_frame, role: str, target: float | None):
    """Horizontal grouped bars (Total + Weekend) sorted by load, target rule."""
    long = role_frame.melt(
        id_vars=["Resident"],
        value_vars=["Total points", "Weekend points"],
        var_name="Kind",
        value_name="Points",
    )
    order = (
        role_frame.sort_values("Total points", ascending=False)["Resident"].tolist()
    )
    base_hue = _ROLE_HUES.get(role, "#5ab478")
    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            y=alt.Y("Resident:N", sort=order, title=None),
            x=alt.X("Points:Q", title="Points"),
            yOffset="Kind:N",
            color=alt.Color(
                "Kind:N",
                scale=alt.Scale(domain=["Total points", "Weekend points"],
                                range=[base_hue, "#c9a227"]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=["Resident", "Kind", "Points"],
        )
        .properties(height=max(120, 16 * len(role_frame)))
    )
    if target:
        rule = (
            alt.Chart(pd.DataFrame({"target": [target]}))
            .mark_rule(color="#7A5800", strokeDash=[4, 3])
            .encode(x="target:Q")
        )
        chart = chart + rule
    return chart


def _cumulative_chart(cum_frame, role: str):
    """Stacked bars: prior-block standing (grey) + this block (role hue)."""
    order = (
        cum_frame.drop_duplicates("Resident")
        .sort_values("Cumulative", ascending=False)["Resident"].tolist()
    )
    return (
        alt.Chart(cum_frame)
        .mark_bar()
        .encode(
            y=alt.Y("Resident:N", sort=order, title=None),
            x=alt.X("sum(Points):Q", title="Cumulative points (prior + this block)"),
            color=alt.Color(
                "Segment:N",
                scale=alt.Scale(domain=["Prior blocks", "This block"],
                                range=["#b9b2a4", _ROLE_HUES.get(role, "#5ab478")]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            order=alt.Order("Segment:N", sort="ascending"),
            tooltip=["Resident", "Segment", "Points", "Cumulative"],
        )
        .properties(height=max(120, 16 * cum_frame["Resident"].nunique()))
    )


def _render_role_fairness(
    role: str, fair_frame, points, data, df, prior_ledger, ledger_policy
) -> None:
    """One role's summary lines, table, workload chart, and cumulative chart."""
    members = data.juniors if role == "Junior" else data.seniors
    role_points = {p: v for p, v in points.items() if p in members}
    role_frame = fair_frame[fair_frame["Role"] == role]
    if not len(role_frame) or not role_points:
        return
    for line in fairness_range_lines(role_points):
        st.write(line)
    display = role_frame.drop(
        columns=[c for c in ("Role", "Notes") if c in role_frame.columns]
    )
    st.dataframe(display, width="stretch", hide_index=True)
    target_map = (df.attrs.get("target_total_map") or {}) if hasattr(df, "attrs") else {}
    role_targets = [target_map[p] for p in members if p in target_map]
    target = sum(role_targets) / len(role_targets) if role_targets else None
    st.caption("Workload by resident — dashed line marks the fair-share target.")
    st.altair_chart(
        _workload_chart(role_frame, role, target), use_container_width=True
    )
    if prior_ledger:
        cum_frame = build_cumulative_frame(
            role_points, prior_ledger, data, ledger_policy=ledger_policy
        )
        if len(cum_frame):
            st.caption(
                "Cumulative standing: grey = carried in from the uploaded "
                "ledger, coloured = earned this block. Even bar ends mean the "
                "history is levelling out."
            )
            st.altair_chart(_cumulative_chart(cum_frame, role), use_container_width=True)


def _render_fairness_workspace(df, data, points, prior_ledger) -> None:
    """Render per-role workload ranges, detail tables, charts, and downloads."""
    render_section_header(
        "Fairness review",
        "Compare regular workload, cumulative history, and night-float duty before publishing.",
        eyebrow="Fairness",
        level=3,
    )
    ranges = fairness_range_lines(points)
    if not ranges:
        render_status(
            "Fairness detail will appear when the result includes resident assignments.",
            title="No fairness rows to compare",
            tone="info",
        )
        return

    ledger_policy = _current_ledger_policy()
    fair_frame = build_fairness_frame(
        points, data, df, prior_ledger, ledger_policy=ledger_policy
    )
    if not len(fair_frame):
        return
    st.caption(
        "Fairness is balanced within each role (juniors and seniors work "
        "different shift pools). Per resident: calls and points per shift "
        "type, targets and deviations, and cumulative history with a ledger."
    )
    roles = [r for r in ("Junior", "Senior") if (fair_frame["Role"] == r).any()]
    if len(roles) == 2:
        role_tabs = st.tabs([
            f"Juniors ({int((fair_frame['Role'] == 'Junior').sum())})",
            f"Seniors ({int((fair_frame['Role'] == 'Senior').sum())})",
        ])
        for tab, role in zip(role_tabs, ("Junior", "Senior")):
            with tab:
                _render_role_fairness(
                    role, fair_frame, points, data, df, prior_ledger, ledger_policy
                )
    else:
        _render_role_fairness(
            roles[0], fair_frame, points, data, df, prior_ledger, ledger_policy
        )

    noted = fair_frame[fair_frame.get("Notes", pd.Series(dtype=object)).notna()] \
        if "Notes" in fair_frame.columns else fair_frame.iloc[0:0]
    if len(noted):
        with st.expander(f"Load annotations ({len(noted)} resident(s))"):
            st.caption(
                "Rules that shaped these residents' fair share — kept out of "
                "the tables above so they stay readable. The same notes appear "
                "as numbered footnotes in the PDF report."
            )
            st.dataframe(
                noted[["Resident", "Role", "Notes"]],
                width="stretch",
                hide_index=True,
            )
    st.download_button(
        "Download fairness table (CSV)",
        spreadsheet_safe_frame(fair_frame).to_csv(index=False),
        file_name="fairness_table.csv",
        mime="text/csv",
    )
    nf_duty = {
        name: int(info.get("night_float", 0))
        for name, info in points.items()
        if info.get("night_float", 0)
    }
    if nf_duty:
        st.caption(
            "Night-float duty (days, outside regular fairness): "
            + " · ".join(f"{p} {d}" for p, d in sorted(nf_duty.items()))
        )


def _render_audit_workspace(df, data) -> None:
    """Render assignment-level evidence and the rationale explorer."""
    render_section_header(
        "Trace every decision",
        "Archive the slot-level record, then inspect why a particular assignment was made.",
        eyebrow="Audit trail",
        level=3,
    )
    with st.expander("Per-call detail (audit)", expanded=False):
        st.caption(
            "Every (date, shift) slot with who took it and what it was worth — "
            "download and archive it for future reference."
        )
        call_frame = build_assignment_frame(df, data)
        st.dataframe(call_frame, width="stretch")
        st.download_button(
            "Download per-call CSV",
            spreadsheet_safe_frame(call_frame).to_csv(index=False),
            file_name="per_call_detail.csv",
            mime="text/csv",
        )
    _render_rationale(df, data)


def render_results() -> None:
    """Render the persistent result as five eager, task-focused workspaces.

    Results come from ``session_state`` so they survive cosmetic reruns without
    re-solving. Streamlit tabs are intentionally eager: every existing widget
    remains mounted and every export is prepared from the same display frame.
    """
    render_section_header(
        "Results studio",
        "Review quality, refine the schedule, verify fairness, audit decisions, and export.",
        eyebrow="Result workspace",
    )
    if st.session_state[Keys.RESULT_DF] is None:
        render_status(
            "Complete the configuration, then use Review & run to generate a schedule.",
            title="No schedule generated yet",
            label="Ready",
            tone="info",
        )
        render_card(
            "Your review workspace is ready",
            "Once a schedule is generated, this area opens into Overview, Schedule, "
            "Fairness, Audit trail, and Export workspaces without losing your inputs.",
            eyebrow="What happens next",
            footer="All result calculations and downloads stay tied to the saved schedule.",
        )
        return

    df = st.session_state[Keys.RESULT_DF]
    data = st.session_state[Keys.RESULT_DATA]
    prior_ledger = st.session_state.get(Keys.RESULT_PRIOR_LEDGER)
    result_fp = st.session_state.get(Keys.RESULT_CONFIG_FINGERPRINT)
    current_fp = st.session_state.get(Keys.CURRENT_CONFIG_FINGERPRINT)
    if result_fp and current_fp and result_fp != current_fp:
        st.warning(
            "Configuration changed after this schedule was generated. These "
            "results still reflect the saved solve; generate again before "
            "publishing or carrying its ledger forward."
        )
    points = calculate_points(df, data)
    quality = schedule_quality(df, data, points=points)

    overview_tab, schedule_tab, fairness_tab, audit_tab, export_tab = st.tabs(
        ["Overview", "Schedule", "Fairness", "Audit trail", "Export"]
    )
    with overview_tab:
        _render_overview(df, data, points, quality)
    with schedule_tab:
        final_df, color_mode, palette = _render_schedule_workspace(df, data)
    with fairness_tab:
        _render_fairness_workspace(df, data, points, prior_ledger)
    with audit_tab:
        _render_audit_workspace(df, data)
    with export_tab:
        render_section_header(
            "Publish and carry forward",
            "Download the schedule, its evidence, and the ledger needed to keep future blocks fair.",
            eyebrow="Export",
            level=3,
        )
        _render_downloads(final_df, df, data, points, color_mode, palette, prior_ledger)
