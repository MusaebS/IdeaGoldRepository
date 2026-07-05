"""Cumulative fairness ledger.

A ledger records each resident's accumulated points across past scheduling
blocks (``total``, ``weekend``, ``night_float``). Feeding it into the next block
lets the optimiser balance *cumulative* load — a resident who carried extra last
block is given a lighter target this block — and the updated ledger is saved for
the block after that.

Alongside the three carryover dimensions, each entry also accumulates an
informational per-shift-type history: ``"labels"`` (points per label) and
``"label_counts"`` (call counts per label). These feed the fairness table's
cumulative "which calls" view; carryover balancing itself stays on the three
dimensions. Old ledger files without them load unchanged, and old app
versions simply strip the extra keys.

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
from .fairness import calculate_label_counts, calculate_points
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
    "ledger_to_rows",
    "rows_to_ledger",
]

DIMENSIONS = ("total", "weekend")


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

    Returns ``{person: {"penalty": e, "excused_total": c, "excused_weekend": c}}``
    — all zero when nothing applies.
    """
    from .night_float import resolve_night_float  # local: avoids a module cycle

    participants = list(data.juniors) + list(data.seniors)
    out: Dict[str, Dict[str, float]] = {
        p: {"penalty": 0.0, "excused_total": 0.0, "excused_weekend": 0.0}
        for p in participants
    }
    n = len(participants)
    if n == 0:
        return out
    prior = prior or {}

    for p, extra in (data.extra_points or {}).items():
        if p in out and extra > 0:
            out[p]["penalty"] = float(extra)

    # Regular demand only — night-float-covered cells are outside the point pool.
    nf_cells, _gaps, _leaves = resolve_night_float(data)
    slots = [s for s in slot_points(data) if (s.day, s.shift.label) not in nf_cells]
    p_tot = sum(s.points for s in slots)
    p_wk = sum(s.points for s in slots if s.weekend)

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
    label_counts = calculate_label_counts(df, data)
    updated: Dict[str, Dict[str, Any]] = {}
    for person, vals in (prior or {}).items():
        entry: Dict[str, Any] = {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        if vals.get("labels"):
            entry["labels"] = {str(k): float(v) for k, v in vals["labels"].items()}
        if vals.get("label_counts"):
            entry["label_counts"] = {str(k): int(v) for k, v in vals["label_counts"].items()}
        updated[person] = entry
    adjustments = (
        block_adjustments(prior, data)
        if (policy.no_refund_penalties or policy.no_catchup_excused)
        else {}
    )
    for person, info in points.items():
        entry = updated.setdefault(person, {dim: 0.0 for dim in DIMENSIONS})
        entry["total"] += info.get("total", 0.0)
        entry["weekend"] += info.get("weekend", 0.0)
        # Informational history: per-shift-type points/counts and night-float
        # duty days (the latter is outside the carryover dimensions).
        nf_days = info.get("night_float", 0.0)
        if nf_days:
            entry["nf_days"] = entry.get("nf_days", 0) + int(nf_days)
        for label, pts in (info.get("labels") or {}).items():
            labels_entry = entry.setdefault("labels", {})
            labels_entry[label] = labels_entry.get(label, 0.0) + pts
        for label, n in (label_counts.get(person) or {}).items():
            counts_entry = entry.setdefault("label_counts", {})
            counts_entry[label] = counts_entry.get(label, 0) + n

        adj = adjustments.get(person)
        if not adj:
            continue
        audit: Dict[str, Any] = {}
        if policy.no_refund_penalties and adj["penalty"]:
            entry["total"] -= adj["penalty"]
            audit["penalty_not_carried"] = round(adj["penalty"], 4)
        if policy.no_catchup_excused:
            credits = {"total": adj["excused_total"], "weekend": adj["excused_weekend"]}
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


def ledger_from_json(text: str) -> Dict[str, Dict[str, Any]]:
    """Parse a ledger JSON, keeping the optional per-label history.

    The transient ``"adjustments"`` audit note is stripped as before; unknown
    keys are ignored so files from newer versions still load.
    """
    raw = json.loads(text)
    out: Dict[str, Dict[str, Any]] = {}
    for person, vals in raw.items():
        # Only the carryover dimensions are loaded; a legacy "night_float"
        # points key is intentionally ignored (NF is no longer balanced).
        entry: Dict[str, Any] = {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        labels = vals.get("labels")
        if isinstance(labels, dict):
            entry["labels"] = {str(k): float(v) for k, v in labels.items()}
        counts = vals.get("label_counts")
        if isinstance(counts, dict):
            entry["label_counts"] = {str(k): int(v) for k, v in counts.items()}
        if vals.get("nf_days"):
            entry["nf_days"] = int(vals["nf_days"])
        out[str(person)] = entry
    return out


# Grid column <-> ledger dimension mapping for the in-app ledger editor.
ROW_FIELDS = (("Total", "total"), ("Weekend", "weekend"))


def ledger_to_rows(ledger) -> list:
    """Flatten a ledger into editable rows (the three carryover dimensions).

    Per-label history and audit notes are not part of the grid; keep the
    loaded ledger around and pass it as ``base`` to :func:`rows_to_ledger` so
    they survive an edit round-trip.
    """
    rows = []
    for person in sorted(ledger or {}):
        vals = ledger[person] or {}
        row: Dict[str, Any] = {"Resident": person}
        for col, dim in ROW_FIELDS:
            row[col] = float(vals.get(dim, 0.0))
        rows.append(row)
    return rows


def rows_to_ledger(rows, base=None):
    """Rebuild a ledger from edited grid rows; returns ``(ledger, problems)``.

    Blank names are skipped (reported when the row carries points), numbers
    are coerced with a note on failure, duplicate names last-wins with a
    note, and negative values are allowed (a maintenance edit may
    legitimately debit). ``base`` supplies the per-label history for
    residents still present.
    """
    problems: list = []
    out: Dict[str, Dict[str, Any]] = {}

    def _coerce(name: str, col: str, value) -> float:
        if value in (None, ""):
            return 0.0
        try:
            number = float(value)
        except (TypeError, ValueError):
            problems.append(f"'{name}' has a non-numeric {col} value ({value!r}); using 0.")
            return 0.0
        if number != number:  # NaN from a cleared grid cell
            return 0.0
        return number

    for row in rows or []:
        raw_name = row.get("Resident")
        name = str(raw_name).strip() if raw_name is not None else ""
        if not name or name.lower() == "nan":
            if any(_coerce("(unnamed)", col, row.get(col)) for col, _ in ROW_FIELDS):
                problems.append("A row with points has no resident name and was skipped.")
            continue
        if name in out:
            problems.append(f"Duplicate ledger row for '{name}'; the last one wins.")
        entry: Dict[str, Any] = {
            dim: _coerce(name, col, row.get(col)) for col, dim in ROW_FIELDS
        }
        extra = (base or {}).get(name) or {}
        for key in ("labels", "label_counts"):
            if extra.get(key):
                entry[key] = dict(extra[key])
        if extra.get("nf_days"):
            entry["nf_days"] = int(extra["nf_days"])
        out[name] = entry
    return out, problems
