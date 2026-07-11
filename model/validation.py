from __future__ import annotations

from datetime import timedelta
from typing import List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import (
    InputData,
    blackout_night_before_dates,
    blackout_person_windows,
    is_regular_night_call,
    normalized_blackouts,
    normalized_closures,
    normalized_leaves,
    normalized_nf_assignments,
    normalized_nf_coverage,
    normalized_perks,
    normalized_reductions,
)
from .closures import reserved_cell_keys
from .night_float import resolve_night_float
from .optimiser import respects_min_gap
from .points import classify_slot, slot_points
from .reductions import reduction_caps
from .utils import weekend_holiday_dates

__all__ = ["validate_input", "config_warnings", "validate_schedule"]


def config_warnings(data: InputData) -> List[str]:
    """Return non-blocking advisories about a valid-but-risky configuration.

    Unlike :func:`validate_input` (which blocks solving), these are hints that a
    configuration will probably leave slots unfilled or be hard to satisfy, shown
    so the user can fix the roster before wondering why coverage is poor:

    * a night-float shift whose eligible pool is empty (its nights cannot be
      covered, and with multi-night blocks this can make the model infeasible);
    * more shifts of a role on a single day than there are residents of that role
      (a resident works at most one shift per day, so the surplus is unfillable);
    * block-level capacity facts (see :func:`_capacity_warnings`): min_gap
      shift ceilings vs slot counts, the weekly-rhythm weekend lock, and
      structural per-head workload gaps between the roles.
    """
    warnings: List[str] = []

    role_people = {"Junior": len(data.juniors), "Senior": len(data.seniors)}
    role_shifts: dict = {}
    for shift in data.shifts:
        role_shifts[shift.role] = role_shifts.get(shift.role, 0) + 1
    for role, n_shifts in role_shifts.items():
        n_people = role_people.get(role, 0)
        if n_shifts > n_people:
            warnings.append(
                f"{n_shifts} {role} shift(s) per day but only {n_people} "
                f"{role.lower()}(s); at least {n_shifts - n_people} slot(s) will "
                "be unfilled each day."
            )

    max_total = data.max_total or {}
    for name, extra in (data.extra_points or {}).items():
        if extra > 0 and name in max_total and max_total[name] < extra:
            warnings.append(
                f"'{name}' has {extra:g} extra points but a max-total cap of "
                f"{max_total[name]:g}; the penalty can't fit under the cap and the "
                "schedule will be infeasible."
            )

    for h_date, _bonus, _weekend in (data.holidays or []):
        if h_date < data.start_date or h_date > data.end_date:
            warnings.append(
                f"Holiday {h_date} is outside the schedule dates "
                f"({data.start_date}–{data.end_date}) and has no effect."
            )

    warnings.extend(_capacity_warnings(data))
    warnings.extend(_leave_rotator_warnings(data))
    warnings.extend(_exemption_perk_warnings(data))
    warnings.extend(_blackout_warnings(data))
    warnings.extend(_reduction_warnings(data))
    warnings.extend(_preference_warnings(data))
    warnings.extend(_avoid_pair_warnings(data))
    warnings.extend(_night_float_warnings(data))
    return warnings


