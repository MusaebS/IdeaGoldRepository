import sys, os
import re
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover - fallback when pandas missing
    from model import optimiser as opt
    pd = opt.pd

from model.data_models import ShiftTemplate, InputData
from model.coloring import COLOR_MODES, schedule_cell_colors, _blend

_HEX = re.compile(r"^#[0-9a-f]{6}$")


def _sample():
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="N", role="Senior", night_float=True, thu_weekend=False, points=2.0),
    ]
    data = InputData(
        start_date=date(2023, 1, 7),
        end_date=date(2023, 1, 9),
        shifts=shifts,
        juniors=["Alice"],
        seniors=["Bob"],
        nf_juniors=[],
        nf_seniors=["Bob"],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    df = pd.DataFrame([
        {"Date": date(2023, 1, 7), "Day": "Saturday", "D": "Alice", "N": "Bob"},   # weekend
        {"Date": date(2023, 1, 9), "Day": "Monday", "D": "Unfilled", "N": "Bob"},  # weekday, one gap
    ])
    return df, data


def test_blend_endpoints():
    hue = (74, 144, 217)
    assert _blend(hue, 0.0) == "#ffffff"          # 0 = white
    assert _blend(hue, 1.0) == "#4a90d9"          # 1 = full hue
    # clamped outside [0, 1]
    assert _blend(hue, -1.0) == "#ffffff"
    assert _blend(hue, 2.0) == "#4a90d9"


def test_all_colors_are_valid_hex():
    df, data = _sample()
    for label in COLOR_MODES:
        mode = COLOR_MODES[label]
        for value in schedule_cell_colors(df, data, mode).values():
            assert _HEX.match(value), f"{mode}: {value!r} is not #rrggbb"


def test_unfilled_always_flagged_red_every_mode():
    df, data = _sample()
    for mode in COLOR_MODES.values():
        colors = schedule_cell_colors(df, data, mode)
        assert colors[(1, "D")] == "#ffcccc", f"unfilled not red in mode {mode!r}"


def test_none_mode_only_colors_unfilled():
    df, data = _sample()
    colors = schedule_cell_colors(df, data, "none")
    # Only the single unfilled cell carries a colour.
    assert colors == {(1, "D"): "#ffcccc"}


def test_weekend_mode_colors_weekend_only():
    df, data = _sample()
    colors = schedule_cell_colors(df, data, "weekend")
    assert (0, "N") in colors          # Saturday -> shaded
    assert (1, "N") not in colors      # Monday -> no shade
    assert colors[(1, "D")] == "#ffcccc"  # unfilled still flagged


def test_role_mode_distinguishes_senior_from_junior():
    df, data = _sample()
    colors = schedule_cell_colors(df, data, "role")
    junior = colors[(0, "D")]          # Alice, Junior shift
    senior = colors[(0, "N")]          # Bob, Senior shift
    assert junior != senior
    # role colour ignores weekend: Monday senior shift matches Saturday senior
    assert colors[(1, "N")] == senior


def test_points_mode_higher_points_more_intense():
    df, data = _sample()
    colors = schedule_cell_colors(df, data, "points")
    low = colors[(0, "D")]             # 1.0 point
    high = colors[(0, "N")]            # 2.0 points
    assert low != high
    # more points -> blended further from white -> smaller channel sum
    def _sum(hexstr):
        h = hexstr.lstrip("#")
        return int(h[0:2], 16) + int(h[2:4], 16) + int(h[4:6], 16)
    assert _sum(high) < _sum(low)


def test_auto_mode_weekend_differs_from_weekday_same_shift():
    df, data = _sample()
    colors = schedule_cell_colors(df, data, "auto")
    weekend_n = colors[(0, "N")]       # Saturday
    weekday_n = colors[(1, "N")]       # Monday
    assert weekend_n != weekday_n


def test_palette_overrides_recolour_roles():
    df, data = _sample()
    default = schedule_cell_colors(df, data, "role")
    # Recolour senior shifts pure red; junior stays default.
    custom = schedule_cell_colors(df, data, "role", palette={"senior": "#ff0000"})
    assert custom[(0, "N")] != default[(0, "N")]   # senior shift changed
    assert custom[(0, "D")] == default[(0, "D")]   # junior shift unchanged


def test_palette_overrides_unfilled_colour():
    df, data = _sample()
    colors = schedule_cell_colors(df, data, "auto", palette={"unfilled": "#000000"})
    assert colors[(1, "D")] == "#000000"


def test_empty_palette_entries_fall_back_to_default():
    df, data = _sample()
    base = schedule_cell_colors(df, data, "auto")
    # Blank/None values must not override the default palette.
    same = schedule_cell_colors(df, data, "auto", palette={"weekend": "", "points": None})
    assert same == base


# --- theme_palette -------------------------------------------------------------

def test_theme_palette_shape_and_format():
    import re
    from model.coloring import theme_palette, DEFAULT_PALETTE

    pal = theme_palette("#4a90d9")
    assert set(pal) == set(DEFAULT_PALETTE)
    assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in pal.values())


def test_theme_palette_roles_pairwise_distinct():
    from model.coloring import theme_palette

    for base in ("#4a90d9", "#808080", "#f5f5f5", "#101010"):
        pal = theme_palette(base)
        roles = [pal[k] for k in ("points", "weekend", "senior", "junior")]
        assert len(set(roles)) == 4, base


def test_theme_palette_unfilled_preserved_or_default():
    from model.coloring import theme_palette, DEFAULT_PALETTE

    assert theme_palette("#4a90d9")["unfilled"] == DEFAULT_PALETTE["unfilled"]
    custom = theme_palette("#4a90d9", current={"unfilled": "#123456"})
    assert custom["unfilled"] == "#123456"


def test_theme_palette_golden_and_deterministic():
    from model.coloring import theme_palette

    expected = {
        "points": "#4a90d9",   # a saturated in-range base is kept exactly
        "weekend": "#6f4ad9",
        "senior": "#4ad9b7",
        "junior": "#d94b4a",
        "unfilled": "#ffcccc",
    }
    assert theme_palette("#4a90d9") == expected
    assert theme_palette("#4a90d9") == theme_palette("#4a90d9")
