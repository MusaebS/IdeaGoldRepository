"""Report builders: fairness/audit frames and the Excel / PDF exports.

The downloadable report is treated as a first-class deliverable: a titled,
legended, print-friendly PDF (landscape A4) and a styled Excel workbook that
carry the same information as the screen — schedule grid, per-role fairness,
annotations — without the raw-dump look. Pure "print view" helpers shape the
data (testable without reportlab/openpyxl); the two byte-builders only lay
that shaped data out.
"""
from __future__ import annotations

import io
from datetime import date as _date
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape

try:
    import pandas as pd
except ImportError:  # pragma: no cover - fallback when pandas missing
    from .pandas_stub import pd

from .coloring import DEFAULT_PALETTE, schedule_cell_colors
from .data_models import InputData
from .fairness import ResidentPoints, calculate_points
from .points import classify_slot
from .utils import compact_date_range, friendly_date, weekend_holiday_dates

__all__ = [
    "build_fairness_frame",
    "build_assignment_frame",
    "build_cumulative_frame",
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


def build_cumulative_frame(
    points: Dict[str, ResidentPoints],
    prior_ledger,
    data: InputData,
) -> "pd.DataFrame":
    """Long-form rows for the cumulative standing chart.

    One row per resident per segment (``Prior blocks`` from the uploaded
    ledger, ``This block`` from the solved schedule) with the resident's role
    and cumulative total, so a stacked bar chart can show how this block
    builds on history. Residents that appear only in the ledger (departed)
    are skipped — the chart describes the people being scheduled now.
    """
    prior = prior_ledger or {}
    rows = []
    for name in sorted(points):
        before = float((prior.get(name) or {}).get("total", 0.0))
        current = float(points[name].get("total", 0.0))
        role = "Senior" if name in data.seniors else "Junior"
        cumulative = round(before + current, 1)
        rows.append({
            "Resident": name, "Role": role, "Segment": "Prior blocks",
            "Points": round(before, 1), "Cumulative": cumulative,
        })
        rows.append({
            "Resident": name, "Role": role, "Segment": "This block",
            "Points": round(current, 1), "Cumulative": cumulative,
        })
    return pd.DataFrame(rows)


# --- print views (pure data shaping, shared by the PDF and tested directly) --

def schedule_print_view(df, data: InputData) -> Tuple[List[str], List[dict], set]:
    """Shape the schedule frame for print: columns, rows, weekend row indexes.

    * ``Date`` and ``Day`` merge into one friendly ``Date`` column
      ("Sat 07 Jan") — the two duplicated each other and wasted width.
    * Empty cells in shift columns become an explicit ``Unfilled`` so a
      printed gap can't be mistaken for a formatting accident.
    * Weekend rows (by date, including weekend-flagged holidays) are returned
      for row shading. Row indexes match the input frame, so the per-cell
      colour map stays valid.
    """
    columns = list(df.columns)
    shift_labels = {s.label for s in data.shifts}
    merged = "Date" in columns
    out_columns = (
        ["Date"] + [c for c in columns if c not in ("Date", "Day")]
        if merged
        else columns
    )
    weekend_days = (
        set(data.weekend_days) if data.weekend_days is not None else {5, 6}
    )
    holiday_weekends = weekend_holiday_dates(data)
    weekend_rows: set = set()
    out_rows: List[dict] = []
    for idx, record in enumerate(df.to_dict("records")):
        day = record.get("Date")
        row: dict = {}
        if merged:
            row["Date"] = friendly_date(day)
        for column in out_columns:
            if column == "Date" and merged:
                continue
            value = record.get(column)
            if column in shift_labels and (
                value is None or (isinstance(value, float) and value != value)
            ):
                value = "Unfilled"
            row[column] = value
        out_rows.append(row)
        if hasattr(day, "weekday") and (
            day.weekday() in weekend_days or day in holiday_weekends
        ):
            weekend_rows.add(idx)
    return out_columns, out_rows, weekend_rows


# Print column order for the per-role fairness tables. Raw per-label POINT
# columns and the Notes column are deliberately left to the CSV/Excel: counts
# read better on paper, and notes become numbered footnotes below the table.
_PRINT_LEAD_COLS = (
    "Resident", "Total", "Total target", "Total dev",
    "Weekend", "Weekend target", "Weekend dev",
    "Prior total", "Cumulative total",
)
_PRINT_TAIL_COLS = ("NF duty (days)", "Pref match")


def fairness_print_sections(
    fairness: "pd.DataFrame", data: InputData
) -> List[Tuple[str, List[str], List[dict]]]:
    """Split the fairness frame into per-role print sections.

    Returns ``[(title, columns, rows)]`` — one section per role present, in
    Junior → Senior order, each with a curated print column set. Columns that
    would be entirely empty/zero (NF duty on a roster without night float,
    Pref match when nobody has preferences) are dropped.
    """
    records = fairness.to_dict("records")
    if not records:
        return []
    count_cols = [
        c for c in fairness.columns
        if c.endswith(" n") and not c.endswith(" n cum")
    ]
    sections: List[Tuple[str, List[str], List[dict]]] = []
    for role, title in (("Junior", "Juniors"), ("Senior", "Seniors")):
        rows = [r for r in records if r.get("Role") == role]
        if not rows:
            continue
        columns = [c for c in _PRINT_LEAD_COLS if c in fairness.columns]
        columns += count_cols
        for col in _PRINT_TAIL_COLS:
            if col in fairness.columns and any(_truthy(r.get(col)) for r in rows):
                columns.append(col)
        sections.append(
            (title, columns, [{c: r.get(c) for c in columns} for r in rows])
        )
    return sections


def _truthy(value) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and value != value:  # NaN
        return False
    return bool(value)


def annotation_footnotes(fairness: "pd.DataFrame") -> Tuple[Dict[str, int], List[str]]:
    """Number the residents that carry notes: (marker map, footnote lines).

    The marker map (resident → 1-based number) lets the fairness table show a
    small reference instead of the full annotation text; the lines render as
    a Notes block after the tables ("1. Alice — [blackout …] [leave 2d comp]").
    """
    if "Notes" not in getattr(fairness, "columns", []):
        return {}, []
    markers: Dict[str, int] = {}
    lines: List[str] = []
    for record in fairness.to_dict("records"):
        note = record.get("Notes")
        if not _truthy(note):
            continue
        markers[record["Resident"]] = len(lines) + 1
        lines.append(f"{len(lines) + 1}. {record['Resident']} — {note}")
    return markers, lines


def report_header_lines(data: InputData, df, quality=None) -> List[str]:
    """Metadata lines for the report title block."""
    days = (data.end_date - data.start_date).days + 1
    lines = [
        f"Block {compact_date_range(data.start_date, data.end_date)} "
        f"({days} days) · {len(data.juniors)} juniors · {len(data.seniors)} seniors "
        f"· {len(data.shifts)} shift types",
        f"Generated {_date.today().strftime('%a %d %b %Y')}",
    ]
    attrs = getattr(df, "attrs", {}) or {}
    status = attrs.get("solver_status")
    detail = []
    if status:
        wall = attrs.get("wall_time_sec")
        detail.append(f"Solver {status}" + (f" in {wall:.0f}s" if wall is not None else ""))
    if quality:
        detail.append(f"Quality {quality.get('score', 0)}/100")
        unfilled = quality.get("unfilled", 0)
        detail.append("all slots filled" if not unfilled else f"{unfilled} slot(s) unfilled")
    if detail:
        lines.append(" · ".join(detail))
    return lines


def legend_entries(color_mode: str, palette=None) -> List[Tuple[str | None, str]]:
    """(hex colour | None, label) pairs describing the schedule shading."""
    pal = {**DEFAULT_PALETTE, **(palette or {})}
    entries: List[Tuple[str | None, str]] = []
    if color_mode in ("auto", "weekend"):
        entries.append((pal["weekend"], "Weekend / holiday shift"))
    if color_mode in ("auto", "points"):
        entries.append((pal["points"], "Weekday shift (deeper = more points)"))
    if color_mode == "role":
        entries.append((pal["senior"], "Senior shift"))
        entries.append((pal["junior"], "Junior shift"))
    entries.append((pal["unfilled"], "Unfilled slot (no resident)"))
    entries.append((None, "Closed = shift stood down (not demand)"))
    return entries


# --- Excel --------------------------------------------------------------------

def schedule_to_excel_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, ResidentPoints] | None = None,
    color_mode: str = "none",
    palette: Dict[str, str] | None = None,
    prior_ledger=None,
) -> bytes:
    """Serialise the schedule, fairness summary, and per-call audit to .xlsx.

    Sheet "Schedule" is the calendar grid (frozen header + date column, real
    date formatting, explicit "Unfilled" in empty slots, cells shaded to match
    the on-screen view); sheet "Fairness" is the per-resident summary with a
    wrapped Notes column; sheet "Per-call" (when the frame still carries its
    Date column) is the slot-by-slot audit. Requires ``openpyxl``.
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    points = points if points is not None else calculate_points(df, data)
    fairness = build_fairness_frame(points, data, df, prior_ledger)

    # Render copy only: an empty shift cell prints as an explicit "Unfilled";
    # the caller's frame (used for fairness maths) is never touched.
    shift_labels = [s.label for s in data.shifts if s.label in df.columns]
    render_df = df.copy()
    for label in shift_labels:
        render_df[label] = [
            "Unfilled" if v is None or (isinstance(v, float) and v != v) else v
            for v in render_df[label]
        ]

    header_fill = PatternFill(start_color="333333", end_color="333333", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    def _polish(worksheet, frame, wide_cols=(), wrap_cols=()):
        worksheet.freeze_panes = "B2"
        for col_idx, column in enumerate(frame.columns, start=1):
            head = worksheet.cell(row=1, column=col_idx)
            head.fill = header_fill
            head.font = header_font
            letter = get_column_letter(col_idx)
            if column in wrap_cols:
                width = 46
                for row_idx in range(2, len(frame) + 2):
                    worksheet.cell(row=row_idx, column=col_idx).alignment = (
                        Alignment(wrap_text=True, vertical="top")
                    )
            elif column in wide_cols:
                width = 16
            else:
                width = max(10, min(20, len(str(column)) + 2))
            worksheet.column_dimensions[letter].width = width

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        render_df.to_excel(writer, sheet_name="Schedule", index=False)
        fairness.to_excel(writer, sheet_name="Fairness", index=False)
        schedule_ws = writer.sheets["Schedule"]
        _polish(schedule_ws, render_df, wide_cols=("Date", "Day"))
        if "Date" in render_df.columns:
            date_col = list(render_df.columns).index("Date") + 1
            for row_idx in range(2, len(render_df) + 2):
                schedule_ws.cell(row=row_idx, column=date_col).number_format = "ddd dd mmm"
        _polish(
            writer.sheets["Fairness"], fairness,
            wide_cols=("Resident",), wrap_cols=("Notes",),
        )
        if color_mode and color_mode != "none":
            columns = list(render_df.columns)
            for (row_idx, label), hexcolor in schedule_cell_colors(df, data, color_mode, palette).items():
                if label in columns:
                    rgb = hexcolor.lstrip("#").upper()
                    schedule_ws.cell(row=row_idx + 2, column=columns.index(label) + 1).fill = (
                        PatternFill(start_color=rgb, end_color=rgb, fill_type="solid")
                    )
        if "Date" in df.columns:
            per_call = build_assignment_frame(df, data)
            per_call.to_excel(writer, sheet_name="Per-call", index=False)
            _polish(writer.sheets["Per-call"], per_call, wide_cols=("Date", "Shift", "Resident"))
    return buffer.getvalue()


# --- PDF ------------------------------------------------------------------------

_WEEKEND_ROW_TINT = "#f6efdc"   # soft parchment behind weekend rows


def schedule_to_pdf_bytes(
    df: "pd.DataFrame",
    data: InputData,
    points: Dict[str, ResidentPoints] | None = None,
    color_mode: str = "none",
    palette: Dict[str, str] | None = None,
    prior_ledger=None,
) -> bytes:
    """Render the full report to a landscape-A4 PDF.

    Layout: title block (block dates, roster size, generated-on, solver
    status + quality) → colour legend → schedule grid (friendly dates,
    weekend rows tinted, explicit Unfilled, cells shaded to match the
    screen) → per-role fairness tables (curated print columns, footnote
    markers) → numbered Notes block. Column widths are content-aware (name
    columns wide, numerics narrow) instead of evenly split, and cell text is
    XML-escaped so names with ``&``/``<`` can't break the renderer.
    Requires ``reportlab``.
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

    from .fairness import schedule_quality

    points = points if points is not None else calculate_points(df, data)
    fairness = build_fairness_frame(points, data, df, prior_ledger)
    quality = schedule_quality(df, data, points=points)

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7, leading=8.5)
    cell_dim = ParagraphStyle(
        "cell_dim", parent=cell, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#8a8378"),
    )
    head = ParagraphStyle(
        "head", fontName="Helvetica-Bold", fontSize=7, leading=8.5,
        textColor=colors.white,
    )
    meta = ParagraphStyle(
        "meta", fontName="Helvetica", fontSize=8.5, leading=11,
        textColor=colors.HexColor("#4a4438"),
    )
    note_style = ParagraphStyle("note", fontName="Helvetica", fontSize=7.5, leading=10)

    page = landscape(A4)
    usable_width = page[0] - 2 * cm
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page,
        leftMargin=cm,
        rightMargin=cm,
        topMargin=cm,
        bottomMargin=1.2 * cm,
        title="Idea Gold Schedule",
    )

    def _cell_par(value, marker=None):
        text = escape(_fmt(value)) or "&nbsp;"
        if value in ("Unfilled", "Closed"):
            return Paragraph(text, cell_dim)
        if marker:
            text += f" <super>{marker}</super>"
        return Paragraph(text, cell)

    def _widths(columns, first_col_cm):
        n = len(columns) or 1
        if n == 1:
            return [usable_width]
        first = min(first_col_cm * cm, usable_width / 2)
        rest = (usable_width - first) / (n - 1)
        return [first] + [rest] * (n - 1)

    def _table(columns, rows, cell_bg=None, weekend_rows=None,
               first_col_cm=2.6, markers=None):
        header = [Paragraph(escape(_fmt(c)) or "&nbsp;", head) for c in columns]
        body = []
        for row in rows:
            cells = []
            for col_idx, column in enumerate(columns):
                marker = None
                if markers and col_idx == 0:
                    marker = markers.get(row.get(column))
                cells.append(_cell_par(row.get(column), marker))
            body.append(cells)
        table = Table(
            [header] + body, colWidths=_widths(columns, first_col_cm), repeatRows=1
        )
        style = [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9b2a4")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        if weekend_rows:
            for row_idx in sorted(weekend_rows):
                style.append((
                    "BACKGROUND", (0, row_idx + 1), (-1, row_idx + 1),
                    colors.HexColor(_WEEKEND_ROW_TINT),
                ))
        elif not cell_bg:
            style.append((
                "ROWBACKGROUNDS", (0, 1), (-1, -1),
                [colors.white, colors.HexColor("#f2f0eb")],
            ))
        if cell_bg:
            # Per-cell shading to match the on-screen view; header stays dark.
            for (row_idx, label), hexcolor in cell_bg.items():
                if label in columns:
                    col = columns.index(label)
                    style.append(
                        ("BACKGROUND", (col, row_idx + 1), (col, row_idx + 1),
                         colors.HexColor(hexcolor))
                    )
        table.setStyle(TableStyle(style))
        return table

    def _legend_flowable():
        cells = [
            Paragraph(
                (f'<font color="{hexcolor}">■ </font>' if hexcolor else "")
                + escape(label),
                meta,
            )
            for hexcolor, label in legend_entries(color_mode, palette)
        ]
        table = Table([cells], colWidths=[usable_width / max(1, len(cells))] * len(cells))
        table.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return table

    def _footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#8a8378"))
        canvas.drawString(cm, 0.6 * cm, "Idea Gold Scheduler")
        canvas.drawRightString(page[0] - cm, 0.6 * cm, f"Page {document.page}")
        canvas.restoreState()

    schedule_bg = (
        schedule_cell_colors(df, data, color_mode, palette)
        if color_mode and color_mode != "none"
        else None
    )
    sched_cols, sched_rows, weekend_rows = schedule_print_view(df, data)
    markers, footnotes = annotation_footnotes(fairness)

    elements = [Paragraph("Idea Gold Schedule", styles["Title"])]
    for line in report_header_lines(data, df, quality):
        elements.append(Paragraph(escape(line), meta))
    elements.append(Spacer(1, 6))
    elements.append(_legend_flowable())
    elements.append(Spacer(1, 6))
    elements.append(_table(
        sched_cols, sched_rows, cell_bg=schedule_bg, weekend_rows=weekend_rows,
    ))
    for title, columns, rows in fairness_print_sections(fairness, data):
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f"Fairness — {escape(title)}", styles["Heading2"]))
        elements.append(Spacer(1, 3))
        elements.append(_table(columns, rows, first_col_cm=3.4, markers=markers))
    if footnotes:
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("Notes", styles["Heading3"]))
        for line in footnotes:
            elements.append(Paragraph(escape(line), note_style))
    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
