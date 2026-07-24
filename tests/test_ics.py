import sys, os
import io
import zipfile
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pd = pytest.importorskip("pandas")

from model.data_models import InputData, ShiftTemplate
from model.ics import (
    google_calendar_url,
    ics_data_uri,
    resident_events,
    resident_ics,
    schedule_calendars_zip,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _sample():
    shifts = [
        ShiftTemplate(label="ER, night", role="Junior", night_float=False, thu_weekend=False, points=2.0),
        ShiftTemplate(label="Ward", role="Senior", night_float=False, thu_weekend=False, points=1.0),
    ]
    data = InputData(
        start_date=date(2026, 3, 2), end_date=date(2026, 3, 3), shifts=shifts,
        juniors=["Alice"], seniors=["Bob Ödberg"], nf_juniors=[], nf_seniors=[],
        leaves=[], rotators=[], min_gap=0,
    )
    df = pd.DataFrame([
        {"Date": date(2026, 3, 2), "Day": "Mon", "ER, night": "Alice", "Ward": "Bob Ödberg"},
        {"Date": date(2026, 3, 3), "Day": "Tue", "ER, night": "Unfilled", "Ward": "Bob Ödberg"},
    ])
    return df, data


def test_resident_events_collects_only_their_cells():
    df, data = _sample()
    assert resident_events(df, data, "Alice") == [
        {"day": date(2026, 3, 2), "label": "ER, night"}
    ]
    assert len(resident_events(df, data, "Bob Ödberg")) == 2
    assert resident_events(df, data, "Nobody") == []


def test_ics_structure_all_day_and_escaping():
    df, data = _sample()
    text = resident_ics(df, data, "Alice", now=NOW)
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.rstrip().endswith("END:VCALENDAR")
    assert text.count("BEGIN:VEVENT") == 1
    # All-day event with EXCLUSIVE end date (next day).
    assert "DTSTART;VALUE=DATE:20260302" in text
    assert "DTEND;VALUE=DATE:20260303" in text
    # The comma in the label is escaped per RFC 5545.
    assert "SUMMARY:On call: ER\\, night" in text
    # Every content line respects the 75-octet fold limit.
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_zip_has_one_calendar_per_scheduled_resident():
    df, data = _sample()
    blob = schedule_calendars_zip(df, data, now=NOW)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = sorted(archive.namelist())
        assert names == ["Alice.ics", "Bob_dberg.ics"]
        bob = archive.read("Bob_dberg.ics").decode("utf-8")
        assert bob.count("BEGIN:VEVENT") == 2


def test_timestamp_dates_are_normalised():
    df, data = _sample()
    df["Date"] = [pd.Timestamp(2026, 3, 2), pd.Timestamp(2026, 3, 3)]
    events = resident_events(df, data, "Alice")
    assert events == [{"day": date(2026, 3, 2), "label": "ER, night"}]


def test_google_calendar_url_is_all_day_and_encoded():
    url = google_calendar_url(date(2026, 3, 2), "ER, night", "Alice")
    assert url.startswith("https://calendar.google.com/calendar/render?")
    assert "action=TEMPLATE" in url
    # All-day event: exclusive end = next day.
    assert "dates=20260302%2F20260303" in url
    # The label (with its comma) is URL-encoded, never raw.
    assert "ER%2C+night" in url and "ER, night" not in url


def test_ics_data_uri_round_trips():
    import base64

    df, data = _sample()
    text = resident_ics(df, data, "Alice", now=NOW)
    href = ics_data_uri(text)
    assert href.startswith("data:text/calendar;charset=utf-8;base64,")
    decoded = base64.b64decode(href.split(",", 1)[1]).decode("utf-8")
    assert decoded == text


def test_calendar_handout_pdf_lists_residents_with_links():
    pytest.importorskip("reportlab")
    from model.calendar_pdf import calendar_handout_pdf_bytes

    df, data = _sample()
    blob = calendar_handout_pdf_bytes(df, data)
    assert blob.startswith(b"%PDF")
    # The per-date add-to-calendar links are real URI annotations in the PDF.
    assert b"calendar.google.com" in blob
