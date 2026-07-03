"""Pattern expansion for auto-filling custom schedule columns.

Pure helpers (no Streamlit import) so they stay unit-testable under the
stub-only CI job. Keys in the returned map are ``str(date)`` — the same
contract ``custom_columns_editor`` uses for ``extra_vals``.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

__all__ = ["FILL_MODES", "parse_fill_names", "expand_pattern"]

# UI label -> internal mode.
FILL_MODES = {
    "Repeat daily": "daily",
    "Weekly (same value 7 days at a time)": "weekly",
    "Same value every day": "constant",
}


def parse_fill_names(text: str) -> List[str]:
    """Split a comma/newline separated name list, keeping duplicates.

    Unlike roster parsing, duplicates are meaningful here: a cycle like
    ``A, B, A, C`` is a legitimate rota.
    """
    out: List[str] = []
    for line in (text or "").splitlines():
        for part in line.split(","):
            name = part.strip()
            if name:
                out.append(name)
    return out


def expand_pattern(names: Sequence[str], dates: Sequence, mode: str) -> Dict[str, str]:
    """Map each date (as ``str(d)``) to a name according to ``mode``.

    - ``daily``: cycle the list day by day (A, B, C, A, …).
    - ``weekly``: each name covers 7 consecutive days from the first date
      (consultant-of-the-week; runs are block-relative, not calendar-aligned).
    - ``constant``: the first name every day.
    """
    if not names:
        return {}
    k = len(names)
    if mode == "daily":
        return {str(d): names[i % k] for i, d in enumerate(dates)}
    if mode == "weekly":
        return {str(d): names[(i // 7) % k] for i, d in enumerate(dates)}
    if mode == "constant":
        return {str(d): names[0] for d in dates}
    raise ValueError(f"unknown fill mode: {mode!r}")
