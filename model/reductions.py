"""Windowed shift-type load reductions, repaid via the fairness ledger.

A :class:`~model.data_models.LoadReduction` caps how much of specific shift
types each covered member may carry inside a window: at most ``factor`` × the
member's fair share of those labels. Assignment-side it is a hard cap (the
solver hands the remainder to others); fairness-side it deliberately never
touches the availability weights, so the ledger's no-catch-up policy issues
no credit — whatever a member cannot earn carries as repayable debt into the
next block's carryover targets (unlike perks and group factors, which are
excused).

The per-entry ``keep_total`` flag picks the in-block behaviour: ``False``
("work less now") also lowers the member's total/night-float targets by the
reduced amount so they genuinely work less this block and repay everything
later; ``True`` ("same monthly share") leaves targets alone, so the solver
compensates them with other shift types now.

This module must stay importable without pandas/OR-Tools/Streamlit (it is
used by the solver, validation, and reporting alike).
"""
from __future__ import annotations

from datetime import date
from typing import FrozenSet, List, NamedTuple

from .data_models import InputData, ShiftTemplate, normalized_reductions
from .points import block_days, classify_slot
from .utils import weekend_holiday_dates
from .weights import availability_weights

__all__ = ["ReductionCap", "reduction_caps", "eligible_for_shift"]


class ReductionCap(NamedTuple):
    """One person's resolved cap on points from ``labels`` inside a window."""

    person: str
    labels: FrozenSet[str]
    start: date  # clipped to the schedule block
    end: date
    cap_points: float    # factor × the person's fair share of these labels
    reduce_total: float  # (1 − factor) × share: the load taken away
    reduce_nf: float     # the night-float-flagged portion of reduce_total
    factor: float
    keep_total: bool


def eligible_for_shift(person: str, shift: ShiftTemplate, data: InputData) -> bool:
    """True if ``person`` may ever work ``shift`` (role, NF pool, exemptions).

    The single eligibility predicate shared by reduction caps, per-label
    fairness targets, and the audit harness, so they can never disagree on
    who is in a shift's pool.
    """
    if shift.role == "Junior":
        pool, nf_pool = data.juniors, data.nf_juniors
    else:
        pool, nf_pool = data.seniors, data.nf_seniors
    if person not in pool:
        return False
    if shift.night_float and person not in nf_pool:
        return False
    return shift.label not in (data.exempt_shifts or {}).get(person, ())


# Back-compat private alias (existing internal callers).
_eligible = eligible_for_shift


def reduction_caps(data: InputData) -> List[ReductionCap]:
    """Resolve every reduction entry into per-person caps.

    The fair share mirrors how the fairness targets are computed: the
    window's points on the reduced labels, split availability-weighted over
    the pool of residents eligible for at least one of those labels. Group
    membership resolves at call time; windows are clipped to the block.
    Overlapping reductions emit multiple caps — all are enforced, so the
    tightest wins.
    """
    entries = list(normalized_reductions(data.reductions))
    if not entries:
        return []
    weights = availability_weights(data)
    named_groups = data.named_groups or {}
    weekend_dates = weekend_holiday_dates(data)
    days = block_days(data) if data.end_date >= data.start_date else []
    shift_by_label = {s.label: s for s in data.shifts}
    roster = list(data.juniors) + list(data.seniors)

    caps: List[ReductionCap] = []
    for red in entries:
        labels = frozenset(lbl for lbl in red.labels if lbl in shift_by_label)
        members = (
            named_groups.get(red.group, ()) if red.group is not None else red.members
        )
        start = max(red.start, data.start_date)
        end = min(red.end, data.end_date)
        if not labels or not members or end < start:
            continue
        window_points = 0.0
        window_nf_points = 0.0
        for day in (d for d in days if start <= d <= end):
            for lbl in labels:
                slot = classify_slot(day, shift_by_label[lbl], data, weekend_dates)
                window_points += slot.points
                if slot.night_float:
                    window_nf_points += slot.points
        pool = [
            p for p in roster
            if any(_eligible(p, shift_by_label[lbl], data) for lbl in labels)
        ]
        pool_weight = sum(weights.get(p, 0.0) for p in pool)
        if pool_weight <= 0:
            continue
        for person in members:
            if person not in pool:
                continue  # cannot work these labels anyway
            share = window_points * weights.get(person, 0.0) / pool_weight
            nf_share = window_nf_points * weights.get(person, 0.0) / pool_weight
            caps.append(ReductionCap(
                person=person,
                labels=labels,
                start=start,
                end=end,
                cap_points=red.factor * share,
                reduce_total=(1.0 - red.factor) * share,
                reduce_nf=(1.0 - red.factor) * nf_share,
                factor=red.factor,
                keep_total=red.keep_total,
            ))
    return caps
