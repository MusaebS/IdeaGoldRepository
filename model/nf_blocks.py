from __future__ import annotations

from datetime import date
from typing import Dict, List

try:
    import pandas as pd
except Exception:  # pragma: no cover - fallback when pandas missing
    from . import optimiser as opt
    pd = opt.pd

from .data_models import ShiftTemplate

__all__ = ["respects_nf_blocks"]


def respects_nf_blocks(
    df: pd.DataFrame,
    nf_block_length: int,
    shifts: List[ShiftTemplate],
) -> bool:
    """Return True if NF assignments occur in fixed-length blocks."""
    if nf_block_length <= 1:
        return True
    nf_labels = [s.label for s in shifts if s.night_float]
    if not nf_labels:
        return True

    assignments: Dict[str, Dict[str, List[date]]] = {}
    for row in df.to_dict("records"):
        day = row.get("Date")
        for label in nf_labels:
            person = row.get(label)
            if person in (None, "Unfilled"):
                continue
            assignments.setdefault(person, {}).setdefault(label, []).append(day)

    for label_assignments in assignments.values():
        for days in label_assignments.values():
            days.sort()
            run_len = 1
            prev = days[0]
            for d in days[1:]:
                if (d - prev).days == 1:
                    run_len += 1
                else:
                    if run_len != nf_block_length:
                        return False
                    run_len = 1
                prev = d
            if run_len != nf_block_length:
                return False
    return True
