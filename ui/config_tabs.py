"""Configuration workspaces and the Generate/solve flow."""
from __future__ import annotations

import os
from dataclasses import replace

import pandas as pd
import streamlit as st

from model.availability import (
    availability_template_csv,
    availability_template_xlsx,
    parse_availability_rows,
    read_availability_csv,
    read_availability_xlsx,
    rows_to_leaves,
)
from model.config_io import display_from_json, input_data_to_json, input_data_from_json
from model.data_models import (
    InputData,
    normalized_blackouts,
    normalized_closures,
    normalized_leaves,
    normalized_nf_assignments,
    normalized_reductions,
)
from model.demo_data import sample_shifts, sample_names
from model.optimiser import build_schedule
from model.validation import validate_input, config_warnings

from ui.editors import (
    WEEKDAY_LABELS,
    avoid_pairs_editor,
    blackouts_editor,
    caps_editor,
    closures_editor,
    date_range_editor,
    exemptions_editor,
    extra_points_editor,
    holidays_editor,
    named_groups_editor,
    night_float_editor,
    perks_editor,
    preferences_editor,
    reductions_editor,
    roster_editor,
    seniority_editor,
    shift_template_editor,
    weekday_points_editor,
)
from ui.diagnostics import render_diagnostics
from ui.ledger_panel import render_ledger_panel
from ui.state import Keys, flash, restore_display_state, set_result
from ui.theme import card_container, render_section_header, render_status
from ui.uploads import consume_upload_once


def load_demo_data_once() -> None:
    """Preload example shifts/names the first time Test mode is ticked."""
    if st.session_state[Keys.DEMO_LOADED]:
        return
    st.session_state[Keys.SHIFTS] = sample_shifts()
    juniors, seniors, nf_juniors, nf_seniors = sample_names()
    st.session_state[Keys.JUNIORS] = juniors
    st.session_state[Keys.SENIORS] = seniors
    st.session_state[Keys.NF_JUNIORS] = nf_juniors
    st.session_state[Keys.NF_SENIORS] = nf_seniors
    st.session_state[Keys.DEMO_LOADED] = True


def populate_editors_from_config(data: InputData, state=None) -> None:
    """Fill every editor's session state from a loaded config.

    The inverse of the ``InputData`` assembly in :func:`session_config_from_state` +
    ``_active_config_maps``: an uploaded config lands in the tabs themselves,
    ready to review and tweak, instead of being consumed invisibly at
    Generate time. Widgets pick the values up on the rerun that follows.
    ``state`` defaults to ``st.session_state``; tests may pass a plain dict.
    """
    ss = st.session_state if state is None else state
    ss[Keys.SHIFTS] = list(data.shifts)
    ss[Keys.JUNIORS] = list(data.juniors)
    ss[Keys.SENIORS] = list(data.seniors)
    ss[Keys.NF_JUNIORS] = list(data.nf_juniors)
    ss[Keys.NF_SENIORS] = list(data.nf_seniors)
    ss[Keys.START_DATE] = data.start_date
    ss[Keys.END_DATE] = data.end_date
    ss[Keys.MIN_GAP] = int(data.min_gap)
    ss[Keys.SEED] = int(data.seed)
    weekend_days = data.weekend_days if data.weekend_days is not None else [5, 6]
    ss[Keys.WEEKEND_LABELS] = [WEEKDAY_LABELS[d] for d in weekend_days]
    ss[Keys.WEEKEND_MULTIPLIER] = float(data.weekend_multiplier)
    ss[Keys.LEAVES] = list(data.leaves or [])
    ss[Keys.ROTATORS] = list(data.rotators or [])
    caps = {p: {"total": v} for p, v in (data.max_total or {}).items()}
    for person, excused in (data.max_total_excused or {}).items():
        if person in caps and excused:
            caps[person]["excused"] = True
    ss[Keys.CAPS] = caps
    ss[Keys.EXTRA_POINTS] = dict(data.extra_points or {})
    ss[Keys.WEEKDAY_POINTS] = dict(data.weekday_points or {})
    ss[Keys.HOLIDAYS] = list(data.holidays or [])
    ss[Keys.GROUP_FACTORS] = dict(data.group_factors or {})
    ss[Keys.RESIDENT_GROUPS] = dict(data.resident_groups or {})
    ss[Keys.PERKS] = list(data.perks or [])
    ss[Keys.EXEMPT_SHIFTS] = {
        p: list(labels) for p, labels in (data.exempt_shifts or {}).items()
    }
    ss[Keys.NAMED_GROUPS] = {
        g: list(members) for g, members in (data.named_groups or {}).items()
    }
    ss[Keys.BLACKOUTS] = list(data.blackouts or [])
    ss[Keys.REDUCTIONS] = list(data.reductions or [])
    ss[Keys.PREFERRED_SHIFTS] = {
        p: list(labels) for p, labels in (data.preferred_shifts or {}).items()
    }
    ss[Keys.PREFERRED_DAY_TYPE] = dict(data.preferred_day_type or {})
    ss[Keys.AVOID_PAIRS] = [tuple(pair) for pair in (data.avoid_pairs or [])]
    ss[Keys.NF_COVERAGE] = dict(data.nf_coverage or {})
    ss[Keys.NF_ASSIGNMENTS] = list(data.nf_assignments or [])
    ss[Keys.NF_REST_DAYS] = int(data.nf_rest_days)
    ss[Keys.CLOSURES] = list(data.closures or [])


