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
from model.coloring import COLOR_MODES, schedule_cell_colors
import os

st.set_page_config(page_title="Idea Gold Scheduler", page_icon="🗓️", layout="wide")
st.title("🗓️ Idea Gold Scheduler")
st.caption(
    "Build a provably fair on-call schedule. Set up shifts and people, tweak the "
    "rules, then Generate — the optimiser balances the workload for you."
)


def _date_range_editor(title: str, key: str, people: list, with_compensation: bool = False) -> None:
    """Inline editor for (resident, start, end[, compensated]) windows.

    With ``with_compensation`` (leaves) each entry carries a Compensated flag:
    compensated keeps the resident's full fair share, uncompensated scales it down
    like a rotator.
    """
    st.markdown(f"**{title}**")
    if not people:
        st.caption("Add participants first to configure this.")
        return
    layout = [3, 2, 2, 2, 1] if with_compensation else [3, 2, 2, 1]
    c = st.columns(layout)
    with c[0]:
        who = st.selectbox("Resident", people, key=f"{key}_who")
    with c[1]:
        start = st.date_input("Start", date.today(), key=f"{key}_start")
    with c[2]:
        end = st.date_input("End", date.today(), key=f"{key}_end")
    compensated = True
    if with_compensation:
        with c[3]:
            compensated = st.checkbox("Compensated", value=True, key=f"{key}_comp")
    with c[-1]:
        st.markdown("&nbsp;")
        if st.button("Add", key=f"{key}_add"):
            entry = (who, start, end, compensated) if with_compensation else (who, start, end)
            st.session_state[key].append(entry)
    rows = st.session_state[key]
    if rows:
        table_rows = []
        for entry in rows:
            row = {"Resident": entry[0], "Start": entry[1], "End": entry[2]}
            if with_compensation:
                row["Compensated"] = entry[3] if len(entry) > 3 else True
            table_rows.append(row)
        st.table(pd.DataFrame(table_rows))
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


def _extra_points_editor(people: list) -> None:
    """Inline editor for mandatory per-resident extra points (e.g. a penalty)."""
    st.markdown(
        "**Extra points — mandatory points a resident must carry above their "
        "fair share (e.g. a penalty). Enforced, not just preferred.**"
    )
    if not people:
        st.caption("Add participants first to configure this.")
        return
    c = st.columns([3, 2, 1])
    with c[0]:
        who = st.selectbox("Resident", people, key="extra_who")
    with c[1]:
        pts = st.number_input("Extra points", 0.0, 999.0, 0.0, 0.5, key="extra_pts")
    with c[2]:
        st.markdown("&nbsp;")
        if st.button("Add", key="extra_add"):
            if pts > 0:
                st.session_state.extra_points[who] = pts
            else:
                st.session_state.extra_points.pop(who, None)
    ep = st.session_state.extra_points
    if ep:
        st.table(pd.DataFrame([{"Resident": p, "Extra points": v} for p, v in ep.items()]))
        rm = st.selectbox("Remove extra", list(ep.keys()), key="extra_del")
        if st.button("Remove", key="extra_del_btn"):
            st.session_state.extra_points.pop(rm, None)


_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _weekday_points_editor(shift_labels: list) -> None:
    """Inline editor: a shift's exact points on a given weekday (e.g. night = 2 on Tue)."""
    st.markdown("**Weekday point overrides — a shift's exact points on a weekday**")
    if not shift_labels:
        st.caption("Add shift templates first.")
        return
    c = st.columns([3, 2, 2, 1])
    with c[0]:
        label = st.selectbox("Shift", shift_labels, key="wp_shift")
    with c[1]:
        wd = st.selectbox("Weekday", _WD, key="wp_wd")
    with c[2]:
        pts = st.number_input("Points", 0.0, 99.0, 1.0, 0.5, key="wp_pts")
    with c[3]:
        st.markdown("&nbsp;")
        if st.button("Add", key="wp_add"):
            st.session_state.weekday_points[(label, _WD.index(wd))] = pts
    wp = st.session_state.weekday_points
    if wp:
        st.table(pd.DataFrame(
            [{"Shift": lbl, "Weekday": _WD[d], "Points": v} for (lbl, d), v in wp.items()]
        ))
        opts = list(wp.keys())
        idx = st.selectbox(
            "Remove override", list(range(len(opts))),
            format_func=lambda i: f"{opts[i][0]} / {_WD[opts[i][1]]}", key="wp_del_idx",
        )
        if st.button("Remove", key="wp_del"):
            st.session_state.weekday_points.pop(opts[idx], None)


