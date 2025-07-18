import streamlit as st
import pandas as pd
from datetime import date, timedelta

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule
from model.demo_data import sample_shifts, sample_names
import os

st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("Idea Gold Scheduler – CP-SAT")

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

date_cols = st.columns(2)
with date_cols[0]:
    start_date = st.date_input("Start Date", date.today())
with date_cols[1]:
    end_date = st.date_input("End Date", date.today() + timedelta(days=27))
min_gap = st.slider("Minimum Gap", 0, 7, 1)
nf_block_len = st.number_input("NF Block Length", 1, 7, 5)

if st.button("Generate Schedule"):
    data = InputData(
        start_date=start_date,
        end_date=end_date,
        shifts=st.session_state.shifts,
        juniors=st.session_state.juniors,
        seniors=st.session_state.seniors,
        nf_juniors=st.session_state.nf_juniors,
        nf_seniors=st.session_state.nf_seniors,
        leaves=[],
        rotators=[],
        min_gap=min_gap,
        nf_block_length=nf_block_len,
    )
    try:
        env = os.getenv("ENV", "prod")
        df = build_schedule(data, env=env)
        st.dataframe(df)
    except Exception as e:
        st.error(str(e))