def _active_config_maps() -> dict:
    """Filter caps / extra points / overrides down to the live roster & shifts.

    Returns the values ready for the ``InputData`` kwargs of the same names
    (empty maps already collapsed to ``None``).
    """
    active_people = set(st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS])
    max_total = {
        p: v["total"]
        for p, v in st.session_state[Keys.CAPS].items()
        if p in active_people and v.get("total")
    }
    # "Do not compensate later" flag per active cap (excused shortfall).
    max_total_excused = {
        p: True
        for p, v in st.session_state[Keys.CAPS].items()
        if p in max_total and v.get("excused")
    }
    extra_points = {
        p: v
        for p, v in st.session_state[Keys.EXTRA_POINTS].items()
        if p in active_people and v
    }
    shift_labels = {s.label for s in st.session_state[Keys.SHIFTS]}
    weekday_points = {
        k: v for k, v in st.session_state[Keys.WEEKDAY_POINTS].items() if k[0] in shift_labels
    }
    holidays = list(st.session_state[Keys.HOLIDAYS])
    group_factors = dict(st.session_state[Keys.GROUP_FACTORS])
    resident_groups = {
        p: g
        for p, g in st.session_state[Keys.RESIDENT_GROUPS].items()
        if p in active_people and g in group_factors
    }
    perks = [p for p in st.session_state[Keys.PERKS] if p.name in active_people]
    exempt_shifts = {
        p: [lbl for lbl in labels if lbl in shift_labels]
        for p, labels in st.session_state[Keys.EXEMPT_SHIFTS].items()
        if p in active_people
    }
    exempt_shifts = {p: labels for p, labels in exempt_shifts.items() if labels}
    # Groups keep their name even when every member left the roster; the
    # blackout/reduction warnings point out empty groups where it matters.
    named_groups = {
        g: [m for m in members if m in active_people]
        for g, members in st.session_state[Keys.NAMED_GROUPS].items()
    }
    blackouts = []
    for b in normalized_blackouts(st.session_state[Keys.BLACKOUTS]):
        if b.group is not None:
            if b.group in named_groups:
                blackouts.append(b)
        else:
            members = tuple(m for m in b.members if m in active_people)
            if members:
                blackouts.append(b._replace(members=members))
    preferred_shifts = {
        p: [lbl for lbl in labels if lbl in shift_labels]
        for p, labels in st.session_state[Keys.PREFERRED_SHIFTS].items()
        if p in active_people
    }
    preferred_shifts = {p: labels for p, labels in preferred_shifts.items() if labels}
    preferred_day_type = {
        p: kind
        for p, kind in st.session_state[Keys.PREFERRED_DAY_TYPE].items()
        if p in active_people and kind in ("weekend", "weekday")
    }
    avoid_pairs = []
    seen_pairs = set()
    for pair in st.session_state[Keys.AVOID_PAIRS]:
        first, second = pair[0], pair[1]
        unordered = frozenset((first, second))
        if (
            first in active_people and second in active_people
            and first != second and unordered not in seen_pairs
        ):
            seen_pairs.add(unordered)
            avoid_pairs.append((first, second))
    reductions = []
    for r in normalized_reductions(st.session_state[Keys.REDUCTIONS]):
        labels = tuple(lbl for lbl in r.labels if lbl in shift_labels)
        if not labels:
            continue
        if r.group is not None:
            if r.group in named_groups:
                reductions.append(r._replace(labels=labels))
        else:
            members = tuple(m for m in r.members if m in active_people)
            if members:
                reductions.append(r._replace(members=members, labels=labels))
    # Night float: keep coverage for shifts still marked NF-eligible, and
    # assignments whose coverer is still on the roster (labels filtered to live).
    nf_labels = {s.label for s in st.session_state[Keys.SHIFTS] if s.night_float}
    nf_coverage = {
        lbl: cov for lbl, cov in st.session_state[Keys.NF_COVERAGE].items()
        if lbl in nf_labels
    }
    nf_assignments = []
    for a in normalized_nf_assignments(
        st.session_state[Keys.NF_ASSIGNMENTS],
        default_rest=st.session_state[Keys.NF_REST_DAYS],
    ):
        if a.name not in active_people:
            continue
        keep_labels = tuple(lbl for lbl in a.labels if lbl in nf_labels)
        nf_assignments.append(a._replace(labels=keep_labels))
    # Closures: keep only those naming a shift still on the roster.
    closures = [
        c for c in normalized_closures(st.session_state[Keys.CLOSURES])
        if c.label in shift_labels
    ]
    return {
        "max_total": max_total or None,
        "max_total_excused": max_total_excused or None,
        "extra_points": extra_points or None,
        "weekday_points": weekday_points or None,
        "holidays": holidays or None,
        "group_factors": group_factors or None,
        "resident_groups": resident_groups or None,
        "perks": perks or None,
        "exempt_shifts": exempt_shifts or None,
        "named_groups": named_groups or None,
        "blackouts": blackouts or None,
        "reductions": reductions or None,
        "preferred_shifts": preferred_shifts or None,
        "preferred_day_type": preferred_day_type or None,
        "avoid_pairs": avoid_pairs or None,
        "nf_coverage": nf_coverage or None,
        "nf_assignments": nf_assignments or None,
        "nf_rest_days": int(st.session_state[Keys.NF_REST_DAYS]),
        "closures": closures or None,
    }