def _holidays_editor() -> None:
    """Inline editor: bonus points for every shift on a date (with weekend option)."""
    st.markdown("**Holidays — bonus points for shifts on a date**")
    c = st.columns([3, 2, 2, 1])
    with c[0]:
        hd = st.date_input("Date", date.today(), key="hol_date")
    with c[1]:
        bonus = st.number_input("Bonus points", 0.0, 99.0, 1.0, 0.5, key="hol_bonus")
    with c[2]:
        wk = st.checkbox("Count as weekend", value=False, key="hol_wk")
    with c[3]:
        st.markdown("&nbsp;")
        if st.button("Add", key="hol_add"):
            st.session_state.holidays = [h for h in st.session_state.holidays if h[0] != hd]
            st.session_state.holidays.append((hd, bonus, wk))
    hs = st.session_state.holidays
    if hs:
        st.table(pd.DataFrame(
            [{"Date": d, "Bonus": b, "Counts as weekend": w} for d, b, w in hs]
        ))
        idx = st.selectbox(
            "Remove holiday", list(range(len(hs))),
            format_func=lambda i: str(hs[i][0]), key="hol_del_idx",
        )
        if st.button("Remove", key="hol_del"):
            st.session_state.holidays.pop(idx)


def _style_schedule(df, data, color_mode):
    """Return a Styler shading the grid by ``color_mode`` (unfilled always flagged).

    Uses the same ``schedule_cell_colors`` map the Excel/PDF exports use, so the
    on-screen view and the downloads agree cell-for-cell.
    """
    color_map = schedule_cell_colors(df, data, color_mode)
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
if "extra_points" not in st.session_state:
    st.session_state.extra_points = {}
if "weekday_points" not in st.session_state:
    st.session_state.weekday_points = {}
if "holidays" not in st.session_state:
    st.session_state.holidays = []
if "result_df" not in st.session_state:
    st.session_state.result_df = None
if "result_data" not in st.session_state:
    st.session_state.result_data = None
if "result_prior_ledger" not in st.session_state:
    st.session_state.result_prior_ledger = None
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
tab_people, tab_rules, tab_adv, tab_save = st.tabs(
    ["① Shifts & people", "② Dates & rules", "③ Advanced", "④ Save / carryover"]
)

with tab_people:
    st.subheader("Shift templates")
    label = st.text_input("Label")
    role = st.selectbox("Role", ["Junior", "Senior"])
    sc = st.columns(3)
    with sc[0]:
        nf = st.checkbox("Night float")
    with sc[1]:
        thu_wk = st.checkbox("Thu counts as weekend")
    with sc[2]:
        points = st.number_input("Points", 1.0, 10.0, 1.0, 0.5)
    if st.button("Add shift", key="add_shift"):
        st.session_state.shifts.append(
            ShiftTemplate(label=label, role=role, night_float=nf, thu_weekend=thu_wk, points=points)
        )
    if st.session_state.shifts:
        st.table(pd.DataFrame([s.__dict__ for s in st.session_state.shifts]))
        del_idx = st.selectbox(
            "Delete shift", list(range(len(st.session_state.shifts))),
            format_func=lambda i: st.session_state.shifts[i].label,
        )
        if st.button("Delete shift", key="del_shift"):
            st.session_state.shifts.pop(del_idx)

    st.divider()
    cols = st.columns(2)
    with cols[0]:
        st.subheader("Participants")
        juniors_text = st.text_area("Juniors (one per line)", "\n".join(st.session_state.juniors))
        seniors_text = st.text_area("Seniors (one per line)", "\n".join(st.session_state.seniors))
        st.session_state.juniors = [n.strip() for n in juniors_text.splitlines() if n.strip()]
        st.session_state.seniors = [n.strip() for n in seniors_text.splitlines() if n.strip()]
    with cols[1]:
        st.subheader("Night-float eligible")
        st.session_state.nf_juniors = st.multiselect(
            "Juniors", st.session_state.juniors, default=st.session_state.nf_juniors
        )
        st.session_state.nf_seniors = st.multiselect(
            "Seniors", st.session_state.seniors, default=st.session_state.nf_seniors
        )

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
            "Weekend days", _WD, default=["Sat", "Sun"],
            help="Days that count as weekend for fairness (a shift's 'Thu' flag also adds Thursday).",
        )
    weekend_days = [_WD.index(name) for name in weekend_labels]

    st.divider()
    st.subheader("Leaves & rotators")
    _people = st.session_state.juniors + st.session_state.seniors
    _date_range_editor(
        "Leaves — resident unavailable during window", "leaves", _people, with_compensation=True,
    )
    st.caption(
        "Compensated leave keeps the resident's full fair share; uncompensated "
        "scales it down for the absence (like a rotator)."
    )
    st.divider()
    _date_range_editor("Rotators — resident only available during window", "rotators", _people)

