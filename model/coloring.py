"""Cell colouring for the schedule grid (screen + Excel/PDF exports).

Returns a ``{(row_index, shift_label): '#rrggbb'}`` map so the same colours can be
applied on-screen (a pandas Styler), in Excel (openpyxl fills) and in the PDF
(reportlab backgrounds). Modes let the user pick what the colour means; unfilled
slots are always flagged red. A ``palette`` lets the user recolour each role.
"""
from __future__ import annotations

import colorsys
import re
from typing import Dict, Mapping, Tuple

from .data_models import InputData
from .utils import effective_points, is_weekend, weekend_holiday_dates

__all__ = [
    "COLOR_MODES", "DEFAULT_PALETTE", "is_hex_color", "schedule_cell_colors",
    "theme_palette",
]

# UI label -> internal mode. The first entry is the on-screen default.
COLOR_MODES = {
    "Senior / Junior / Weekend": "role_weekend_3",
    "Role + weekend": "role_weekend",
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

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_hex_color(value: object) -> bool:
    """Whether ``value`` is a complete six-digit CSS hex colour."""
    return isinstance(value, str) and _HEX_COLOR.fullmatch(value) is not None


def _hex_to_rgb(hexstr: str) -> Tuple[int, int, int]:
    if not is_hex_color(hexstr):
        raise ValueError(f"Invalid colour {hexstr!r}; expected #RRGGBB")
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _blend(hue: Tuple[int, int, int], ratio: float) -> str:
    """Blend white with ``hue`` by ``ratio`` (0 = white, 1 = full hue)."""
    ratio = max(0.0, min(1.0, ratio))
    r, g, b = (round(255 + (channel - 255) * ratio) for channel in hue)
    return f"#{r:02x}{g:02x}{b:02x}"


# Hue rotation (degrees) per palette role for theme_palette. Roles must differ
# by *hue*, not lightness: the renderer already encodes per-cell intensity via
# the white-blend ratios in schedule_cell_colors, so lightness variations
# would collapse together in every mode.
_THEME_HUE_SHIFTS = {"points": 0.0, "weekend": 45.0, "senior": -45.0, "junior": 150.0}


def theme_palette(base: str, current: Mapping[str, str] | None = None) -> Dict[str, str]:
    """Derive a full palette from one theme colour.

    The base is normalised (lightness clamped to 0.35–0.65, saturation floored
    at 0.40 so grey or near-white inputs still yield distinct roles), then the
    four schedule roles get hue-rotated variants of it. ``unfilled`` is a
    warning flag, not a theme colour: it keeps the caller's current choice, or
    the red default. Deterministic — the same base always yields the same map.
    """
    r, g, b = _hex_to_rgb(base)
    h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    lightness = min(0.65, max(0.35, lightness))
    s = max(s, 0.40)
    palette: Dict[str, str] = {}
    for role, shift_deg in _THEME_HUE_SHIFTS.items():
        rr, gg, bb = colorsys.hls_to_rgb((h + shift_deg / 360.0) % 1.0, lightness, s)
        palette[role] = f"#{round(rr * 255):02x}{round(gg * 255):02x}{round(bb * 255):02x}"
    palette["unfilled"] = (current or {}).get("unfilled") or DEFAULT_PALETTE["unfilled"]
    return palette


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
        pal.update({k: v for k, v in palette.items() if k in pal and is_hex_color(v)})
    weekend_hue = _hex_to_rgb(pal["weekend"])
    point_hue = _hex_to_rgb(pal["points"])
    senior_hue = _hex_to_rgb(pal["senior"])
    junior_hue = _hex_to_rgb(pal["junior"])
    unfilled = pal["unfilled"]

    records = df.to_dict("records")
    weekend_dates = weekend_holiday_dates(data)
    # Every holiday date (not only weekend-flagged ones): holidays carry more
    # points, so they are shaded like weekends to flag that at a glance.
    holiday_dates = {h[0] for h in (getattr(data, "holidays", None) or [])}
    max_pts = 1.0
    for row in records:
        for shift in data.shifts:
            max_pts = max(max_pts, effective_points(row.get("Date"), shift, data))

    colors: Dict[Tuple[int, str], str] = {}
    for i, row in enumerate(records):
        day = row.get("Date")
        for shift in data.shifts:
            value = row.get(shift.label)
            if value == "Closed":
                continue  # stood-down shift: no fill (reads as a plain cell)
            if value in (None, "Unfilled"):
                colors[(i, shift.label)] = unfilled
                continue
            if mode == "none":
                continue
            weekend = (
                is_weekend(day, shift, data.weekend_days, weekend_dates)
                or day in holiday_dates
            )
            ratio = effective_points(day, shift, data) / max_pts
            if mode == "role_weekend_3":
                # Three independent colours: seniors, juniors, and one for every
                # weekend/holiday shift (regardless of role). Each is a palette
                # picker the user can recolour.
                if weekend:
                    colors[(i, shift.label)] = _blend(weekend_hue, 0.5)
                else:
                    hue = senior_hue if shift.role == "Senior" else junior_hue
                    colors[(i, shift.label)] = _blend(hue, 0.4)
            elif mode == "role_weekend":
                # Role hue chooses the colour; juniors read paler than seniors
                # and weekend cells are a darker shade of the same role hue.
                # The junior/senior palette pickers still drive the two hues.
                hue = senior_hue if shift.role == "Senior" else junior_hue
                if shift.role == "Senior":
                    blend = 0.70 if weekend else 0.45
                else:
                    blend = 0.48 if weekend else 0.25
                colors[(i, shift.label)] = _blend(hue, blend)
            elif mode == "role":
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
