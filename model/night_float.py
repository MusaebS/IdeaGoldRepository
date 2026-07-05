"""Night-float overlay: resolve NF coverage before the regular scheduler runs.

Night float is a dedicated coverage rotation, not a regular on-call. This
module turns the user's NF configuration into three things the regular
scheduler consumes:

* ``nf_cells`` — the (date, shift) slots the overlay covers, mapped to their
  coverer. These are **removed from regular demand** and written straight into
  the final schedule; they carry no regular points or fairness weight.
* ``gap_slots`` — dates that are NF-covered in the pattern but have no assigned
  coverer. Per the coverage-first rule these **fall back to regular**
  scheduling (a warning is raised in validation).
* ``leaves`` — each NF assignment becomes an *uncompensated* leave over its
  period plus ``rest_days`` recovery days, so the coverer is blocked from
  regular shifts during (and just after) their NF block, their regular target
  drops, and the ledger never makes them catch the missed regular work up.

Pure and stub-safe (no pandas / OR-Tools / Streamlit).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Set, Tuple

from .data_models import (
    InputData,
    Leave,
    nf_covered,
    normalized_nf_assignments,
)
from .points import block_days

__all__ = [
    "resolve_night_float",
    "nf_leave_windows",
    "nf_duty_days",
    "nf_cells_to_attr",
    "nf_cells_from_attr",
]

Slot = Tuple[date, str]


def nf_cells_to_attr(nf_cells: Dict[Slot, str]) -> Dict[str, Dict[str, str]]:
    """``{(date, label): name}`` → a JSON/Arrow-serializable nested dict.

    ``df.attrs`` is serialized by pandas/Streamlit, which rejects tuple keys, so
    the overlay cells are stored on the frame as ``{date-iso: {label: name}}``.
    """
    out: Dict[str, Dict[str, str]] = {}
    for (day, label), name in nf_cells.items():
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        out.setdefault(key, {})[label] = name
    return out


def nf_cells_from_attr(df) -> Dict[Tuple[str, str], str]:
    """Read ``df.attrs['nf_cells']`` back into ``{(date-iso, label): name}``."""
    attrs = getattr(df, "attrs", {}) or {}
    raw = attrs.get("nf_cells", {}) or {}
    out: Dict[Tuple[str, str], str] = {}
    for iso, labels in raw.items():
        if isinstance(labels, dict):  # {date-iso: {label: name}}
            for label, name in labels.items():
                out[(str(iso), str(label))] = name
    return out


def nf_leave_windows(data: InputData) -> List[Leave]:
    """Each NF assignment as an *uncompensated* leave over its period + rest.

    Fed to the regular scheduler's availability and blocking so a night floater
    is off regular shifts (and recovering) during their block, with a reduced
    regular target and no future catch-up — computed straight from the config
    so every consumer (weights, solver, ledger) agrees without threading state.
    """
    windows: List[Leave] = []
    for a in normalized_nf_assignments(data.nf_assignments, default_rest=data.nf_rest_days):
        rest = max(0, int(a.rest_days))
        windows.append(Leave(a.name, a.start, a.end + timedelta(days=rest), False))
    return windows


def resolve_night_float(
    data: InputData,
) -> Tuple[Dict[Slot, str], Set[Slot], List[Leave]]:
    """Return ``(nf_cells, gap_slots, leaves)`` for the block (see module doc)."""
    assignments = list(
        normalized_nf_assignments(data.nf_assignments, default_rest=data.nf_rest_days)
    )
    # Deterministic coverer selection: earliest start, then name.
    assignments.sort(key=lambda a: (a.start, a.name))
    # A coverer only covers night-float shifts of their own role (a junior never
    # ends up written onto a senior night-float shift, and vice versa).
    role_of: Dict[str, str] = {p: "Junior" for p in data.juniors}
    role_of.update({p: "Senior" for p in data.seniors})

    nf_cells: Dict[Slot, str] = {}
    gap_slots: Set[Slot] = set()
    for day in block_days(data):
        for shift in data.shifts:
            if not nf_covered(day, shift, data):
                continue
            coverer = _coverer_for(day, shift, assignments, role_of)
            if coverer is None:
                gap_slots.add((day, shift.label))  # → regular fallback
            else:
                nf_cells[(day, shift.label)] = coverer

    return nf_cells, gap_slots, nf_leave_windows(data)


def _coverer_for(day: date, shift, assignments, role_of: Dict[str, str]) -> str | None:
    """The assignment covering ``shift`` on ``day``, role-matched.

    An empty ``labels`` means "all night-float shifts of the coverer's role", so
    a junior with a blanket assignment covers only junior night-float shifts.
    """
    for a in assignments:
        if not (a.start <= day <= a.end):
            continue
        if role_of.get(a.name) != shift.role:
            continue  # a coverer only covers their own role's NF shifts
        if not a.labels or shift.label in a.labels:
            return a.name
    return None


def nf_duty_days(nf_cells: Dict[Slot, str]) -> Dict[str, int]:
    """Per-resident count of NF slots covered (informational, outside fairness)."""
    out: Dict[str, int] = {}
    for coverer in nf_cells.values():
        out[coverer] = out.get(coverer, 0) + 1
    return out
