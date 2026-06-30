import streamlit as st
import pandas as pd
from dataclasses import replace
from datetime import date, timedelta

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule
from model.config_io import input_data_to_json, input_data_from_json
from model.ledger import ledger_from_json, ledger_to_json, update_ledger
from model.validation import validate_input, config_warnings, validate_schedule
from model.fairness import (
    calculate_points,
    fairness_range_lines,
    format_fairness_log,
    schedule_quality,
    assignment_rationale,
)
from model.demo_data import sample_shifts, sample_names
from model.exporters import build_fairness_frame, schedule_to_excel_bytes, schedule_to_pdf_bytes
import os

st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("Idea Gold Scheduler – CP-SAT")


def _date_range_editor(title: str, key: str, people: list) -> None:
    """Inline editor for (resident, start, end) windows kept in session state."""
    st.markdown(f"**{title}**")
    if not people:
        st.caption("Add participants first to configure this.")
        return
    c = st.columns([3, 2, 2, 1])
    with c[0]:
        who = st.selectbox("Resident", people, key=f"{key}_who")
    with c[1]:
        start = st.date_input("Start", date.today(), key=f"{key}_start")
    with c[2]:
        end = st.date_input("End", date.today(), key=f"{key}_end")
    with c[3]:
        st.markdown("&nbsp;")
        if st.button("Add", key=f"{key}_add"):
            st.session_state[key].append((who, start, end))
    rows = st.session_state[key]
    if rows:
        st.table(
            pd.DataFrame([{"Resident": r, "Start": s, "End": e} for r, s, e in rows])
        )
        idx = st.selectbox(
            "Remove entry",
            list(range(len(rows))),
            format_func=lambda i: f"{rows[i][0]}: {rows[i][1]} → {rows[i][2]}",
            key=f"{key}_del_idx",
        )
        if st.button("Remove", key=f"{key}_del"):
            st.session_state[key].pop(idx)


def _caps_editor(people: list) -> None:
    """Inline editor for per-resident hard caps (0 = no cap)."""
    st.markdown("**Caps — limit a resident's total / night-float points (0 = no cap)**")
    if not people:
        st.caption("Add participants first to configure caps.")
        return
    c = st.columns([3, 2, 2, 1])
    with c[0]:
        who = st.selectbox("Resident", people, key="cap_who")
    with c[1]:
        mt = st.number_input("Max total pts", 0.0, 999.0, 0.0, 0.5, key="cap_total")
    with c[2]:
        mn = st.number_input("Max night pts", 0.0, 999.0, 0.0, 0.5, key="cap_nights")
    with c[3]:
        st.markdown("&nbsp;")
        if st.button("Add", key="cap_add"):
            st.session_state.caps[who] = {"total": mt, "nights": mn}
    caps = st.session_state.caps
    if caps:
        st.table(pd.DataFrame([
            {
                "Resident": p,
                "Max total": v["total"] or "—",
                "Max nights": v["nights"] or "—",
            }
            for p, v in caps.items()
        ]))
        rm = st.selectbox("Remove cap", list(caps.keys()), key="cap_del")
        if st.button("Remove", key="cap_del_btn"):
            st.session_state.caps.pop(rm, None)


# Session defaults
if "shifts" not in st.session_state:
    st.session_state.shifts = []
if "juniors" not in st.session_state:
    st.session_state.juniors = []
if "seniors" not in st.session_state:
    st.session_state.seniors = []
if "nf_juniors" not in st.session_state:
    st.session_state.nf_juniors = []
if "nf_seniors" not in st.session_state:
    st.session_state.nf_seniors = []
if "leaves" not in st.session_state:
    st.session_state.leaves = []
if "rotators" not in st.session_state:
    st.session_state.rotators = []
if "caps" not in st.session_state:
    st.session_state.caps = {}
if "result_df" not in st.session_state:
    st.session_state.result_df = None
if "result_data" not in st.session_state:
    st.session_state.result_data = None
if "demo_loaded" not in st.session_state:
    st.session_state.demo_loaded = False

# optional sample data for quick testing
test_mode = st.checkbox("Test mode (preload example data)")
if test_mode and not st.session_state.demo_loaded:
    st.session_state.shifts = sample_shifts()
    juniors, seniors, nf_juniors, nf_seniors = sample_names()
    st.session_state.juniors = juniors
    st.session_state.seniors = seniors
    st.session_state.nf_juniors = nf_juniors
    st.session_state.nf_seniors = nf_seniors
    st.session_state.demo_loaded = True

st.header("Configuration")

