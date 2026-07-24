"""The calendar handout: ONE compact PDF carrying the whole roster's on-calls.

This is the single file a scheduler sends to the department. Every resident
gets a small block with their dates, and two ways to get them into a phone:

* **Add all** — one link per resident holding their entire ``.ics`` inline (a
  ``data:`` URI), so a single tap imports every one of their shifts. Nothing is
  fetched from the app's server, so it keeps working wherever the PDF travels.
  Readers differ in how they treat non-``http`` links, which is why the
  per-date links below exist too.
* **Each date** — a plain Google Calendar link that adds that one shift. These
  work in any reader and are the fallback when "Add all" is blocked.

Laid out in two columns with tight type so a large department fits a page or
two instead of one section per page.

Requires ``reportlab`` (guarded import, like the schedule PDF).
"""
from __future__ import annotations

import io
from xml.sax.saxutils import escape, quoteattr

from .data_models import InputData
from .ics import google_calendar_url, ics_data_uri, resident_ics, resident_events
from .utils import friendly_date

__all__ = ["calendar_handout_pdf_bytes"]

_INK = "#2f2a24"
_MUTED = "#6d6459"
_LINK = "#1a56b0"
_RULE = "#d9d3c7"


def _short_date(day) -> str:
    """``Sat 04 Apr`` trimmed to ``Sat 04`` when the month repeats is overkill —
    keep the full short form, it is only 10 characters."""
    return friendly_date(day)


def calendar_handout_pdf_bytes(df, data: InputData) -> bytes:
    """Render the compact per-resident on-call handout to PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        KeepTogether,
        PageTemplate,
        Paragraph,
        Spacer,
    )

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "Name", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8.6, leading=10.4, textColor=colors.HexColor(_INK),
        spaceBefore=5, spaceAfter=1,
    )
    row_style = ParagraphStyle(
        "Row", parent=styles["Normal"], fontSize=7.6, leading=9.4,
        textColor=colors.HexColor(_INK), leftIndent=5,
    )
    head_style = ParagraphStyle(
        "Head", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=14, leading=17, textColor=colors.HexColor(_INK),
    )
    note_style = ParagraphStyle(
        "Note", parent=styles["Normal"], fontSize=7.8, leading=10,
        textColor=colors.HexColor(_MUTED),
    )

    buffer = io.BytesIO()
    margin = 1.1 * cm
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
        title="On-call calendar handout",
    )
    usable_w = doc.width
    gutter = 0.7 * cm
    col_w = (usable_w - gutter) / 2
    # The masthead only exists on page 1; both templates share the two columns.
    head_h = 2.5 * cm
    first_frames = [
        Frame(margin, margin, col_w, doc.height - head_h, id="c1", showBoundary=0),
        Frame(margin + col_w + gutter, margin, col_w, doc.height - head_h,
              id="c2", showBoundary=0),
    ]
    rest_frames = [
        Frame(margin, margin, col_w, doc.height, id="r1", showBoundary=0),
        Frame(margin + col_w + gutter, margin, col_w, doc.height, id="r2",
              showBoundary=0),
    ]

    span = f"{friendly_date(data.start_date)} – {friendly_date(data.end_date)}"

    def _masthead(canvas, _doc):
        canvas.saveState()
        top = A4[1] - margin
        canvas.setFillColor(colors.HexColor(_INK))
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(margin, top - 12, "On-call calendar handout")
        canvas.setFillColor(colors.HexColor(_MUTED))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(margin, top - 26, f"Block {span}")
        canvas.drawString(
            margin, top - 38,
            "Find your name, then tap a date — that adds the shift to your "
            "calendar. This works on any phone.",
        )
        canvas.drawString(
            margin, top - 50,
            "“Add all” adds every shift at once, but only in readers that allow "
            "it (usually on a computer, rarely on a phone).",
        )
        canvas.setStrokeColor(colors.HexColor(_RULE))
        canvas.setLineWidth(0.6)
        canvas.line(margin, top - 58, A4[0] - margin, top - 58)
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="first", frames=first_frames, onPage=_masthead),
        PageTemplate(id="rest", frames=rest_frames),
    ])

    story = []
    listed = 0
    for person in list(data.juniors) + list(data.seniors):
        events = resident_events(df, data, person)
        if not events:
            continue
        listed += 1
        add_all = quoteattr(ics_data_uri(resident_ics(df, data, person)))
        block = [
            Paragraph(
                f"{escape(person)}"
                f"<font size=7 color='{_MUTED}'>  {len(events)} on-call(s)</font>"
                f"  <link href={add_all}>"
                f"<font color='{_LINK}'><u>Add all</u></font></link>",
                name_style,
            )
        ]
        for event in events:
            url = quoteattr(google_calendar_url(event["day"], event["label"], person))
            block.append(Paragraph(
                f"<link href={url}><font color='{_LINK}'>"
                f"{escape(_short_date(event['day']))}</font></link>"
                f"  <font color='{_MUTED}'>{escape(event['label'])}</font>",
                row_style,
            ))
        story.append(KeepTogether(block))

    if not listed:
        story.append(Paragraph("No assignments in this schedule.", head_style))
    else:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            "Each date link adds that single shift and works everywhere. "
            "“Add all” carries the whole calendar inside the link — no internet "
            "connection to the scheduling app — but phone PDF readers usually "
            "block that kind of link; ask for your .ics file if you want every "
            "shift in one go.",
            note_style,
        ))
    doc.build(story)
    return buffer.getvalue()
