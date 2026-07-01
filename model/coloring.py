"""Cell colouring for the schedule grid (screen + Excel/PDF exports).

Returns a ``{(row_index, shift_label): '#rrggbb'}`` map so the same colours can be
applied on-screen (a pandas Styler), in Excel (openpyxl fills) and in the PDF
(reportlab backgrounds). Modes let the user pick what the colour means; unfilled
slots are always flagged red. A ``palette`` lets the user recolour each role.
"""
from __future__ import annotations

from typing import Dict, Tuple

from .data_models import InputData
from .utils import effective_points, is_weekend, weekend_holiday_dates

__all__ = ["COLOR_MODES", "DEFAULT_PALETTE", "schedule_cell_colors"]

# UI label -> internal mode.
COLOR_MODES = {
    "Weekend + points": "auto",
    "Weekend only": "weekend",
    "Point value": "points",
    "Role (junior/senior)": "role",
    "None": "none",
}

# Named colour roles the user can override; values are #rrggbb hex strings.
DEFAULT_PALETTE = {
    "weekend": "#ffc107",    # amber
    "points": "#4a90d9",     # blue
    "senior": "#966edc",     # purple
    "junior": "#5ab478",     # green
    "unfilled": "#ffcccc",   # red
}


def _hex_to_rgb(hexstr: str) -> Tuple[int, int, int]:
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blend(hue: Tuple[int, int, int], ratio: float) -> str:
    """Blend white with ``hue`` by ``ratio`` (0 = white, 1 = full hue)."""
    ratio = max(0.0, min(1.0, ratio))
    r, g, b = (round(255 + (channel - 255) * ratio) for channel in hue)
    return f"#{r:02x}{g:02x}{b:02x}"


def schedule_cell_colors(
    df, data: InputData, mode: str = "auto", palette: Dict[str, str] | None = None
) -> Dict[Tuple[int, str], str]:
    """Return a colour per assigned/unfilled schedule cell for the given mode.

    ``palette`` overrides any of the named colour roles in ``DEFAULT_PALETTE``
    (``weekend``/``points``/``senior``/``junior``/``unfilled``); missing or empty
    entries fall back to the default.
    """
    pal = dict(DEFAULT_PALETTE)
    if palette:
        pal.update({k: v for k, v in palette.items() if v})
    weekend_hue = _hex_to_rgb(pal["weekend"])
    point_hue = _hex_to_rgb(pal["points"])
    senior_hue = _hex_to_rgb(pal["senior"])
    junior_hue = _hex_to_rgb(pal["junior"])
    unfilled = pal["unfilled"]

    records = df.to_dict("records")
    weekend_dates = weekend_holiday_dates(data)
    max_pts = 1.0
    for row in records:
        for shift in data.shifts:
            max_pts = max(max_pts, effective_points(row.get("Date"), shift, data))

    colors: Dict[Tuple[int, str], str] = {}
    for i, row in enumerate(records):
        day = row.get("Date")
        for shift in data.shifts:
            value = row.get(shift.label)
            if value in (None, "Unfilled"):
                colors[(i, shift.label)] = unfilled
                continue
            if mode == "none":
                continue
            weekend = is_weekend(day, shift, data.weekend_days, weekend_dates)
            ratio = effective_points(day, shift, data) / max_pts
            if mode == "role":
                colors[(i, shift.label)] = _blend(
                    senior_hue if shift.role == "Senior" else junior_hue, 0.35
                )
            elif mode == "weekend":
                if weekend:
                    colors[(i, shift.label)] = _blend(weekend_hue, 0.5)
            elif mode == "points":
                colors[(i, shift.label)] = _blend(point_hue, 0.2 + 0.6 * ratio)
            else:  # "auto": weekend hue vs weekday hue, intensity by points
                hue = weekend_hue if weekend else point_hue
                base = 0.25 if weekend else 0.08
                colors[(i, shift.label)] = _blend(hue, base + 0.55 * ratio)
    return colors
