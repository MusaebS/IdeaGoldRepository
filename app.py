import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta, date
import json
import subprocess
import threading
import shlex
import re
from scheduler import build_schedule, build_median_report

# ------------------------------------------------------------------
# Optional test data
# ------------------------------------------------------------------
TEST_JUNIORS = [
    "Alice", "Bob", "Charlie", "Dina", "Ethan", "Fiona", "George", "Hannah",
    "Ian", "Jade", "Kevin", "Lily", "Mason", "Nina", "Oscar", "Paula",
    "Quincy", "Rosa", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xander",
    "Yara", "Zane", "Amy", "Brian", "Cindy", "David",
]

TEST_SENIORS = [
    "Eli", "Fay", "Gina", "Hank", "Iris", "Jack", "Kara", "Leo",
    "Mira", "Neil", "Olga", "Pete", "Quinn", "Ruth", "Steve",
]

TEST_SHIFTS = [
    {"label": "Junior NF", "role": "Junior", "night_float": True,  "thur_weekend": False, "points": 2.0},
    {"label": "Senior NF", "role": "Senior", "night_float": True,  "thur_weekend": False, "points": 2.0},
    {"label": "ER night", "role": "Junior", "night_float": False, "thur_weekend": True,  "points": 1.0},
    {"label": "Ward night", "role": "Junior", "night_float": False, "thur_weekend": True,  "points": 1.0},
    {"label": "Senior night", "role": "Senior", "night_float": False, "thur_weekend": True,  "points": 1.0},
    {"label": "Evening shift", "role": "Senior", "night_float": False, "thur_weekend": False, "points": 1.0},
    {"label": "Morning shift", "role": "Senior", "night_float": False, "thur_weekend": False, "points": 1.0},
    {"label": "Ward morning", "role": "Junior", "night_float": False, "thur_weekend": False, "points": 1.0},
    {"label": "ER zone 1 morning", "role": "Junior", "night_float": False, "thur_weekend": False, "points": 1.0},
    {"label": "ER zone 2 morning", "role": "Junior", "night_float": False, "thur_weekend": False, "points": 1.0},
]

# ────────────────────────────────────────────────────────────────────
# Page configuration – MUST precede every other Streamlit call
# ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("🪙 Idea Gold Scheduler – Stable & Fair v2025-05-16")

# Debug tools
debug_mode = st.sidebar.checkbox("Debug Mode")

# Optional toggle to load predefined test data and persist choice
st.session_state.setdefault("use_test_data", False)
use_test_data = st.checkbox("🧪 Use Test Data", value=st.session_state.use_test_data)
st.session_state.use_test_data = use_test_data
if use_test_data:
    st.session_state.shifts = TEST_SHIFTS.copy()

# ────────────────────────────────────────────────────────────────────────────────
# Helper: integer‑quota allocator (Hare–Niemeyer)
# ────────────────────────────────────────────────────────────────────────────────



# ────────────────────────────────────────────────────────────────────────────────
# Session‑state defaults and reset
# ────────────────────────────────────────────────────────────────────────────────

def_state = {
    "shifts": [],
    "rotators": [],
    "leaves": [],
    "extra_oncalls": {},
    "weights": {},
    "nf_juniors": [],
    "nf_seniors": [],
    "start_date": date.today(),
    "end_date": date.today() + timedelta(days=27),
    "min_gap": 2,
    "nf_block_length": 5,
    "seed": 0,
    "pytest_opts": "-q",
    "use_cov": False,
    "fail_fast": False,
    "test_progress": 0.0,

}
for k, v in def_state.items():
    st.session_state.setdefault(k, v)


@st.cache_data(show_spinner=False)
def cached_build(state_json, seed):
    random.seed(seed)
    df, wide, unf, compact = build_schedule()
    med = build_median_report(wide, 0)
    return df, wide, unf, compact, med

if st.button("🔁 Reset All Data", key="btn_reset"):
    for k in (
        "shifts",
        "rotators",
        "leaves",
        "extra_oncalls",
        "weights",
        "nf_juniors",
        "nf_seniors",
        "seed",
        "df_sched",
        "df_summary",
        "df_unfilled",
        "median_df",
        "generated",
        "test_log",
        "test_summary",
        "coverage_pct",
        "cov_xml",
        "test_running",
        "test_progress",

        "pytest_opts",
        "use_cov",
        "fail_fast",
    ):
        st.session_state.pop(k, None)
    st.experimental_rerun()

# Tabs for navigation
tab_setup, tab_staff, tab_schedule, tab_results = st.tabs(
    ["Setup", "Staff", "Schedule", "Results"]
)