def _availability_import_section(people: list) -> None:
    """Upload a monthly availability-request form export; apply as leaves.

    Parsed once per file (content signature guard) into a preview with a per-row
    status, so one bad answer never blocks the rest; Apply adds only the valid
    rows as compensated leaves, deduplicated against what is already entered.
    """
    with st.expander("Import availability requests (Excel/CSV)", expanded=False):
        st.caption(
            "Collect requests each month with a form (columns Name, Start, End "
            "— one row per period, several rows per person are fine, an empty "
            "End means a single day; dates as YYYY-MM-DD or DD/MM/YYYY). "
            "Upload the exported file, review the preview, then apply: every "
            "valid row becomes a compensated leave, exactly as if entered "
            "above by hand."
        )
        tcols = st.columns(2)
        tcols[0].download_button(
            "Template (CSV)",
            availability_template_csv(),
            file_name="availability_template.csv",
            mime="text/csv",
            width="stretch",
        )
        try:
            tcols[1].download_button(
                "Template (Excel)",
                availability_template_xlsx(),
                file_name="availability_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        except RuntimeError as exc:  # pragma: no cover - openpyxl missing
            tcols[1].info(str(exc))
        uploaded = st.file_uploader(
            "Upload responses (xlsx / csv)", type=["xlsx", "csv"], key="avail_upload"
        )
        blob = consume_upload_once(
            uploaded,
            Keys.AVAIL_SIG,
            state=st.session_state,
        )
        if blob is not None:
            try:
                if uploaded.name.lower().endswith(".csv"):
                    raw_rows = read_availability_csv(blob.decode("utf-8-sig"))
                else:
                    raw_rows = read_availability_xlsx(blob)
                st.session_state[Keys.AVAIL_PREVIEW] = parse_availability_rows(
                    raw_rows, people
                )
            except Exception as exc:
                st.session_state[Keys.AVAIL_PREVIEW] = None
                st.error(f"Could not read the file: {exc}")

        preview = st.session_state.get(Keys.AVAIL_PREVIEW)
        if not preview:
            return
        st.dataframe(
            pd.DataFrame([
                {
                    "Row": r.row_no,
                    "Name": r.raw_name,
                    "Matched": r.name or "—",
                    "Start": r.start,
                    "End": r.end,
                    "Status": r.error or "OK",
                }
                for r in preview
            ]),
            width="stretch",
        )
        valid_leaves = rows_to_leaves(preview)
        st.caption(f"{len(valid_leaves)} of {len(preview)} row(s) valid.")
        existing = {tuple(lv) for lv in normalized_leaves(st.session_state[Keys.LEAVES])}
        new_leaves = [lv for lv in valid_leaves if tuple(lv) not in existing]
        bcols = st.columns(2)
        if new_leaves:
            if bcols[0].button(
                f"Apply {len(new_leaves)} request(s) as compensated leaves",
                key="avail_apply",
                type="primary",
            ):
                st.session_state[Keys.LEAVES].extend(new_leaves)
                st.session_state[Keys.AVAIL_PREVIEW] = None
                flash(f"Added {len(new_leaves)} compensated leave(s) from the import.")
                st.rerun()
        elif valid_leaves:
            bcols[0].caption("All valid rows are already in the leaves list.")
        if bcols[1].button("Discard preview", key="avail_discard"):
            st.session_state[Keys.AVAIL_PREVIEW] = None
            st.rerun()


def _inline_config_hints(config: InputData) -> list:
    """Configuration problems worth flagging *before* Generate is clicked.

    Reuses ``validate_input`` but drops the empty-state nags (no shifts yet /
    unset dates) so a fresh page isn't covered in warnings while the user is
    still filling things in.
    """
    empty_state_fragments = ("Add at least one shift", "End date must not be before")
    return [
        problem
        for problem in validate_input(config)
        if not any(fragment in problem for fragment in empty_state_fragments)
    ]


def session_config_from_state() -> InputData:
    """Assemble the active configuration from canonical session state."""
    weekend_days = [
        WEEKDAY_LABELS.index(name)
        for name in st.session_state[Keys.WEEKEND_LABELS]
        if name in WEEKDAY_LABELS
    ]
    return InputData(
        start_date=st.session_state[Keys.START_DATE],
        end_date=st.session_state[Keys.END_DATE],
        shifts=st.session_state[Keys.SHIFTS],
        juniors=st.session_state[Keys.JUNIORS],
        seniors=st.session_state[Keys.SENIORS],
        nf_juniors=st.session_state[Keys.NF_JUNIORS],
        nf_seniors=st.session_state[Keys.NF_SENIORS],
        leaves=st.session_state[Keys.LEAVES],
        rotators=st.session_state[Keys.ROTATORS],
        min_gap=int(st.session_state[Keys.MIN_GAP]),
        seed=int(st.session_state[Keys.SEED]),
        weekend_days=weekend_days,
        weekend_multiplier=float(st.session_state[Keys.WEEKEND_MULTIPLIER]),
        **_active_config_maps(),
    )


def _display_state() -> dict:
    return {
        "palette": dict(st.session_state[Keys.PALETTE]),
        "extra_cols": list(st.session_state[Keys.EXTRA_COLS]),
        "extra_vals": {
            col: dict(vals) for col, vals in st.session_state[Keys.EXTRA_VALS].items()
        },
        "col_order": list(st.session_state[Keys.COL_ORDER]),
    }


def _render_setup_workspace() -> None:
    render_section_header(
        "Build the scheduling block",
        "Define the calendar, shift catalogue, and resident roster—the foundation "
        "used by every coverage and fairness rule.",
        eyebrow="Step 1 · Setup",
    )
    tab_block, tab_shifts, tab_roster = st.tabs(
        ["Block settings", "Shift templates", "Resident roster"]
    )
    with tab_block:
        with card_container(
            "Calendar & rest",
            "Choose the block window and the baseline spacing rules used by the optimiser.",
        ):
            dc = st.columns(2)
            dc[0].date_input("Start date", key=Keys.START_DATE)
            dc[1].date_input("End date", key=Keys.END_DATE)
            rc = st.columns(2)
            rc[0].slider(
                "Minimum gap (rest days between shifts)", 0, 7, key=Keys.MIN_GAP
            )
            rc[1].number_input(
                "Random seed",
                0,
                1_000_000,
                step=1,
                key=Keys.SEED,
                help="Same seed reproduces the same schedule when the solver finishes.",
            )
            wc = st.columns(2)
            wc[0].multiselect(
                "Weekend days",
                WEEKDAY_LABELS,
                key=Keys.WEEKEND_LABELS,
                help="Days that count as weekend for fairness (a shift's 'Thu' flag also adds Thursday).",
            )
            wc[1].number_input(
                "Weekend shift points (×)",
                min_value=1.0,
                max_value=5.0,
                step=0.5,
                key=Keys.WEEKEND_MULTIPLIER,
                help="Weekend shifts count this many times their points (×2 by "
                "default: one weekend shift ≈ two weekday shifts). This folds "
                "weekend fairness into the strongest balancing tier, so weekend "
                "load evens out as part of the totals. Set ×1 for the old "
                "equal-points behaviour; old saved configs load as ×1.",
            )
    with tab_shifts:
        with card_container():
            shift_template_editor()
    with tab_roster:
        with card_container():
            roster_editor()


def _render_coverage_workspace() -> None:
    render_section_header(
        "Shape coverage and availability",
        "Record when people can work, define the night-float overlay, and stand down "
        "slots that should not enter demand.",
        eyebrow="Step 2 · Coverage",
    )
    people = st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS]
    shift_labels = [s.label for s in st.session_state[Keys.SHIFTS]]
    start_date = st.session_state[Keys.START_DATE]
    end_date = st.session_state[Keys.END_DATE]
    tab_absence, tab_nf, tab_closures = st.tabs(
        ["Leaves & rotators", "Night float", "Shift closures"]
    )
    with tab_absence:
        with card_container():
            date_range_editor(
                "Leaves — resident unavailable during window",
                Keys.LEAVES,
                people,
                with_compensation=True,
                default_start=start_date,
                default_end=end_date,
            )
            st.caption(
                "Compensated leave keeps the resident's full fair share; uncompensated "
                "scales it down for the absence (like a rotator)."
            )
            _availability_import_section(people)
        with card_container():
            date_range_editor(
                "Rotators — resident only available during window",
                Keys.ROTATORS,
                people,
                default_start=start_date,
                default_end=end_date,
                shift_labels=shift_labels,
            )
            st.caption(
                "Rotators are normal roster members while active: groups, "
                "blackouts, reductions, and preferences all apply to them."
            )
    with tab_nf:
        with card_container():
            night_float_editor(
                people,
                {s.label: s.role for s in st.session_state[Keys.SHIFTS] if s.night_float},
                default_start=start_date,
                default_end=end_date,
            )
    with tab_closures:
        with card_container():
            closures_editor(shift_labels, default_start=start_date, default_end=end_date)


