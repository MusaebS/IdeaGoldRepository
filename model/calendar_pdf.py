"""The calendar handout: a sendable PDF listing every resident's on-calls.

One document the scheduler can drop into a group chat or email: every
resident has their own section listing their on-call dates, and each date
carries a tappable "Add to calendar" link (a pre-filled Google Calendar URL —
plain https, so it stays clickable wherever the PDF travels and depends on
nothing hosted by the app).

A PDF cannot bulk-import events by itself — tapping adds one event at a
time. The per-resident ``.ics`` files remain the one-tap "everything at once"
route; this handout is the zero-setup, works-anywhere companion.

Requires ``reportlab`` (guarded import, like the schedule PDF).
"""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from .data_models import InputData
from .ics import google_calendar_url, resident_events
from .utils import friendly_date

__all__ = ["calendar_handout_pdf_bytes"]


def calendar_handout_pdf_bytes(df, data: InputData) -> bytes:
    """Render the per-resident on-call handout to PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    title = styles["Title"]
    name_style = ParagraphStyle(
        "ResidentName", parent=styles["Heading2"], spaceBefore=10, spaceAfter=2
    )
    cell = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9, leading=12)
    note = ParagraphStyle(
        "Note", parent=styles["Normal"], fontSize=8, leading=10,
        textColor=colors.HexColor("#555555"),
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.6 * cm, rightMargin=1.6 * cm,
        topMargin=1.4 * cm, bottomMargin=1.4 * cm,
        title="On-call calendar handout",
    )
    span = f"{friendly_date(data.start_date)} – {friendly_date(data.end_date)}"
    story = [
        Paragraph("On-call calendar handout", title),
        Paragraph(
            escape(f"Block {span}. Find your name; tap “Add to calendar” on a "
                   "date to put that on-call into your phone's calendar."),
            note,
        ),
        Spacer(1, 8),
    ]

    grid_style = TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1),
         [colors.white, colors.HexColor("#f4f1e8")]),
        ("LINEBELOW", (0, -1), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])

    listed = 0
    for person in list(data.juniors) + list(data.seniors):
        events = resident_events(df, data, person)
        if not events:
            continue
        listed += 1
        rows = []
        for event in events:
            url = google_calendar_url(event["day"], event["label"], person)
            rows.append([
                Paragraph(escape(friendly_date(event["day"])), cell),
                Paragraph(escape(event["label"]), cell),
                Paragraph(
                    f'<link href="{escape(url, {chr(34): "&quot;"})}">'
                    "<u><font color='#1a56b0'>Add to calendar</font></u></link>",
                    cell,
                ),
            ])
        table = Table(rows, colWidths=[4.2 * cm, 9.2 * cm, 4.0 * cm])
        table.setStyle(grid_style)
        block = [
            Paragraph(escape(person), name_style),
            Paragraph(escape(f"{len(events)} on-call(s)"), note),
            table,
        ]
        # Keep short sections on one page; long ones may flow.
        story.append(KeepTogether(block) if len(rows) <= 12 else block[0])
        if len(rows) > 12:
            story.extend(block[1:])

    if not listed:
        story.append(Paragraph("No assignments in this schedule.", cell))
    doc.build(story)
    return buffer.getvalue()
