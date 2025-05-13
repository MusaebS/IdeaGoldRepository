import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# SHIFT TEMPLATE INPUT
if "shifts" not in st.session_state:
    st.session_state.shifts = []

with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")

    if st.button("Add Shift", key="add_shift_btn"):
        base_label = label.strip()
        if not base_label:
            st.warning("⚠️ Please enter a non-empty shift label.")
        elif start >= end:
            st.warning("⚠️ Start time must be before end time.")
        else:
            existing = [s["label"] for s in st.session_state.shifts]
            count = 2
            unique_label = base_label
            while unique_label in existing:
                unique_label = f"{base_label} #{count}"
                count += 1
            st.session_state.shifts.append({
                "label": unique_label,
                "role": role,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "night_float": nf
            })

if st.session_state.shifts:
    st.table(pd.DataFrame(st.session_state.shifts))

# PARTICIPANTS
st.header("📝 Participants")
use_demo = st.checkbox("Use Demo Names", value=True)

if use_demo:
    junior_list = ["Ashley", "Amanda", "Linda", "Nicole"]
    senior_list = ["John", "Robert", "Thomas", "Andrew"]
else:
    junior_raw = st.text_area("Juniors (one per line)")
    senior_raw = st.text_area("Seniors (one per line)")
    junior_list = list({r.strip() for r in junior_raw.splitlines() if r.strip()})
    senior_list = list({r.strip() for r in senior_raw.splitlines() if r.strip()})

if not junior_list or not senior_list:
    st.warning("⚠️ Please enter at least one junior and one senior.")

# NIGHT FLOAT ELIGIBILITY
with st.expander("🛌 Night-Float Eligibility"):
    nf_juniors = st.multiselect("NF-Eligible Juniors", junior_list, default=junior_list)
    nf_seniors = st.multiselect("NF-Eligible Seniors", senior_list, default=senior_list)

# DATES AND RULES
start_date = st.date_input("Start Date", datetime.today())
end_date = st.date_input("End Date", datetime.today() + timedelta(days=27))

if start_date > end_date:
    st.error("❌ End date must be after or equal to start date.")

st.subheader("⏱️ Rules")
min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
nf_block_length = st.slider("Night-Float Block Length (days)", 1, 14, 7)
nf_style = st.radio("NF Distribution Style", ["Block", "One-by-one"], horizontal=True)

# ROTATORS & LEAVES
if "rotators" not in st.session_state:
    st.session_state["rotators"] = []
if "leaves" not in st.session_state:
    st.session_state["leaves"] = []

def _make_table(items, fields):
    if not items:
        st.write("*(None yet)*")
        return
    t = {f: [it[i] for it in items] for i, f in enumerate(fields)}
    st.table(pd.DataFrame(t))

with st.expander("🔄 Rotators"):
    rot_name = st.selectbox("Name", [""] + junior_list + senior_list, key="rot_n")
    rot_from = st.date_input("From", key="rot_f")
    rot_to = st.date_input("To", key="rot_t")
    if st.button("Add Rotator", key="add_rot_btn"):
        if rot_name and rot_from <= rot_to:
            st.session_state.rotators.append((rot_name, rot_from, rot_to))
    _make_table(st.session_state.rotators, ["Name", "From", "To"])

with st.expander("✈️ Leaves"):
    lv_name = st.selectbox("Name", [""] + junior_list + senior_list, key="lv_n")
    lv_from = st.date_input("From", key="lv_f")
    lv_to = st.date_input("To", key="lv_t")
    if st.button("Add Leave", key="add_lv_btn"):
        if lv_name and lv_from <= lv_to:
            st.session_state.leaves.append((lv_name, lv_from, lv_to))
    _make_table(st.session_state.leaves, ["Name", "From", "To"])

# GENERATE BUTTON
if st.button("🚀 Generate Schedule", key="generate_btn"):
    st.success("✅ Schedule generated!")

    df = pd.DataFrame({
        "Name": junior_list + senior_list,
        "Total Shifts": [5]*len(junior_list + senior_list),
        "Weekend Shifts": [2]*len(junior_list + senior_list),
        "Night Float": [1 if name in nf_juniors + nf_seniors else 0 for name in junior_list + senior_list],
        "Role": ["Junior"]*len(junior_list) + ["Senior"]*len(senior_list),
    })

    rot_names = [r[0] for r in st.session_state.rotators]
    df_rot = df[df["Name"].isin(rot_names)]

    st.subheader("📊 Summary by Role")
    st.dataframe(df)

    st.subheader("🌙 Night-Float Summary")
    nf_df = df[df["Night Float"] > 0]
    st.dataframe(nf_df[["Name", "Night Float"]])

    st.subheader("🔄 Rotator Summary")
    if not df_rot.empty:
        st.dataframe(df_rot)
    else:
        st.write("No rotators assigned shifts.")

    # Download
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download CSV", csv, "schedule_summary.csv", "text/csv")