with st.expander("Shift Templates", expanded=True):
    label = st.text_input("Label")
    role = st.selectbox("Role", ["Junior", "Senior"])
    nf = st.checkbox("Night Float")
    thu_wk = st.checkbox("Thu counts as weekend")
    points = st.number_input("Points", 1.0, 10.0, 1.0, 0.5)
    if st.button("Add Shift", key="add_shift"):
        st.session_state.shifts.append(
            ShiftTemplate(label=label, role=role, night_float=nf, thu_weekend=thu_wk, points=points)
        )
    if st.session_state.shifts:
        st.table(pd.DataFrame([s.__dict__ for s in st.session_state.shifts]))
        del_opts = list(range(len(st.session_state.shifts)))
        del_idx = st.selectbox(
            "Delete shift", del_opts,
            format_func=lambda i: st.session_state.shifts[i].label,
        )
        if st.button("Delete Shift", key="del_shift"):
            st.session_state.shifts.pop(del_idx)

cols = st.columns(2)
with cols[0]:
    st.subheader("Participants")
    juniors_text = st.text_area("Juniors", "\n".join(st.session_state.juniors))
    seniors_text = st.text_area("Seniors", "\n".join(st.session_state.seniors))
    st.session_state.juniors = [n.strip() for n in juniors_text.splitlines() if n.strip()]
    st.session_state.seniors = [n.strip() for n in seniors_text.splitlines() if n.strip()]

with cols[1]:
    st.subheader("Night Float Eligible")
    st.session_state.nf_juniors = st.multiselect(
        "Juniors", st.session_state.juniors, default=st.session_state.nf_juniors
    )
    st.session_state.nf_seniors = st.multiselect(
        "Seniors", st.session_state.seniors, default=st.session_state.nf_seniors
    )

with st.expander("Leaves & Rotators", expanded=False):
    _people = st.session_state.juniors + st.session_state.seniors
    _date_range_editor("Leaves — resident unavailable during window", "leaves", _people)
    st.divider()
    _date_range_editor(
        "Rotators — resident only available during window", "rotators", _people
    )

with st.expander("Per-resident caps", expanded=False):
    _caps_editor(st.session_state.juniors + st.session_state.seniors)

date_cols = st.columns(2)
with date_cols[0]:
    start_date = st.date_input("Start Date", date.today())
with date_cols[1]:
    end_date = st.date_input("End Date", date.today() + timedelta(days=27))
min_gap = st.slider("Minimum Gap", 0, 7, 1)
nf_block_len = st.number_input("NF Block Length", 1, 7, 5)
seed = st.number_input(
    "Random seed", 0, 1_000_000, 0, 1,
    help="Seeds the solver's search. The same seed reproduces the same schedule "
    "when the solver finishes; under a tight time limit results may still vary.",
)

_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
weekend_labels = st.multiselect(
    "Weekend days",
    _WEEKDAY_NAMES,
    default=["Sat", "Sun"],
    help="Days that count as weekend for fairness. A shift's 'Thu counts as "
    "weekend' flag still adds Thursday for that shift.",
)
weekend_days = [_WEEKDAY_NAMES.index(name) for name in weekend_labels]

_active_people = set(st.session_state.juniors + st.session_state.seniors)
max_total = {
    p: v["total"]
    for p, v in st.session_state.caps.items()
    if p in _active_people and v.get("total")
}
max_nights = {
    p: v["nights"]
    for p, v in st.session_state.caps.items()
    if p in _active_people and v.get("nights")
}

session_config = InputData(
    start_date=start_date,
    end_date=end_date,
    shifts=st.session_state.shifts,
    juniors=st.session_state.juniors,
    seniors=st.session_state.seniors,
    nf_juniors=st.session_state.nf_juniors,
    nf_seniors=st.session_state.nf_seniors,
    leaves=st.session_state.leaves,
    rotators=st.session_state.rotators,
    min_gap=min_gap,
    nf_block_length=nf_block_len,
    seed=int(seed),
    weekend_days=weekend_days,
    max_total=max_total or None,
    max_nights=max_nights or None,
)

with st.expander("Save / Load configuration", expanded=False):
    st.download_button(
        "Download config (JSON)",
        input_data_to_json(session_config),
        file_name="idea_gold_config.json",
        mime="application/json",
    )
    uploaded_config = st.file_uploader("Load config (JSON), then click Generate", type="json")

with st.expander("Carryover fairness (cumulative across blocks)", expanded=False):
    st.caption(
        "Upload the fairness ledger from your previous block to balance cumulative "
        "load: residents who carried extra before get lighter targets now. After "
        "generating, download the updated ledger to use for the next block."
    )
    uploaded_ledger = st.file_uploader("Load fairness ledger (JSON)", type="json", key="ledger_upload")

carryover_ledger = None
if uploaded_ledger is not None:
    try:
        carryover_ledger = ledger_from_json(uploaded_ledger.getvalue().decode("utf-8"))
    except Exception as exc:
        st.error(f"Could not read ledger: {exc}")

generate_clicked = st.button("Generate Schedule")

# A relaxed-constraint retry queued by the recovery buttons takes precedence
# over a fresh click so the chosen relaxation is actually applied.
data = None
relaxation_note = None
if st.session_state.get("retry_config") is not None:
    data, relaxation_note = st.session_state.pop("retry_config")
