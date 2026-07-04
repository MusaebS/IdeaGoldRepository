"""Availability-request import: parsing, matching, templates, leaves."""
import sys, os
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from model.availability import (
    AvailabilityRow,
    availability_template_csv,
    availability_template_xlsx,
    parse_availability_rows,
    read_availability_csv,
    read_availability_xlsx,
    rows_to_leaves,
)

ROSTER = ["Alice Smith", "Bob Jones"]


def test_header_synonyms_and_tolerant_name_matching():
    rows = [
        {"Resident": "  alice   smith ", "From": "2026-08-05", "To": "2026-08-07"},
        {"Resident": "BOB JONES", "From": "06/08/2026", "To": ""},
    ]
    parsed = parse_availability_rows(rows, ROSTER)
    assert parsed[0].name == "Alice Smith" and parsed[0].error is None
    assert parsed[0].start == date(2026, 8, 5) and parsed[0].end == date(2026, 8, 7)
    # DD/MM/YYYY accepted; empty End means a single day.
    assert parsed[1].name == "Bob Jones"
    assert parsed[1].start == parsed[1].end == date(2026, 8, 6)


def test_per_row_errors_do_not_block_other_rows():
    rows = [
        {"Name": "Nobody", "Start": "2026-08-05", "End": "2026-08-06"},
        {"Name": "Alice Smith", "Start": "not-a-date", "End": ""},
        {"Name": "Alice Smith", "Start": "2026-08-07", "End": "2026-08-05"},
        {"Name": "", "Start": "2026-08-05", "End": ""},
        {"Name": "", "Start": "", "End": ""},  # fully blank: skipped silently
        {"Name": "Bob Jones", "Start": "2026-08-10", "End": "2026-08-11"},
    ]
    parsed = parse_availability_rows(rows, ROSTER)
    assert len(parsed) == 5  # the blank line is dropped
    assert "not on the roster" in parsed[0].error
    assert "Unreadable start date" in parsed[1].error
    assert "before start" in parsed[2].error
    assert "Missing name" in parsed[3].error
    assert parsed[4].error is None
    assert parsed[4].row_no == 7  # spreadsheet row numbers survive the blank skip


def test_missing_columns_reported_once():
    parsed = parse_availability_rows([{"Foo": "x", "Bar": "y"}], ROSTER)
    assert len(parsed) == 1
    assert parsed[0].row_no == 0
    assert "Name and Start column" in parsed[0].error


def test_typed_datetime_cells_and_multiple_rows_per_person():
    rows = [
        {"Name": "Alice Smith", "Start": datetime(2026, 8, 5, 0, 0), "End": date(2026, 8, 6)},
        {"Name": "Alice Smith", "Start": date(2026, 8, 20), "End": None},
    ]
    parsed = parse_availability_rows(rows, ROSTER)
    assert [r.error for r in parsed] == [None, None]
    leaves = rows_to_leaves(parsed)
    assert leaves == [
        ("Alice Smith", date(2026, 8, 5), date(2026, 8, 6), True),
        ("Alice Smith", date(2026, 8, 20), date(2026, 8, 20), True),
    ]
    assert all(lv.compensated for lv in leaves)


def test_rows_to_leaves_skips_invalid():
    parsed = [
        AvailabilityRow(2, "Alice Smith", "Alice Smith", date(2026, 8, 5), date(2026, 8, 6), None),
        AvailabilityRow(3, "Nobody", None, None, None, "'Nobody' is not on the roster."),
    ]
    assert len(rows_to_leaves(parsed)) == 1


def test_csv_template_parses_through_its_own_reader():
    rows = read_availability_csv(availability_template_csv())
    parsed = parse_availability_rows(rows, ["Alice Example"])
    assert len(parsed) == 1 and parsed[0].error is None
    assert parsed[0].start == date(2026, 8, 5) and parsed[0].end == date(2026, 8, 7)


def test_xlsx_template_round_trips():
    pytest.importorskip("openpyxl")
    rows = read_availability_xlsx(availability_template_xlsx())
    parsed = parse_availability_rows(rows, ["Alice Example"])
    assert len(parsed) == 1 and parsed[0].error is None
    assert parsed[0].name == "Alice Example"
