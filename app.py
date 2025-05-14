import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random

st.set_page_config(layout="wide")
st.title("🪙 Idea Gold Scheduler")

# Reset All
if st.button("Reset All"):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.experimental_rerun()

# Initialize
for key in ["shifts", "rotators", "leaves", "weights", "extra_oncalls"]:
    st.session_state.setdefault(key, {} if "weights" in key or "extra_oncalls" in key else [])

# Dates and Rules
with st.expander("📆 Date Range & Rules", expanded=True):
    st.session_state.start_date = st.date_input("Start Date", datetime.today())
    st.session_state.end_date = st.date_input("End Date", datetime.today() + timedelta(days=27))
    if st.session_state.start_date > st.session_state.end_date:
        st.error("End date must be after start date")
    st.session_state.min_gap = st.slider("Min Days Between Shifts", 0, 7, 2)
    st.session_state["nf_block_length"] = st.slider("NF Block Length (Days)", 1, 14, 5)

# Shifts
with st.expander("⚙️ Shift Templates", expanded=True):
    col1, col2, col3, col4, col5, col6 = st.columns([2, 1, 1, 1, 1, 1])
    label = col1.text_input("Label")
    role = col2.selectbox("Role", ["Junior", "Senior"])
    start = col3.time_input("Start", value=datetime.strptime("08:00", "%H:%M").time())
    end = col4.time_input("End", value=datetime.strptime("20:00", "%H:%M").time())
    nf = col5.checkbox("Night Float")
    thur_flag = col6.checkbox("Thursday = Weekend")

    if st.button("Add Shift", key="add_shift"):
        if start >= end:
            st.warning("Start must be before End")
        else:
            names = [s["label"] for s in st.session_state.shifts]
            base = label.strip()
            count = 1
            unique = base
            while unique in names:
                count += 1
                unique = f"{base} #{count}"
            st.session_state.shifts.append({
                "label": unique,
                "role": role,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "night_float": nf,
                "thur_weekend": thur_flag
            })

    if st.session_state.shifts:
        st.table(pd.DataFrame(st.session_state.shifts))

# Participants
with st.expander("👥 Participants", expanded=True):
    use_demo = st.checkbox("Use Demo Names", value=True)
    if use_demo:
        juniors = ["Layla", "Sara", "Noura", "Omar", "Khalid", "Yousef", "Alya", "Fahad"]
        seniors = ["Dr. A", "Dr. B", "Dr. C", "Dr. D", "Dr. E"]
    else:
        jraw = st.text_area("Juniors")
        sraw = st.text_area("Seniors")
        juniors = [r.strip() for r in jraw.splitlines() if r.strip()]
        seniors = [r.strip() for r in sraw.splitlines() if r.strip()]
    st.session_state.juniors = juniors
    st.session_state.seniors = seniors

# Extra Oncalls
with st.expander("⚖️ Extra Oncalls", expanded=False):
    all_names = juniors + seniors
    for name in all_names:
        if name not in st.session_state.extra_oncalls:
            st.session_state.extra_oncalls[name] = 0
        st.session_state.extra_oncalls[name] = st.number_input(
            f"{name} extra oncalls", value=st.session_state.extra_oncalls.get(name, 0), min_value=0, max_value=10, step=1, key=f"extra_{name}"
        )

# NF Eligibility
with st.expander("🌙 Night Float Eligibility", expanded=False):
    st.session_state.nf_juniors = st.multiselect("NF Eligible Juniors", juniors, default=juniors)
    st.session_state.nf_seniors = st.multiselect("NF Eligible Seniors", seniors, default=seniors)

# Leaves and Rotators
with st.expander("✈️ Leaves / Rotators", expanded=False):
    lv_name = st.selectbox("Leave Name", [""] + juniors + seniors)
    lv_from = st.date_input("From", key="lv_f")
    lv_to = st.date_input("To", key="lv_t")
    if st.button("Add Leave") and lv_name:
        st.session_state.leaves.append((lv_name, lv_from, lv_to))

    rot_name = st.selectbox("Rotator Name", [""] + juniors + seniors)
    rot_from = st.date_input("From", key="rot_f")
    rot_to = st.date_input("To", key="rot_t")
    if st.button("Add Rotator") and rot_name:
        st.session_state.rotators.append((rot_name, rot_from, rot_to))

    def _tbl(items, cols):
        if not items:
            st.write("*(None yet)*")
            return
        st.table(pd.DataFrame([{c: it[i] for i, c in enumerate(cols)} for it in items]))

    st.write("**Current Leaves**")
    _tbl(st.session_state.leaves, ["Name", "From", "To"])
    st.write("**Current Rotators**")
    _tbl(st.session_state.rotators, ["Name", "From", "To"])

# ... (first half retained above)

# Utility Functions
def is_weekend(day, shift):
    if shift.get("thur_weekend") and day.weekday() == 3:
        return True
    return day.weekday() in [4, 5]  # Friday/Saturday

