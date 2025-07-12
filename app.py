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

# Optional toggle to load predefined test data
use_test_data = st.checkbox("🧪 Use Test Data", value=False)
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

# ────────────────────────────────────────────────────────────────────────────────
# Shift template entry
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("⚙️ Shift Templates"):
    if use_test_data:
        st.info("Using preset test shifts")
        st.session_state.shifts = TEST_SHIFTS.copy()
        st.table(pd.DataFrame(st.session_state.shifts))
    else:
        col1, col2, col3, col4, col5 = st.columns([3, 2, 1, 1, 1])
        shift_label   = col1.text_input("Shift Label (e.g. ER1)")
        role          = col2.selectbox("Role", ["Junior", "Senior"])
        night_float   = col3.checkbox("Night Float")
        thur_weekend  = col4.checkbox("Thursday Night = Weekend")
        points        = col5.number_input("Points", 1.0, 10.0, value=2.0 if night_float else 1.0, step=0.5)

        if st.button("Add Shift", key="btn_add_shift"):
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

        # Show list & delete picker
        if st.session_state.shifts:
            st.table(pd.DataFrame(st.session_state.shifts))
            delete_shift = st.selectbox(
                "Select a shift to delete",
                [""] + [s["label"] for s in st.session_state.shifts],
                key="del_shift_select",
            )
            if st.button("🗑️ Delete Shift") and delete_shift:
                st.session_state.shifts = [
                    s for s in st.session_state.shifts if s["label"] != delete_shift
                ]
                st.session_state.pop("del_shift_select", None)  # forget picker state
                st.experimental_rerun()


# ────────────────────────────────────────────────────────────────────────────────
# Participants
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("👥 Participants"):
    if use_test_data:
        st.info("Using preset test participants")
        juniors = TEST_JUNIORS
        seniors = TEST_SENIORS
    else:
        use_demo = st.checkbox("Use Demo Participants", True)
        if use_demo:
            juniors = ["Alice", "Bob", "Charlie", "Dina"]
            seniors = ["Eli", "Fay", "Gina", "Hank"]
        else:
            juniors = [n.strip() for n in st.text_area("Juniors").splitlines() if n.strip()]
            seniors = [n.strip() for n in st.text_area("Seniors").splitlines() if n.strip()]

    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# ────────────────────────────────────────────────────────────────────────────────
# Night‑float eligibility
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("🌙 Night Float Eligibility"):
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

# ────────────────────────────────────────────────────────────────────────────────
# Extra on‑calls bias
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("⚖️ Extra On‑Calls"):
    for nm in juniors + seniors:
        st.session_state.extra_oncalls[nm] = st.number_input(
            f"Extra on‑calls for {nm}",
            0,
            10,
            value=st.session_state.extra_oncalls.get(nm, 0),
            key=f"extra_{nm}",
        )

# ────────────────────────────────────────────────────────────────────────────────
# Date range & rules
# ────────────────────────────────────────────────────────────────────────────────

st.subheader("📅 Schedule Settings")
st.session_state.start_date = st.date_input("Start Date", st.session_state.start_date)
st.session_state.end_date = st.date_input("End Date", st.session_state.end_date)

if st.session_state.start_date > st.session_state.end_date:
    st.error("Start date must be on or before end date.")
    gen_disabled = True
else:
    gen_disabled = False

st.session_state.min_gap = st.slider(
    "Minimum Days Between Shifts", 0, 7, st.session_state.min_gap
)
st.session_state.nf_block_length = st.slider(
    "Night Float Block Length", 1, 10, st.session_state.nf_block_length
)
st.session_state.seed = st.number_input(
    "Random Seed", value=st.session_state.seed, step=1
)

# ────────────────────────────────────────────────────────────────────────────────
# Leaves & rotators
# ────────────────────────────────────────────────────────────────────────────────

