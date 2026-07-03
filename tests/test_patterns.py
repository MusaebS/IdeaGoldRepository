"""Tests for ui/patterns.py (streamlit-free, runs under the stub CI job)."""
from datetime import date, timedelta

import pytest

from ui.patterns import FILL_MODES, expand_pattern, parse_fill_names


def _dates(n):
    return [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]


def test_parse_commas_newlines_and_whitespace():
    assert parse_fill_names("A, B\nC ,  D\n") == ["A", "B", "C", "D"]


def test_parse_keeps_duplicates_in_order():
    assert parse_fill_names("A, B, A, C") == ["A", "B", "A", "C"]


def test_parse_empty_and_blank():
    assert parse_fill_names("") == []
    assert parse_fill_names(" ,\n , ") == []


def test_daily_cycle_wraps():
    result = expand_pattern(["A", "B", "C"], _dates(5), "daily")
    assert [result[str(d)] for d in _dates(5)] == ["A", "B", "C", "A", "B"]


def test_weekly_covers_seven_day_runs_and_wraps():
    result = expand_pattern(["A", "B"], _dates(10), "weekly")
    assert [result[str(d)] for d in _dates(10)] == ["A"] * 7 + ["B"] * 3
    # And wraps back to A after both names' weeks are used.
    long = expand_pattern(["A", "B"], _dates(15), "weekly")
    assert long[str(_dates(15)[14])] == "A"


def test_constant_uses_first_name():
    result = expand_pattern(["A", "B"], _dates(3), "constant")
    assert set(result.values()) == {"A"}


def test_keys_are_str_of_date():
    result = expand_pattern(["A"], _dates(2), "daily")
    assert set(result) == {str(d) for d in _dates(2)}


def test_empty_names_returns_empty():
    assert expand_pattern([], _dates(3), "daily") == {}


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        expand_pattern(["A"], _dates(2), "biweekly")


def test_fill_modes_map_to_known_internal_modes():
    for internal in FILL_MODES.values():
        expand_pattern(["A"], _dates(1), internal)  # none raises
