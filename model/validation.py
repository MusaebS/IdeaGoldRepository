from __future__ import annotations

from typing import List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import InputData
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

    return warnings


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

    for kind, windows in (("leave", data.leaves), ("rotator", data.rotators)):
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

            for nm, ls, le in data.leaves:
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