with st.expander("✈️ Leaves"):
    leave_name = st.selectbox("Leave Name", [""] + juniors + seniors)
    leave_from, leave_to = st.date_input(
        "Leave Period",
        [st.session_state.start_date, st.session_state.start_date],
        key="leave_period",
    )
    if st.button("Add Leave", key="btn_add_leave") and leave_name and leave_from <= leave_to:
        entry = (leave_name, leave_from, leave_to)
        if entry not in st.session_state.leaves:
            st.session_state.leaves.append(entry)

    # Show list & delete picker
    if st.session_state.leaves:
        leaves_df = pd.DataFrame(
            st.session_state.leaves, columns=["Name", "From", "To"]
        )
        st.table(leaves_df)
        leave_labels = [
            f"{n}  {f.strftime('%Y-%m-%d')} → {t.strftime('%Y-%m-%d')}"
            for n, f, t in st.session_state.leaves
        ]
        delete_leave = st.selectbox(
            "Select a leave to delete", [""] + leave_labels, key="del_leave_select"
        )
        if st.button("🗑️ Delete Leave") and delete_leave:
            idx = leave_labels.index(delete_leave)
            st.session_state.leaves.pop(idx)
            st.session_state.pop("del_leave_select", None)
            st.experimental_rerun()




with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Rotator Name", [""] + juniors + seniors)
    rot_from, rot_to = st.date_input(
        "Rotator Period",
        [st.session_state.start_date, st.session_state.start_date],
        key="rot_period",
    )
    if st.button("Add Rotator", key="btn_add_rotator") and rot_name and rot_from <= rot_to:
        entry = (rot_name, rot_from, rot_to)
        if entry not in st.session_state.rotators:
            st.session_state.rotators.append(entry)

    # Show list & delete picker
    if st.session_state.rotators:
        rot_df = pd.DataFrame(
            st.session_state.rotators, columns=["Name", "From", "To"]
        )
        st.table(rot_df)
        rot_labels = [
            f"{n}  {f.strftime('%Y-%m-%d')} → {t.strftime('%Y-%m-%d')}"
            for n, f, t in st.session_state.rotators
        ]
        delete_rot = st.selectbox(
            "Select a rotator period to delete", [""] + rot_labels, key="del_rot_select"
        )
        if st.button("🗑️ Delete Rotator") and delete_rot:
            idx = rot_labels.index(delete_rot)
            st.session_state.rotators.pop(idx)
            st.session_state.pop("del_rot_select", None)
            st.experimental_rerun()


# ────────────────────────────────────────────────────────────────────────────────
# Helper predicates
# ────────────────────────────────────────────────────────────────────────────────



# ────────────────────────────────────────────────────────────────────────────────
# Generate button & outputs
# ────────────────────────────────────────────────────────────────────────────────

if st.button("🚀 Generate Schedule", disabled=False):
    random.seed(st.session_state.seed)
    df, wide_summ, unf, compact_summ = build_schedule()
    st.session_state.df_sched = df
    st.session_state.df_summary = wide_summ
    st.session_state.df_unfilled = unf
    st.session_state.compact_summ = compact_summ
    FAIR_TOL = 0  # 0 = show every deviation, 1 = ignore ±1
    st.session_state.median_df = build_median_report(wide_summ, FAIR_TOL)

    st.session_state.generated = True
    st.success("✅ Schedule generated!")

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
    # ---------- fairness vs MEDIAN ----------
    FAIR_TOL = 0    # 0 = show every deviation, 1 = ignore ±1
    median_df = st.session_state.median_df
 

    if not median_df.empty:
        st.warning("⚖️  Median fairness – residents above / below peer median")
        st.dataframe(median_df, hide_index=True)
        st.download_button(
            "Download median fairness CSV",
            median_df.to_csv(index=False),
            "median_fairness.csv",
            key="btn_dl_median",
        )
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
    st.download_button(
        "Download Log",
        json.dumps(log_data, default=str),
        "schedule_log.json",
        key="btn_dl_log",
    )
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
            st.text_area("Test Output", st.session_state.test_log, height=300, key="txt_test_output")
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




