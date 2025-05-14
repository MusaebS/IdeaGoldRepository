import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date\import random

# ── App Configuration ─────────────────────────────────────────────────────────
st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# ── Reset All ──────────────────────────────────────────────────────────────────
if st.button("🔁 Reset All Data"):
    if st.confirm("Are you sure you want to reset all data? This cannot be undone."):
        st.session_state.clear()
        st.experimental_rerun()

# ── Session-State Initialization ────────────────────────────────────────────────
DEFAULT_KEYS = {
    "shifts": [],
    "rotators": [],
    "leaves": [],
    "weights": {},
    "extra_oncalls": {},
    "nf_juniors": [],
    "nf_seniors": [],
    "start_date": date.today(),
    "end_date": date.today() + timedelta(days=27),
    "min_gap": 2,
    "nf_block_length": 7,
}
for key, default in DEFAULT_KEYS.items():
    st.session_state.setdefault(key, default)

# ── Shift Templates ────────────────────────────────────────────────────────────
with st.expander("⚙️ Shift Templates", expanded=True):
    cols = st.columns([2,1,1,1,1,1])
    label = cols[0].text_input("Label (e.g. ER1, Ward)", value="")
    role  = cols[1].selectbox("Role", ["Junior","Senior"] )
    start_time = cols[2].time_input("Start", datetime.strptime("08:00","%H:%M").time())
    end_time   = cols[3].time_input("End",   datetime.strptime("20:00","%H:%M").time())
    is_nf      = cols[4].checkbox("Night Float")
    thur_flag  = cols[5].checkbox("Thursday = Weekend")

    if st.button("Add Shift", key="add_shift"):
        base = label.strip()
        if not base:
            st.warning("⚠️ Label cannot be empty.")
        elif start_time >= end_time:
            st.warning("⚠️ Start must be before End.")
        else:
            existing = [s['label'] for s in st.session_state.shifts]
            unique = base
            i = 2
            while unique in existing:
                unique = f"{base} #{i}"
                i += 1
            st.session_state.shifts.append({
                "label": unique,
                "role": role,
                "start": start_time.strftime("%H:%M"),
                "end":   end_time.strftime("%H:%M"),
                "night_float": is_nf,
                "thur_weekend": thur_flag,
            })
    if st.session_state.shifts:
        st.dataframe(pd.DataFrame(st.session_state.shifts), use_container_width=True)

# ── Participants ───────────────────────────────────────────────────────────────
with st.expander("👥 Participants", expanded=True):
    use_demo = st.checkbox("Use Demo Names", value=True)
    if use_demo:
        juniors = ["Amanda","Emily","Jessica","Laura","Linda","Mary","Nicole","Ashley"]
        seniors = ["John","Robert","Thomas","Andrew","Daniel","David","James","Joseph"]
    else:
        jr = st.text_area("Juniors (one per line)")
        sr = st.text_area("Seniors (one per line)")
        juniors = list(dict.fromkeys([x.strip() for x in jr.splitlines() if x.strip()]))
        seniors = list(dict.fromkeys([x.strip() for x in sr.splitlines() if x.strip()]))
    if not juniors or not seniors:
        st.error("❌ Please provide at least one Junior and one Senior.")
        st.stop()
    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# ── Extra Oncalls & Bias ─────────────────────────────────────────────────────────
with st.expander("⚖️ Extra Oncalls & Shift Bias", expanded=False):
    for name in st.session_state.juniors + st.session_state.seniors:
        st.session_state.extra_oncalls.setdefault(name, 0)
        st.session_state.weights.setdefault(name, 1.0)
        bias_col, extra_col = st.columns([3,2])
        st.session_state.weights[name] = bias_col.slider(
            f"Bias for {name}", 0.5, 2.0, st.session_state.weights[name], 0.1, key=f"w_{name}" )
        st.session_state.extra_oncalls[name] = extra_col.number_input(
            f"Extra Oncalls for {name}", min_value=0, max_value=20,
            value=st.session_state.extra_oncalls[name], key=f"e_{name}" )

# ── Night-Float Eligibility ────────────────────────────────────────────────────
with st.expander("🛌 Night-Float Eligibility", expanded=False):
    st.session_state.nf_juniors = st.multiselect(
        "NF Eligible Juniors", st.session_state.juniors, default=st.session_state.nf_juniors)
    st.session_state.nf_seniors = st.multiselect(
        "NF Eligible Seniors", st.session_state.seniors, default=st.session_state.nf_seniors)

# ── Leaves & Rotators ───────────────────────────────────────────────────────────
with st.expander("✈️ Leaves & 🔄 Rotators", expanded=False):
    lcol1, lcol2, lcol3 = st.columns(3)
    lv_name = lcol1.selectbox("Leave Name", [""] + st.session_state.juniors + st.session_state.seniors)
    lv_from = lcol2.date_input("From", key="lv_from")
    lv_to   = lcol3.date_input("To", key="lv_to")
    if st.button("Add Leave", key="add_leave"):
        if lv_name and lv_from <= lv_to:
            st.session_state.leaves.append((lv_name, lv_from, lv_to))
    tcol1, tcol2, tcol3 = st.columns(3)
    rot_name = tcol1.selectbox("Rotator Name", [""] + st.session_state.juniors + st.session_state.seniors)
    rot_from = tcol2.date_input("From", key="rot_from")
    rot_to   = tcol3.date_input("To", key="rot_to")
    if st.button("Add Rotator", key="add_rotator"):
        if rot_name and rot_from <= rot_to:
            st.session_state.rotators.append((rot_name, rot_from, rot_to))
    if st.session_state.leaves:
        st.write("**Leaves:**")
        st.table(pd.DataFrame(st.session_state.leaves, columns=["Name","From","To"]))
    if st.session_state.rotators:
        st.write("**Rotators:**")
        st.table(pd.DataFrame(st.session_state.rotators, columns=["Name","From","To"]))

