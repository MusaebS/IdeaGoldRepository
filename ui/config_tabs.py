"""Configuration tabs (①-④) and the Generate/solve flow."""
from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import date, timedelta

import streamlit as st

from model.config_io import display_from_json, input_data_to_json, input_data_from_json
from model.data_models import InputData
from model.demo_data import sample_shifts, sample_names
from model.ledger import ledger_from_json
from model.optimiser import build_schedule
from model.validation import validate_input, config_warnings

from ui.editors import (
    WEEKDAY_LABELS,
    caps_editor,
    date_range_editor,
    exemptions_editor,
    extra_points_editor,
    holidays_editor,
    perks_editor,
    roster_editor,
    seniority_editor,
    shift_template_editor,
    weekday_points_editor,
)
from ui.state import Keys, restore_display_state, set_result


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


def _active_config_maps() -> tuple:
    """Filter caps / extra points / overrides down to the live roster & shifts."""
    active_people = set(st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS])
    max_total = {
        p: v["total"]
        for p, v in st.session_state[Keys.CAPS].items()
        if p in active_people and v.get("total")
    }
    max_nights = {
        p: v["nights"]
        for p, v in st.session_state[Keys.CAPS].items()
        if p in active_people and v.get("nights")
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
    return (
        max_total, max_nights, extra_points, weekday_points, holidays,
        group_factors, resident_groups, perks, exempt_shifts,
    )


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


def render_config_tabs() -> tuple:
    """Render tabs ①-④; returns (session_config, uploaded_config, carryover_ledger)."""
    st.header("Configuration")
    tab_people, tab_rules, tab_adv, tab_save = st.tabs(
        ["① Shifts & people", "② Dates & rules", "③ Advanced", "④ Save / carryover"]
    )

    with tab_people:
        shift_template_editor()
        st.divider()
        roster_editor()

    with tab_rules:
        dc = st.columns(2)
        with dc[0]:
            start_date = st.date_input("Start date", date.today())
        with dc[1]:
            end_date = st.date_input("End date", date.today() + timedelta(days=27))
        rc = st.columns(2)
        with rc[0]:
            min_gap = st.slider("Minimum gap (rest days between shifts)", 0, 7, 1)
        with rc[1]:
            nf_block_len = st.number_input("Night-float block length", 1, 7, 5)
        oc = st.columns(2)
        with oc[0]:
            seed = st.number_input(
                "Random seed", 0, 1_000_000, 0, 1,
                help="Same seed reproduces the same schedule when the solver finishes.",
            )
        with oc[1]:
            weekend_labels = st.multiselect(
                "Weekend days", WEEKDAY_LABELS, default=["Sat", "Sun"],
                help="Days that count as weekend for fairness (a shift's 'Thu' flag also adds Thursday).",
            )
        weekend_days = [WEEKDAY_LABELS.index(name) for name in weekend_labels]

        st.divider()
        st.subheader("Leaves & rotators")
        people = st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS]
        # Date pickers default to the schedule block so entries land in the
        # right month without scrolling from today.
        date_range_editor(
            "Leaves — resident unavailable during window", Keys.LEAVES, people,
            with_compensation=True, default_start=start_date, default_end=end_date,
        )
        st.caption(
            "Compensated leave keeps the resident's full fair share; uncompensated "
            "scales it down for the absence (like a rotator)."
        )
        st.divider()
        date_range_editor(
            "Rotators — resident only available during window", Keys.ROTATORS, people,
            default_start=start_date, default_end=end_date,
        )

    with tab_adv:
        st.subheader("Per-resident caps & extra points")
        people = st.session_state[Keys.JUNIORS] + st.session_state[Keys.SENIORS]
        caps_editor(people)
        st.divider()
        extra_points_editor(people)
        st.divider()
        st.subheader("Seniority groups, perks & exemptions")
        seniority_editor(people)
        st.divider()
        perks_editor(people, default_start=start_date, default_end=end_date)
        st.divider()
        exemptions_editor(people, [s.label for s in st.session_state[Keys.SHIFTS]])
        st.divider()
        st.subheader("Point overrides & holidays")
        weekday_points_editor([s.label for s in st.session_state[Keys.SHIFTS]])
        st.divider()
        holidays_editor(default_date=start_date)

    (
        max_total, max_nights, extra_points, weekday_points, holidays,
        group_factors, resident_groups, perks, exempt_shifts,
    ) = _active_config_maps()
    session_config = InputData(
        start_date=start_date,
        end_date=end_date,
        shifts=st.session_state[Keys.SHIFTS],
        juniors=st.session_state[Keys.JUNIORS],
        seniors=st.session_state[Keys.SENIORS],
        nf_juniors=st.session_state[Keys.NF_JUNIORS],
        nf_seniors=st.session_state[Keys.NF_SENIORS],
        leaves=st.session_state[Keys.LEAVES],
        rotators=st.session_state[Keys.ROTATORS],
        min_gap=min_gap,
        nf_block_length=nf_block_len,
        seed=int(seed),
        weekend_days=weekend_days,
        max_total=max_total or None,
        max_nights=max_nights or None,
        extra_points=extra_points or None,
        weekday_points=weekday_points or None,
        holidays=holidays or None,
        group_factors=group_factors or None,
        resident_groups=resident_groups or None,
        perks=perks or None,
        exempt_shifts=exempt_shifts or None,
    )

    # Early feedback: misconfigurations surface here as warnings while the
    # user is still on the form, not only after clicking Generate.
    for hint in _inline_config_hints(session_config):
        st.warning(hint)

    with tab_save:
        st.subheader("Save / load configuration")
        display_state = {
            "palette": dict(st.session_state[Keys.PALETTE]),
            "extra_cols": list(st.session_state[Keys.EXTRA_COLS]),
            "extra_vals": {
                col: dict(vals) for col, vals in st.session_state[Keys.EXTRA_VALS].items()
            },
            "col_order": list(st.session_state[Keys.COL_ORDER]),
        }
        st.download_button(
            "Download config (JSON)",
            input_data_to_json(session_config, display=display_state),
            file_name="idea_gold_config.json",
            mime="application/json",
        )
        st.caption("Includes the display setup: colours, custom columns, column order.")
        uploaded_config = st.file_uploader("Load config (JSON), then click Generate", type="json")
        if uploaded_config is not None:
            # Restore the display section once per uploaded file (the uploader
            # returns the same file on every rerun; the signature guard stops
            # it clobbering later manual colour/column changes).
            sig = hashlib.md5(uploaded_config.getvalue()).hexdigest()
            if st.session_state.get(Keys.DISPLAY_RESTORED) != sig:
                st.session_state[Keys.DISPLAY_RESTORED] = sig  # set first: a bad file never loops
                try:
                    display = display_from_json(uploaded_config.getvalue().decode("utf-8"))
                except Exception:
                    display = None
                if display:
                    restore_display_state(display)
                    st.rerun()
        st.divider()
        st.subheader("Carryover fairness (optional)")
        st.caption(
            "Leave this empty for a standalone, one-off schedule — this block is "
            "balanced on its own, with no link to fairness history. To keep fairness "
            "across months instead, upload the previous block's ledger here (residents "
            "who carried extra get lighter targets now) and download the updated "
            "ledger afterwards for next time."
        )
        uploaded_ledger = st.file_uploader(
            "Load fairness ledger (JSON)", type="json", key="ledger_upload"
        )
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

    carryover_ledger = None
    if uploaded_ledger is not None:
        try:
            carryover_ledger = ledger_from_json(uploaded_ledger.getvalue().decode("utf-8"))
        except Exception as exc:
            st.error(f"Could not read ledger: {exc}")

    return session_config, uploaded_config, carryover_ledger


