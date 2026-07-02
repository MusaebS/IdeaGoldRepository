"""Reusable add/remove editors for the configuration tabs.

Each editor renders an input row with an Add button, a table of current
entries, and a shared remove control. Widget keys are unchanged from the
original single-file app so saved links / tests keep working.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from model.data_models import ShiftTemplate
from ui.state import Keys

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _remove_control(options, label: str, key: str, format_func=str) -> object | None:
    """Shared selectbox + Remove button row; returns the choice on click."""
    choice = st.selectbox(label, options, format_func=format_func, key=f"{key}_idx")
    if st.button("Remove", key=f"{key}_btn"):
        return choice
    return None


def _add_button(key: str) -> bool:
    """An Add button vertically aligned with the input widgets beside it."""
    st.markdown("&nbsp;")
    return st.button("Add", key=key)


def date_range_editor(
    title: str,
    key: str,
    people: list,
    with_compensation: bool = False,
    default_start: date | None = None,
    default_end: date | None = None,
) -> None:
    """Inline editor for (resident, start, end[, compensated]) windows.

    With ``with_compensation`` (leaves) each entry carries a Compensated flag:
    compensated keeps the resident's full fair share, uncompensated scales it down
    like a rotator. ``default_start`` / ``default_end`` seed the date pickers
    (pass the schedule block's range so entries land in the right month).
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
        start = st.date_input("Start", default_start or date.today(), key=f"{key}_start")
    with c[2]:
        end = st.date_input("End", default_end or default_start or date.today(), key=f"{key}_end")
    compensated = True
    if with_compensation:
        with c[3]:
            compensated = st.checkbox("Compensated", value=True, key=f"{key}_comp")
    with c[-1]:
        if _add_button(f"{key}_add"):
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
        removed = _remove_control(
            list(range(len(rows))),
            "Remove entry",
            f"{key}_del",
            format_func=lambda i: f"{rows[i][0]}: {rows[i][1]} → {rows[i][2]}",
        )
        if removed is not None:
            st.session_state[key].pop(removed)


def caps_editor(people: list) -> None:
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
        if _add_button("cap_add"):
            st.session_state[Keys.CAPS][who] = {"total": mt, "nights": mn}
    caps = st.session_state[Keys.CAPS]
    if caps:
        st.table(pd.DataFrame([
            {
                "Resident": p,
                "Max total": v["total"] or "—",
                "Max nights": v["nights"] or "—",
            }
            for p, v in caps.items()
        ]))
        removed = _remove_control(list(caps.keys()), "Remove cap", "cap_rm")
        if removed is not None:
            caps.pop(removed, None)


def extra_points_editor(people: list) -> None:
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
        if _add_button("extra_add"):
            if pts > 0:
                st.session_state[Keys.EXTRA_POINTS][who] = pts
            else:
                st.session_state[Keys.EXTRA_POINTS].pop(who, None)
    ep = st.session_state[Keys.EXTRA_POINTS]
    if ep:
        st.table(pd.DataFrame([{"Resident": p, "Extra points": v} for p, v in ep.items()]))
        removed = _remove_control(list(ep.keys()), "Remove extra", "extra_rm")
        if removed is not None:
            ep.pop(removed, None)


def weekday_points_editor(shift_labels: list) -> None:
    """Inline editor: a shift's exact points on a given weekday (e.g. night = 2 on Tue)."""
    st.markdown("**Weekday point overrides — a shift's exact points on a weekday**")
    if not shift_labels:
        st.caption("Add shift templates first.")
        return
    c = st.columns([3, 2, 2, 1])
    with c[0]:
        label = st.selectbox("Shift", shift_labels, key="wp_shift")
    with c[1]:
        wd = st.selectbox("Weekday", WEEKDAY_LABELS, key="wp_wd")
    with c[2]:
        pts = st.number_input("Points", 0.0, 99.0, 1.0, 0.5, key="wp_pts")
    with c[3]:
        if _add_button("wp_add"):
            st.session_state[Keys.WEEKDAY_POINTS][(label, WEEKDAY_LABELS.index(wd))] = pts
    wp = st.session_state[Keys.WEEKDAY_POINTS]
    if wp:
        st.table(pd.DataFrame(
            [{"Shift": lbl, "Weekday": WEEKDAY_LABELS[d], "Points": v}
             for (lbl, d), v in wp.items()]
        ))
        opts = list(wp.keys())
        removed = _remove_control(
            list(range(len(opts))),
            "Remove override",
            "wp_rm",
            format_func=lambda i: f"{opts[i][0]} / {WEEKDAY_LABELS[opts[i][1]]}",
        )
        if removed is not None:
            wp.pop(opts[removed], None)


def holidays_editor(default_date: date | None = None) -> None:
    """Inline editor: bonus points for every shift on a date (with weekend option)."""
    st.markdown("**Holidays — bonus points for shifts on a date**")
    c = st.columns([3, 2, 2, 1])
    with c[0]:
        hd = st.date_input("Date", default_date or date.today(), key="hol_date")
    with c[1]:
        bonus = st.number_input("Bonus points", 0.0, 99.0, 1.0, 0.5, key="hol_bonus")
    with c[2]:
        wk = st.checkbox("Count as weekend", value=False, key="hol_wk")
    with c[3]:
        if _add_button("hol_add"):
            st.session_state[Keys.HOLIDAYS] = [
                h for h in st.session_state[Keys.HOLIDAYS] if h[0] != hd
            ]
            st.session_state[Keys.HOLIDAYS].append((hd, bonus, wk))
    hs = st.session_state[Keys.HOLIDAYS]
    if hs:
        st.table(pd.DataFrame(
            [{"Date": d, "Bonus": b, "Counts as weekend": w} for d, b, w in hs]
        ))
        removed = _remove_control(
            list(range(len(hs))), "Remove holiday", "hol_rm",
            format_func=lambda i: str(hs[i][0]),
        )
        if removed is not None:
            st.session_state[Keys.HOLIDAYS].pop(removed)


def shift_template_editor() -> None:
    """Editor for the shift templates (label, role, NF, Thu-weekend, points)."""
    st.subheader("Shift templates")
    label = st.text_input("Label")
    role = st.selectbox("Role", ["Junior", "Senior"])
    sc = st.columns(3)
    with sc[0]:
        nf = st.checkbox("Night float")
    with sc[1]:
        thu_wk = st.checkbox("Thu counts as weekend (this shift only)")
    with sc[2]:
        points = st.number_input("Points", 1.0, 10.0, 1.0, 0.5)
    existing = {s.label for s in st.session_state[Keys.SHIFTS]}
    if st.button("Add shift", key="add_shift"):
        cleaned = label.strip()
        if not cleaned:
            st.warning("Give the shift a label before adding it.")
        elif cleaned in existing:
            st.warning(f"A shift labelled '{cleaned}' already exists.")
        else:
            st.session_state[Keys.SHIFTS].append(
                ShiftTemplate(
                    label=cleaned, role=role, night_float=nf, thu_weekend=thu_wk, points=points
                )
            )
    shifts = st.session_state[Keys.SHIFTS]
    if shifts:
        st.table(pd.DataFrame([s.__dict__ for s in shifts]))
        # format_func closes over the local list, not session state: the test
        # harness may call it outside a script run where the proxy is empty.
        del_idx = st.selectbox(
            "Delete shift", list(range(len(shifts))),
            format_func=lambda i: shifts[i].label,
        )
        if st.button("Delete shift", key="del_shift"):
            shifts.pop(del_idx)


def _parse_names(text: str) -> list:
    """Parse one-name-per-line input, trimming and de-duplicating in order."""
    return list(dict.fromkeys(n.strip() for n in text.splitlines() if n.strip()))


def roster_editor() -> None:
    """Participants (text areas) and night-float eligibility, side by side."""
    cols = st.columns(2)
    with cols[0]:
        st.subheader("Participants")
        juniors_text = st.text_area(
            "Juniors (one per line)", "\n".join(st.session_state[Keys.JUNIORS])
        )
        seniors_text = st.text_area(
            "Seniors (one per line)", "\n".join(st.session_state[Keys.SENIORS])
        )
        st.session_state[Keys.JUNIORS] = _parse_names(juniors_text)
        st.session_state[Keys.SENIORS] = _parse_names(seniors_text)
        both = set(st.session_state[Keys.JUNIORS]) & set(st.session_state[Keys.SENIORS])
        if both:
            st.warning(
                "Listed as both junior and senior (fix before generating): "
                + ", ".join(sorted(both))
            )
    with cols[1]:
        st.subheader("Night-float eligible")
        st.session_state[Keys.NF_JUNIORS] = st.multiselect(
            "Juniors", st.session_state[Keys.JUNIORS],
            default=[n for n in st.session_state[Keys.NF_JUNIORS]
                     if n in st.session_state[Keys.JUNIORS]],
        )
        st.session_state[Keys.NF_SENIORS] = st.multiselect(
            "Seniors", st.session_state[Keys.SENIORS],
            default=[n for n in st.session_state[Keys.NF_SENIORS]
                     if n in st.session_state[Keys.SENIORS]],
        )


def custom_columns_editor(base_df) -> None:
    """UI to add/remove/fill cosmetic columns (on-call team, consultant, notes…)."""
    st.markdown("**Custom columns — cosmetic labels added to the final schedule only**")
    st.caption(
        "These don't affect scheduling, fairness, or validation — they're just "
        "extra columns (e.g. on-call team, consultant on service) you can label "
        "per day and carry into the downloads."
    )
    ac = st.columns([3, 1])
    new_name = ac[0].text_input("New column name", key="newcol_name")
    if ac[1].button("Add column", key="newcol_add"):
        name = new_name.strip()
        if name and name not in base_df.columns and name not in st.session_state[Keys.EXTRA_COLS]:
            st.session_state[Keys.EXTRA_COLS].append(name)
    if not st.session_state[Keys.EXTRA_COLS]:
        return
    rc = st.columns([3, 1])
    rm = rc[0].selectbox("Remove column", st.session_state[Keys.EXTRA_COLS], key="rmcol_sel")
    if rc[1].button("Remove column", key="rmcol_btn"):
        st.session_state[Keys.EXTRA_COLS].remove(rm)
        st.session_state[Keys.EXTRA_VALS].pop(rm, None)
        st.rerun()
    dates = list(base_df["Date"])
    editor_df = pd.DataFrame({"Date": dates})
    for name in st.session_state[Keys.EXTRA_COLS]:
        vals = st.session_state[Keys.EXTRA_VALS].get(name, {})
        editor_df[name] = [vals.get(str(d), "") for d in dates]
    edited = st.data_editor(
        editor_df, key="extra_cols_editor", disabled=["Date"], use_container_width=True
    )
    for name in st.session_state[Keys.EXTRA_COLS]:
        st.session_state[Keys.EXTRA_VALS][name] = {
            str(d): ("" if v is None else str(v))
            for d, v in zip(list(edited["Date"]), list(edited[name]))
        }
