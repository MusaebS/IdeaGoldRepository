import streamlit as st
import pandas as pd
from datetime import date, timedelta

from model.data_models import ShiftTemplate, InputData
from model.optimiser import build_schedule

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

st.sidebar.header("Configuration")

with st.sidebar.expander("Shift Templates"):
    label = st.text_input("Label")
    role = st.selectbox("Role", ["Junior", "Senior"])
    nf = st.checkbox("Night Float")
    thu_wk = st.checkbox("Thu counts as weekend")
    points = st.number_input("Points", 1.0, 10.0, 1.0, 0.5)
    if st.button("Add Shift"):
        st.session_state.shifts.append(
            ShiftTemplate(label=label, role=role, night_float=nf, thu_weekend=thu_wk, points=points)
        )
    if st.session_state.shifts:
        st.table(pd.DataFrame([s.__dict__ for s in st.session_state.shifts]))

with st.sidebar.expander("Participants"):
    juniors_text = st.text_area("Juniors", "\n".join(st.session_state.juniors))
    seniors_text = st.text_area("Seniors", "\n".join(st.session_state.seniors))
    st.session_state.juniors = [n.strip() for n in juniors_text.splitlines() if n.strip()]
    st.session_state.seniors = [n.strip() for n in seniors_text.splitlines() if n.strip()]

with st.sidebar.expander("Night Float Eligible"):
    st.session_state.nf_juniors = st.multiselect(
        "Juniors", st.session_state.juniors, default=st.session_state.nf_juniors
    )
    st.session_state.nf_seniors = st.multiselect(
        "Seniors", st.session_state.seniors, default=st.session_state.nf_seniors
    )

start_date = st.sidebar.date_input("Start Date", date.today())
end_date = st.sidebar.date_input("End Date", date.today() + timedelta(days=27))
min_gap = st.sidebar.slider("Minimum Gap", 0, 7, 1)

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
    )
    try:
        df = build_schedule(data)
        st.dataframe(df)
    except Exception as e:
        st.error(str(e))