elif generate_clicked:
    if uploaded_config is not None:
        try:
            data = input_data_from_json(uploaded_config.getvalue().decode("utf-8"))
        except Exception as exc:
            st.error(f"Could not read config: {exc}")
    else:
        data = session_config

if data is not None:
    problems = validate_input(data)
    if problems:
        st.error("Fix the configuration before generating:")
        for problem in problems:
            st.write(f"- {problem}")
    else:
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
                st.session_state.retry_config = (
                    replace(data, min_gap=data.min_gap - 1),
                    f"Relaxed minimum gap to {data.min_gap - 1} to find a feasible schedule.",
                )
                st.rerun()
            if data.nf_block_length > 1 and rcols[1].button(
                f"Retry with NF block length {data.nf_block_length - 1}"
            ):
                st.session_state.retry_config = (
                    replace(data, nf_block_length=data.nf_block_length - 1),
                    f"Relaxed NF block length to {data.nf_block_length - 1} to find a feasible schedule.",
                )
                st.rerun()
        except Exception as exc:
            st.error(str(exc))

        if df is not None:
            st.session_state.result_df = df
            st.session_state.result_data = data
            warning = df.attrs.get("solver_warning") if hasattr(df, "attrs") else None
            if warning:
                st.warning(warning)
            status = df.attrs.get("solver_status")
            wall = df.attrs.get("wall_time_sec")
            if status:
                detail = f"Solver status: {status} · seed {data.seed}"
                if wall is not None:
                    detail += f" · {wall:.2f}s"
                st.caption(detail)
            points = calculate_points(df, data)
            st.dataframe(df)
            quality = schedule_quality(df, data, points=points)
            st.metric("Schedule quality", f"{quality['score']} / 100")
            st.caption(
                f"Coverage {quality['filled']}/{quality['total_slots']} slots filled "
                f"({quality['unfilled']} unfilled) · total range {quality['total_range']:.1f} "
                f"· weekend range {quality['weekend_range']:.1f}"
            )
            ranges = fairness_range_lines(points)
            if ranges:
                st.subheader("Fairness summary")
                for line in ranges:
                    st.write(line)
                fair_frame = build_fairness_frame(points, data)
                if len(fair_frame):
                    chart_df = fair_frame[
                        ["Resident", "Total", "Weekend", "Night Float"]
                    ].set_index("Resident")
                    st.caption("Workload by resident (points)")
                    st.bar_chart(chart_df, stack=False)
            log_text = format_fairness_log(df, data, points=points)
            st.download_button(
                "Download CSV (schedule)",
                df.to_csv(index=False),
                file_name="schedule.csv",
                mime="text/csv",
            )
            st.download_button("Download Fairness Log", log_text, file_name="fairness_log.txt")
            st.download_button(
                "Download updated ledger (for next block)",
                ledger_to_json(update_ledger(carryover_ledger, df, data)),
                file_name="fairness_ledger.json",
                mime="application/json",
            )
            try:
                excel_bytes = schedule_to_excel_bytes(df, data, points=points)
                st.download_button(
                    "Download Excel (schedule + fairness)",
                    excel_bytes,
                    file_name="schedule.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as exc:  # pragma: no cover - e.g. openpyxl not installed
                st.info(f"Excel export unavailable: {exc}")
            try:
                pdf_bytes = schedule_to_pdf_bytes(df, data, points=points)
                st.download_button(
                    "Download PDF (schedule + fairness)",
                    pdf_bytes,
                    file_name="schedule.pdf",
                    mime="application/pdf",
                )
            except Exception as exc:  # pragma: no cover - e.g. reportlab not installed
                st.info(f"PDF export unavailable: {exc}")
            if st.checkbox("Show Fairness Log"):
                st.text(log_text)

if st.session_state.result_df is not None:
    with st.expander("Manual edit & revalidate", expanded=False):
        st.caption("Edit the shift assignments below, then review any constraint issues.")
        edited = st.data_editor(
            st.session_state.result_df,
            key="schedule_editor",
            disabled=["Date", "Day"],
        )
        result_data = st.session_state.result_data
        issues = validate_schedule(edited, result_data)
        if issues:
            st.error(f"{len(issues)} constraint issue(s):")
            for issue in issues:
                st.write(f"- {issue}")
        else:
            st.success("No constraint violations.")
        edited_points = calculate_points(edited, result_data)
        edited_quality = schedule_quality(edited, result_data, points=edited_points)
        st.caption(f"Edited schedule quality: {edited_quality['score']} / 100")

    with st.expander("Why was a slot assigned?", expanded=False):
        rdf = st.session_state.result_df
        result_data = st.session_state.result_data
        labels = [s.label for s in result_data.shifts]
        dates = [row.get("Date") for row in rdf.to_dict("records")]
        if labels and dates:
            why_date = st.selectbox("Date", dates, key="why_date")
            why_label = st.selectbox("Shift", labels, key="why_label")
            for line in assignment_rationale(rdf, result_data, why_date, why_label):
                st.write(f"- {line}")
        else:
            st.caption("Generate a schedule with at least one shift to use this.")
