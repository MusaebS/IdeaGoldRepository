from __future__ import annotations

import io
from typing import Dict

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .coloring import schedule_cell_colors
from .data_models import InputData
from .fairness import ResidentPoints, calculate_points
from .points import classify_slot
from .utils import weekend_holiday_dates

__all__ = [
    "build_fairness_frame",
    "build_assignment_frame",
    "schedule_to_excel_bytes",
    "schedule_to_pdf_bytes",
]


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def build_fairness_frame(
    points: Dict[str, ResidentPoints],
    data: InputData,
    df=None,
    prior_ledger=None,
) -> "pd.DataFrame":
    """Return a per-resident fairness table (total, weekend, NF, per-label).

    When the solved ``df`` is given, target and deviation columns are added
    from the same solver-resolved targets the fairness log uses (one source of
    truth), plus per-label call *counts* (``"<label> n"``). ``prior_ledger``
    adds ``Prior …`` / ``Cumulative …`` columns (and cumulative call counts
    when the ledger carries a per-label history), showing the multi-block
    picture the carryover balancing works from. A ``Notes`` column carries the
    same load annotations as the fairness log (groups, perks, exemptions,
    blackouts, reductions, leaves).
    """
    from .fairness import (  # shared target resolution / annotations
        _resolved_target,
        calculate_label_counts,
        load_annotation_notes,
        preference_satisfaction,
    )

    target_total = _resolved_target(df, "target_total", data.target_total) if df is not None else None
    target_total_map = _resolved_target(df, "target_total_map", data.target_total_map) if df is not None else None
    target_weekend = _resolved_target(df, "target_weekend", data.target_weekend) if df is not None else None
    counts = calculate_label_counts(df, data) if df is not None else None
    pref_stats = preference_satisfaction(df, data) if df is not None else {}

    labels = sorted(
        {label for info in points.values() for label in info.get("labels", {})}
    )
    prior = prior_ledger or {}
    prior_has_counts = any((entry or {}).get("label_counts") for entry in prior.values())
    rows = []
    for name in sorted(points):
        info = points[name]
        role = "Senior" if name in data.seniors else "Junior"
        row = {
            "Resident": name,
            "Role": role,
            "Total": info.get("total", 0.0),
            "Weekend": info.get("weekend", 0.0),
            # NF duty is a day count (coverage overlay), outside regular fairness.
            "NF duty (days)": int(info.get("night_float", 0.0)),
        }
        total_tgt = (target_total_map or {}).get(name, target_total)
        if total_tgt is not None:
            row["Total target"] = round(total_tgt, 1)
            row["Total dev"] = round(info.get("total", 0.0) - total_tgt, 1)
        if target_weekend and name in target_weekend:
            row["Weekend target"] = round(target_weekend[name], 1)
            row["Weekend dev"] = round(info.get("weekend", 0.0) - target_weekend[name], 1)
        for label in labels:
            row[label] = info.get("labels", {}).get(label, 0.0)
            if counts is not None:
                row[f"{label} n"] = counts.get(name, {}).get(label, 0)
        if prior:
            prior_entry = prior.get(name) or {}
            current = {
                "total": info.get("total", 0.0),
                "weekend": info.get("weekend", 0.0),
            }
            for col, dim in (("total", "total"), ("weekend", "weekend")):
                before = float(prior_entry.get(dim, 0.0))
                row[f"Prior {col}"] = round(before, 1)
                row[f"Cumulative {col}"] = round(before + current[dim], 1)
            if prior_has_counts and counts is not None:
                prior_counts = prior_entry.get("label_counts") or {}
                for label in labels:
                    row[f"{label} n cum"] = (
                        int(prior_counts.get(label, 0)) + counts.get(name, {}).get(label, 0)
                    )
        if name in pref_stats:
            matched, total = pref_stats[name]
            row["Pref match"] = f"{matched}/{total}"
        notes = load_annotation_notes(name, data)
        if notes:
            row["Notes"] = " ".join(notes)
        rows.append(row)
    return pd.DataFrame(rows)