# ────────────────────────────────────────────────────────────────────────────────
# Shift template entry
# ────────────────────────────────────────────────────────────────────────────────

with tab_setup:
    st.subheader("⚙️ Shift Templates")
    if use_test_data:
        st.info("Using preset test shifts")
        st.session_state.shifts = TEST_SHIFTS.copy()
    else:
        with st.form("shift_form"):
            col1, col2, col3, col4, col5 = st.columns([3, 2, 1, 1, 1])
            shift_label = col1.text_input("Shift Label (e.g. ER1)")
            role = col2.selectbox("Role", ["Junior", "Senior"])
            night_float = col3.checkbox("Night Float")
            thur_weekend = col4.checkbox(
                "Thursday Night = Weekend",
                help="Counts Thursday night as a weekend for fairness",
            )
            points = col5.number_input(
                "Points",
                1.0,
                10.0,
                value=2.0 if night_float else 1.0,
                step=0.5,
            )
            submitted_shift = st.form_submit_button("Add Shift")
        if submitted_shift:
            if not shift_label.strip():
                st.error("Shift label cannot be empty.")
            else:
                base = shift_label.strip()
                existing = {s["label"] for s in st.session_state.shifts}
                idx, unique = 1, base
                while unique in existing:
                    idx += 1
                    unique = f"{base} #{idx}"
                st.session_state.shifts.append(
                    {
                        "label": unique,
                        "role": role,
                        "night_float": night_float,
                        "thur_weekend": thur_weekend,
                        "points": points,
                    }
                )

        # Edit existing shifts inline
        if st.session_state.shifts:
            edited = st.data_editor(
                pd.DataFrame(st.session_state.shifts),
                num_rows="dynamic",
                key="edit_shifts",
            )
        st.session_state.shifts = edited.to_dict(orient="records")

    st.subheader("📅 Schedule Settings")
    st.session_state.start_date = st.date_input(
        "Start Date",
        st.session_state.start_date,
    )
    st.session_state.end_date = st.date_input(
        "End Date",
        st.session_state.end_date,
    )

    if st.session_state.start_date > st.session_state.end_date:
        st.error("Start date must be on or before end date.")
        gen_disabled = True
    else:
        gen_disabled = False

    st.session_state.min_gap = st.slider(
        "Minimum Days Between Shifts",
        0,
        7,
        st.session_state.min_gap,
    )
    st.session_state.nf_block_length = st.slider(
        "Night Float Block Length",
        1,
        10,
        st.session_state.nf_block_length,
        help="Length of consecutive night float block",
    )
    st.session_state.seed = st.number_input(
        "Random Seed",
        value=st.session_state.seed,
        step=1,
    )

    cfg_keys = list(def_state.keys()) + ["juniors", "seniors"]
    cfg = {k: st.session_state.get(k) for k in cfg_keys}
    st.download_button(
        "Download config JSON",
        json.dumps(cfg, default=str),
        "config.json",
        key="btn_dl_cfg",
    )
    uploaded = st.file_uploader("Load config JSON", type="json")
    if uploaded:
        data = json.load(uploaded)
        for k, v in data.items():
            st.session_state[k] = v
        st.experimental_rerun()

# ────────────────────────────────────────────────────────────────────────────────
# Participants
# ────────────────────────────────────────────────────────────────────────────────

