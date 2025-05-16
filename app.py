import streamlit as st
import pandas as pd
import random
from datetime import datetime, timedelta, date
import math

# --- Apportionment helper (Hare–Niemeyer) ---
def allocate_integer_quotas(float_quotas: dict, total_slots: int) -> dict:
    base = {p: math.floor(q) for p, q in float_quotas.items()}
    used = sum(base.values())
    remainder = {p: float_quotas[p] - base[p] for p in float_quotas}
    to_assign = total_slots - used
    if to_assign <= 0:
        return base
    extras = sorted(remainder.items(), key=lambda x: (-x[1], x[0]))[:to_assign]
    for p, _ in extras:
        base[p] += 1
    return base

# --- Streamlit Setup ---
st.set_page_config(page_title="Idea Gold Scheduler", layout="wide")
st.title("🪙 Idea Gold Scheduler – Stable & Robust")

# --- State Reset ---
def reset_state():
    for key in ["shifts","rotators","leaves","extra_oncalls","weights","nf_juniors","nf_seniors"]:
        st.session_state.pop(key, None)
    st.experimental_rerun()

if st.button("🔁 Reset All Data", key="btn_reset"):
    reset_state()

# --- Session Defaults ---
defaults = {
    "shifts": [],
    "rotators": [],
    "leaves": [],
    "extra_oncalls": {},
    "weights": {},
    "nf_juniors": [],
    "nf_seniors": [],
    "start_date": date.today(),
    "end_date": date.today()+timedelta(days=27),
    "min_gap": 2,
    "nf_block_length": 5,
}
for k,v in defaults.items():
    st.session_state.setdefault(k,v)

# --- Shift Templates ---
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4 = st.columns([3,2,1,1])
    label = col1.text_input("Shift Label (e.g., ER1)")
    role = col2.selectbox("Role", ["Junior","Senior"])
    night_float = col3.checkbox("Night Float")
    thur_weekend = col4.checkbox("Thursday Night = Weekend")
    if st.button("Add Shift", key="btn_add_shift"):
        if label.strip():
            existing = [s['label'] for s in st.session_state.shifts]
            base=label.strip(); i=1; uniq=base
            while uniq in existing:
                i+=1; uniq=f"{base} #{i}"
            st.session_state.shifts.append({"label":uniq,"role":role,"night_float":night_float,"thur_weekend":thur_weekend})
        else:
            st.error("Shift label cannot be empty.")
if st.session_state.shifts:
    st.table(pd.DataFrame(st.session_state.shifts))

# --- Participants ---
with st.expander("👥 Participants", expanded=True):
    demo = st.checkbox("Use Demo Participants", True)
    if demo:
        juniors = ["Alice", "Bob", "Charlie", "Dina", "Eli", "Fay", "Gina", "Hank", "Ivy", "Jack"]
        seniors = ["Ken", "Laura", "Mona", "Nina", "Oscar", "Paula", "Quinn", "Rose"]
    else:
        juniors = [x.strip() for x in st.text_area("Juniors (one per line)").splitlines() if x.strip()]
        seniors = [x.strip() for x in st.text_area("Seniors (one per line)").splitlines() if x.strip()]
    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# --- NF Eligibility ---
with st.expander("🌙 Night Float Eligibility",expanded=False):
    nf_j=st.multiselect("NF-Eligible Juniors",options=st.session_state.juniors,default=st.session_state.nf_juniors,key="nf_j_select")
    nf_s=st.multiselect("NF-Eligible Seniors",options=st.session_state.seniors,default=st.session_state.nf_seniors,key="nf_s_select")
    st.session_state.nf_juniors=nf_j; st.session_state.nf_seniors=nf_s

# --- Extra Oncalls ---
with st.expander("⚖️ Extra Oncalls",expanded=False):
    for p in st.session_state.juniors+st.session_state.seniors:
        st.session_state.extra_oncalls[p]=st.number_input(f"Extra oncalls for {p}",0,10,value=st.session_state.extra_oncalls.get(p,0),key=f"extra_{p}")

# --- Date & Rules ---
st.subheader("📅 Schedule Settings")
st.session_state.start_date=st.date_input("Start Date",st.session_state.start_date)
st.session_state.end_date=st.date_input("End Date",st.session_state.end_date)
bad=st.session_state.start_date>st.session_state.end_date
if bad: st.error("Start date must be on or before End date."); st.stop()
st.session_state.min_gap=st.slider("Minimum Days Between Shifts",0,7,st.session_state.min_gap)
st.session_state.nf_block_length=st.slider("Night Float Block Length",1,10,st.session_state.nf_block_length)

# --- Leaves & Rotators ---
with st.expander("✈️ Leaves",expanded=False):
    nm=st.selectbox("Leave Name",[""]+st.session_state.juniors+st.session_state.seniors,key="leave_name")
    frm,to=st.date_input("Leave Period",[st.session_state.start_date,st.session_state.start_date],key="leave_period")
    if st.button("Add Leave",key="btn_leave") and nm and frm<=to:
        entry=(nm,frm,to)
        if entry not in st.session_state.leaves: st.session_state.leaves.append(entry)