def build_assignment_frame(df, data: InputData) -> "pd.DataFrame":
    """One row per (date, shift) slot: the per-call audit detail.

    Includes Unfilled slots so the record is complete; points/weekend/NF come
    from the same ``classify_slot`` the solver and fairness reporting use.
    """
    weekend_dates = weekend_holiday_dates(data)
    rows = []
    for record in df.to_dict("records"):
        day = record.get("Date")
        for sh in data.shifts:
            slot = classify_slot(day, sh, data, weekend_dates)
            person = record.get(sh.label)
            rows.append({
                "Date": day,
                "Day": record.get("Day") or (day.strftime("%A") if hasattr(day, "strftime") else ""),
                "Shift": sh.label,
                "Resident": "Unfilled" if person in (None, "Unfilled") else person,
                "Points": slot.points,
                "Weekend": slot.weekend,
                "Night float": slot.night_float,
            })
    return pd.DataFrame(rows)


def schedule_to_excel_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, ResidentPoints] | None = None,
    color_mode: str = "none",
    palette: Dict[str, str] | None = None,
    prior_ledger=None,
) -> bytes:
    """Serialise the schedule and fairness summary to an .xlsx workbook.

    Sheet "Schedule" is the calendar grid (rows = dates, columns = shift
    labels); sheet "Fairness" is the per-resident point summary. ``color_mode``
    (with an optional ``palette``) shades the schedule cells to match the
    on-screen view. Requires ``openpyxl``.
    """
    from openpyxl.styles import PatternFill

    points = points if points is not None else calculate_points(df, data)
    fairness = build_fairness_frame(points, data, df, prior_ledger)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Schedule", index=False)
        fairness.to_excel(writer, sheet_name="Fairness", index=False)
        if color_mode and color_mode != "none":
            worksheet = writer.sheets["Schedule"]
            columns = list(df.columns)
            for (row_idx, label), hexcolor in schedule_cell_colors(df, data, color_mode, palette).items():
                if label in columns:
                    rgb = hexcolor.lstrip("#").upper()
                    worksheet.cell(row=row_idx + 2, column=columns.index(label) + 1).fill = (
                        PatternFill(start_color=rgb, end_color=rgb, fill_type="solid")
                    )
    return buffer.getvalue()


def schedule_to_pdf_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, ResidentPoints] | None = None,
    color_mode: str = "none",
    palette: Dict[str, str] | None = None,
    prior_ledger=None,
) -> bytes:
    """Render the schedule (calendar grid) and fairness summary to a PDF.

    Cells are wrapping paragraphs with fixed, evenly-divided column widths so a
    wide 10-shift schedule fits the landscape page rather than overflowing.
    ``color_mode`` (with an optional ``palette``) shades the schedule cells to
    match the on-screen view. Requires ``reportlab`` to be installed.
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
    fairness = build_fairness_frame(points, data, df, prior_ledger)

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

    def _table(columns, rows, cell_bg=None):
        n = len(columns) or 1
        widths = [usable_width / n] * n
        header = [Paragraph(_fmt(c) or "  ", head) for c in columns]
        body = [[Paragraph(_fmt(r.get(c)) or "  ", cell) for c in columns] for r in rows]
        table = Table([header] + body, colWidths=widths, repeatRows=1)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]
        if cell_bg:
            # Per-cell shading to match the on-screen view; header stays dark.
            for (row_idx, label), hexcolor in cell_bg.items():
                if label in columns:
                    col = columns.index(label)
                    style.append(
                        ("BACKGROUND", (col, row_idx + 1), (col, row_idx + 1),
                         colors.HexColor(hexcolor))
                    )
        else:
            style.append(
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eeeeee")])
            )
        table.setStyle(TableStyle(style))
        return table

    schedule_bg = (
        schedule_cell_colors(df, data, color_mode, palette)
        if color_mode and color_mode != "none"
        else None
    )
    elements = [Paragraph("Idea Gold Schedule", styles["Title"]), Spacer(1, 8)]
    elements.append(_table(list(df.columns), df.to_dict("records"), cell_bg=schedule_bg))
    elements.append(Spacer(1, 14))
    elements.append(Paragraph("Fairness summary", styles["Heading2"]))
    elements.append(Spacer(1, 4))
    elements.append(_table(list(fairness.columns), fairness.to_dict("records")))
    doc.build(elements)
    return buffer.getvalue()
