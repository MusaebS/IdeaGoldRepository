import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# INIT
if "shifts" not in st.session_state:
    st.session_state.shifts = []
if "rotators" not in st.session_state:
    st.session_state.rotators = []
if "leaves" not in st.session_state:
    st.session_state.leaves = []

# SHIFT TEMPLATE
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    label = col1.text_input("Label (e.g. ER1, Ward)")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")

    if st.button("Add Shift", key="add_shift_btn"):
        base_label = label.strip()
        if not base_label or base_label.isspace():
            st.warning("⚠️ Please enter a valid shift label.")
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
    junior_list = ["Ashley", "Amanda", "Linda", "Nicole", "Emily", "Mary", "Jessica", "Sarah", "Laura", "Sophia"]
    senior_list = ["John", "Robert", "Thomas", "Andrew", "Daniel", "William", "James", "David", "Michael", "Joseph"]
else:
    junior_raw = st.text_area("Juniors (one per line)")
    senior_raw = st.text_area("Seniors (one per line)")
    junior_list = [line for i, line in enumerate(junior_raw.splitlines()) if line.strip() and line not in junior_raw.splitlines()[:i]]
    senior_list = [line for i, line in enumerate(senior_raw.splitlines()) if line.strip() and line not in senior_raw.splitlines()[:i]]

# NIGHT FLOAT ELIGIBILITY
with st.expander("🛌 Night-Float Eligibility"):
    nf_juniors = st.multiselect("NF-Eligible Juniors", junior_list, default=junior_list)
    nf_seniors = st.multiselect("NF-Eligible Seniors", senior_list, default=senior_list)

# DATES & RULES
start_date = st.date_input("Start Date", datetime.today())
end_date = st.date_input("End Date", datetime.today() + timedelta(days=27))
if start_date > end_date:
    st.error("❌ End date must be after or equal to start date.")

min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
nf_block_length = st.slider("Night-Float Block Length (days)", 1, 14, 7)
nf_style = st.radio("NF Distribution Style", ["Block", "One-by-one"], horizontal=True)

# ROTATORS / LEAVES
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

# AVAILABILITY
leaves = st.session_state.leaves
rotators = st.session_state.rotators

def is_available(name, day, history, apply_gap=True):
    for (n, lo, hi) in leaves:
        if n == name and lo <= day.date() <= hi:
            return False
    for (n, lo, hi) in rotators:
        if n == name and lo <= day.date() <= hi:
            return True
    if any(n == name for (n, _, _) in rotators):
        return False
    last = history.get(name)
    if apply_gap and last and (day - last).days < min_gap:
        return False
    return True

def pick_candidate(pool, day, history, used_today):
    valid = [p for p in pool if is_available(p, day, history) and p not in used_today]
    if not valid:
        return "Unavailable"
    return sorted(valid, key=lambda x: (history.get(x, datetime(2000,1,1)), x))[0]

def build_schedule():
    days = pd.date_range(start_date, end_date)
    shift_defs = st.session_state.shifts
    history = {}
    assignments = []
    stats = {name: {"total":0, "weekend":0, "nf":0} for name in junior_list + senior_list}

    nf_assign = {}
    for shift in shift_defs:
        if shift["night_float"]:
            pool = nf_seniors if shift["role"] == "Senior" else nf_juniors
            if not pool:
                continue
            nf_assign[shift["label"]] = {}
            for i, d in enumerate(days):
                if nf_style == "Block":
                    idx = (i // nf_block_length) % len(pool)
                else:
                    idx = i % len(pool)
                name = pool[idx]
                if is_available(name, d, history, apply_gap=False):
                    nf_assign[shift["label"]][d.date()] = name
                    stats[name]["nf"] += 1

    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        used_today = set()
        nf_today = {nf_assign[s].get(d.date()) for s in nf_assign if nf_assign[s].get(d.date())}
        used_today.update(nf_today)

        for shift in shift_defs:
            label = shift["label"]
            role = shift["role"]
            is_nf = shift["night_float"]

            if is_nf:
                name = nf_assign.get(label, {}).get(d.date(), "Unavailable")
            else:
                pool = senior_list if role == "Senior" else junior_list
                name = pick_candidate(pool, d, history, used_today)
                if name != "Unavailable":
                    history[name] = d
            row[label] = name
            if name != "Unavailable":
                used_today.add(name)
                stats[name]["total"] += 1
                if row["Day"] in ["Friday", "Saturday", "Sunday"]:
                    stats[name]["weekend"] += 1
        assignments.append(row)

    df = pd.DataFrame(assignments)
    df_summary = pd.DataFrame([
        {"Name": k, "Total Shifts": v["total"], "Weekend Shifts": v["weekend"], "Night Float": v["nf"]} for k,v in stats.items()
    ])
    df_rot = df_summary[df_summary["Name"].isin([r[0] for r in rotators])]
    return df, df_summary, df_rot

# GENERATE
if st.button("🚀 Generate Schedule", key="generate_btn"):
    if not st.session_state.shifts:
        st.error("❌ Please define at least one shift.")
        st.stop()
    if not junior_list or not senior_list:
        st.error("❌ Please enter both junior and senior participants.")
        st.stop()
    if start_date > end_date:
        st.error("❌ End date must be after or equal to start date.")
        st.stop()

    df, summary, rot = build_schedule()
    if df.empty:
        st.warning("⚠️ No assignments could be generated.")
    else:
        st.success("✅ Schedule generated!")
        st.dataframe(df, use_container_width=True)

        st.subheader("📊 Summary by Role")
        st.dataframe(summary)

        st.subheader("🌙 Night-Float Summary")
        nf_only = summary[summary["Night Float"] > 0][["Name", "Night Float"]]
        st.dataframe(nf_only)

        st.subheader("🔄 Rotator Summary")
        if not rot.empty:
            st.dataframe(rot)
        else:
            st.write("No rotators assigned shifts.")

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download CSV", csv, "schedule.csv", "text/csv")
