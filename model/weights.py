"""Per-resident load-weight machinery (seniority groups, perks, availability).

A resident's fairness weight for a block is the sum over their *active* days of
a per-day load factor. The factor composes:

- their seniority **group** factor (e.g. every R2 carries 0.9 of an R1's load), and
- any individual **perk** factors whose window covers the day (a perk with no
  dates applies forever; overlapping perks multiply).

Active days keep the long-standing availability rule: a rotator only counts
days inside their window(s), and *uncompensated* leave days don't count
(compensated leave days do — the share is kept).

This module must stay importable without pandas/OR-Tools/Streamlit (it is used
by the ledger under the stub-only CI job).
"""
from __future__ import annotations

from datetime import date
from typing import Dict

from .data_models import (
    InputData,
    blackout_person_windows,
    normalized_leaves,
    normalized_perks,
    normalized_rotators,
)
from .points import block_days

__all__ = ["person_factor", "availability_weights", "reference_weights"]


def person_factor(person: str, day: date, data: InputData) -> float:
    """The load factor for ``person`` on ``day`` (1.0 = a full share)."""
    factor = 1.0
    groups = data.resident_groups or {}
    group_factors = data.group_factors or {}
    group = groups.get(person)
    if group is not None:
        factor *= group_factors.get(group, 1.0)
    for perk in normalized_perks(data.perks):
        if perk.name != person:
            continue
        if perk.start is not None and day < perk.start:
            continue
        if perk.end is not None and day > perk.end:
            continue
        factor *= perk.factor
    return factor


def availability_weights(data: InputData) -> Dict[str, float]:
    """Fairness weight per participant: Σ load factor over their active days.

    With no rotators, no uncompensated leaves, no groups and no perks, every
    weight equals the block length and the targets reduce to an equal split
    (the original behaviour).
    """
    rotator_windows: Dict[str, list] = {}
    for res, start, end in normalized_rotators(data.rotators):
        rotator_windows.setdefault(res, []).append((start, end))
    uncomp_windows: Dict[str, list] = {}
    for name, start, end, compensated in normalized_leaves(data.leaves):
        if not compensated:
            uncomp_windows.setdefault(name, []).append((start, end))
    # Only *uncompensated* blackout windows reduce the share; compensated
    # blackouts (the default) keep the full weight, so the missed load is made
    # up in-block or carried in the ledger as repayable debt — never excused.
    for name, windows in blackout_person_windows(data.blackouts, data.named_groups).items():
        for start, end, compensated in windows:
            if not compensated:
                uncomp_windows.setdefault(name, []).append((start, end))

    days = block_days(data)

    def _weight(person: str) -> float:
        windows = rotator_windows.get(person)
        uncomp = uncomp_windows.get(person, [])
        total = 0.0
        for day in days:
            if windows and not any(s <= day <= e for s, e in windows):
                continue  # outside rotator active window
            if any(s <= day <= e for s, e in uncomp):
                continue  # uncompensated leave day -> quota reduced
            total += person_factor(person, day, data)
        return total

    return {p: _weight(p) for p in data.juniors + data.seniors}


def reference_weights(data: InputData) -> Dict[str, float]:
    """The as-if-fully-available baseline: every participant, every day, factor 1.

    The ledger's no-catch-up policy credits residents against this reference so
    excused reductions (leave, rotator windows, perks, group factors) are not
    repaid in later blocks.
    """
    full = float(len(block_days(data)))
    return {p: full for p in data.juniors + data.seniors}