with tab_staff:
    st.subheader("👥 Participants")
    if use_test_data:
        st.info("Using preset test participants")
        st.session_state.juniors = TEST_JUNIORS
        st.session_state.seniors = TEST_SENIORS
    else:
        with st.form("participants_form"):
            use_demo = st.checkbox("Use Demo Participants", True)
            if use_demo:
                juniors = ["Alice", "Bob", "Charlie", "Dina"]
                seniors = ["Eli", "Fay", "Gina", "Hank"]
            else:
                juniors = [n.strip() for n in st.text_area("Juniors").splitlines() if n.strip()]
                seniors = [n.strip() for n in st.text_area("Seniors").splitlines() if n.strip()]
            save_part = st.form_submit_button("Save Participants")
        if save_part:
            st.session_state.juniors = juniors
            st.session_state.seniors = seniors

    juniors = st.session_state.get("juniors", [])
    seniors = st.session_state.get("seniors", [])

    st.subheader("🌙 Night Float Eligibility")
    st.session_state.nf_juniors = st.multiselect(
        "NF‑Eligible Juniors",
        juniors,
        default=st.session_state.nf_juniors,
        key="nf_j_select",
    )
    st.session_state.nf_seniors = st.multiselect(
        "NF‑Eligible Seniors",
        seniors,
        default=st.session_state.nf_seniors,
        key="nf_s_select",
    )

    st.subheader("⚖️ Extra On‑Calls")
    with st.form("extra_oncalls_form"):
        for nm in juniors + seniors:
            st.session_state.extra_oncalls[nm] = st.number_input(
                f"Extra on‑calls for {nm}",
                0,
                10,
                value=st.session_state.extra_oncalls.get(nm, 0),
                key=f"extra_{nm}",
                help="Bias scheduling toward/away from this person",
            )
        st.form_submit_button("Save Extra On‑Calls")

    st.subheader("✈️ Leaves")
    with st.form("leave_form"):
        leave_name = st.selectbox("Leave Name", ["" ] + juniors + seniors)
        leave_from, leave_to = st.date_input(
            "Leave Period",
            [st.session_state.start_date, st.session_state.start_date],
            key="leave_period",
        )
        submitted_leave = st.form_submit_button("Add Leave")
    if submitted_leave and leave_name and leave_from <= leave_to:
        entry = (leave_name, leave_from, leave_to)
        if entry not in st.session_state.leaves:
            st.session_state.leaves.append(entry)

    if st.session_state.leaves:
        leaves_df = pd.DataFrame(st.session_state.leaves, columns=["Name", "From", "To"])
        edited_leaves = st.data_editor(
            leaves_df,
            num_rows="dynamic",
            key="edit_leaves",
        )
        st.session_state.leaves = [tuple(r) for r in edited_leaves.to_records(index=False)]

    st.subheader("🔄 Rotators")
    with st.form("rot_form"):
        rot_name = st.selectbox("Rotator Name", ["" ] + juniors + seniors)
        rot_from, rot_to = st.date_input(
            "Rotator Period",
            [st.session_state.start_date, st.session_state.start_date],
            key="rot_period",
        )
        submitted_rot = st.form_submit_button("Add Rotator")
    if submitted_rot and rot_name and rot_from <= rot_to:
        entry = (rot_name, rot_from, rot_to)
        if entry not in st.session_state.rotators:
            st.session_state.rotators.append(entry)

    if st.session_state.rotators:
        rot_df = pd.DataFrame(st.session_state.rotators, columns=["Name", "From", "To"])
        edited_rot = st.data_editor(
            rot_df,
            num_rows="dynamic",
            key="edit_rotators",
        )
        st.session_state.rotators = [tuple(r) for r in edited_rot.to_records(index=False)]

# ────────────────────────────────────────────────────────────────────────────────
# Date range & rules
# ────────────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────────
# ────────────────────────────────────────────────────────────────────────────────



# ────────────────────────────────────────────────────────────────────────────────
# Helper predicates
# ────────────────────────────────────────────────────────────────────────────────



# ────────────────────────────────────────────────────────────────────────────────
# Generate button & outputs
# ────────────────────────────────────────────────────────────────────────────────
with tab_schedule:
    disabled = gen_disabled or not st.session_state.shifts or not (
        st.session_state.juniors or st.session_state.seniors
    )
    if st.button("🚀 Generate Schedule", disabled=disabled):
        cfg = {k: st.session_state[k] for k in (
            "shifts", "rotators", "leaves", "juniors", "seniors",
            "start_date", "end_date", "min_gap", "nf_block_length", "seed"
        )}
        state_json = json.dumps(cfg, default=str)
        with st.spinner("Generating schedule…"):
            df, wide_summ, unf, compact_summ, med_df = cached_build(state_json, st.session_state.seed)
        st.session_state.df_sched = df
        st.session_state.df_summary = wide_summ
        st.session_state.df_unfilled = unf
        st.session_state.compact_summ = compact_summ
        st.session_state.median_df = med_df
        st.session_state.generated = True
        st.success("✅ Schedule generated!")