with st.expander("🔄 Rotators",expanded=False):
    nm=st.selectbox("Rotator Name",[""]+st.session_state.juniors+st.session_state.seniors,key="rot_name")
    frm,to=st.date_input("Rotator Period",[st.session_state.start_date,st.session_state.start_date],key="rot_period")
    if st.button("Add Rotator",key="btn_rot") and nm and frm<=to:
        entry=(nm,frm,to)
        if entry not in st.session_state.rotators: st.session_state.rotators.append(entry)

# --- Utility Functions ---
def is_weekend(d,shift): return d.weekday() in [4,5] or (d.weekday()==3 and shift.get("thur_weekend",False))
def on_leave(p,d): return any(n==p and s<=d<=e for n,s,e in st.session_state.leaves)
def is_active_rotator(p,d):
    for n,s,e in st.session_state.rotators:
        if n==p: return s<=d<=e
    return True

# --- Scheduler ---
def build_schedule():
    days=pd.date_range(st.session_state.start_date,st.session_state.end_date)
    shifts=st.session_state.shifts;juniors=st.session_state.juniors;seniors=st.session_state.seniors;pool=juniors+seniors
    span=(st.session_state.end_date-st.session_state.start_date).days+1
    leave_days={p:0 for p in pool}
    for n,s,e in st.session_state.leaves:
        ov=max(0,(min(e,st.session_state.end_date)-max(s,st.session_state.start_date)).days+1)
        leave_days[n]+=ov
    active={p:sum(1 for d in days if not on_leave(p,d.date()) and is_active_rotator(p,d.date())) for p in pool}
    weighted={p:active[p]*(1+leave_days[p]/span)*(1+st.session_state.extra_oncalls.get(p,0)) for p in pool}
    total_w=sum(weighted.values()) or 1
    shift_labels=[s['label'] for s in shifts if not s['night_float']]
    slot_counts={lbl:0 for lbl in shift_labels};weekend_counts={lbl:0 for lbl in shift_labels}
    for d in days:
        for s in shifts:
            if s['night_float']: continue
            slot_counts[s['label']]+=1
            if is_weekend(d,s): weekend_counts[s['label']]+=1
    expected={p:{lbl:{'total':slot_counts[lbl]*weighted[p]/total_w,'weekend':weekend_counts[lbl]*weighted[p]/total_w} for lbl in shift_labels} for p in pool}
    # ─── Integer quotas via Hare–Niemeyer ───
    targets={lbl:allocate_integer_quotas({p:expected[p][lbl]['total'] for p in pool},slot_counts[lbl]) for lbl in shift_labels}
    stats={p:{lbl:{'total':0,'weekend':0} for lbl in shift_labels} for p in pool}
    last_assigned={p:None for p in pool}
    schedule=[];unfilled=[]
    for d in days:
        row={'Date':d.date(),'Day':d.strftime('%A')}
        nf_today=set()
        for s in shifts:
            lbl=s['label']
            if s['night_float']:
                nf_pool=st.session_state.nf_juniors if s['role']=='Junior' else st.session_state.nf_seniors
                person=None
                if nf_pool:
                    idx=(d-days[0]).days//st.session_state.nf_block_length
                    cand=nf_pool[idx%len(nf_pool)]
                    if not on_leave(cand,d.date()) and is_active_rotator(cand,d.date()):
                        person=cand; nf_today.add(person)
                    else: unfilled.append((d.date(),lbl))
                row[lbl]=person or 'Unfilled'; continue
            pool_role=juniors if s['role']=='Junior' else seniors
            cand=[p for p in pool_role if p not in nf_today and not on_leave(p,d.date()) and is_active_rotator(p,d.date()) and (last_assigned[p] is None or (d.date()-last_assigned[p]).days>=st.session_state.min_gap)]
            if not cand:
                row[lbl]='Unfilled'; unfilled.append((d.date(),lbl))
            else:
                under=[p for p in cand if stats[p][lbl]['total']<targets[lbl][p]]
                if under: best=sorted(under)[0]
                else: best=max(sorted(cand),key=lambda p:(expected[p][lbl]['weekend']-stats[p][lbl]['weekend'],expected[p][lbl]['total']-stats[p][lbl]['total']))
                row[lbl]=best; stats[best][lbl]['total']+=1; 
                if is_weekend(d,s): stats[best][lbl]['weekend']+=1
                last_assigned[best]=d.date()
        schedule.append(row)
    df=pd.DataFrame(schedule)
    summary=pd.DataFrame([{**{'Name':p},**{f"{lbl}_assigned":stats[p][lbl]['total'] for lbl in shift_labels},**{f"{lbl}_expected":round(expected[p][lbl]['total'],1) for lbl in shift_labels}} for p in pool])
    df_unfilled=pd.DataFrame(unfilled,columns=["Date","Shift"])
    return df,summary,df_unfilled

if st.button("🚀 Generate Schedule",key="btn_generate"):
    df,summary,unfilled=build_schedule()
    st.success("✅ Schedule generated!")
    st.dataframe(df)
    st.subheader("📊 Assignment Summary")
    st.dataframe(summary)
    if not unfilled.empty:
        st.warning("⚠️ Unfilled Slots Detected")
        st.dataframe(unfilled)
    st.download_button("Download Schedule CSV",df.to_csv(index=False),"schedule.csv")
    st.download_button("Download Summary CSV",summary.to_csv(index=False),"summary.csv")
    if not unfilled.empty:
        st.download_button("Download Unfilled CSV",df_unfilled.to_csv(index=False),"unfilled.csv")