def is_active_rotator(name, day):
    return any(n == name and f <= day <= t for n, f, t in st.session_state.rotators)

def on_leave(name, day):
    return any(n == name and f <= day <= t for n, f, t in st.session_state.leaves)

def build_schedule():
    days = pd.date_range(st.session_state.start_date, st.session_state.end_date)
    all_shifts = st.session_state.shifts
    juniors, seniors = st.session_state.juniors, st.session_state.seniors

    pool = juniors + seniors
    if not pool or not all_shifts:
        st.error("Missing participants or shifts.")
        return None, None, None

    min_gap = st.session_state.min_gap
    nf_block = st.session_state["nf_block_length"]
    nf_pool_j = st.session_state.nf_juniors
    nf_pool_s = st.session_state.nf_seniors
    leaves = st.session_state.leaves

    # Active days
    def active_days(name):
        in_range = [d.date() for d in days if not on_leave(name, d.date()) and (not is_active_rotator(name, d.date()) or is_active_rotator(name, d.date()))]
        return len(in_range)

    weighted_days = {p: active_days(p) * (1 + st.session_state.extra_oncalls.get(p, 0)) for p in pool}
    total_weighted = sum(weighted_days.values())

    total_slots = {"total": 0, "weekend": 0}
    for d in days:
        for s in all_shifts:
            if not s["night_float"]:
                total_slots["total"] += 1
                if is_weekend(d, s):
                    total_slots["weekend"] += 1

    expected = {
        p: {
            "total": round(total_slots["total"] * (weighted_days[p]/total_weighted), 1),
            "weekend": round(total_slots["weekend"] * (weighted_days[p]/total_weighted), 1),
        } for p in pool
    }

    stats = {p: {"total": 0, "weekend": 0} for p in pool}
    last_assigned = {p: None for p in pool}
    nf_people_today = set()
    schedule = []
    unfilled = []

    # Assign NF
    for shift in [s for s in all_shifts if s["night_float"]]:
        nf_pool = nf_pool_s if shift["role"] == "Senior" else nf_pool_j
        if not nf_pool:
            continue
        d_idx = 0
        while d_idx < len(days):
            p = nf_pool[d_idx % len(nf_pool)]
            for offset in range(nf_block):
                if d_idx + offset >= len(days):
                    break
                d = days[d_idx + offset].date()
                if not on_leave(p, d):
                    nf_people_today.add((d, p))
                    last_assigned[p] = datetime.combine(d, datetime.min.time())
            d_idx += nf_block

    # Assign Regular Shifts
    for d in days:
        row = {"Date": d.date(), "Day": d.strftime("%A")}
        assigned_today = set(p for dt, p in nf_people_today if dt == d.date())
        for s in all_shifts:
            if s["night_float"]:
                label = s["label"]
                person = next((p for dt, p in nf_people_today if dt == d.date()), "Unavailable")
                row[label] = person
                continue

            pool = juniors if s["role"] == "Junior" else seniors
            candidates = []
            for p in pool:
                if p in assigned_today or on_leave(p, d.date()):
                    continue
                if (p in juniors or p in seniors) and not is_active_rotator(p, d.date()) and any(n == p for n, _, _ in st.session_state.rotators):
                    continue
                last = last_assigned.get(p)
                if last and (d - last.date()).days < min_gap:
                    continue
                candidates.append(p)

            if not candidates:
                row[s["label"]] = "Unavailable"
                unfilled.append((d.date(), s["label"]))
                continue

            random.shuffle(candidates)
            top = max(candidates, key=lambda p: (expected[p]["weekend"] - stats[p]["weekend"], expected[p]["total"] - stats[p]["total"]))
            row[s["label"]] = top
            stats[top]["total"] += 1
            if is_weekend(d, s):
                stats[top]["weekend"] += 1
            last_assigned[top] = d
            assigned_today.add(top)

        schedule.append(row)

    df = pd.DataFrame(schedule)
    df_summary = pd.DataFrame([{**{"Name": k}, **v, **expected[k]} for k, v in stats.items()])
    df_unfilled = pd.DataFrame(unfilled, columns=["Date", "Shift"])
    return df, df_summary, df_unfilled

# Generate Button
if st.button("🚀 Generate Schedule"):
    df, summary, unfilled = build_schedule()
    if df is None:
        st.stop()
    st.success("✅ Schedule generated!")
    st.dataframe(df, use_container_width=True)
    st.download_button("📥 Download CSV", df.to_csv(index=False).encode("utf-8"), "schedule.csv")

    st.subheader("📊 Summary")
    st.dataframe(summary)
    st.download_button("📥 Download Summary", summary.to_csv(index=False).encode("utf-8"), "summary.csv")

    if not unfilled.empty:
        st.warning("Some shifts could not be filled")
        st.dataframe(unfilled)
        st.download_button("📥 Download Unfilled", unfilled.to_csv(index=False).encode("utf-8"), "unfilled.csv")
