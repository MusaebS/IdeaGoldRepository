"""Name matching helpers with display-safe deduplication.

Canonical names are comparison keys only.  They must not replace the spelling
shown to users or written to exports; :func:`dedupe_names` intentionally keeps
the first original string for that reason.
"""

from __future__ import annotations

from typing import Iterable, Literal
import unicodedata

__all__ = ["NameMatchMode", "canonical_name", "dedupe_names"]

NameMatchMode = Literal["exact", "canonical"]


def canonical_name(name: str) -> str:
    """Return a Unicode-aware, case-insensitive comparison key for ``name``.

    Processing is deliberately small and predictable:

    1. Unicode NFKC normalization folds compatibility spellings such as
       full-width Latin characters and ligatures.
    2. ``str.split`` plus ``" ".join`` trims the ends and collapses every run
       of Unicode whitespace to one ordinary space.
    3. ``casefold`` supplies Unicode's stronger form of case-insensitive
       matching (for example, ``"Straße"`` and ``"STRASSE"`` match).

    The returned value is suitable as a dictionary/set key, not as display
    text.  Blank or whitespace-only input has the valid canonical key ``""``.
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")
    normalized = unicodedata.normalize("NFKC", name)
    collapsed = " ".join(normalized.split())
    return collapsed.casefold()


def dedupe_names(
    names: Iterable[str],
    *,
    mode: NameMatchMode = "exact",
) -> list[str]:
    """Return names once, in input order, preserving the first display text.

    ``mode="exact"`` uses ordinary case- and whitespace-sensitive string
    equality.  ``mode="canonical"`` compares :func:`canonical_name` keys while
    still returning the first original spelling.  Empty names are not filtered;
    callers that prohibit them should validate that policy separately.
    """
    if mode not in ("exact", "canonical"):
        raise ValueError("mode must be 'exact' or 'canonical'")

    canonical = mode == "canonical"
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if not isinstance(name, str):
            raise TypeError(f"name must be str, got {type(name).__name__}")
        key = canonical_name(name) if canonical else name
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result