with tab_results:
    if st.session_state.get("generated"):
        df = st.session_state.df_sched
        wide_summ = st.session_state.df_summary
        unf = st.session_state.df_unfilled
        compact_summ = st.session_state.compact_summ
        median_df = st.session_state.median_df
        st.dataframe(df)
        st.subheader("📊 Compact Summary")
        st.dataframe(compact_summ)
        st.subheader("📊 Assignment Summary (wide)")
        st.dataframe(wide_summ)
        FAIR_TOL = 0
        if not median_df.empty:
            st.warning("⚖️  Median fairness – residents above / below peer median")
            st.dataframe(median_df, hide_index=True)
            st.download_button("Download median fairness CSV", median_df.to_csv(index=False), "median_fairness.csv", key="btn_dl_median")
        else:
            st.info(f"✨ Everyone is within ±{FAIR_TOL} of the median for every label.")
        if not unf.empty:
            st.warning("⚠️ Unfilled Slots Detected")
            st.dataframe(unf)
        st.download_button("Download Schedule CSV", df.to_csv(index=False), "schedule.csv")
        st.download_button("Download Summary CSV", wide_summ.to_csv(index=False), "summary.csv")
        log_data = {
            "config": {k: st.session_state[k] for k in st.session_state if k in def_state or k in ("juniors", "seniors", "seed")},
            "schedule": df.to_dict(orient="records"),
            "summary": wide_summ.to_dict(orient="records"),
            "unfilled": unf.to_dict(orient="records"),
        }
        st.download_button("Download Log", json.dumps(log_data, default=str), "schedule_log.json", key="btn_dl_log")
        if not unf.empty:
            st.download_button("Download Unfilled CSV", unf.to_csv(index=False), "unfilled.csv")

    # --------------------------------------
    # Heavy testing utilities
    # --------------------------------------

    def parse_summary(log_text: str) -> str:
        m = re.search(r"=+\s*(.+?)\s*in\s*([0-9.]+s)\s*=+", log_text)
        if m:
            return f"{m.group(1)} in {m.group(2)}"

        lines = log_text.splitlines()
        for ln in reversed(lines):
            if "passed" in ln or "failed" in ln:
                return ln.strip()
        return ""

    def parse_coverage(log_text: str) -> str | None:
        m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+%)", log_text)
        return m.group(1) if m else None

    def pytest_cov_available() -> bool:
        import importlib.util
        return importlib.util.find_spec("pytest_cov") is not None


    def run_tests(opts: str, cov: bool, fail_fast: bool):
        cmd = ["pytest"]
        if fail_fast:
            cmd.append("-x")
        if opts:
            cmd.extend(shlex.split(opts))
        use_cov = cov and pytest_cov_available()
        if cov and not use_cov:
            st.session_state.test_log = "pytest-cov not installed; running without coverage\n"
        if use_cov:
            cmd.extend(["--cov=.", "--cov-report=term", "--cov-report=xml"])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        st.session_state.test_progress = 0.0
        total = None
        progress = 0
        for line in proc.stdout:
            if total is None:
                m = re.search(r"collected (\d+) items", line)
                if m:
                    total = int(m.group(1))
                    st.session_state.test_progress = 0.0
            if total is not None:
                if re.match(r"^[.FE]+$", line.strip()):
                    progress += len(line.strip())
                    st.session_state.test_progress = min(progress / total, 1.0)
            st.session_state.test_log += line
        proc.wait()
        st.session_state.test_summary = parse_summary(st.session_state.test_log)
        st.session_state.coverage_pct = parse_coverage(st.session_state.test_log)
        if use_cov:


            try:
                with open("coverage.xml") as f:
                    st.session_state.cov_xml = f.read()
            except Exception:
                st.session_state.cov_xml = None
        st.session_state.test_running = False
        st.session_state.test_progress = 1.0
        try:
            st.experimental_rerun()
        except Exception:
            pass


    if debug_mode:
        with st.expander("🧪 Heavy Testing"):
            opts = st.text_input("Extra pytest options", st.session_state.pytest_opts, key="pytest_opts")
            cov = st.checkbox("Enable coverage", value=st.session_state.use_cov, key="use_cov")
            ff = st.checkbox("Fail fast (-x)", value=st.session_state.fail_fast, key="fail_fast")
    
            if st.button("Run Heavy Tests", key="btn_heavy_tests"):
                st.session_state.test_running = True
                st.session_state.test_log = ""
                st.session_state.test_summary = ""
                st.session_state.coverage_pct = None
                st.session_state.cov_xml = None
                st.session_state.test_progress = 0.0
    
                threading.Thread(target=run_tests, args=(opts, cov, ff), daemon=True).start()
    
            if st.session_state.get("test_running"):
                st.info("Running tests in background...")
                st.progress(st.session_state.get("test_progress", 0.0))
    
    
            if st.session_state.get("test_log"):
                if st.session_state.get("test_summary"):
                    st.success(st.session_state.test_summary)
                if st.session_state.get("coverage_pct"):
                    st.write(f"Coverage: {st.session_state.coverage_pct}")
                st.text_area(
                    "Test Output",
                    st.session_state.test_log,
                    height=300,
                    key="txt_test_output",
                )
                st.download_button(
                    "Download Test Log",
                    st.session_state.test_log,
                    "test_log.txt",
                    key="btn_dl_test_log",
                )
                if st.session_state.get("cov_xml"):
                    st.download_button(
                        "Download Coverage XML",
                        st.session_state.cov_xml,
                        "coverage.xml",
                        key="btn_dl_cov",
                    )




