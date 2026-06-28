from __future__ import annotations

from typing import List

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import InputData
from .nf_blocks import respects_nf_blocks
from .optimiser import respects_min_gap

__all__ = ["validate_schedule"]


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

    if not respects_min_gap(df, data.min_gap):
        issues.append(f"Minimum gap of {data.min_gap} day(s) is violated")
    if not respects_nf_blocks(df, data.nf_block_length, data.shifts):
        issues.append(
            f"Night-float assignments are not all blocks of length {data.nf_block_length}"
        )
    return issues
