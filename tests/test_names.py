"""Tests for Unicode-aware, display-preserving name matching."""

import pytest

from model.names import canonical_name, dedupe_names


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Alice\t  Smith\n", "alice smith"),
        ("ＡＬＩＣＥ　ＳＭＩＴＨ", "alice smith"),
        ("Straße", "strasse"),
        ("ﬁancée", "fiancée"),
        ("Cafe\N{COMBINING ACUTE ACCENT}", "café"),
        (" أحمد\N{NO-BREAK SPACE}\tعلي ", "أحمد علي"),
        (" \n\t ", ""),
    ],
)
def test_canonical_name_nfkc_whitespace_and_casefold(raw, expected):
    assert canonical_name(raw) == expected


def test_exact_dedupe_only_removes_identical_strings():
    names = [" Alice ", "Alice", "Alice", "alice", "Ａｌｉｃｅ"]
    assert dedupe_names(names) == [" Alice ", "Alice", "alice", "Ａｌｉｃｅ"]


def test_canonical_dedupe_preserves_first_display_spelling_and_order():
    names = [
        "  Alice Smith ",
        "Bob",
        "alice\t smith",
        "ＢＯＢ",
        "Cara",
        "CARA",
    ]
    assert dedupe_names(names, mode="canonical") == ["  Alice Smith ", "Bob", "Cara"]


def test_canonical_dedupe_handles_casefold_expansion():
    assert dedupe_names(["Straße", "STRASSE", "Strasse"], mode="canonical") == ["Straße"]


def test_canonical_dedupe_collapses_blank_spellings_without_filtering_them():
    assert dedupe_names(["  ", "\t", "", "Alice"], mode="canonical") == ["  ", "Alice"]


def test_dedupe_accepts_one_pass_iterables():
    names = (name for name in ["Alice", "ALICE", "Bob"])
    assert dedupe_names(names, mode="canonical") == ["Alice", "Bob"]


def test_invalid_mode_is_rejected_before_consuming_input():
    consumed = False

    def names():
        nonlocal consumed
        consumed = True
        yield "Alice"

    with pytest.raises(ValueError, match="exact.*canonical"):
        dedupe_names(names(), mode="fuzzy")  # type: ignore[arg-type]
    assert consumed is False


@pytest.mark.parametrize(
    "call",
    [
        lambda: canonical_name(None),
        lambda: dedupe_names(["Alice", None]),
    ],
)
def test_non_string_names_raise_clear_type_error(call):
    with pytest.raises(TypeError, match="name must be str, got NoneType"):
        call()