def _capacity_warnings(data: InputData) -> List[str]:
    """Structural supply-vs-demand advisories for the whole block.

    These are the facts behind "why is my schedule uneven": how many shifts a
    resident can physically work under ``min_gap``, whether a role's roster can
    cover its slots at all, whether the rest rule locks everyone onto the same
    weekday (making weekend fairness impossible), and whether the two roles'
    per-head workloads differ structurally (totals balance *within* each role,
    so a cross-role gap is a roster/shift-mix fact, not solver unfairness).
    Counts are raw demand — closures and night-float coverage reduce it, so a
    tight verdict errs on the cautious side.
    """
    out: List[str] = []
    days = (data.end_date - data.start_date).days + 1
    if days <= 0 or not data.shifts:
        return out
    gap = max(0, int(data.min_gap))
    # With a gap of g, shifts are at least g+1 days apart: at most this many
    # fit in the block.
    per_person_max = -(-days // (gap + 1))  # ceil

    role_people = {"Junior": len(data.juniors), "Senior": len(data.seniors)}
    role_slots = {"Junior": 0, "Senior": 0}
    for shift in data.shifts:
        role_slots[shift.role] = role_slots.get(shift.role, 0) + days
    for role, slots in role_slots.items():
        heads = role_people.get(role, 0)
        if not slots or not heads:
            continue  # the per-day supply warning already covers heads == 0
        cover = heads * per_person_max
        if cover < slots:
            out.append(
                f"min_gap {gap} lets each resident work at most {per_person_max} "
                f"shift(s) in {days} days, so {heads} {role.lower()}(s) can cover at "
                f"most {cover} of the {slots} {role} slot(s) — at least "
                f"{slots - cover} will be unfilled. Lower min_gap or add "
                f"{role.lower()}s."
            )
        elif cover < slots * 1.1:
            out.append(
                f"Capacity is very tight for {role.lower()}s: min_gap {gap} allows "
                f"{cover} shift(s) across {heads} {role.lower()}(s) for {slots} "
                f"slot(s). Expect unfilled slots or an uneven spread; lowering "
                "min_gap gives the optimiser room to balance."
            )

    if gap >= 1 and (gap + 1) % 7 == 0 and days >= 21:
        out.append(
            f"min_gap {gap} forces a {gap + 1}-day rhythm: every resident repeats "
            "the same weekday all block, so whoever starts on a Saturday works "
            "every Saturday and weekend fairness is impossible. Use a smaller "
            "min_gap (e.g. ≤ 3) and let the point balance spread the load."
        )

    # Per-head workload by role (points, so overrides/multipliers count).
    role_points = {"Junior": 0.0, "Senior": 0.0}
    for slot in slot_points(data):
        role_points[slot.shift.role] += slot.points
    averages = {
        role: role_points[role] / role_people[role]
        for role in ("Junior", "Senior")
        if role_people.get(role) and role_points[role]
    }
    if len(averages) == 2 and abs(averages["Junior"] - averages["Senior"]) > 1.0:
        out.append(
            f"Structural workload difference between roles: juniors average "
            f"≈{averages['Junior']:.1f} points each, seniors ≈{averages['Senior']:.1f}. "
            "Totals are balanced within each role; to narrow the cross-role gap, "
            "change the roster sizes or the shift mix."
        )
    return out


def _night_float_warnings(data: InputData) -> List[str]:
    """Advisories for the night-float overlay configuration."""
    out: List[str] = []
    covered_labels = {c.label for c in normalized_nf_coverage(data.nf_coverage)}
    for shift in data.shifts:
        if shift.night_float and shift.label not in covered_labels:
            out.append(
                f"Shift '{shift.label}' is night-float-eligible but has no coverage "
                "pattern; it will be scheduled entirely as a regular shift."
            )

    if not data.nf_coverage and not data.nf_assignments:
        return out

    # Covered dates with no assigned coverer fall back to regular scheduling.
    _cells, gap_slots, _leaves = resolve_night_float(data)
    if gap_slots:
        shown = ", ".join(f"{d} '{lbl}'" for d, lbl in sorted(gap_slots)[:5])
        more = f" (+{len(gap_slots) - 5} more)" if len(gap_slots) > 5 else ""
        out.append(
            f"Night-float-covered slots have no assigned coverer and fall back to "
            f"regular scheduling: {shown}{more}."
        )

    for a in normalized_nf_assignments(data.nf_assignments, default_rest=data.nf_rest_days):
        if a.end < data.start_date or a.start > data.end_date:
            out.append(
                f"Night-float assignment for '{a.name}' ({a.start}–{a.end}) is "
                f"outside the schedule dates ({data.start_date}–{data.end_date})."
            )
    return out


def _avoid_pair_warnings(data: InputData) -> List[str]:
    """Advisories for avoid pairs that will visibly hurt coverage."""
    out: List[str] = []
    role_shifts: dict = {}
    for shift in data.shifts:
        role_shifts[shift.role] = role_shifts.get(shift.role, 0) + 1
    for pair in (data.avoid_pairs or []):
        first, second = pair[0], pair[1]
        for role, people in (("Junior", data.juniors), ("Senior", data.seniors)):
            if role_shifts.get(role) and set(people) == {first, second}:
                out.append(
                    f"Avoid pair '{first}' / '{second}' are the only "
                    f"{role.lower()}s; at most one can work per day, so expect "
                    "unfilled slots."
                )
    return out


def _preference_warnings(data: InputData) -> List[str]:
    """Advisories for preferences that can never take effect."""
    out: List[str] = []
    shift_by_label = {s.label: s for s in data.shifts}
    exempt = data.exempt_shifts or {}
    for name, labels in (data.preferred_shifts or {}).items():
        for label in labels:
            shift = shift_by_label.get(label)
            if shift is None:
                continue  # unknown labels are validate_input errors
            pool = set(data.juniors if shift.role == "Junior" else data.seniors)
            if shift.night_float:
                pool &= set(data.nf_juniors if shift.role == "Junior" else data.nf_seniors)
            if name not in pool or label in exempt.get(name, ()):
                out.append(
                    f"'{name}' prefers '{label}' but can never work it (role, "
                    "night-float eligibility, or exemption); the preference "
                    "has no effect."
                )
    return out


def _reduction_warnings(data: InputData) -> List[str]:
    """Advisories for load reductions that are likely mistakes or coverage risks."""
    out: List[str] = []
    if not data.reductions:
        return out
    named_groups = data.named_groups or {}
    start, end = data.start_date, data.end_date

    for red in normalized_reductions(data.reductions):
        who = f"group '{red.group}'" if red.group is not None else "ad-hoc reduction"
        if red.factor >= 1.0:
            out.append(
                f"Reduction {red.start}–{red.end} for {who} has a 100% load "
                "factor and has no effect."
            )
        if red.group is not None and not named_groups.get(red.group):
            out.append(
                f"Reduction {red.start}–{red.end} references empty group "
                f"'{red.group}' and has no effect."
            )
        if red.end < start or red.start > end:
            out.append(
                f"Reduction window {red.start}–{red.end} for {who} is outside "
                f"the schedule dates ({start}–{end}) and has no effect."
            )

    # Coverage risk: a label whose whole eligible pool is under a factor-0
    # reduction on some day cannot be assigned there at all.
    zero_caps: dict = {}
    for cap in reduction_caps(data):
        if cap.factor <= 0:
            for label in cap.labels:
                zero_caps.setdefault(label, []).append(cap)
    for shift in data.shifts:
        caps = zero_caps.get(shift.label)
        if not caps:
            continue
        pool = set(data.juniors if shift.role == "Junior" else data.seniors)
        if shift.night_float:
            pool &= set(data.nf_juniors if shift.role == "Junior" else data.nf_seniors)
        pool = {p for p in pool if shift.label not in (data.exempt_shifts or {}).get(p, ())}
        if not pool:
            continue
        uncovered_day = next(
            (
                d
                for d in _block_days_safe(data)
                if all(
                    any(c.person == p and c.start <= d <= c.end for c in caps)
                    for p in pool
                )
            ),
            None,
        )
        if uncovered_day is not None:
            out.append(
                f"Every resident eligible for '{shift.label}' is fully reduced "
                f"on {uncovered_day}; those slots will be unfilled."
            )
    return out


def _block_days_safe(data: InputData) -> list:
    from .points import block_days

    return block_days(data) if data.end_date >= data.start_date else []


def _unavailable_person_days(data: InputData) -> dict:
    """Person -> set of block days they cannot work (leave/rotator/blackout).

    The advisory-side mirror of the solver's blocking, kept in dates rather
    than indices so warnings can name the days. Blackout windows count as
    whole days here even though they spare night-float slots — a slight
    overcount that is fine for an advisory (the night-before partial block is
    ignored for the same reason).
    """
    from .points import block_days

    rotator_windows: dict = {}
    for name, ws, we in data.rotators:
        rotator_windows.setdefault(name, []).append((ws, we))
    blocked_windows: dict = {}
    for name, ws, we, _c in normalized_leaves(data.leaves):
        blocked_windows.setdefault(name, []).append((ws, we))
    for name, windows in blackout_person_windows(data.blackouts, data.named_groups).items():
        for ws, we, _c in windows:
            blocked_windows.setdefault(name, []).append((ws, we))

    days = block_days(data) if data.end_date >= data.start_date else []
    out: dict = {}
    for person in list(data.juniors) + list(data.seniors):
        rwins = rotator_windows.get(person)
        bwins = blocked_windows.get(person, [])
        unavailable = {
            d
            for d in days
            if (rwins and not any(ws <= d <= we for ws, we in rwins))
            or any(ws <= d <= we for ws, we in bwins)
        }
        if unavailable:
            out[person] = unavailable
    return out


def _blackout_warnings(data: InputData) -> List[str]:
    """Advisories for group blackouts that are likely mistakes or coverage risks."""
    out: List[str] = []
    if not data.blackouts:
        return out
    named_groups = data.named_groups or {}
    start, end = data.start_date, data.end_date

    for b in normalized_blackouts(data.blackouts):
        who = f"group '{b.group}'" if b.group is not None else "ad-hoc blackout"
        if b.group is not None and not named_groups.get(b.group):
            out.append(
                f"Blackout {b.start}–{b.end} references empty group '{b.group}' "
                "and has no effect."
            )
        night_date = b.start - timedelta(days=1)
        window_outside = b.end < start or b.start > end
        night_outside = (not b.night_before) or night_date < start or night_date > end
        if window_outside and night_outside:
            out.append(
                f"Blackout window {b.start}–{b.end} for {who} is outside the "
                f"schedule dates ({start}–{end}) and has no effect."
            )

    unavailable = _unavailable_person_days(data)

    # Coverage risk: a day where blackouts (plus leaves/rotator windows) leave
    # fewer available residents of a role than that role has shifts.
    from .points import block_days

    days = block_days(data) if end >= start else []
    role_shifts = {"Junior": 0, "Senior": 0}
    for shift in data.shifts:
        role_shifts[shift.role] = role_shifts.get(shift.role, 0) + 1
    for role, people in (("Junior", data.juniors), ("Senior", data.seniors)):
        n_shifts = role_shifts.get(role, 0)
        if not n_shifts:
            continue
        short_days = [
            d
            for d in days
            if sum(1 for p in people if d not in unavailable.get(p, ())) < n_shifts
        ]
        if short_days:
            shown = ", ".join(str(d) for d in short_days[:5])
            more = f" (+{len(short_days) - 5} more)" if len(short_days) > 5 else ""
            out.append(
                f"Blackouts/leaves leave fewer available {role.lower()}s than "
                f"{role} shifts on {shown}{more}; expect unfilled slots."
            )

    # Whole-block compensated blackout: full share kept but no days to earn it.
    comp_windows: dict = {}
    for name, windows in blackout_person_windows(data.blackouts, data.named_groups).items():
        for ws, we, comp in windows:
            if comp:
                comp_windows.setdefault(name, []).append((ws, we))
    for name, windows in comp_windows.items():
        if days and all(any(ws <= d <= we for ws, we in windows) for d in days):
            out.append(
                f"'{name}' is in a compensated blackout for the whole block; "
                "their full fair share is kept but cannot be earned, so expect "
                "a large fairness deviation."
            )
            if (data.extra_points or {}).get(name, 0.0) > 0:
                out.append(
                    f"'{name}' has mandatory extra points but is blacked out for "
                    "the whole block; the penalty cannot fit and the schedule "
                    "will be infeasible."
                )
    return out


def _exemption_perk_warnings(data: InputData) -> List[str]:
    """Advisories for exemptions/perks that are likely mistakes."""
    out: List[str] = []
    exempt = data.exempt_shifts or {}

    for shift in data.shifts:
        pool = set(data.juniors if shift.role == "Junior" else data.seniors)
        if shift.night_float:
            pool &= set(data.nf_juniors if shift.role == "Junior" else data.nf_seniors)
        if not pool:
            continue  # empty-pool cases are covered by the NF advisory above
        remaining = {p for p in pool if shift.label not in exempt.get(p, ())}
        if not remaining:
            out.append(
                f"Every eligible resident is exempt from '{shift.label}'; it "
                "will always be unfilled."
            )

    role_labels: dict = {"Junior": set(), "Senior": set()}
    for shift in data.shifts:
        role_labels[shift.role].add(shift.label)
    for name, labels in exempt.items():
        role = "Junior" if name in data.juniors else "Senior"
        own = role_labels.get(role, set())
        if own and own <= set(labels):
            out.append(
                f"'{name}' is exempt from every {role} shift but keeps a full "
                "fairness target; expect a large deviation (add a perk if their "
                "share should be lower)."
            )

    nf_pools = {"Junior": set(data.nf_juniors), "Senior": set(data.nf_seniors)}
    for shift in data.shifts:
        if not shift.night_float:
            continue
        for name in nf_pools.get(shift.role, ()):  # NF-eligible yet exempt: pick one
            if shift.label in exempt.get(name, ()):
                out.append(
                    f"'{name}' is night-float eligible but exempt from "
                    f"'{shift.label}'; remove one of the two."
                )

    for perk in normalized_perks(data.perks):
        start = perk.start or data.start_date
        end = perk.end or data.end_date
        if end < data.start_date or start > data.end_date:
            out.append(
                f"{perk.name}'s perk window {perk.start}–{perk.end} is outside "
                f"the schedule dates ({data.start_date}–{data.end_date}) and has "
                "no effect this block."
            )
    return out


def _leave_rotator_warnings(data: InputData) -> List[str]:
    """Advisories about leave / rotator windows that are likely mistakes."""
    out: List[str] = []
    start, end = data.start_date, data.end_date
    block_days = (
        [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if end >= start
        else []
    )

    leaves3 = [(n, s, e) for n, s, e, _c in normalized_leaves(data.leaves)]

    # Windows that fall entirely outside the schedule dates do nothing.
    for kind, windows in (("leave", leaves3), ("rotator", data.rotators)):
        for name, ws, we in windows:
            if we < start or ws > end:
                out.append(
                    f"{name}'s {kind} window {ws}–{we} is outside the schedule "
                    f"dates ({start}–{end}) and has no effect."
                )

    rotator_windows: dict = {}
    for name, ws, we in data.rotators:
        rotator_windows.setdefault(name, []).append((ws, we))
    leave_windows: dict = {}
    for name, ws, we in leaves3:
        leave_windows.setdefault(name, []).append((ws, we))

    # A rotator with no active day in the block is fully excluded.
    for name, windows in rotator_windows.items():
        if block_days and not any(
            any(ws <= d <= we for ws, we in windows) for d in block_days
        ):
            out.append(
                f"Rotator '{name}' has no active days in the block and will not "
                "be scheduled."
            )

    # Compensated leave covering the whole block keeps full quota with no days to
    # earn it, so the resident is guaranteed a large deviation. (An uncompensated
    # whole-block leave just zeroes their quota, which is expected, so no warning.)
    comp_leave_windows: dict = {}
    for name, ws, we, comp in normalized_leaves(data.leaves):
        if comp:
            comp_leave_windows.setdefault(name, []).append((ws, we))
    for name, windows in comp_leave_windows.items():
        if block_days and all(
            any(ws <= d <= we for ws, we in windows) for d in block_days
        ):
            out.append(
                f"'{name}' has compensated leave for the whole block; their full "
                "fair share is kept but cannot be earned, so expect a large "
                "fairness deviation."
            )

    # A rotator's leave that never overlaps their active window is redundant.
    for name, lwins in leave_windows.items():
        rwins = rotator_windows.get(name)
        if not rwins:
            continue
        for ws, we in lwins:
            overlaps = any(
                ws <= d <= we and any(rs <= d <= re for rs, re in rwins)
                for d in block_days
            )
            if not overlaps:
                out.append(
                    f"'{name}' has a leave {ws}–{we} outside their rotator active "
                    "window, so it has no effect."
                )

    return out


def validate_input(data: InputData) -> List[str]:
    """Return human-readable problems with a configuration *before* solving.

    An empty list means the configuration is well-formed. These checks catch the
    silent misconfigurations that otherwise produce confusing or wrong output: an
    empty/backwards date range, no shifts, duplicate shift labels (which collapse
    to a single schedule column and drop assignments), night-float eligibility
    listing people who are not in the roster, a name in both the junior and senior
    lists, and leave/rotator windows that reference unknown people or run
    backwards.
    """
    issues: List[str] = []

    if data.end_date < data.start_date:
        issues.append(
            f"End date ({data.end_date}) is before start date ({data.start_date})."
        )

    if not data.shifts:
        issues.append("Add at least one shift template.")

    seen_labels = set()
    for sh in data.shifts:
        if sh.label in seen_labels:
            issues.append(
                f"Duplicate shift label '{sh.label}': labels must be unique "
                "(two shifts sharing a label would overwrite each other)."
            )
        if sh.label in {"Date", "Day"}:
            issues.append(
                f"Shift label '{sh.label}' is reserved (the schedule grid uses "
                "'Date' and 'Day' columns); rename the shift."
            )
        if not sh.label.strip():
            issues.append("A shift has a blank label; give every shift a name.")
        seen_labels.add(sh.label)

    juniors = set(data.juniors)
    seniors = set(data.seniors)
    roster = juniors | seniors

    for name in juniors & seniors:
        issues.append(f"'{name}' is listed as both a Junior and a Senior.")

    for label, names in (("Junior", data.juniors), ("Senior", data.seniors)):
        for name in sorted({n for n in names if names.count(n) > 1}):
            issues.append(f"'{name}' is listed more than once in {label}s.")

    for name in data.nf_juniors:
        if name not in juniors:
            issues.append(
                f"Night-float-eligible junior '{name}' is not in the Juniors list."
            )
    for name in data.nf_seniors:
        if name not in seniors:
            issues.append(
                f"Night-float-eligible senior '{name}' is not in the Seniors list."
            )

    leave_windows3 = [(n, s, e) for n, s, e, _c in normalized_leaves(data.leaves)]
    for kind, windows in (("leave", leave_windows3), ("rotator", data.rotators)):
        for name, start, end in windows:
            if name not in roster:
                issues.append(
                    f"{kind.capitalize()} window references unknown resident '{name}'."
                )
            if end < start:
                issues.append(
                    f"{kind.capitalize()} window for '{name}' ends ({end}) before it "
                    f"starts ({start})."
                )

    if data.min_gap < 0:
        issues.append("Minimum gap cannot be negative.")
    if data.nf_block_length < 1:
        issues.append("Night-float block length must be at least 1.")

    for label, caps in (("total", data.max_total), ("night-float", data.max_nights)):
        for name, value in (caps or {}).items():
            if name not in roster:
                issues.append(f"Max {label} cap references unknown resident '{name}'.")
            if value < 0:
                issues.append(f"Max {label} cap for '{name}' cannot be negative.")

    for name, value in (data.extra_points or {}).items():
        if name not in roster:
            issues.append(f"Extra points reference unknown resident '{name}'.")
        if value < 0:
            issues.append(f"Extra points for '{name}' cannot be negative.")

    shift_labels = {s.label for s in data.shifts}
    for (label, weekday), _pts in (data.weekday_points or {}).items():
        if label not in shift_labels:
            issues.append(f"Weekday point override references unknown shift '{label}'.")
        if not 0 <= weekday <= 6:
            issues.append(
                f"Weekday point override for '{label}' has an invalid weekday "
                f"{weekday} (expected 0=Mon .. 6=Sun)."
            )

    group_factors = data.group_factors or {}
    for group, factor in group_factors.items():
        if not 0 < factor <= 2.0:
            issues.append(
                f"Group '{group}' load factor must be > 0 and ≤ 2 (got {factor:g})."
            )
    for name, group in (data.resident_groups or {}).items():
        if name not in roster:
            issues.append(f"Group assignment references unknown resident '{name}'.")
        if group not in group_factors:
            issues.append(
                f"'{name}' is assigned to undefined group '{group}'; define the "
                "group and its load factor first."
            )

    for perk in normalized_perks(data.perks):
        if perk.name not in roster:
            issues.append(f"Perk references unknown resident '{perk.name}'.")
        if not 0 < perk.factor <= 2.0:
            issues.append(
                f"Perk load factor for '{perk.name}' must be > 0 and ≤ 2 "
                f"(got {perk.factor:g})."
            )
        if perk.start is not None and perk.end is not None and perk.end < perk.start:
            issues.append(
                f"Perk window for '{perk.name}' ends ({perk.end}) before it "
                f"starts ({perk.start})."
            )

    for name, labels in (data.exempt_shifts or {}).items():
        if name not in roster:
            issues.append(f"Shift exemption references unknown resident '{name}'.")
        for label in labels:
            if label not in shift_labels:
                issues.append(
                    f"'{name}' is exempted from unknown shift '{label}'."
                )

    for group, members in (data.named_groups or {}).items():
        if not str(group).strip():
            issues.append("A named group has a blank name; give every group a name.")
        for member in members:
            if member not in roster:
                issues.append(
                    f"Group '{group}' lists unknown resident '{member}'."
                )

    named_groups = data.named_groups or {}
    for b in normalized_blackouts(data.blackouts):
        if b.group is not None and b.group not in named_groups:
            issues.append(f"Blackout references undefined group '{b.group}'.")
        if b.group is None:
            if not b.members:
                issues.append(
                    f"Blackout {b.start}–{b.end} has no group and no members."
                )
            for member in b.members:
                if member not in roster:
                    issues.append(
                        f"Blackout references unknown resident '{member}'."
                    )
        if b.end < b.start:
            who = f"group '{b.group}'" if b.group is not None else "ad-hoc members"
            issues.append(
                f"Blackout window for {who} ends ({b.end}) before it starts ({b.start})."
            )

    for red in normalized_reductions(data.reductions):
        who = f"group '{red.group}'" if red.group is not None else "ad-hoc members"
        if not 0.0 <= red.factor <= 1.0:
            issues.append(
                f"Reduction factor for {who} must be between 0 and 1 "
                f"(got {red.factor:g})."
            )
        if not red.labels:
            issues.append(f"Reduction for {who} names no shift types.")
        for label in red.labels:
            if label not in shift_labels:
                issues.append(f"Reduction for {who} references unknown shift '{label}'.")
        if red.group is not None and red.group not in named_groups:
            issues.append(f"Reduction references undefined group '{red.group}'.")
        if red.group is None:
            if not red.members:
                issues.append(
                    f"Reduction {red.start}–{red.end} has no group and no members."
                )
            for member in red.members:
                if member not in roster:
                    issues.append(
                        f"Reduction references unknown resident '{member}'."
                    )
        if red.end < red.start:
            issues.append(
                f"Reduction window for {who} ends ({red.end}) before it starts "
                f"({red.start})."
            )

    for name, labels in (data.preferred_shifts or {}).items():
        if name not in roster:
            issues.append(f"Shift preference references unknown resident '{name}'.")
        for label in labels:
            if label not in shift_labels:
                issues.append(f"'{name}' prefers unknown shift '{label}'.")
    for name, day_kind in (data.preferred_day_type or {}).items():
        if name not in roster:
            issues.append(
                f"Day-type preference references unknown resident '{name}'."
            )
        if day_kind not in ("weekend", "weekday"):
            issues.append(
                f"Day-type preference for '{name}' must be 'weekend' or "
                f"'weekday' (got '{day_kind}')."
            )

    for pair in (data.avoid_pairs or []):
        first, second = pair[0], pair[1]
        for name in (first, second):
            if name not in roster:
                issues.append(f"Avoid pair references unknown resident '{name}'.")
        if first == second:
            issues.append(f"Avoid pair lists '{first}' with themselves.")

    nf_labels = {s.label for s in data.shifts if s.night_float}
    for cov in normalized_nf_coverage(data.nf_coverage):
        if cov.label not in shift_labels:
            issues.append(f"Night-float coverage references unknown shift '{cov.label}'.")
        elif cov.label not in nf_labels:
            issues.append(
                f"Night-float coverage set for '{cov.label}', which is not marked "
                "night-float-eligible."
            )
        for wd in cov.weekdays:
            if not 0 <= wd <= 6:
                issues.append(
                    f"Night-float coverage for '{cov.label}' has an invalid weekday "
                    f"{wd} (expected 0=Mon .. 6=Sun)."
                )
    nf_pool = set(data.nf_juniors) | set(data.nf_seniors)
    nf_label_role = {s.label: s.role for s in data.shifts if s.night_float}
    for a in normalized_nf_assignments(data.nf_assignments, default_rest=data.nf_rest_days):
        coverer_role = (
            "Junior" if a.name in juniors else "Senior" if a.name in seniors else None
        )
        if a.name not in roster:
            issues.append(f"Night-float assignment references unknown resident '{a.name}'.")
        elif a.name not in nf_pool:
            issues.append(
                f"'{a.name}' has a night-float assignment but is not marked "
                "night-float-eligible."
            )
        if a.end < a.start:
            issues.append(
                f"Night-float assignment for '{a.name}' ends ({a.end}) before it "
                f"starts ({a.start})."
            )
        if a.rest_days < 0:
            issues.append(f"Night-float rest days for '{a.name}' cannot be negative.")
        for label in a.labels:
            if label not in nf_labels:
                issues.append(
                    f"Night-float assignment for '{a.name}' names '{label}', which "
                    "is not a night-float-eligible shift."
                )
            elif coverer_role is not None and nf_label_role.get(label) != coverer_role:
                issues.append(
                    f"Night-float assignment for '{a.name}' ({coverer_role}) names "
                    f"'{label}', a {nf_label_role[label]} night-float shift; a "
                    "coverer can only cover their own role's shifts."
                )

    for c in normalized_closures(data.closures):
        if c.label not in shift_labels:
            issues.append(
                f"Shift closure names '{c.label}', which is not a configured shift."
            )
        if c.end < c.start:
            issues.append(
                f"Shift closure for '{c.label}' ends ({c.end}) before it starts "
                f"({c.start})."
            )
        for wd in c.weekdays:
            if not 0 <= wd <= 6:
                issues.append(
                    f"Shift closure for '{c.label}' has an invalid weekday {wd} "
                    "(expected 0=Mon .. 6=Sun)."
                )

    return issues


def validate_schedule(df: "pd.DataFrame", data: InputData) -> List[str]:
    """Return human-readable constraint violations for a schedule.

    Intended for revalidating a schedule after manual edits. An empty list means
    the schedule satisfies every hard rule the solver enforces: role and
    night-float eligibility, leaves, rotator windows, one shift per person per
    day, the minimum gap, and night-float block length.
    """
    issues: List[str] = []
    juniors = set(data.juniors)
    seniors = set(data.seniors)

    rotator_windows: dict = {}
    for name, start, end in data.rotators:
        rotator_windows.setdefault(name, []).append((start, end))
    blackout_windows = blackout_person_windows(data.blackouts, data.named_groups)
    night_before = blackout_night_before_dates(data.blackouts, data.named_groups)
    # Reserved cells (night-float overlay + closed) are not regular assignments —
    # the regular rules below don't apply to them.
    reserved = reserved_cell_keys(df)

    for row in df.to_dict("records"):
        day = row.get("Date")
        day_key = day.isoformat() if hasattr(day, "isoformat") else day
        assigned_today: List[str] = []
        for shift in data.shifts:
            if (day_key, shift.label) in reserved:
                continue  # NF overlay or closed cell — skip regular-rule checks
            person = row.get(shift.label)
            if person in (None, "Unfilled"):
                continue
            assigned_today.append(person)

            if shift.role == "Junior" and person not in juniors:
                issues.append(f"{day}: {person} on '{shift.label}' is not a Junior")
            if shift.role == "Senior" and person not in seniors:
                issues.append(f"{day}: {person} on '{shift.label}' is not a Senior")

            if shift.label in (data.exempt_shifts or {}).get(person, ()):
                issues.append(
                    f"{day}: {person} on '{shift.label}' is exempt from this shift"
                )

            for nm, ls, le, _comp in normalized_leaves(data.leaves):
                if nm == person and ls <= day <= le:
                    issues.append(
                        f"{day}: {person} on '{shift.label}' is on leave ({ls} to {le})"
                    )

            for bs, be, _comp in blackout_windows.get(person, ()):
                if bs <= day <= be:
                    issues.append(
                        f"{day}: {person} on '{shift.label}' is in a group "
                        f"blackout ({bs} to {be})"
                    )
            if is_regular_night_call(day, shift, data) and day in night_before.get(person, ()):
                issues.append(
                    f"{day}: {person} on night call '{shift.label}' the day "
                    "before their group blackout (would be post-call on an "
                    "off day)"
                )

            windows = rotator_windows.get(person)
            if windows and not any(ws <= day <= we for ws, we in windows):
                issues.append(
                    f"{day}: {person} on '{shift.label}' is outside their rotator window"
                )

        for person in {p for p in assigned_today if assigned_today.count(p) > 1}:
            issues.append(f"{day}: {person} is assigned to more than one shift")

        for pair in (data.avoid_pairs or []):
            first, second = pair[0], pair[1]
            if first != second and first in assigned_today and second in assigned_today:
                issues.append(
                    f"{day}: {first} and {second} are both on call (avoid pair)"
                )

    # Reduced-shift caps: recompute each member's window points on the reduced
    # labels so a manual edit cannot silently exceed the cap.
    records = df.to_dict("records")
    shift_by_label = {s.label: s for s in data.shifts}
    weekend_dates = weekend_holiday_dates(data)
    for cap in reduction_caps(data):
        actual = 0.0
        for row in records:
            day = row.get("Date")
            if day is None or not cap.start <= day <= cap.end:
                continue
            for label in cap.labels:
                if row.get(label) == cap.person:
                    actual += classify_slot(day, shift_by_label[label], data, weekend_dates).points
        if actual > cap.cap_points + 1e-6:
            labels = ", ".join(sorted(cap.labels))
            issues.append(
                f"{cap.person} carries {actual:.1f} points on reduced shift(s) "
                f"{labels} in {cap.start}–{cap.end} (cap {cap.cap_points:.1f})"
            )

    if not respects_min_gap(df, data.min_gap, data.shifts):
        issues.append(f"Minimum gap of {data.min_gap} day(s) is violated")
    return issues