def _render_policies_workspace() -> None:
    render_section_header(
        "Tune fairness and operational policy",
        "Apply team rules, load adjustments, preferences, exemptions, and point "
        "semantics without hiding how each mechanism affects the solve.",
        eyebrow="Step 3 · Policies",
    )
    people = st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS]
    shift_labels = [s.label for s in st.session_state[Keys.SHIFTS]]
    start_date = st.session_state[Keys.START_DATE]
    end_date = st.session_state[Keys.END_DATE]
    tab_teams, tab_fairness, tab_preference = st.tabs(
        ["Teams & restrictions", "Fairness controls", "Preferences & points"]
    )
    with tab_teams:
        with card_container():
            named_groups_editor(people)
        with card_container():
            blackouts_editor(people, default_start=start_date, default_end=end_date)
        with card_container():
            exemptions_editor(people, shift_labels)
        with card_container():
            reductions_editor(
                people,
                shift_labels,
                default_start=start_date,
                default_end=end_date,
            )
    with tab_fairness:
        with card_container():
            caps_editor(people)
        with card_container():
            extra_points_editor(people)
        with card_container():
            seniority_editor(people)
        with card_container():
            perks_editor(people, default_start=start_date, default_end=end_date)
    with tab_preference:
        with card_container():
            preferences_editor(people, shift_labels)
        with card_container():
            avoid_pairs_editor(people)
        with card_container():
            weekday_points_editor(shift_labels)
        with card_container():
            holidays_editor(default_date=start_date)


