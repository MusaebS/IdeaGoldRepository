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
from typing import Dict, FrozenSet, List, NamedTuple, Tuple

from .data_models import InputData, ShiftTemplate, normalized_reductions
from .points import block_days, classify_slot
from .utils import weekend_holiday_dates
from .weights import availability_weights

__all__ = [
    "ReductionCap",
    "reduction_caps",
    "reduction_target_relief",
    "eligible_for_shift",
]


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
        pool = data.juniors
    else:
        pool = data.seniors
    if person not in pool:
        return False
    # ``night_float`` means overlay-eligible, not permanently restricted to the
    # NF pool. On dates the overlay does not cover, this is an ordinary regular
    # shift open to the full role roster. NF pools govern overlay assignments.
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
    # Reserved overlay/closure cells carry no regular points and cannot be part
    # of a regular-work reduction. Local imports avoid an import-time cycle.
    from .closures import resolve_closures
    from .night_float import resolve_night_float

    nf_cells, _gaps, _leaves = resolve_night_float(data)
    reserved = set(nf_cells) | resolve_closures(data)

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
        for person in members:
            person_labels = frozenset(
                lbl for lbl in labels
                if _eligible(person, shift_by_label[lbl], data)
            )
            if not person_labels:
                continue  # cannot work these labels anyway
            # Resolve the person's share label-by-label. A reduction may name
            # both Junior and Senior shifts (or labels with different exemption
            # pools); a single union pool would incorrectly make one role absorb
            # the other role's work.
            share = 0.0
            for lbl in person_labels:
                label_pool = [
                    p for p in roster
                    if _eligible(p, shift_by_label[lbl], data)
                ]
                label_pool_weight = sum(weights.get(p, 0.0) for p in label_pool)
                if label_pool_weight <= 0:
                    continue
                label_points = sum(
                    classify_slot(day, shift_by_label[lbl], data, weekend_dates).points
                    for day in days
                    if start <= day <= end and (day, lbl) not in reserved
                )
                share += (
                    label_points
                    * weights.get(person, 0.0)
                    / label_pool_weight
                )
            caps.append(ReductionCap(
                person=person,
                labels=person_labels,
                start=start,
                end=end,
                cap_points=red.factor * share,
                reduce_total=(1.0 - red.factor) * share,
                reduce_nf=0.0,
                factor=red.factor,
                keep_total=red.keep_total,
            ))
    # Exact duplicate import/UI rows must not add repeated constraints or
    # repeated target relief. Preserve first-seen order for diagnostics.
    return list(dict.fromkeys(caps))


def reduction_target_relief(data: InputData) -> Dict[str, float]:
    """Return overlap-normalised relief for ``keep_total=False`` reductions.

    All hard caps remain active, so the tightest applicable cap wins. For target
    relief, however, a regular ``(date, label)`` cell contributes at most once:
    the strongest applicable reduction wins. Duplicate and partially
    overlapping rows therefore cannot lower a resident's total target twice for
    the same work.
    """
    caps = [cap for cap in reduction_caps(data) if not cap.keep_total]
    if not caps:
        return {}

    from .closures import resolve_closures
    from .night_float import resolve_night_float

    nf_cells, _gaps, _leaves = resolve_night_float(data)
    reserved = set(nf_cells) | resolve_closures(data)
    weekend_dates = weekend_holiday_dates(data)
    shift_by_label = {s.label: s for s in data.shifts}
    days = block_days(data) if data.end_date >= data.start_date else []

    weights = availability_weights(data)
    roster = list(data.juniors) + list(data.seniors)
    atom_relief: Dict[Tuple[str, date, str], float] = {}
    for cap in caps:
        if cap.reduce_total <= 0:
            continue
        for label in cap.labels:
            label_pool = [
                person for person in roster
                if _eligible(person, shift_by_label[label], data)
            ]
            label_pool_weight = sum(weights.get(person, 0.0) for person in label_pool)
            if label_pool_weight <= 0:
                continue
            share_fraction = weights.get(cap.person, 0.0) / label_pool_weight
            for day in days:
                if not cap.start <= day <= cap.end or (day, label) in reserved:
                    continue
                points = classify_slot(
                    day, shift_by_label[label], data, weekend_dates
                ).points
                relief = (1.0 - cap.factor) * share_fraction * points
                key = (cap.person, day, label)
                atom_relief[key] = max(atom_relief.get(key, 0.0), relief)

    out: Dict[str, float] = {}
    for (person, _day, _label), relief in atom_relief.items():
        out[person] = out.get(person, 0.0) + relief
    return out