with tab_adv:
    st.subheader("Per-resident caps & extra points")
    _caps_editor(st.session_state.juniors + st.session_state.seniors)
    st.divider()
    _extra_points_editor(st.session_state.juniors + st.session_state.seniors)
    st.divider()
    st.subheader("Point overrides & holidays")
    _weekday_points_editor([s.label for s in st.session_state.shifts])
    st.divider()
    _holidays_editor()

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
extra_points = {
    p: v
    for p, v in st.session_state.extra_points.items()
    if p in _active_people and v
}
_shift_labels = {s.label for s in st.session_state.shifts}
weekday_points = {
    k: v for k, v in st.session_state.weekday_points.items() if k[0] in _shift_labels
}
holidays = list(st.session_state.holidays)

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
    extra_points=extra_points or None,
    weekday_points=weekday_points or None,
    holidays=holidays or None,
)

with tab_save:
    st.subheader("Save / load configuration")
    st.download_button(
        "Download config (JSON)",
        input_data_to_json(session_config),
        file_name="idea_gold_config.json",
        mime="application/json",
    )
    uploaded_config = st.file_uploader("Load config (JSON), then click Generate", type="json")
    st.divider()
    st.subheader("Carryover fairness (optional)")
    st.caption(
        "Leave this empty for a standalone, one-off schedule — this block is "
        "balanced on its own, with no link to fairness history. To keep fairness "
        "across months instead, upload the previous block's ledger here (residents "
        "who carried extra get lighter targets now) and download the updated "
        "ledger afterwards for next time."
    )
    uploaded_ledger = st.file_uploader("Load fairness ledger (JSON)", type="json", key="ledger_upload")

carryover_ledger = None
if uploaded_ledger is not None:
    try:
        carryover_ledger = ledger_from_json(uploaded_ledger.getvalue().decode("utf-8"))
    except Exception as exc:
        st.error(f"Could not read ledger: {exc}")

st.divider()
generate_clicked = st.button(
    "⚙️ Generate schedule", type="primary", use_container_width=True
)

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
            st.session_state.result_prior_ledger = carryover_ledger

# Results render from session_state so they survive reruns (e.g. changing the
# colour mode) without re-solving.
if st.session_state.result_df is not None:
    df = st.session_state.result_df
    data = st.session_state.result_data
    prior_ledger = st.session_state.get("result_prior_ledger")

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

    color_label = st.selectbox(
        "Colour cells by",
        list(COLOR_MODES),
        index=0,
        help="Shade the schedule grid; the same colours flow into the Excel and PDF downloads.",
    )
    color_mode = COLOR_MODES[color_label]
    try:
        st.dataframe(_style_schedule(df, data, color_mode), use_container_width=True)
    except Exception:  # pragma: no cover - styling is best-effort
        st.dataframe(df, use_container_width=True)

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
        ledger_to_json(update_ledger(prior_ledger, df, data)),
        file_name=f"fairness_ledger_through_{data.end_date.isoformat()}.json",
        mime="application/json",
    )
    st.caption(
        "Keep this file — it's the cumulative fairness record. Streamlit "
        "Cloud doesn't store anything between sessions, so re-upload it "
        "under 'Carryover fairness' next block to keep months fair."
    )
    try:
        excel_bytes = schedule_to_excel_bytes(df, data, points=points, color_mode=color_mode)
        st.download_button(
            "Download Excel (schedule + fairness)",
            excel_bytes,
            file_name="schedule.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:  # pragma: no cover - e.g. openpyxl not installed
        st.info(f"Excel export unavailable: {exc}")
    try:
        pdf_bytes = schedule_to_pdf_bytes(df, data, points=points, color_mode=color_mode)
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