def _render_history_workspace(session_config: InputData) -> dict | None:
    render_section_header(
        "Move safely between blocks",
        "Save the complete setup, restore it later, and carry cumulative fairness "
        "forward without keeping resident data on the app server.",
        eyebrow="Step 4 · History & files",
    )
    tab_config, tab_ledger = st.tabs(["Configuration file", "Fairness ledger"])
    with tab_config:
        with card_container(
            "Save / load configuration",
            "The JSON is a portable snapshot of scheduling inputs and display settings.",
        ):
            st.download_button(
                "Download config (JSON)",
                input_data_to_json(session_config, display=_display_state()),
                file_name="idea_gold_config.json",
                mime="application/json",
                width="stretch",
            )
            st.caption("Includes the display setup: colours, custom columns, column order.")
            uploaded_config = st.file_uploader(
                "Load config (JSON) — fills in all the tabs for review", type="json"
            )
            # Import once per uploaded file (the uploader returns the same file
            # on every rerun; the guard stops it clobbering later tab/display edits).
            blob = consume_upload_once(
                uploaded_config,
                Keys.DISPLAY_RESTORED,
                state=st.session_state,
            )
            if blob is not None:
                try:
                    text = blob.decode("utf-8")
                    loaded = input_data_from_json(text)
                except Exception as exc:
                    st.error(f"Could not read config: {exc}")
                else:
                    try:
                        display = display_from_json(text)
                    except Exception:
                        display = None
                    # The Setup widgets already rendered this run, so their
                    # session keys can't be written from here — queue the
                    # import for apply_pending_updates() on the next run.
                    st.session_state[Keys.PENDING_CONFIG] = (loaded, display)
                    st.rerun()
    with tab_ledger:
        with card_container():
            st.subheader("Carryover fairness (optional)")
            st.caption(
                "Leave this empty for a standalone, one-off schedule — this block is "
                "balanced on its own, with no link to fairness history. To keep fairness "
                "across months instead, upload the previous block's ledger here (residents "
                "who carried extra get lighter targets now), tweak it in the grid if the "
                "real world diverged, and download the updated ledger afterwards for "
                "next time."
            )
            carryover_ledger = render_ledger_panel(
                st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS],
                [s.label for s in st.session_state[Keys.SHIFTS]],
            )
        with card_container("Carryover policy", "Recommended defaults protect real-world exceptions."):
            st.checkbox(
                "Penalties don't earn future relief (recommended)",
                key=Keys.LEDGER_NO_REFUND,
                help="Extra points imposed as a penalty are debited from the saved "
                "ledger, so cumulative balancing never refunds a punishment with a "
                "lighter later block.",
            )
            st.checkbox(
                "Excused shortfalls aren't repaid later (recommended)",
                key=Keys.LEDGER_NO_CATCHUP,
                help="Load excused by uncompensated leave, rotator windows, perks, "
                "or group load factors is credited in the saved ledger, so the "
                "resident is not made to catch it up in later blocks (e.g. after a "
                "perk expires).",
            )
            st.checkbox(
                "Repay shift-type debt in the same shift type (recommended)",
                key=Keys.LEDGER_LABEL_CARRYOVER,
                help="Uses the ledger's per-shift-type history so someone who "
                "carried extra of one shift type (e.g. nights) gets a lighter "
                "target on that type now — not just fewer points overall. Off, "
                "prior imbalance is repaid through total/weekend points only. "
                "Never overrides total or weekend fairness, and on very large "
                "blocks (where per-type targets are skipped for solver "
                "performance) the history is recorded but not repaid.",
            )
    return carryover_ledger