def render_generate_and_solve(session_config, uploaded_config, carryover_ledger) -> None:
    """The Generate button, validation, solve, and relax-and-retry recovery."""
    st.divider()
    generate_clicked = st.button(
        "⚙️ Generate schedule", type="primary", use_container_width=True
    )

    # A relaxed-constraint retry queued by the recovery buttons takes precedence
    # over a fresh click so the chosen relaxation is actually applied.
    data = None
    relaxation_note = None
    if st.session_state.get(Keys.RETRY_CONFIG) is not None:
        data, relaxation_note = st.session_state.pop(Keys.RETRY_CONFIG)
    elif generate_clicked:
        if uploaded_config is not None:
            try:
                data = input_data_from_json(uploaded_config.getvalue().decode("utf-8"))
            except Exception as exc:
                st.error(f"Could not read config: {exc}")
        else:
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
            df = build_schedule(data, env=env, ledger=carryover_ledger)
    except RuntimeError as exc:
        st.error(str(exc))
        st.caption("No feasible schedule — relax a constraint and try again:")
        rcols = st.columns(2)
        if data.min_gap > 0 and rcols[0].button(
            f"Retry with min_gap {data.min_gap - 1}"
        ):
            st.session_state[Keys.RETRY_CONFIG] = (
                replace(data, min_gap=data.min_gap - 1),
                f"Relaxed minimum gap to {data.min_gap - 1} to find a feasible schedule.",
            )
            st.rerun()
        if data.nf_block_length > 1 and rcols[1].button(
            f"Retry with NF block length {data.nf_block_length - 1}"
        ):
            st.session_state[Keys.RETRY_CONFIG] = (
                replace(data, nf_block_length=data.nf_block_length - 1),
                f"Relaxed NF block length to {data.nf_block_length - 1} to find a feasible schedule.",
            )
            st.rerun()
    except Exception as exc:
        st.error(str(exc))

    if df is not None:
        set_result(df, data, carryover_ledger)
