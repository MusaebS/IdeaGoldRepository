"""Cumulative fairness ledger.

A ledger records each resident's accumulated points across past scheduling
blocks (``total``, ``weekend``, ``night_float``). Feeding it into the next block
lets the optimiser balance *cumulative* load — a resident who carried extra last
block is given a lighter target this block — and the updated ledger is saved for
the block after that.

The ledger only influences the fairness *targets* (computed in
``build_schedule``); the solver itself is unchanged.

Ledger policy (no auto-compensation)
------------------------------------

Pure cumulative balancing has two side effects real rosters usually don't want:

* a resident punished with **extra points** would get a lighter target next
  block (the penalty is "refunded"), and
* a resident whose share was **excused** down (uncompensated leave, rotator
  window, a perk, or a group load factor) would be loaded extra next block to
  "catch up" — including after a perk expires.

:class:`LedgerPolicy` (default: both corrections ON) fixes this at
``update_ledger`` time. The recorded numbers become *fairness-countable*
points: penalty extras are debited from the total dimension, and each
resident is credited the fair-share shortfall their excusals caused, per
dimension. Credits are a pure redistribution (they sum to zero across
residents), so the cumulative pool is only reduced by debited penalties.

Invariant: starting from equal standing, if a block meets its resolved
targets, the adjusted ledger entries come out identical for every resident —
so an identical next block with the excusals/penalties gone yields *equal*
targets (nothing is refunded or repaid). With both toggles off the behaviour
is exactly the historical raw accumulation. Caveats: a binding carryover
clamp makes this approximate for the clamped resident, and unfilled slots are
not an excusal (they reduce earned points but earn no credit).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

from .data_models import InputData
from .fairness import calculate_points
from .points import slot_points
from .weights import availability_weights

__all__ = [
    "DIMENSIONS",
    "LedgerPolicy",
    "DEFAULT_POLICY",
    "empty_ledger",
    "block_adjustments",
    "update_ledger",
    "ledger_to_json",
    "ledger_from_json",
]

DIMENSIONS = ("total", "weekend", "night_float")


@dataclass(frozen=True)
class LedgerPolicy:
    """How ``update_ledger`` treats penalties and excused shortfalls."""

    no_refund_penalties: bool = True
    no_catchup_excused: bool = True


DEFAULT_POLICY = LedgerPolicy()


def empty_ledger() -> Dict[str, Dict[str, float]]:
    return {}


def block_adjustments(prior, data: InputData) -> Dict[str, Dict[str, float]]:
    """Per-resident ledger adjustments for this block's configuration.

    Pure and schedule-free: the credits/debits are *target-side* quantities
    (what the resident's share should have been vs. what it was reduced to),
    so they depend only on the configuration and the prior ledger.

    Returns ``{person: {"penalty": e, "excused_total": c, "excused_weekend": c,
    "excused_night_float": c}}`` — all zero when nothing applies.
    """
    participants = list(data.juniors) + list(data.seniors)
    out: Dict[str, Dict[str, float]] = {
        p: {
            "penalty": 0.0,
            "excused_total": 0.0,
            "excused_weekend": 0.0,
            "excused_night_float": 0.0,
        }
        for p in participants
    }
    n = len(participants)
    if n == 0:
        return out
    prior = prior or {}

    for p, extra in (data.extra_points or {}).items():
        if p in out and extra > 0:
            out[p]["penalty"] = float(extra)

    slots = list(slot_points(data))
    p_tot = sum(s.points for s in slots)
    p_wk = sum(s.points for s in slots if s.weekend)
    p_nf: Dict[str, float] = {}
    for s in slots:
        if s.night_float:
            p_nf[s.shift.role] = p_nf.get(s.shift.role, 0.0) + s.points

    weights = availability_weights(data)
    weight_sum = sum(weights.get(p, 0.0) for p in participants)

    # Penalty scale: resolve_targets lowers everyone's non-penalty share by
    # f = (P - E)/P so the raised targets still reconcile; the excused-total
    # credit must be measured on the same scaled share.
    extra_sum = sum(v["penalty"] for v in out.values())
    f = (p_tot - extra_sum) / p_tot if p_tot > 0 and 0 < extra_sum < p_tot else 1.0

    def _cumulative(dim: str, pool) -> float:
        block = {"total": p_tot, "weekend": p_wk}.get(dim, 0.0)
        return block + sum(float((prior.get(p) or {}).get(dim, 0.0)) for p in pool)

    if weight_sum > 0:
        c_tot = _cumulative("total", participants)
        c_wk = _cumulative("weekend", participants)
        for p in participants:
            gap = 1.0 / n - weights.get(p, 0.0) / weight_sum
            out[p]["excused_total"] = f * c_tot * gap
            out[p]["excused_weekend"] = c_wk * gap

    for role, pool in (("Junior", data.nf_juniors), ("Senior", data.nf_seniors)):
        pool = [p for p in pool if p in out]
        pool_weight = sum(weights.get(p, 0.0) for p in pool)
        if not pool or pool_weight <= 0:
            continue
        c_nf = p_nf.get(role, 0.0) + sum(
            float((prior.get(p) or {}).get("night_float", 0.0)) for p in pool
        )
        for p in pool:
            out[p]["excused_night_float"] = c_nf * (
                1.0 / len(pool) - weights.get(p, 0.0) / pool_weight
            )
    return out


def update_ledger(
    prior, df, data: InputData, *, policy: LedgerPolicy | None = None
) -> Dict[str, Dict[str, Any]]:
    """Return ``prior`` plus the fairness-countable points from this block.

    Residents new this block are added; residents only in ``prior`` are kept so
    history is never lost. ``policy`` (default :data:`DEFAULT_POLICY`) debits
    penalty extras and credits excused shortfalls so they are not compensated
    in later blocks; entries that were adjusted carry a transparent
    ``"adjustments"`` audit sub-dict for this update (old loaders strip it).
    """
    policy = DEFAULT_POLICY if policy is None else policy
    points = calculate_points(df, data)
    updated: Dict[str, Dict[str, Any]] = {
        person: {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        for person, vals in (prior or {}).items()
    }
    adjustments = (
        block_adjustments(prior, data)
        if (policy.no_refund_penalties or policy.no_catchup_excused)
        else {}
    )
    for person, info in points.items():
        entry = updated.setdefault(person, {dim: 0.0 for dim in DIMENSIONS})
        entry["total"] += info.get("total", 0.0)
        entry["weekend"] += info.get("weekend", 0.0)
        entry["night_float"] += info.get("night_float", 0.0)

        adj = adjustments.get(person)
        if not adj:
            continue
        audit: Dict[str, Any] = {}
        if policy.no_refund_penalties and adj["penalty"]:
            entry["total"] -= adj["penalty"]
            audit["penalty_not_carried"] = round(adj["penalty"], 4)
        if policy.no_catchup_excused:
            credits = {
                "total": adj["excused_total"],
                "weekend": adj["excused_weekend"],
                "night_float": adj["excused_night_float"],
            }
            if any(abs(c) > 1e-9 for c in credits.values()):
                for dim, credit in credits.items():
                    entry[dim] += credit
                audit["excused_credit"] = {
                    dim: round(c, 4) for dim, c in credits.items()
                }
        if audit:
            entry["adjustments"] = audit
    return updated


def ledger_to_json(ledger) -> str:
    return json.dumps(ledger or {}, indent=2, sort_keys=True)


def ledger_from_json(text: str) -> Dict[str, Dict[str, float]]:
    raw = json.loads(text)
    return {
        str(person): {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        for person, vals in raw.items()
    }