def _rule_count() -> int:
    keys = (
        Keys.LEAVES,
        Keys.ROTATORS,
        Keys.CAPS,
        Keys.EXTRA_POINTS,
        Keys.HOLIDAYS,
        Keys.PERKS,
        Keys.EXEMPT_SHIFTS,
        Keys.NAMED_GROUPS,
        Keys.BLACKOUTS,
        Keys.REDUCTIONS,
        Keys.PREFERRED_SHIFTS,
        Keys.AVOID_PAIRS,
        Keys.NF_ASSIGNMENTS,
        Keys.CLOSURES,
    )
    return sum(len(st.session_state.get(key) or {}) for key in keys)


def _render_review_workspace(session_config: InputData, carryover_ledger) -> None:
    render_section_header(
        "Review, validate, and generate",
        "Confirm the block at a glance. Blocking issues and non-blocking advisories "
        "are shown before the optimiser starts.",
        eyebrow="Step 5 · Review & run",
    )
    span = max(0, (session_config.end_date - session_config.start_date).days + 1)
    summary = st.columns(5)
    summary[0].metric("Days", span)
    summary[1].metric("Shifts", len(session_config.shifts))
    summary[2].metric("Residents", len(session_config.juniors) + len(session_config.seniors))
    summary[3].metric("Rules", _rule_count())
    summary[4].metric("History", "Active" if carryover_ledger else "Standalone")

    problems = validate_input(session_config)
    hints = _inline_config_hints(session_config)
    advisories = config_warnings(session_config) if not problems else []
    if problems:
        render_status(
            f"{len(problems)} blocking issue(s) must be fixed before generation.",
            tone="error",
            title="Configuration needs attention",
            label="Not ready",
        )
        with st.expander("Show blocking issues", expanded=True):
            for problem in problems:
                st.write(f"- {problem}")
    elif advisories:
        render_status(
            f"Ready to solve, with {len(advisories)} advisory warning(s) to review.",
            tone="warning",
            title="Ready with advisories",
            label="Review",
        )
        with st.expander("Show advisories", expanded=True):
            for warning in advisories:
                st.write(f"- {warning}")
    else:
        render_status(
            "All required inputs pass validation. The optimiser is ready.",
            tone="success",
            title="Configuration ready",
            label="Ready",
        )
    if hints and not problems:
        for hint in hints:
            st.warning(hint)
    render_generate_and_solve(session_config, carryover_ledger)


