"""Reusable add/remove editors for the configuration tabs.

Each editor renders an input row with an Add button, a table of current
entries, and a shared remove control. Widget keys are unchanged from the
original single-file app so saved links / tests keep working.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib

import pandas as pd
import streamlit as st

from model.data_models import (
    Blackout,
    LoadReduction,
    NightFloatAssignment,
    NightFloatCoverage,
    Perk,
    ShiftClosure,
    ShiftTemplate,
    normalized_blackouts,
    normalized_closures,
    normalized_nf_assignments,
    normalized_reductions,
)
from model.names import canonical_name, dedupe_names
from model.utils import friendly_date
from ui.patterns import FILL_MODES, expand_pattern, parse_fill_names
from ui.state import Keys

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Sentinel entry in group selectors that reveals an ad-hoc name multiselect.
ADHOC_CHOICE = "(ad-hoc names…)"


def _stable_widget_key(prefix: str, value: object) -> str:
    """A compact deterministic key safe for arbitrary user-entered labels."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _summary_table(data: pd.DataFrame) -> None:
    """Render every read-only editor summary with the same compact grid."""
    st.dataframe(data, hide_index=True, width="stretch")


def _remove_control(options, label: str, key: str, format_func=str) -> object | None:
    """Shared selectbox + Remove button row; returns the choice on click."""
    selector, action = st.columns([4, 1], vertical_alignment="bottom")
    with selector:
        choice = st.selectbox(label, options, format_func=format_func, key=f"{key}_idx")
    with action:
        clicked = st.button("Remove", key=f"{key}_btn", width="stretch")
    if clicked:
        return choice
    return None


def _add_button(key: str) -> bool:
    """An Add button vertically aligned with the input widgets beside it."""
    return st.button("Add", key=key, width="stretch")


def date_range_editor(
    title: str,
    key: str,
    people: list,
    with_compensation: bool = False,
    default_start: date | None = None,
    default_end: date | None = None,
    shift_labels: list | None = None,
) -> None:
    """Inline editor for (resident, start, end[, compensated]) windows.

    With ``with_compensation`` (leaves) each entry carries a Compensated flag:
    compensated keeps the resident's full fair share, uncompensated scales it down
    like a rotator. ``default_start`` / ``default_end`` seed the date pickers
    (pass the schedule block's range so entries land in the right month).
    ``shift_labels`` (rotators) adds a "covers only these shift types"
    multiselect: leaving some out exempts the resident from them, via the
    normal exemptions mechanism.
    """
    st.markdown(f"**{title}**")
    if not people:
        st.caption("Add participants first to configure this.")
        return
    layout = [3, 2, 2, 2, 1] if with_compensation else [3, 2, 2, 1]
    c = st.columns(layout, vertical_alignment="bottom")
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
        clicked = _add_button(f"{key}_add")
    covers = None
    if shift_labels:
        covers = st.multiselect(
            "Covers only these shift types (empty = all)",
            shift_labels,
            key=f"{key}_cover",
            help="Anything left out is added to this resident's exemptions "
            "(③ Policies → Teams & restrictions), where it can be reviewed or removed.",
        )
    if clicked:
        entry = (who, start, end, compensated) if with_compensation else (who, start, end)
        st.session_state[key].append(entry)
        if shift_labels and covers and set(covers) != set(shift_labels):
            merged = set(st.session_state[Keys.EXEMPT_SHIFTS].get(who, []))
            merged |= set(shift_labels) - set(covers)
            st.session_state[Keys.EXEMPT_SHIFTS][who] = sorted(merged)
        st.toast(f"Added entry for {who}.")
    rows = st.session_state[key]
    if rows:
        table_rows = []
        for entry in rows:
            row = {"Resident": entry[0], "Start": entry[1], "End": entry[2]}
            if with_compensation:
                row["Compensated"] = entry[3] if len(entry) > 3 else True
            table_rows.append(row)
        _summary_table(pd.DataFrame(table_rows))
        removed = _remove_control(
            list(range(len(rows))),
            "Remove entry",
            f"{key}_del",
            format_func=lambda i: f"{rows[i][0]}: {rows[i][1]} → {rows[i][2]}",
        )
        if removed is not None:
            st.session_state[key].pop(removed)


