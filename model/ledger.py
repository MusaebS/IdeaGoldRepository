"""Cumulative fairness ledger.

A ledger records each resident's accumulated points across past scheduling
blocks (``total`` and ``weekend``). Feeding it into the next block lets the
optimiser balance *cumulative* load — a resident who carried extra last block
is given a lighter target this block — and the updated ledger is saved for
the block after that.

Alongside the two carryover dimensions, each entry also accumulates a
per-shift-type history: ``"labels"`` (points per label) and ``"label_counts"``
(call counts per label), plus an informational ``"nf_days"`` night-float duty
count. The label history feeds the fairness table's cumulative "which calls"
view and — when per-label carryover is enabled (the default) — the next
block's per-label targets, so shift-type debt is repaid in the same shift
type. Old ledger files without these keys load unchanged, and old app
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

import difflib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

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
    "ReconcileReport",
    "reconcile_report",
    "rename_person",
    "rename_label",
    "drop_person",
    "drop_label",
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
    (what the resident's share should have been vs. what it was reduced to for
    *this* block), so they depend only on the configuration. ``prior`` is
    accepted for API stability but no longer used (see below).

    Returns ``{person: {"penalty": e, "excused_total": c, "excused_weekend": c}}``
    — all zero when nothing applies. Excusals come from two sources: availability
    reductions (uncompensated leave, rotator windows, perks, group factors —
    weighted below) and **excused caps** (a ``max_total`` marked "do not
    compensate later" — credited its shortfall below fair share, debited from the
    residents who absorb it). A plain "compensate later" cap adds nothing here,
    so its shortfall is left for cumulative balancing to make up.

    Credit is measured on **this block only**, not the cumulative pool. An
    earlier version scaled the excused credit by the whole running total
    (``block + Σ prior``); because that grows every block while the credit is
    re-applied each block, a *recurring* excusal (a standing group factor or
    perk, or repeatedly covering night float — which the overlay records as an
    uncompensated absence) made the recorded ledger diverge without bound, so
    the resident doing the *least* work ended up recorded with the *most*. A
    per-block credit stays bounded and honest: the excused resident's record
    tracks their genuinely lighter load instead of running away from it. For a
    first block (``prior`` empty) the two are identical, so the one-time
    excusal guarantee — and every exact-number test — is unchanged.
    """
    from .closures import resolve_closures  # local: avoids a module cycle
    from .night_float import resolve_night_float  # local: avoids a module cycle

    participants = list(data.juniors) + list(data.seniors)
    out: Dict[str, Dict[str, float]] = {
        p: {"penalty": 0.0, "excused_total": 0.0, "excused_weekend": 0.0}
        for p in participants
    }
    if not participants:
        return out
    n = len(participants)

    for p, extra in (data.extra_points or {}).items():
        if p in out and extra > 0:
            out[p]["penalty"] = float(extra)

    # Regular demand only — reserved (night-float-covered or closed) cells are
    # outside the point pool.
    nf_cells, _gaps, _leaves = resolve_night_float(data)
    reserved = set(nf_cells) | resolve_closures(data)
    slots = [s for s in slot_points(data) if (s.day, s.shift.label) not in reserved]
    p_tot = sum(s.points for s in slots)
    p_wk = sum(s.points for s in slots if s.weekend)

    weights = availability_weights(data)
    weight_sum = sum(weights.get(p, 0.0) for p in participants)

    # Penalty scale: resolve_targets lowers everyone's non-penalty share by
    # f = (P - E)/P so the raised targets still reconcile; the excused-total
    # credit must be measured on the same scaled share.
    extra_sum = sum(v["penalty"] for v in out.values())
    f = (p_tot - extra_sum) / p_tot if p_tot > 0 and 0 < extra_sum < p_tot else 1.0

    if weight_sum > 0:
        for p in participants:
            # gap = this block's fair-share shortfall the excusal caused:
            # an equal 1/n share minus the availability-weighted share.
            gap = 1.0 / n - weights.get(p, 0.0) / weight_sum
            out[p]["excused_total"] = f * p_tot * gap
            out[p]["excused_weekend"] = p_wk * gap

        # Excused caps ("do not compensate later"): a resident capped below their
        # fair share whose shortfall is *not* to be repaid is credited it here,
        # and the residents who absorb the freed load are debited it in
        # proportion to their weight — so the ledger records everyone at fair
        # standing and no one catches up (the same shape as a perk). A
        # "compensate later" cap (the default) sets no flag here, so its
        # shortfall stays uncredited and cumulative balancing makes it up.
        fair = {p: p_tot * weights.get(p, 0.0) / weight_sum for p in participants}
        excused = data.max_total_excused or {}
        caps = data.max_total or {}
        capped = [
            p for p in participants
            if excused.get(p) and caps.get(p) is not None and caps[p] < fair[p]
        ]
        receivers = [p for p in participants if p not in capped]
        recv_weight = sum(weights.get(p, 0.0) for p in receivers)
        for p in capped:
            shortfall = f * (fair[p] - caps[p])
            out[p]["excused_total"] += shortfall
            if recv_weight > 0:
                for r in receivers:
                    out[r]["excused_total"] -= shortfall * weights.get(r, 0.0) / recv_weight
    # Re-resolve excusal credits inside the role pool that can actually absorb
    # the work. The calculations above are retained for backward-readable audit
    # history, but these role-aware values are authoritative: a junior excusal
    # must never debit seniors who cannot work junior shifts (or vice versa).
    for role, members in (
        ("Junior", list(data.juniors)),
        ("Senior", list(data.seniors)),
    ):
        if not members:
            continue
        role_slots = [s for s in slots if s.shift.role == role]
        role_total = sum(s.points for s in role_slots)
        role_weekend = sum(s.points for s in role_slots if s.weekend)
        role_weight = sum(weights.get(p, 0.0) for p in members)
        if role_weight <= 0:
            continue
        role_extra = sum(out[p]["penalty"] for p in members)
        role_scale = (
            (role_total - role_extra) / role_total
            if role_total > 0 and 0 < role_extra < role_total
            else 1.0
        )
        for p in members:
            gap = 1.0 / len(members) - weights.get(p, 0.0) / role_weight
            out[p]["excused_total"] = role_scale * role_total * gap
            out[p]["excused_weekend"] = role_weekend * gap

        fair = {
            p: role_total * weights.get(p, 0.0) / role_weight
            for p in members
        }
        excused = data.max_total_excused or {}
        caps = data.max_total or {}
        capped = [
            p for p in members
            if excused.get(p) and caps.get(p) is not None and caps[p] < fair[p]
        ]
        receivers = [p for p in members if p not in capped]
        receiver_weight = sum(weights.get(p, 0.0) for p in receivers)
        for p in capped:
            shortfall = role_scale * (fair[p] - caps[p])
            out[p]["excused_total"] += shortfall
            if receiver_weight > 0:
                for receiver in receivers:
                    out[receiver]["excused_total"] -= (
                        shortfall * weights.get(receiver, 0.0) / receiver_weight
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
    label_counts = calculate_label_counts(df, data)
    updated: Dict[str, Dict[str, Any]] = {}
    for person, vals in (prior or {}).items():
        entry: Dict[str, Any] = {dim: float(vals.get(dim, 0.0)) for dim in DIMENSIONS}
        if vals.get("labels"):
            entry["labels"] = {str(k): float(v) for k, v in vals["labels"].items()}
        if vals.get("label_counts"):
            entry["label_counts"] = {str(k): int(v) for k, v in vals["label_counts"].items()}
        # Carry the informational night-float duty count forward; without this
        # the cumulative NF-duty history was silently reset to the last block.
        if vals.get("nf_days"):
            entry["nf_days"] = int(vals["nf_days"])
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
    """Flatten a ledger into editable rows (the Total / Weekend dimensions).

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


# --- Reconciliation: match an uploaded ledger against the current config ----
#
# Ledger names and shift labels are matched by exact, case-sensitive string
# everywhere else in the app, so real-world drift — a misspelling fixed on the
# roster, a renamed shift, a shift retired or added, a resident joining or
# leaving — silently splits or orphans history. These pure helpers turn that
# drift into an explicit report plus small corrective operations the UI can
# offer for confirmation. None of them mutate their input.


@dataclass(frozen=True)
class ReconcileReport:
    """Mismatches between a ledger and the current roster / shift catalogue.

    ``unknown_*`` are ledger entries with no exact match in the current
    config (a misspelling, a rename, or someone/something genuinely gone);
    ``new_*`` are current names/labels absent from the ledger (they simply
    start with no history). ``*_suggestions`` map each unknown to close
    matches in the current config, best first. ``new_labels`` is only
    reported when the ledger carries some label history at all — a legacy
    file without any is not evidence of drift.
    """

    unknown_people: Tuple[str, ...]
    new_people: Tuple[str, ...]
    unknown_labels: Tuple[str, ...]
    new_labels: Tuple[str, ...]
    person_suggestions: Dict[str, Tuple[str, ...]]
    label_suggestions: Dict[str, Tuple[str, ...]]

    @property
    def has_mismatches(self) -> bool:
        return bool(self.unknown_people or self.unknown_labels)


def _ledger_labels(ledger) -> list:
    """Every shift label appearing in any entry's label history, sorted."""
    seen: set[str] = set()
    for entry in (ledger or {}).values():
        for key in ("labels", "label_counts"):
            seen.update(str(label) for label in (entry or {}).get(key) or {})
    return sorted(seen)


def _suggestions(
    unknown: Sequence[str], candidates: Sequence[str]
) -> Dict[str, Tuple[str, ...]]:
    return {
        name: tuple(difflib.get_close_matches(name, list(candidates), n=3, cutoff=0.6))
        for name in unknown
    }


def reconcile_report(ledger, roster, shift_labels) -> ReconcileReport:
    """Compare a ledger's names and labels with the current configuration.

    This is what turns a near-miss (a misspelling, a renamed shift) into an
    explicit choice instead of a silent zero-history restart.
    """
    ledger = ledger or {}
    roster = [str(r) for r in (roster or [])]
    labels = [str(lbl) for lbl in (shift_labels or [])]
    history_labels = _ledger_labels(ledger)
    unknown_people = tuple(sorted(set(ledger) - set(roster)))
    unknown_labels = tuple(lbl for lbl in history_labels if lbl not in set(labels))
    return ReconcileReport(
        unknown_people=unknown_people,
        new_people=tuple(n for n in roster if n not in ledger),
        unknown_labels=unknown_labels,
        new_labels=(
            tuple(lbl for lbl in labels if lbl not in set(history_labels))
            if history_labels
            else ()
        ),
        person_suggestions=_suggestions(unknown_people, roster),
        label_suggestions=_suggestions(unknown_labels, labels),
    )


def _copy_entry(entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in (entry or {}).items()
    }


def _merge_entries(a: Mapping[str, Any], b: Mapping[str, Any]) -> Dict[str, Any]:
    """Combine two residents' histories: dimensions summed, labels union-summed.

    The transient ``adjustments`` audit note describes a single block update
    of a single identity, so it has no meaning for a merged one and is dropped
    (it is stripped on every load anyway).
    """
    merged: Dict[str, Any] = {
        dim: float(a.get(dim, 0.0)) + float(b.get(dim, 0.0)) for dim in DIMENSIONS
    }
    for key, cast in (("labels", float), ("label_counts", int)):
        if (a.get(key) or b.get(key)):
            hist = {str(k): cast(v) for k, v in (a.get(key) or {}).items()}
            for k, v in (b.get(key) or {}).items():
                hist[str(k)] = hist.get(str(k), cast(0)) + cast(v)
            merged[key] = hist
    nf_days = int(a.get("nf_days") or 0) + int(b.get("nf_days") or 0)
    if nf_days:
        merged["nf_days"] = nf_days
    return merged


def rename_person(ledger, old: str, new: str) -> Dict[str, Dict[str, Any]]:
    """Return a copy of ``ledger`` with ``old``'s history filed under ``new``.

    Fixes a misspelled or changed name so the person keeps their carryover
    and per-label history. If ``new`` already exists the two histories are
    merged. A missing ``old`` (or ``old == new``) returns an unchanged copy.
    """
    out = {name: _copy_entry(entry) for name, entry in (ledger or {}).items()}
    if old not in out or old == new:
        return out
    moved = out.pop(old)
    out[new] = _merge_entries(out[new], moved) if new in out else moved
    return out


def rename_label(ledger, old: str, new: str) -> Dict[str, Dict[str, Any]]:
    """Return a copy with label history under ``old`` folded into ``new``.

    Repairs a renamed shift so its history keeps feeding the cumulative
    per-label view and per-label carryover. The total/weekend dimensions are
    untouched: the work was done regardless of what the shift is called now.
    """
    if old == new:
        return {name: _copy_entry(entry) for name, entry in (ledger or {}).items()}
    out: Dict[str, Dict[str, Any]] = {}
    for name, entry in (ledger or {}).items():
        entry = _copy_entry(entry)
        for key, cast in (("labels", float), ("label_counts", int)):
            hist = entry.get(key)
            if hist and old in hist:
                moved = hist.pop(old)
                hist[new] = cast(hist.get(new, 0)) + cast(moved)
        out[name] = entry
    return out


def drop_person(ledger, name: str) -> Dict[str, Dict[str, Any]]:
    """Return a copy without ``name``'s entry (their history is discarded)."""
    return {n: _copy_entry(e) for n, e in (ledger or {}).items() if n != name}


def drop_label(ledger, label: str) -> Dict[str, Dict[str, Any]]:
    """Return a copy with ``label`` removed from every label history.

    Deliberately leaves the total/weekend dimensions alone: removing a dead
    shift's history is bookkeeping, not un-earning the points worked on it.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, entry in (ledger or {}).items():
        entry = _copy_entry(entry)
        for key in ("labels", "label_counts"):
            hist = entry.get(key)
            if hist and label in hist:
                del hist[label]
                if not hist:
                    del entry[key]
        out[name] = entry
    return out
