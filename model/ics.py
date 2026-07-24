"""Per-resident calendar export (RFC 5545 .ics + add-to-calendar links).

Every assignment a resident holds — regular calls and night-float overlay
duty alike — becomes an all-day event in their personal calendar file, so the
rota lands in Google/Apple/Outlook calendars with one import. Shifts in this
scheduler are day-granular (no clock times), so all-day events are the honest
representation; the shift label rides in the event title.

Two delivery routes, chosen for hostile hosting (serverless instances that
recycle mid-session break server-mediated downloads):

* ``ics_data_uri`` — the .ics as a ``data:`` link embedded in the page itself,
  so tapping it needs nothing from the server at all;
* ``google_calendar_url`` — one plain https link per on-call that opens the
  event pre-filled in Google Calendar; works in any browser and stays
  clickable inside a PDF handout.

Pure and stub-safe: standard library only.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List
from urllib.parse import urlencode

from .data_models import InputData

__all__ = [
    "resident_events",
    "resident_ics",
    "schedule_calendars_zip",
    "google_calendar_url",
    "ics_data_uri",
]

_PRODID = "-//Idea Gold Scheduler//Rota//EN"


def _escape(text: str) -> str:
    """Escape per RFC 5545 (backslash, semicolon, comma, newline)."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> List[str]:
    """Fold long content lines (RFC 5545 §3.1: max 75 octets, CRLF + space)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 74:
        return [line]
    out: List[str] = []
    chunk = b""
    for char in line:
        piece = char.encode("utf-8")
        if len(chunk) + len(piece) > 74 - (0 if not out else 1):
            out.append((" " if out else "") + chunk.decode("utf-8"))
            chunk = piece
        else:
            chunk += piece
    if chunk:
        out.append((" " if out else "") + chunk.decode("utf-8"))
    return out


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_")
    return slug or "resident"


def resident_events(df, data: InputData, person: str) -> List[dict]:
    """This resident's assignments as ``{day, label}`` dicts, date-ordered."""
    events: List[dict] = []
    for row in df.to_dict("records"):
        day = row.get("Date")
        if isinstance(day, datetime):
            day = day.date()  # datetime / pandas Timestamp -> plain date
        if not isinstance(day, date):
            continue
        for shift in data.shifts:
            if row.get(shift.label) == person:
                events.append({"day": day, "label": shift.label})
    events.sort(key=lambda e: (e["day"], e["label"]))
    return events


def resident_ics(
    df, data: InputData, person: str, *, now: datetime | None = None
) -> str:
    """One resident's calendar as .ics text (all-day event per assignment)."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(f'On-call — {person}')}",
    ]
    for event in resident_events(df, data, person):
        day: date = event["day"]
        label = event["label"]
        uid = f"{day.isoformat()}-{_slug(label)}-{_slug(person)}@idea-gold-scheduler"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            # All-day event: DTEND is exclusive, so it names the next day.
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(f'On call: {label}')}",
            f"DESCRIPTION:{_escape(f'{person} — {label} (Idea Gold Scheduler)')}",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    folded: List[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"


def google_calendar_url(day: date, label: str, person: str | None = None) -> str:
    """A pre-filled "add this on-call to Google Calendar" link.

    A plain https URL — clickable from any browser, chat message, or PDF, and
    entirely independent of the app's server (nothing to expire or break).
    Creates a single all-day event; the end date is exclusive per the API.
    """
    details = "Idea Gold Scheduler" + (f" — {person}" if person else "")
    query = urlencode({
        "action": "TEMPLATE",
        "text": f"On call: {label}",
        "dates": f"{day.strftime('%Y%m%d')}/{(day + timedelta(days=1)).strftime('%Y%m%d')}",
        "details": details,
    })
    return f"https://calendar.google.com/calendar/render?{query}"


def ics_data_uri(ics_text: str) -> str:
    """The .ics as a self-contained ``data:`` link href.

    The whole calendar rides inside the link itself, so tapping it works even
    when the hosting has recycled the instance that rendered the page — the
    failure mode behind "the file is not available on this website" on
    serverless hosts.
    """
    encoded = base64.b64encode(ics_text.encode("utf-8")).decode("ascii")
    return f"data:text/calendar;charset=utf-8;base64,{encoded}"


def schedule_calendars_zip(df, data: InputData, *, now: datetime | None = None) -> bytes:
    """A ZIP with one .ics per resident who holds at least one assignment."""
    stamp_now = now or datetime.now(timezone.utc)
    files: Dict[str, str] = {}
    for person in list(data.juniors) + list(data.seniors):
        if resident_events(df, data, person):
            files[f"{_slug(person)}.ics"] = resident_ics(df, data, person, now=stamp_now)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in sorted(files.items()):
            archive.writestr(name, text)
    return buffer.getvalue()
