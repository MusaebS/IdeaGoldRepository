"""Focused tests for roster parsing and optional canonical name matching."""

import pytest

pytest.importorskip("pandas")
pytest.importorskip("streamlit")

from ui.editors import _parse_names, _roster_overlap


def test_parse_names_keeps_exact_matching_as_the_default():
    text = " Alice \nalice\nAlice\nAlice   Smith\nAlice Smith\n"

    assert _parse_names(text) == ["Alice", "alice", "Alice   Smith", "Alice Smith"]


def test_parse_names_canonical_mode_preserves_first_display_spelling():
    text = " Alice   Smith \nalice smith\nＡＬＩＣＥ   ＳＭＩＴＨ\nBob\nBOB\n"

    assert _parse_names(text, normalize=True) == ["Alice   Smith", "Bob"]


def test_roster_overlap_exact_mode_keeps_existing_behavior():
    assert _roster_overlap(["Alice", "Bob"], ["alice", "Bob"]) == ["Bob"]


def test_roster_overlap_canonical_mode_shows_both_display_spellings():
    assert _roster_overlap(
        ["Alice  Smith", "Bob"],
        ["ALICE SMITH", "Cara"],
        normalize=True,
    ) == ["Alice  Smith / ALICE SMITH"]