def apply_pending_updates() -> None:
    """Apply queued cross-tab state changes before any widget renders.

    Streamlit forbids writing a keyed widget's session state once that widget
    has been instantiated in the current run, and the Setup widgets (dates,
    min_gap, seed, weekend days) render before the History upload handler and
    the Review retry button execute. Those handlers therefore queue their
    changes (``Keys.PENDING_CONFIG`` / ``Keys.PENDING_STATE``) and rerun; this
    hook drains the queue first, when writing widget state is still legal.
    """
    pending_state = st.session_state.get(Keys.PENDING_STATE)
    st.session_state[Keys.PENDING_STATE] = None
    for key, value in (pending_state or {}).items():
        st.session_state[key] = value
    pending = st.session_state.get(Keys.PENDING_CONFIG)
    st.session_state[Keys.PENDING_CONFIG] = None
    if not pending:
        return
    loaded, display = pending
    try:
        populate_editors_from_config(loaded)
        if display:
            restore_display_state(display)
    except Exception as exc:
        st.error(f"Could not apply the uploaded config: {exc}")
    else:
        flash(
            f"Loaded config: {len(loaded.shifts)} shift(s), "
            f"{len(loaded.juniors) + len(loaded.seniors)} resident(s) — "
            "review the tabs, then Generate."
        )