# ── Utility Functions ───────────────────────────────────────────────────────────
def is_weekend(day, shift):
    if shift.get("thur_weekend") and day.weekday() == 3:
        return True
    return day.weekday() in [4,5]  # Fri/Sat

def on_leave(name, day):
    return any(n==name and f <= day <= t for (n,f,t) in st.session_state.leaves)

def is_active_rotator(name, day):
    return any(n==name and f <= day <= t for (n,f,t) in st.session_state.rotators)

# ── Schedule Builder ────────────────────────────────────────────────────────────
```python
def build_schedule():
    # Date range
    start = st.session_state.start_date
    end   = st.session_state.end_date
    days  = pd.date_range(start, end, freq='D')

    # Shifts and pools
    shifts   = st.session_state.shifts
    juniors  = st.session_state.juniors
    seniors  = st.session_state.seniors
    pool     = juniors + seniors
    min_gap  = st.session_state.min_gap
    nf_block = st.session_state.nf_block_length

    # Early validation
    if not pool or not shifts:
        st.error("Define at least one participant and one shift template.")
        return None, None, None

    # Count total and weekend slots
    total_slots = {'total':0, 'weekend':0}
    for d in days:
        for s in shifts:
            if not s['night_float']:
                total_slots['total'] += 1
                if is_weekend(d, s):
                    total_slots['weekend'] += 1

    # Compute weighted active days
    weighted = {}
    total_weighted = 0.0
    for p in pool:
        # Determine active days (rotators restricted to window)
        if any(r[0]==p for r in st.session_state.rotators):
            active = [d for d in days if is_active_rotator(p, d.date()) and not on_leave(p,d.date())]
        else:
            active = [d for d in days if not on_leave(p,d.date())]
        bias = 1 + st.session_state.extra_oncalls.get(p,0)
        wd = len(active) * bias
        weighted[p] = wd
        total_weighted += wd

    # Compute expected quotas
    expected = {}
    for p in pool:
        ratio = (weighted[p]/total_weighted) if total_weighted else 0.0
        expected[p] = {
            'total': round(total_slots['total']*ratio,1),
            'weekend': round(total_slots['weekend']*ratio,1)
        }

    # Night-float assignment in blocks
    nf_assignments = {}
    for s in shifts:
        if s['night_float']:
            pool_nf = st.session_state.nf_seniors if s['role']=='Senior' else st.session_state.nf_juniors
            nf_map = {}
            idx = 0
            while idx < len(days):
                for p in pool_nf:
                    block = days[idx: idx+nf_block]
                    for d in block:
                        if not on_leave(p, d.date()):
                            nf_map[d.date()] = p
                idx += nf_block
            nf_assignments[s['label']] = nf_map

    # Regular assignment
    schedule = []
    stats    = {p:{'total':0,'weekend':0} for p in pool}
    last_assigned = {p:None for p in pool}
    unfilled = []

    for d in days:
        row = {'Date':d.date(), 'Day':d.strftime('%A')}
        assigned_today = set()
        # NF slots
        nf_today = set()
        for s in shifts:
            if s['night_float']:
                person = nf_assignments.get(s['label'],{}).get(d.date(),'Unavailable')
                row[s['label']] = person
                if person!='Unavailable': nf_today.add(person)

        # Regular slots
        for s in shifts:
            if s['night_float']: continue
            label = s['label']
            role  = s['role']
            pool_role = juniors if role=='Junior' else seniors
            candidates = []
            for p in pool_role:
                if p in nf_today or p in assigned_today: continue
                if on_leave(p,d.date()): continue
                if any(r[0]==p for r in st.session_state.rotators) and not is_active_rotator(p,d.date()): continue
                last = last_assigned[p]
                if last and (d - last).days < min_gap: continue
                candidates.append(p)
            if not candidates:
                row[label] = 'Unavailable'
                unfilled.append((d.date(), label))
                continue
            # Select by two-stage deficit
            wknd = is_weekend(d, s)
            if wknd:
                # prioritize weekend deficit
                best = max(candidates, key=lambda p: (expected[p]['weekend']-stats[p]['weekend'], expected[p]['total']-stats[p]['total']))
            else:
                best = max(candidates, key=lambda p: (expected[p]['total']-stats[p]['total'],))
            row[label] = best
            stats[best]['total'] += 1
            if wknd: stats[best]['weekend'] += 1
            last_assigned[best] = d
            assigned_today.add(best)
        schedule.append(row)

    return pd.DataFrame(schedule), pd.DataFrame([
        {'Name':p, 'Total':stats[p]['total'], 'Weekend':stats[p]['weekend'],
         'Expected Total':expected[p]['total'], 'Expected Weekend':expected[p]['weekend']}
        for p in pool
    ]), pd.DataFrame(unfilled,columns=['Date','Shift'])
```}
]}
