from __future__ import annotations

from datetime import timedelta
from typing import List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import InputData, normalized_leaves
from .nf_blocks import respects_nf_blocks
from .optimiser import respects_min_gap

__all__ = ["validate_input", "config_warnings", "validate_schedule"]


def config_warnings(data: InputData) -> List[str]:
    """Return non-blocking advisories about a valid-but-risky configuration.

    Unlike :func:`validate_input` (which blocks solving), these are hints that a
    configuration will probably leave slots unfilled or be hard to satisfy, shown
    so the user can fix the roster before wondering why coverage is poor:

    * a night-float shift whose eligible pool is empty (its nights cannot be
      covered, and with multi-night blocks this can make the model infeasible);
    * more shifts of a role on a single day than there are residents of that role
      (a resident works at most one shift per day, so the surplus is unfillable).
    """
    warnings: List[str] = []

    for shift in data.shifts:
        if shift.night_float:
            pool = data.nf_juniors if shift.role == "Junior" else data.nf_seniors
            if not pool:
                warnings.append(
                    f"Night-float shift '{shift.label}' ({shift.role}) has no "
                    "eligible residents; its nights will be unfilled or, with "
                    "multi-night blocks, the schedule may be infeasible."
                )

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

    warnings.extend(_leave_rotator_warnings(data))
    return warnings


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
    nf_juniors = set(data.nf_juniors)
    nf_seniors = set(data.nf_seniors)

    rotator_windows: dict = {}
    for name, start, end in data.rotators:
        rotator_windows.setdefault(name, []).append((start, end))

    for row in df.to_dict("records"):
        day = row.get("Date")
        assigned_today: List[str] = []
        for shift in data.shifts:
            person = row.get(shift.label)
            if person in (None, "Unfilled"):
                continue
            assigned_today.append(person)

            if shift.role == "Junior" and person not in juniors:
                issues.append(f"{day}: {person} on '{shift.label}' is not a Junior")
            if shift.role == "Senior" and person not in seniors:
                issues.append(f"{day}: {person} on '{shift.label}' is not a Senior")

            if shift.night_float:
                pool = nf_juniors if shift.role == "Junior" else nf_seniors
                if person not in pool:
                    issues.append(
                        f"{day}: {person} on night-float '{shift.label}' is not NF-eligible"
                    )

            for nm, ls, le, _comp in normalized_leaves(data.leaves):
                if nm == person and ls <= day <= le:
                    issues.append(
                        f"{day}: {person} on '{shift.label}' is on leave ({ls} to {le})"
                    )

            windows = rotator_windows.get(person)
            if windows and not any(ws <= day <= we for ws, we in windows):
                issues.append(
                    f"{day}: {person} on '{shift.label}' is outside their rotator window"
                )

        for person in {p for p in assigned_today if assigned_today.count(p) > 1}:
            issues.append(f"{day}: {person} is assigned to more than one shift")

    if not respects_min_gap(df, data.min_gap, data.shifts):
        issues.append(f"Minimum gap of {data.min_gap} day(s) is violated")
    if not respects_nf_blocks(df, data.nf_block_length, data.shifts):
        issues.append(
            f"Night-float assignments are not all blocks of length {data.nf_block_length}"
        )
    return issues