def render_application() -> None:
    """Render the complete seven-workspace application in one stable script run."""
    apply_pending_updates()
    tabs = st.tabs(
        [
            "① Setup",
            "② Coverage",
            "③ Policies",
            "④ History",
            "⑤ Review & run",
            "⑥ Results",
            "Diagnostics",
        ]
    )
    with tabs[0]:
        _render_setup_workspace()
    with tabs[1]:
        _render_coverage_workspace()
    with tabs[2]:
        _render_policies_workspace()

    session_config = session_config_from_state()
    with tabs[3]:
        carryover_ledger = _render_history_workspace(session_config)
    with tabs[4]:
        _render_review_workspace(session_config, carryover_ledger)
    with tabs[5]:
        from ui.results import render_results

        render_results()
    with tabs[6]:
        render_diagnostics()


def render_generate_and_solve(session_config, carryover_ledger) -> None:
    """The Generate button, validation, solve, and relax-and-retry recovery."""
    st.divider()
    generate_clicked = st.button(
        "⚙️ Generate schedule", type="primary", width="stretch"
    )

    # A relaxed-constraint retry queued by the recovery buttons takes precedence
    # over a fresh click so the chosen relaxation is actually applied.
    data = None
    relaxation_note = None
    if st.session_state.get(Keys.RETRY_CONFIG) is not None:
        data, relaxation_note = st.session_state.pop(Keys.RETRY_CONFIG)
    elif generate_clicked:
        # An uploaded config has already been imported into the editors (see
        # the History workspace), so the session config always reflects it — plus any
        # tweaks made since. No re-parse at Generate time.
        data = session_config

    if data is None:
        return
    problems = validate_input(data)
    if problems:
        st.error("Fix the configuration before generating:")
        for problem in problems:
            st.write(f"- {problem}")
        return

    if relaxation_note:
        st.info(relaxation_note)
    for warning in config_warnings(data):
        st.warning(warning)
    env = os.getenv("ENV", "prod")
    if carryover_ledger:
        st.info("Carryover fairness active: balancing cumulative load from the uploaded ledger.")
    df = None
    try:
        with st.spinner("Optimising…"):
            df = build_schedule(
                data, env=env, ledger=carryover_ledger,
                label_carryover=st.session_state.get(Keys.LEDGER_LABEL_CARRYOVER, True),
            )
    except RuntimeError as exc:
        st.error(str(exc))
        if data.min_gap > 0:
            st.caption("No feasible schedule — relax a constraint and try again:")
            if st.button(f"Retry with min_gap {data.min_gap - 1}"):
                st.session_state[Keys.RETRY_CONFIG] = (
                    replace(data, min_gap=data.min_gap - 1),
                    f"Relaxed minimum gap to {data.min_gap - 1} to find a feasible schedule.",
                )
                # Reflect the relaxed gap in the Setup slider (queued: the
                # slider already rendered this run), so the UI never shows a
                # different min_gap than the one the schedule was built with.
                st.session_state[Keys.PENDING_STATE] = {Keys.MIN_GAP: data.min_gap - 1}
                st.rerun()
    except Exception as exc:
        st.error(str(exc))

    if df is not None:
        set_result(df, data, carryover_ledger)
        shift_cols = [c for c in df.columns if c not in ("Date", "Day")]
        unfilled = int((df[shift_cols] == "Unfilled").sum().sum()) if shift_cols else 0
        status = df.attrs.get("solver_status") or "done"
        detail = "all slots filled" if unfilled == 0 else f"{unfilled} slot(s) unfilled"
        st.toast("Schedule generated ✅")
        st.success(
            f"Schedule generated ({status}, {detail}) — open the **⑥ Results** tab "
            "to review, edit, and export it."
        )
