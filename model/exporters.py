from __future__ import annotations

import io
from typing import Dict

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .data_models import InputData
from .fairness import calculate_points

__all__ = ["build_fairness_frame", "schedule_to_excel_bytes", "schedule_to_pdf_bytes"]


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def build_fairness_frame(
    points: Dict[str, Dict[str, float]], data: InputData
) -> "pd.DataFrame":
    """Return a per-resident fairness table (total, weekend, NF, per-label)."""
    labels = sorted(
        {label for info in points.values() for label in info.get("labels", {})}
    )
    rows = []
    for name in sorted(points):
        info = points[name]
        role = "Senior" if name in data.seniors else "Junior"
        row = {
            "Resident": name,
            "Role": role,
            "Total": info.get("total", 0.0),
            "Weekend": info.get("weekend", 0.0),
            "Night Float": info.get("night_float", 0.0),
        }
        for label in labels:
            row[label] = info.get("labels", {}).get(label, 0.0)
        rows.append(row)
    return pd.DataFrame(rows)


def schedule_to_excel_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, Dict[str, float]] | None = None,
) -> bytes:
    """Serialise the schedule and fairness summary to an .xlsx workbook.

    Sheet "Schedule" is the calendar grid (rows = dates, columns = shift
    labels); sheet "Fairness" is the per-resident point summary. Requires
    ``openpyxl`` to be installed.
    """
    points = points if points is not None else calculate_points(df, data)
    fairness = build_fairness_frame(points, data)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Schedule", index=False)
        fairness.to_excel(writer, sheet_name="Fairness", index=False)
    return buffer.getvalue()


def schedule_to_pdf_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, Dict[str, float]] | None = None,
) -> bytes:
    """Render the schedule (calendar grid) and fairness summary to a PDF.

    Cells are wrapping paragraphs with fixed, evenly-divided column widths so a
    wide 10-shift schedule fits the landscape page rather than overflowing.
    Requires ``reportlab`` to be installed.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    points = points if points is not None else calculate_points(df, data)
    fairness = build_fairness_frame(points, data)

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=6, leading=7)
    head = ParagraphStyle(
        "head", fontName="Helvetica-Bold", fontSize=6, leading=7, textColor=colors.white
    )

    page = landscape(A4)
    usable_width = page[0] - 2 * cm
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page,
        leftMargin=cm,
        rightMargin=cm,
        topMargin=cm,
        bottomMargin=cm,
    )

    def _table(columns, rows):
        n = len(columns) or 1
        widths = [usable_width / n] * n
        header = [Paragraph(_fmt(c) or " ", head) for c in columns]
        body = [[Paragraph(_fmt(r.get(c)) or " ", cell) for c in columns] for r in rows]
        table = Table([header] + body, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eeeeee")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        return table

    elements = [Paragraph("Idea Gold Schedule", styles["Title"]), Spacer(1, 8)]
    elements.append(_table(list(df.columns), df.to_dict("records")))
    elements.append(Spacer(1, 14))
    elements.append(Paragraph("Fairness summary", styles["Heading2"]))
    elements.append(Spacer(1, 4))
    elements.append(_table(list(fairness.columns), fairness.to_dict("records")))
    doc.build(elements)
    return buffer.getvalue()