def caps_editor(people: list) -> None:
    """Inline editor for per-resident hard caps on total points (0 = no cap)."""
    st.markdown("**Caps — limit a resident's total points (0 = no cap)**")
    if not people:
        st.caption("Add participants first to configure caps.")
        return
    c = st.columns([3, 2, 2, 1], vertical_alignment="bottom")
    with c[0]:
        who = st.selectbox("Resident", people, key="cap_who")
    with c[1]:
        mt = st.number_input("Max total pts", 0.0, 999.0, 0.0, 0.5, key="cap_total")
    with c[2]:
        compensate = st.checkbox(
            "Compensate later", value=True, key="cap_comp",
            help="On: the shortfall below the resident's fair share is made up "
            "in later blocks (cumulative fairness). Off: the reduced capacity is "
            "excused — a standing limit that is never caught up (like a perk).",
        )
    with c[3]:
        if _add_button("cap_add"):
            st.session_state[Keys.CAPS][who] = {"total": mt, "excused": not compensate}
            st.toast(f"Set cap for {who}.")
    caps = st.session_state[Keys.CAPS]
    if caps:
        _summary_table(pd.DataFrame([
            {
                "Resident": p,
                "Max total": v.get("total") or "—",
                "Carryover": "excused" if v.get("excused") else "compensate later",
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
    c = st.columns([3, 2, 1], vertical_alignment="bottom")
    with c[0]:
        who = st.selectbox("Resident", people, key="extra_who")
    with c[1]:
        pts = st.number_input("Extra points", 0.0, 999.0, 0.0, 0.5, key="extra_pts")
    with c[2]:
        if _add_button("extra_add"):
            if pts > 0:
                st.session_state[Keys.EXTRA_POINTS][who] = pts
                st.toast(f"Set {pts:g} extra point(s) for {who}.")
            else:
                st.session_state[Keys.EXTRA_POINTS].pop(who, None)
    ep = st.session_state[Keys.EXTRA_POINTS]
    if ep:
        _summary_table(pd.DataFrame([{"Resident": p, "Extra points": v} for p, v in ep.items()]))
        removed = _remove_control(list(ep.keys()), "Remove extra", "extra_rm")
        if removed is not None:
            ep.pop(removed, None)


def weekday_points_editor(shift_labels: list) -> None:
    """Inline editor: a shift's exact points on a given weekday (e.g. night = 2 on Tue)."""
    st.markdown("**Weekday point overrides — a shift's exact points on a weekday**")
    if not shift_labels:
        st.caption("Add shift templates first.")
        return
    c = st.columns([3, 2, 2, 1], vertical_alignment="bottom")
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
        _summary_table(pd.DataFrame(
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
    c = st.columns([3, 2, 2, 1], vertical_alignment="bottom")
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
        _summary_table(pd.DataFrame(
            [{"Date": d, "Bonus": b, "Counts as weekend": w} for d, b, w in hs]
        ))
        removed = _remove_control(
            list(range(len(hs))), "Remove holiday", "hol_rm",
            format_func=lambda i: str(hs[i][0]),
        )
        if removed is not None:
            st.session_state[Keys.HOLIDAYS].pop(removed)


def seniority_editor(people: list) -> None:
    """Groups with a load percentage (e.g. R2 = 90%) and resident assignment."""
    st.markdown("**Seniority groups — a load % per group (R2 at 90% carries ~10% less)**")
    gc = st.columns([3, 2, 1], vertical_alignment="bottom")
    with gc[0]:
        gname = st.text_input("Group name (e.g. R2)", key="grp_name")
    with gc[1]:
        gpct = st.number_input("Load %", 10, 200, 100, 5, key="grp_pct")
    with gc[2]:
        if _add_button("grp_add"):
            name = gname.strip()
            if name:
                st.session_state[Keys.GROUP_FACTORS][name] = gpct / 100.0
                st.toast(f"Added seniority group “{name}”.")
            else:
                st.warning("Give the group a name.")
    groups = st.session_state[Keys.GROUP_FACTORS]
    if groups:
        _summary_table(pd.DataFrame(
            [{"Group": g, "Load %": f"{f * 100:g}%"} for g, f in groups.items()]
        ))
        removed = _remove_control(list(groups.keys()), "Remove group", "grp_rm")
        if removed is not None:
            groups.pop(removed, None)
            st.session_state[Keys.RESIDENT_GROUPS] = {
                p: g for p, g in st.session_state[Keys.RESIDENT_GROUPS].items()
                if g != removed
            }

    if not people or not groups:
        if not groups:
            st.caption("Define a group above, then assign residents to it.")
        return
    ac = st.columns([3, 2, 1], vertical_alignment="bottom")
    with ac[0]:
        who = st.multiselect("Residents", people, key="grp_who")
    with ac[1]:
        target_group = st.selectbox("Group", list(groups.keys()), key="grp_target")
    with ac[2]:
        if st.button("Assign", key="grp_assign", width="stretch"):
            for p in who:
                st.session_state[Keys.RESIDENT_GROUPS][p] = target_group
    assigned = st.session_state[Keys.RESIDENT_GROUPS]
    if assigned:
        _summary_table(pd.DataFrame(
            [{"Resident": p, "Group": g} for p, g in assigned.items()]
        ))
        removed = _remove_control(list(assigned.keys()), "Unassign resident", "grp_unassign")
        if removed is not None:
            assigned.pop(removed, None)


def named_groups_editor(people: list) -> None:
    """Named resident groups — reusable member sets for bulk entries.

    Distinct from seniority groups (which carry a load %): these are plain
    member lists used by the group blackout / reduction editors so a set of
    residents can be picked once instead of person by person.
    """
    st.markdown(
        "**Groups — name a set of residents once, then apply blackouts or "
        "shift reductions to the whole group**"
    )
    gc = st.columns([3, 1], vertical_alignment="bottom")
    with gc[0]:
        gname = st.text_input("Group name (e.g. Team A)", key="team_name")
    with gc[1]:
        if _add_button("team_add"):
            name = gname.strip()
            if name:
                st.session_state[Keys.NAMED_GROUPS].setdefault(name, [])
            else:
                st.warning("Give the group a name.")
    groups = st.session_state[Keys.NAMED_GROUPS]
    if groups and people:
        ac = st.columns([3, 2, 1], vertical_alignment="bottom")
        with ac[0]:
            who = st.multiselect("Residents", people, key="team_who")
        with ac[1]:
            target = st.selectbox("Group", list(groups.keys()), key="team_target")
        with ac[2]:
            if st.button("Add to group", key="team_assign", width="stretch"):
                for p in who:
                    if p not in groups[target]:
                        groups[target].append(p)
    elif not people:
        st.caption("Add participants first to fill the groups.")
    if groups:
        _summary_table(pd.DataFrame([
            {"Group": g, "Members": ", ".join(members) or "—"}
            for g, members in groups.items()
        ]))
        removed = _remove_control(list(groups.keys()), "Remove group", "team_rm")
        if removed is not None:
            groups.pop(removed, None)
        member_opts = [(g, m) for g, members in groups.items() for m in members]
        if member_opts:
            removed_member = _remove_control(
                list(range(len(member_opts))),
                "Remove member",
                "team_member_rm",
                format_func=lambda i: f"{member_opts[i][0]}: {member_opts[i][1]}",
            )
            if removed_member is not None:
                group, member = member_opts[removed_member]
                groups[group].remove(member)


def _group_or_adhoc_selector(people: list, key_prefix: str) -> tuple:
    """Group selectbox with an ad-hoc-names escape hatch.

    Returns ``(group_name_or_None, members_tuple)`` — a named-group choice
    yields ``(name, ())`` (membership resolves at use time, so editing the
    group later updates the entry), the ad-hoc choice ``(None, names)``.
    """
    groups = st.session_state[Keys.NAMED_GROUPS]
    options = list(groups.keys()) + [ADHOC_CHOICE]
    who = st.selectbox("Group", options, key=f"{key_prefix}_who")
    members: tuple = ()
    if who == ADHOC_CHOICE:
        members = tuple(st.multiselect("Residents", people, key=f"{key_prefix}_adhoc"))
        return None, members
    return who, members


_BO_PERIOD = "A period (start → end)"
_BO_DATES = "Specific dates"


def _block_date_options(start: date | None, end: date | None) -> list:
    """Every date in the configured block, for the specific-dates picker."""
    if start is None or end is None or end < start:
        return []
    span = (end - start).days + 1
    # Guard against an absurd range making a multiselect with thousands of
    # options; a block is normally a few weeks.
    return [start + timedelta(days=i) for i in range(min(span, 400))]


def blackouts_editor(
    people: list,
    default_start: date | None = None,
    default_end: date | None = None,
) -> None:
    """Group blackout periods: nobody covered is on call during the window."""
    st.markdown("**Blackouts — a whole group is off call**")
    st.caption(
        "Everyone covered is blocked for the dates chosen, and (by default) "
        "from the night calls of the day before each of them — night call "
        "meaning the shifts flagged 'Thu counts as weekend' — so nobody is "
        "post-call on their first off day. Night-float duty is never affected. "
        "Not a leave: with Compensated on, each member keeps their full fair "
        "share — missed load is made up on other days, or carried in the "
        "fairness ledger as debt to repay next block."
    )
    if not people:
        st.caption("Add participants first to configure blackouts.")
        return
    mode = st.radio(
        "Which dates?",
        [_BO_PERIOD, _BO_DATES],
        key="bo_mode",
        horizontal=True,
        help="A period covers every day between two dates (a course, annual "
        "leave). Specific dates lets you tick several separate days at once — "
        "one entry is added per date, so a team that is off seven scattered "
        "days is seven ticks instead of seven trips through this form.",
    )
    if mode == _BO_DATES:
        c = st.columns([3, 2, 2, 1], vertical_alignment="bottom")
        with c[0]:
            group, members = _group_or_adhoc_selector(people, "bo")
        with c[1]:
            night_before = st.checkbox(
                "Block night call before", value=True, key="bo_nightbefore"
            )
        with c[2]:
            compensated = st.checkbox("Compensated", value=True, key="bo_comp")
        with c[-1]:
            clicked = _add_button("bo_add")
        options = _block_date_options(default_start, default_end)
        if not options:
            st.caption("Set the schedule dates first to pick specific days.")
            return
        picked = st.multiselect(
            "Dates off call",
            options,
            format_func=friendly_date,
            key="bo_dates",
            help="Tick every day this group is off. Dates outside the schedule "
            "block are not listed because they would have no effect.",
        )
        if clicked:
            if group is None and not members:
                st.warning("Pick a group or at least one resident.")
            elif not picked:
                st.warning("Pick at least one date.")
            else:
                existing = {
                    (b.group, b.members, b.start)
                    for b in normalized_blackouts(st.session_state[Keys.BLACKOUTS])
                    if b.start == b.end
                }
                added = 0
                for day in sorted(picked):
                    if (group, tuple(members), day) in existing:
                        continue  # already blacked out for this group
                    st.session_state[Keys.BLACKOUTS].append(
                        Blackout(group, members, day, day, night_before, compensated)
                    )
                    added += 1
                skipped = len(picked) - added
                note = f" ({skipped} already set)" if skipped else ""
                st.toast(f"Added {added} blackout date(s){note}.")
    else:
        c = st.columns([3, 2, 2, 2, 2, 1], vertical_alignment="bottom")
        with c[0]:
            group, members = _group_or_adhoc_selector(people, "bo")
        with c[1]:
            start = st.date_input("Start", default_start or date.today(), key="bo_start")
        with c[2]:
            end = st.date_input(
                "End", default_end or default_start or date.today(), key="bo_end"
            )
        with c[3]:
            night_before = st.checkbox(
                "Block night call before", value=True, key="bo_nightbefore"
            )
        with c[4]:
            compensated = st.checkbox("Compensated", value=True, key="bo_comp")
        with c[-1]:
            if _add_button("bo_add"):
                if group is None and not members:
                    st.warning("Pick a group or at least one resident.")
                else:
                    st.session_state[Keys.BLACKOUTS].append(
                        Blackout(group, members, start, end, night_before, compensated)
                    )
                    st.toast("Added blackout window.")
    entries = list(normalized_blackouts(st.session_state[Keys.BLACKOUTS]))
    if entries:
        groups = st.session_state[Keys.NAMED_GROUPS]
        table_rows = []
        for b in entries:
            covered = groups.get(b.group, []) if b.group is not None else list(b.members)
            table_rows.append({
                "Group": b.group or "(ad-hoc)",
                "Members": ", ".join(covered) or "—",
                # Single dates read as one day, not a start/end pair repeated.
                "Dates": (
                    friendly_date(b.start) if b.start == b.end
                    else f"{friendly_date(b.start)} → {friendly_date(b.end)}"
                ),
                "Night call before": b.night_before,
                "Compensated": b.compensated,
            })
        _summary_table(pd.DataFrame(table_rows))
        # Picking specific dates adds one entry per day, so a few teams quickly
        # fill the table; this line is the at-a-glance check that each group
        # got the number of days intended.
        per_group: dict = {}
        for b in entries:
            per_group[b.group or "(ad-hoc)"] = per_group.get(b.group or "(ad-hoc)", 0) + 1
        if len(entries) > len(per_group):
            st.caption(
                "Entries per group: "
                + ", ".join(f"{name} {count}" for name, count in sorted(per_group.items()))
            )
        removed = _remove_control(
            list(range(len(entries))),
            "Remove blackout",
            "bo_rm",
            format_func=lambda i: (
                f"{entries[i].group or 'ad-hoc'}: {friendly_date(entries[i].start)}"
                + (
                    ""
                    if entries[i].start == entries[i].end
                    else f" → {friendly_date(entries[i].end)}"
                )
            ),
        )
        if removed is not None:
            st.session_state[Keys.BLACKOUTS].pop(removed)
        # Clearing a group's dates one at a time is the reverse of the problem
        # the multi-date picker solves, so offer a bulk remove too.
        clearable = sorted({b.group for b in entries if b.group is not None})
        if clearable:
            cleared = _remove_control(
                clearable,
                "Clear every blackout for a group",
                "bo_clear",
                format_func=lambda name: f"{name} ({per_group.get(name, 0)} entries)",
            )
            if cleared is not None:
                st.session_state[Keys.BLACKOUTS] = [
                    entry
                    for entry, norm in zip(st.session_state[Keys.BLACKOUTS], entries)
                    if norm.group != cleared
                ]
                st.toast(f"Cleared blackouts for “{cleared}”.")


def perks_editor(
    people: list,
    default_start: date | None = None,
    default_end: date | None = None,
) -> None:
    """Individual load reductions, optionally time-bounded (or forever)."""
    st.markdown(
        "**Perks — an individual load % for a window or forever "
        "(e.g. 80% for a month). Stacks with the group %.**"
    )
    if not people:
        st.caption("Add participants first to configure perks.")
        return
    c = st.columns([3, 2, 2, 2, 2, 1], vertical_alignment="bottom")
    with c[0]:
        who = st.selectbox("Resident", people, key="perk_who")
    with c[1]:
        pct = st.number_input("Load %", 10, 200, 80, 5, key="perk_pct")
    with c[2]:
        forever = st.checkbox("Forever", value=False, key="perk_forever")
    start = end = None
    if not forever:
        with c[3]:
            start = st.date_input("From", default_start or date.today(), key="perk_start")
        with c[4]:
            end = st.date_input("To", default_end or default_start or date.today(), key="perk_end")
    with c[-1]:
        if _add_button("perk_add"):
            st.session_state[Keys.PERKS].append(Perk(who, pct / 100.0, start, end))
            st.toast(f"Added perk for {who}.")
    perks = st.session_state[Keys.PERKS]
    if perks:
        _summary_table(pd.DataFrame([
            {
                "Resident": p.name,
                "Load %": f"{p.factor * 100:g}%",
                "From": p.start or "—",
                "To": p.end or ("forever" if p.start is None else "—"),
            }
            for p in perks
        ]))
        removed = _remove_control(
            list(range(len(perks))), "Remove perk", "perk_rm",
            format_func=lambda i: f"{perks[i].name} ×{perks[i].factor:g}",
        )
        if removed is not None:
            perks.pop(removed)


def closures_editor(
    shift_labels: list,
    default_start: date | None = None,
    default_end: date | None = None,
) -> None:
    """Shift closures — a shift is stood down (not staffed) on a set of dates."""
    st.markdown("**Shift closures — stand a shift down on specific dates**")
    st.caption(
        "For a resident shortage or a dropped holiday shift: the closed dates "
        "show as \"Closed\", are never counted as unfilled, and stay out of "
        "points and fairness entirely. Leave the weekday filter empty to close "
        "every day in the range, or pick weekdays (e.g. Sat/Sun) to close only "
        "those."
    )
    if not shift_labels:
        st.caption("Add shift templates first.")
        return
    c = st.columns([3, 2, 2, 1], vertical_alignment="bottom")
    with c[0]:
        label = st.selectbox("Shift", shift_labels, key="close_label")
    with c[1]:
        start = st.date_input("Start", default_start or date.today(), key="close_start")
    with c[2]:
        end = st.date_input(
            "End", default_end or default_start or date.today(), key="close_end"
        )
    with c[3]:
        clicked = _add_button("close_add")
    weekday_names = st.multiselect(
        "Only these weekdays (empty = every day in range)",
        WEEKDAY_LABELS,
        key="close_weekdays",
    )
    if clicked:
        weekdays = tuple(WEEKDAY_LABELS.index(name) for name in weekday_names)
        st.session_state[Keys.CLOSURES].append(
            ShiftClosure(label, start, end, weekdays)
        )
        st.toast(f"Added closure for “{label}”.")
    rows = list(normalized_closures(st.session_state[Keys.CLOSURES]))
    if rows:
        _summary_table(pd.DataFrame([
            {
                "Shift": c.label,
                "Start": c.start,
                "End": c.end,
                "Weekdays": ", ".join(WEEKDAY_LABELS[w] for w in c.weekdays) or "all",
            }
            for c in rows
        ]))
        removed = _remove_control(
            list(range(len(rows))),
            "Remove closure",
            "close_rm",
            format_func=lambda i: f"{rows[i].label}: {rows[i].start} → {rows[i].end}",
        )
        if removed is not None:
            st.session_state[Keys.CLOSURES].pop(removed)


def exemptions_editor(people: list, shift_labels: list) -> None:
    """Residents who never work specific shift types (hard block)."""
    st.markdown("**Exemptions — a resident never works these shift types**")
    st.caption(
        "Exempt residents keep their full fairness target — they carry their "
        "share on the other shift types (same rule as night-float eligibility). "
        "Add a perk too if their overall share should also be lower."
    )
    if not people or not shift_labels:
        st.caption("Add participants and shift templates first.")
        return
    c = st.columns([3, 3, 1], vertical_alignment="bottom")
    with c[0]:
        who = st.selectbox("Resident", people, key="ex_who")
    with c[1]:
        labels = st.multiselect("Never works", shift_labels, key="ex_labels")
    with c[2]:
        if st.button("Set", key="ex_add", width="stretch"):
            if labels:
                st.session_state[Keys.EXEMPT_SHIFTS][who] = sorted(labels)
            else:
                st.session_state[Keys.EXEMPT_SHIFTS].pop(who, None)
    ex = st.session_state[Keys.EXEMPT_SHIFTS]
    if ex:
        _summary_table(pd.DataFrame(
            [{"Resident": p, "Exempt from": ", ".join(v)} for p, v in ex.items()]
        ))
        removed = _remove_control(list(ex.keys()), "Remove exemption", "ex_rm")
        if removed is not None:
            ex.pop(removed, None)


# Deliberate friction, not security: using avoid pairs needs sign-off from
# higher authority, so the editor asks for this code before revealing itself.
AVOID_UNLOCK_CODE = "1221"


def avoid_pairs_editor(people: list) -> None:
    """Pairs never on call the same day; gated behind an access code."""
    with st.expander("Avoid pairs (restricted)", expanded=False):
        st.caption(
            "Two residents who must never be on call on the same day. Use "
            "only with approval from higher authority — the access code is a "
            "deliberate extra step, not a security measure. Fairness targets "
            "are unaffected."
        )
        pairs = st.session_state[Keys.AVOID_PAIRS]
        if not st.session_state[Keys.AVOID_UNLOCKED]:
            if pairs:
                status = (
                    "awaiting reconfirmation (inactive)"
                    if st.session_state.get(Keys.AVOID_RECONFIRM_REQUIRED, False)
                    else "configured and active"
                )
                st.caption(f"{len(pairs)} avoid pair(s): {status}.")
            code = st.text_input("Access code", type="password", key="avoid_code")
            if st.button("Unlock", key="avoid_unlock"):
                if code == AVOID_UNLOCK_CODE:
                    st.session_state[Keys.AVOID_UNLOCKED] = True
                    st.rerun()
                else:
                    st.warning("Wrong code.")
            return
        if len(people) < 2:
            st.caption("Add at least two participants first.")
            return
        if pairs and st.session_state.get(Keys.AVOID_RECONFIRM_REQUIRED, False):
            st.warning(
                "These avoid pairs came from an imported config and are not "
                "active yet. Confirm them explicitly after reviewing the names."
            )
            if st.button("Confirm imported avoid pairs", key="avoid_reconfirm"):
                st.session_state[Keys.AVOID_RECONFIRM_REQUIRED] = False
                st.rerun()
        c = st.columns([3, 3, 1], vertical_alignment="bottom")
        with c[0]:
            first = st.selectbox("Resident A", people, key="avoid_a")
        with c[1]:
            second = st.selectbox("Resident B", people, key="avoid_b")
        with c[2]:
            if _add_button("avoid_add"):
                if first == second:
                    st.warning("Pick two different residents.")
                elif not any({p[0], p[1]} == {first, second} for p in pairs):
                    pairs.append((first, second))
        if pairs:
            _summary_table(pd.DataFrame(
                [{"Resident A": p[0], "Resident B": p[1]} for p in pairs]
            ))
            removed = _remove_control(
                list(range(len(pairs))),
                "Remove pair",
                "avoid_rm",
                format_func=lambda i: f"{pairs[i][0]} / {pairs[i][1]}",
            )
            if removed is not None:
                pairs.pop(removed)


DAY_TYPE_CHOICES = {"No preference": None, "Weekends": "weekend", "Weekdays": "weekday"}


def preferences_editor(people: list, shift_labels: list) -> None:
    """Soft per-resident preferences: preferred shift types and day type."""
    st.markdown("**Shift preferences — quality of life, never fairness**")
    st.caption(
        "Soft: preferences only break ties between equally fair schedules — "
        "they never change anyone's fair share, deviations, or the ledger. "
        "Two people wanting opposite things simply swap slots."
    )
    if not people or not shift_labels:
        st.caption("Add participants and shift templates first.")
        return
    c = st.columns([3, 3, 2, 1], vertical_alignment="bottom")
    with c[0]:
        who = st.selectbox("Resident", people, key="pref_who")
    with c[1]:
        labels = st.multiselect("Prefers these shift types", shift_labels, key="pref_labels")
    with c[2]:
        day_choice = st.selectbox("Day type", list(DAY_TYPE_CHOICES), key="pref_day")
    with c[3]:
        if st.button("Set", key="pref_set", width="stretch"):
            if labels:
                st.session_state[Keys.PREFERRED_SHIFTS][who] = sorted(labels)
            else:
                st.session_state[Keys.PREFERRED_SHIFTS].pop(who, None)
            day_kind = DAY_TYPE_CHOICES[day_choice]
            if day_kind:
                st.session_state[Keys.PREFERRED_DAY_TYPE][who] = day_kind
            else:
                st.session_state[Keys.PREFERRED_DAY_TYPE].pop(who, None)
    shifts_map = st.session_state[Keys.PREFERRED_SHIFTS]
    days_map = st.session_state[Keys.PREFERRED_DAY_TYPE]
    everyone = sorted(set(shifts_map) | set(days_map))
    if everyone:
        _summary_table(pd.DataFrame([
            {
                "Resident": p,
                "Prefers": ", ".join(shifts_map.get(p, [])) or "—",
                "Day type": {"weekend": "Weekends", "weekday": "Weekdays"}.get(
                    days_map.get(p, ""), "—"
                ),
            }
            for p in everyone
        ]))
        removed = _remove_control(everyone, "Remove preference", "pref_rm")
        if removed is not None:
            shifts_map.pop(removed, None)
            days_map.pop(removed, None)


REDUCTION_MODES = {
    "Work less now, repay later": False,   # keep_total=False
    "Keep full share (more of the other shifts now)": True,
}


def reductions_editor(
    people: list,
    shift_labels: list,
    default_start: date | None = None,
    default_end: date | None = None,
) -> None:
    """A group carries less of specific shift types for a window (repaid later)."""
    st.markdown("**Shift reductions — a group carries less of specific shift types for a period**")
    st.caption(
        "e.g. few or no night calls during a heavy rotation, with others "
        "covering. 0% = none of these shifts in the window. The shortfall is "
        "carried in the fairness ledger as debt and repaid in later blocks — "
        "never excused (unlike a perk)."
    )
    if not people or not shift_labels:
        st.caption("Add participants and shift templates first.")
        return
    c = st.columns([3, 3, 2, 2, 2, 1], vertical_alignment="bottom")
    with c[0]:
        group, members = _group_or_adhoc_selector(people, "red")
    with c[1]:
        labels = st.multiselect("Shift types", shift_labels, key="red_labels")
    with c[2]:
        pct = st.slider("Load % of fair share", 0, 100, 0, 5, key="red_pct")
    with c[3]:
        start = st.date_input("Start", default_start or date.today(), key="red_start")
    with c[4]:
        end = st.date_input("End", default_end or default_start or date.today(), key="red_end")
    mode = st.radio(
        "Rest of their load this block",
        list(REDUCTION_MODES),
        key="red_mode",
        horizontal=True,
    )
    with c[-1]:
        if _add_button("red_add"):
            if group is None and not members:
                st.warning("Pick a group or at least one resident.")
            elif not labels:
                st.warning("Pick at least one shift type to reduce.")
            else:
                st.session_state[Keys.REDUCTIONS].append(
                    LoadReduction(
                        group, members, tuple(labels), pct / 100.0,
                        start, end, REDUCTION_MODES[mode],
                    )
                )
                st.toast("Added shift-type reduction.")
    entries = list(normalized_reductions(st.session_state[Keys.REDUCTIONS]))
    if entries:
        groups = st.session_state[Keys.NAMED_GROUPS]
        _summary_table(pd.DataFrame([
            {
                "Group": r.group or "(ad-hoc)",
                "Members": ", ".join(
                    groups.get(r.group, []) if r.group is not None else r.members
                ) or "—",
                "Shift types": ", ".join(r.labels),
                "Load %": f"{r.factor * 100:g}%",
                "Start": r.start,
                "End": r.end,
                "This block": "keep full share" if r.keep_total else "work less now",
            }
            for r in entries
        ]))
        removed = _remove_control(
            list(range(len(entries))),
            "Remove reduction",
            "red_rm",
            format_func=lambda i: (
                f"{entries[i].group or 'ad-hoc'}: {', '.join(entries[i].labels)} "
                f"×{entries[i].factor:g}"
            ),
        )
        if removed is not None:
            st.session_state[Keys.REDUCTIONS].pop(removed)


def night_float_editor(
    people: list,
    nf_shift_roles: dict,
    default_start: date | None = None,
    default_end: date | None = None,
) -> None:
    """Night-float overlay: coverage pattern, coverer periods, and rest days.

    ``nf_shift_roles`` maps each night-float-eligible shift label to its role, so
    the editor can restrict a coverer to their own role's shifts.
    """
    st.markdown("**Night float — a separate coverage layer, outside regular fairness**")
    st.caption(
        "Mark shifts night-float-eligible in the shift editor. Here you choose "
        "which dates the overlay actually covers (the rest stay regular shifts) "
        "and who covers them, for which period. A floater is off regular shifts "
        "during their block plus rest days, works less regular load (no future "
        "catch-up), and their NF duty is tracked separately — it never counts "
        "toward regular points."
    )
    nf_shift_labels = list(nf_shift_roles)
    if not nf_shift_labels:
        st.caption("Mark at least one shift 'Night-float eligible' first.")
        return

    st.markdown("**Coverage — which weekdays the overlay covers each NF shift**")
    coverage = st.session_state[Keys.NF_COVERAGE]
    for label in nf_shift_labels:
        current = coverage.get(label)
        default_days = (
            [WEEKDAY_LABELS[w] for w in current.weekdays]
            if isinstance(current, NightFloatCoverage)
            else []
        )
        chosen = st.multiselect(
            f"'{label}' ({nf_shift_roles[label]}) covered on", WEEKDAY_LABELS,
            default=default_days, key=_stable_widget_key("nfcov", label),
            help="Weekends are covered only if you select them here; unselected "
            "days are filled by regular shifters.",
        )
        include_dates = current.include_dates if isinstance(current, NightFloatCoverage) else ()
        exclude_dates = current.exclude_dates if isinstance(current, NightFloatCoverage) else ()
        if chosen or include_dates or exclude_dates:
            coverage[label] = NightFloatCoverage(
                label,
                tuple(WEEKDAY_LABELS.index(d) for d in chosen),
                tuple(include_dates),
                tuple(exclude_dates),
            )
        else:
            coverage.pop(label, None)
        if include_dates or exclude_dates:
            st.caption(
                f"Imported date exceptions preserved for '{label}': "
                f"{len(include_dates)} included, {len(exclude_dates)} excluded."
            )

    st.divider()
    st.session_state[Keys.NF_REST_DAYS] = st.number_input(
        "Default rest days after an NF block", 0, 7,
        int(st.session_state[Keys.NF_REST_DAYS]), 1,
        help="A leave-like buffer so nobody goes straight from nights to a "
        "regular shift.",
    )

    st.markdown("**Night-float assignments — who covers the overlay, when**")
    nf_pool = [p for p in people if p in set(st.session_state[Keys.NF_JUNIORS])
               | set(st.session_state[Keys.NF_SENIORS])]
    if not nf_pool:
        st.caption("Mark residents 'Night-float eligible' in the roster first.")
        return
    juniors = set(st.session_state[Keys.JUNIORS])
    c = st.columns([3, 2, 2, 3, 1, 1], vertical_alignment="bottom")
    with c[0]:
        who = st.selectbox("Resident", nf_pool, key="nfasg_who")
    # A coverer only covers their own role's NF shifts (the role comes from the
    # roster — no need to re-enter it). Key the label picker by role so switching
    # coverer never leaves a stale cross-role selection behind.
    who_role = "Junior" if who in juniors else "Senior"
    role_labels = [lbl for lbl in nf_shift_labels if nf_shift_roles[lbl] == who_role]
    with c[1]:
        start = st.date_input("Start", default_start or date.today(), key="nfasg_start")
    with c[2]:
        end = st.date_input("End", default_end or default_start or date.today(), key="nfasg_end")
    with c[3]:
        labels = st.multiselect(
            f"Covers (empty = all {who_role} NF)", role_labels,
            key=f"nfasg_labels_{who_role}",
        )
    with c[4]:
        rest = st.number_input("Rest", 0, 7, int(st.session_state[Keys.NF_REST_DAYS]), 1,
                               key="nfasg_rest")
    with c[5]:
        if _add_button("nfasg_add"):
            st.session_state[Keys.NF_ASSIGNMENTS].append(
                NightFloatAssignment(who, start, end, tuple(labels), int(rest))
            )
    entries = list(normalized_nf_assignments(
        st.session_state[Keys.NF_ASSIGNMENTS],
        default_rest=st.session_state[Keys.NF_REST_DAYS],
    ))
    if entries:
        _summary_table(pd.DataFrame([
            {
                "Resident": a.name,
                "Start": a.start,
                "End": a.end,
                "Covers": ", ".join(a.labels) or "all NF",
                "Rest": a.rest_days,
            }
            for a in entries
        ]))
        removed = _remove_control(
            list(range(len(entries))),
            "Remove NF assignment",
            "nfasg_rm",
            format_func=lambda i: f"{entries[i].name}: {entries[i].start} → {entries[i].end}",
        )
        if removed is not None:
            st.session_state[Keys.NF_ASSIGNMENTS].pop(removed)


def shift_template_editor() -> None:
    """Editor for the shift templates (label, role, NF, Thu-weekend, points)."""
    st.subheader("Shift templates")
    label = st.text_input("Label")
    role = st.selectbox("Role", ["Junior", "Senior"])
    sc = st.columns(3)
    with sc[0]:
        nf = st.checkbox(
            "Night-float eligible (overlay)",
            help="On dates you mark covered under Night float, this shift is "
            "handled by the NF coverage overlay (outside regular points); on "
            "uncovered dates it is an ordinary regular shift.",
        )
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
            st.toast(f"Added shift “{cleaned}”.")
    shifts = st.session_state[Keys.SHIFTS]
    if shifts:
        _summary_table(pd.DataFrame([s.__dict__ for s in shifts]))
        # format_func closes over the local list, not session state: the test
        # harness may call it outside a script run where the proxy is empty.
        selector, action = st.columns([4, 1], vertical_alignment="bottom")
        with selector:
            del_idx = st.selectbox(
                "Delete shift", list(range(len(shifts))),
                format_func=lambda i: shifts[i].label,
            )
        with action:
            delete_shift = st.button("Delete shift", key="del_shift", width="stretch")
        if delete_shift:
            shifts.pop(del_idx)


def _parse_names(text: str, *, normalize: bool = False) -> list[str]:
    """Parse roster lines and preserve the first spelling of each name.

    Exact matching is the long-standing default. The optional canonical mode
    changes comparison only: case, Unicode compatibility forms, and repeated
    whitespace are ignored while the first entered display spelling survives.
    """
    names = [line.strip() for line in text.splitlines() if line.strip()]
    return dedupe_names(names, mode="canonical" if normalize else "exact")


def _roster_overlap(
    juniors: list[str], seniors: list[str], *, normalize: bool = False
) -> list[str]:
    """Return cross-role duplicates, with both spellings shown when useful."""
    if not normalize:
        return sorted(set(juniors) & set(seniors))

    senior_by_key = {canonical_name(name): name for name in seniors}
    collisions = []
    for junior in juniors:
        senior = senior_by_key.get(canonical_name(junior))
        if senior is None:
            continue
        collisions.append(junior if junior == senior else f"{junior} / {senior}")
    return collisions


def roster_editor() -> None:
    """Participants (text areas) and night-float eligibility, side by side."""
    cols = st.columns(2)
    with cols[0]:
        st.subheader("Participants")
        normalize_names = st.toggle(
            "Canonical roster deduplication (case/spacing-insensitive)",
            key=Keys.NORMALIZE_NAMES,
            help=(
                "Optional. Compares names after Unicode normalization, collapsed "
                "whitespace, and case folding. It never rewrites the first spelling "
                "you entered."
            ),
        )
        st.caption(
            "When on, names such as ‘Alice Smith’, ‘ALICE SMITH’, and equivalent "
            "Unicode or spacing variants count as one person. The first spelling "
            "stays unchanged in the schedule and exports."
        )
        juniors_text = st.text_area(
            "Juniors (one per line)", "\n".join(st.session_state[Keys.JUNIORS])
        )
        seniors_text = st.text_area(
            "Seniors (one per line)", "\n".join(st.session_state[Keys.SENIORS])
        )
        st.session_state[Keys.JUNIORS] = _parse_names(
            juniors_text, normalize=normalize_names
        )
        st.session_state[Keys.SENIORS] = _parse_names(
            seniors_text, normalize=normalize_names
        )
        both = _roster_overlap(
            st.session_state[Keys.JUNIORS],
            st.session_state[Keys.SENIORS],
            normalize=normalize_names,
        )
        if both:
            st.warning(
                "Listed as both junior and senior (fix before generating): "
                + ", ".join(both)
            )
    with cols[1]:
        st.subheader("Night-float eligible")
        # Two role-filtered pickers: each list already holds only that role, so
        # there is nothing to remember — pick the juniors, then the seniors.
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
    ac = st.columns([3, 1], vertical_alignment="bottom")
    new_name = ac[0].text_input("New column name", key="newcol_name")
    if ac[1].button("Add column", key="newcol_add", width="stretch"):
        name = new_name.strip()
        reserved = {
            str(col).strip().casefold()
            for col in [*base_df.columns, *st.session_state[Keys.EXTRA_COLS]]
        }
        if name and name.casefold() not in reserved:
            st.session_state[Keys.EXTRA_COLS].append(name)
        elif name:
            st.warning(
                f"'{name}' conflicts with Date, Day, a shift, or an existing "
                "custom column. Choose a unique name."
            )
    if not st.session_state[Keys.EXTRA_COLS]:
        return
    rc = st.columns([3, 1], vertical_alignment="bottom")
    rm = rc[0].selectbox("Remove column", st.session_state[Keys.EXTRA_COLS], key="rmcol_sel")
    if rc[1].button("Remove column", key="rmcol_btn", width="stretch"):
        st.session_state[Keys.EXTRA_COLS].remove(rm)
        st.session_state[Keys.EXTRA_VALS].pop(rm, None)
        st.rerun()
    dates = list(base_df["Date"])

    # Auto-fill: paste a name list once instead of typing a value per day.
    st.markdown("**Auto-fill a column**")
    fc = st.columns([2, 3, 3, 1], vertical_alignment="bottom")
    with fc[0]:
        fill_col = st.selectbox("Column to fill", st.session_state[Keys.EXTRA_COLS],
                                key="fill_col")
    with fc[1]:
        fill_text = st.text_area("Names (comma or newline separated)", key="fill_names")
    with fc[2]:
        fill_mode = st.radio("Pattern", list(FILL_MODES), key="fill_mode")
    with fc[3]:
        if st.button("Fill", key="fill_apply", width="stretch"):
            names = parse_fill_names(fill_text)
            if not names:
                st.warning("Enter at least one name to fill with.")
            else:
                st.session_state[Keys.EXTRA_VALS][fill_col] = expand_pattern(
                    names, dates, FILL_MODES[fill_mode]
                )
                # Drop the editor's stored cell deltas, or stale manual edits
                # would instantly overwrite the fill on the next render.
                st.session_state.pop("extra_cols_editor", None)
                st.rerun()

    editor_df = pd.DataFrame({"Date": dates})
    for name in st.session_state[Keys.EXTRA_COLS]:
        vals = st.session_state[Keys.EXTRA_VALS].get(name, {})
        editor_df[name] = [vals.get(str(d), "") for d in dates]
    edited = st.data_editor(
        editor_df, key="extra_cols_editor", disabled=["Date"], width="stretch"
    )
    for name in st.session_state[Keys.EXTRA_COLS]:
        st.session_state[Keys.EXTRA_VALS][name] = {
            str(d): ("" if v is None else str(v))
            for d, v in zip(list(edited["Date"]), list(edited[name]))
        }
