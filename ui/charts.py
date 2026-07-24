"""Fairness chart builders (shared by the results studio and the ledger panel).

These charts are the artefact people screenshot and send round, so they are
built to stay readable at any roster size and to survive being saved as an
image: every resident keeps a fixed row height (the plot grows with the
roster instead of squeezing everyone into a fixed box), names are never
dropped, and each chart carries its own title so an exported PNG explains
itself.

Palette note: the hues below were checked with the dataviz palette validator
against the app's light surface (#f7f6f2). Role hue vs weekend gold passes
the CVD, normal-vision and 3:1 contrast checks; the cumulative pair is a
sequential two-step of the same role hue (past = lighter step, this block =
full hue), which keeps role identity constant across both charts. The lighter
step sits under 3:1 on its own, so it always ships with a legend, direct
value labels and the table above it.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

__all__ = [
    "ROLE_HUES",
    "WEEKEND_HUE",
    "COMFORTABLE",
    "COMPACT",
    "DENSITY_LABELS",
    "chart_height",
    "density_spec",
    "workload_chart",
    "cumulative_chart",
    "standings_chart",
]

ROLE_HUES = {
    "Junior": {"main": "#1f7a52", "prior": "#5cb98d"},
    "Senior": {"main": "#6a4bbc", "prior": "#a08fd4"},
}
WEEKEND_HUE = "#b07d00"
_SURFACE = "#f7f6f2"
_INK = "#3f3a33"
_INK_MUTED = "#6d6459"
_GRID = "#e4dfd4"

# Per-resident row heights. A grouped row carries two bars, so it needs
# roughly double a stacked row; anything under ~30px per resident is what made
# the old charts collapse into hairlines and silently drop the name labels.
GROUPED_ROW_PX = 40
STACKED_ROW_PX = 28
_MIN_HEIGHT = 170
_MAX_HEIGHT = 4200  # a 100-strong roster scrolls rather than becoming unreadable

COMFORTABLE = "comfortable"
COMPACT = "compact"

# Compact keeps every resident and their name — it only tightens the rows and
# type, then splits a long roster into two side-by-side columns so a big
# department fits one screen (and one screenshot) instead of scrolling.
_DENSITY = {
    COMFORTABLE: {
        "grouped_row": GROUPED_ROW_PX, "stacked_row": STACKED_ROW_PX,
        "name_font": 12, "value_font": 11, "axis_font": 11, "split_at": None,
    },
    COMPACT: {
        "grouped_row": 22, "stacked_row": 15,
        "name_font": 10, "value_font": 9, "axis_font": 10, "split_at": 24,
    },
}


# UI label -> density key.
DENSITY_LABELS = {
    "Comfortable (one column, roomy rows)": COMFORTABLE,
    "Compact (tight rows, two columns — fits a big roster on one screen)": COMPACT,
}


def density_spec(density: str) -> dict:
    """Row heights / type sizes for a density name (unknown → comfortable)."""
    return _DENSITY.get(density, _DENSITY[COMFORTABLE])


def chart_height(n_rows: int, row_px: int) -> int:
    """Plot height that guarantees every resident a readable row.

    The old charts fixed ``16 * residents`` for the whole plot; with two bars
    per resident that left ~8px each, so bars rendered as hairlines and Vega
    dropped the y-axis names — which read as "residents are missing".
    """
    return int(max(_MIN_HEIGHT, min(_MAX_HEIGHT, max(1, int(n_rows)) * row_px)))


def _split_columns(order, spec) -> list:
    """Split a roster into side-by-side column groups for compact layout."""
    split_at = spec.get("split_at")
    if not split_at or len(order) < split_at:
        return [list(order)]
    half = (len(order) + 1) // 2
    return [list(order[:half]), list(order[half:])]


def _resident_axis(order, spec):
    """A y-axis that always shows every resident's name."""
    return alt.Y(
        "Resident:N",
        sort=list(order),
        title=None,
        axis=alt.Axis(
            labelOverlap=False,   # never silently drop names
            labelLimit=260,
            labelFontSize=spec["name_font"],
            labelColor=_INK,
            labelPadding=6,
            domain=False,
            ticks=False,
        ),
    )


def _value_axis(title: str, headroom_max: float | None, spec):
    scale = alt.Scale(domainMin=0, domainMax=headroom_max, nice=False) if headroom_max \
        else alt.Scale(domainMin=0)
    return alt.X(
        "Points:Q",
        title=title,
        scale=scale,
        axis=alt.Axis(
            grid=True, gridColor=_GRID, gridDash=[2, 3],
            domain=False, ticks=False,
            labelColor=_INK_MUTED, titleColor=_INK_MUTED,
            labelFontSize=spec["axis_font"], titleFontSize=spec["axis_font"],
            titlePadding=8,
        ),
    )


def _title(text: str, subtitle: str):
    return alt.Title(
        text, subtitle=subtitle, anchor="start",
        fontSize=15, color=_INK, subtitleFontSize=11, subtitleColor=_INK_MUTED,
        offset=12,
    )


def _legend(spec, title=None):
    return alt.Legend(
        title=title, orient="top", direction="horizontal",
        labelFontSize=spec["name_font"] + 1, labelColor=_INK,
        symbolType="square", symbolSize=150, offset=6,
    )


def _finish(chart):
    """Apply the top-level configuration (legal only on the outermost chart)."""
    return (
        chart
        .configure_view(stroke=None)
        .configure_axis(labelFont="sans-serif", titleFont="sans-serif")
        .configure_concat(spacing=34)
    )


def _workload_column(long, order, hue, target, spec, show_legend: bool):
    """One column of the workload chart (all of it, unless compact splits it)."""
    subset = long[long["Resident"].isin(order)]
    peak = float(long["Points"].max() or 0.0)
    headroom = max(peak * 1.12, (target or 0.0) * 1.12, 1.0)
    kinds = ["Total points", "Weekend points"]
    bars = (
        alt.Chart(subset)
        .mark_bar(cornerRadiusEnd=3, height={"band": 0.86})
        .encode(
            y=_resident_axis(order, spec),
            x=_value_axis("Points", headroom, spec),
            yOffset=alt.YOffset("Kind:N", sort=kinds),
            color=alt.Color(
                "Kind:N",
                scale=alt.Scale(domain=kinds, range=[hue, WEEKEND_HUE]),
                legend=_legend(spec) if show_legend else None,
            ),
            tooltip=[
                alt.Tooltip("Resident:N"),
                alt.Tooltip("Kind:N", title="Measure"),
                alt.Tooltip("Points:Q", format=".1f"),
            ],
        )
    )
    # Direct labels on the primary series only (the weekend bar is read
    # against it) — a number on every bar of both series would be noise.
    labels = (
        alt.Chart(subset)
        .transform_filter(alt.datum.Kind == "Total points")
        .mark_text(align="left", dx=4, fontSize=spec["value_font"], color=_INK_MUTED)
        .encode(
            y=_resident_axis(order, spec),
            x=alt.X("Points:Q"),
            yOffset=alt.YOffset("Kind:N", sort=kinds),
            text=alt.Text("Points:Q", format=".1f"),
        )
    )
    # Order matters: the target rule sits above the bars but *below* the value
    # labels, and its caption is pinned to the top of the plot so it never
    # lands on top of a resident's row.
    layers = [bars]
    if target:
        rule_data = pd.DataFrame({"Points": [float(target)]})
        layers.append(
            alt.Chart(rule_data)
            .mark_rule(color=_INK, strokeDash=[5, 4], strokeWidth=1.5)
            .encode(x=alt.X("Points:Q"))
        )
    layers.append(labels)
    if target and show_legend:
        layers.append(
            alt.Chart(pd.DataFrame({"Points": [float(target)]}))
            .mark_text(
                align="left", dx=6, dy=-4, fontSize=spec["value_font"],
                fontStyle="italic", color=_INK, baseline="bottom",
            )
            .encode(
                x=alt.X("Points:Q"),
                y=alt.value(0),  # pinned to the top edge of the plot area
                text=alt.value("fair-share target"),
            )
        )
    return alt.layer(*layers).properties(
        height=chart_height(len(order), spec["grouped_row"])
    )


def workload_chart(role_frame, role: str, target: float | None, density: str = COMFORTABLE):
    """Grouped bars per resident: total points and, beside them, weekend points.

    Sorted heaviest first, with the fair-share target as a labelled rule so a
    reader can see at a glance who sits above or below their own target.
    ``density`` trades row height for compactness; compact additionally lays a
    long roster out in two side-by-side columns (still every resident, still
    every name) so a big department fits one screen.
    """
    hue = ROLE_HUES.get(role, ROLE_HUES["Junior"])["main"]
    long = role_frame.melt(
        id_vars=["Resident"],
        value_vars=["Total points", "Weekend points"],
        var_name="Kind",
        value_name="Points",
    )
    order = role_frame.sort_values(
        "Total points", ascending=False
    )["Resident"].tolist()
    spec = density_spec(density)
    groups = _split_columns(order, spec)
    columns = [
        _workload_column(long, group, hue, target, spec, show_legend=(i == 0))
        for i, group in enumerate(groups)
    ]
    body = columns[0] if len(columns) == 1 else alt.hconcat(*columns)
    subtitle = (
        f"{len(order)} {role.lower()}s · sorted by total points"
        + (f" · target {target:.1f}" if target else "")
        + (" · compact, two columns" if len(columns) > 1 else "")
    )
    return _finish(
        body.properties(title=_title(f"{role} workload — this block", subtitle))
    )


def cumulative_chart(cum_frame, role: str, density: str = COMFORTABLE):
    """Stacked bars: what each resident carried in, plus what they earned now.

    Two steps of the role's own hue (lighter = prior blocks), separated by a
    surface-coloured gap, with the cumulative figure labelled at the end of
    each bar so the standing is readable without hovering.
    """
    hues = ROLE_HUES.get(role, ROLE_HUES["Junior"])
    totals = (
        cum_frame.drop_duplicates("Resident")[["Resident", "Cumulative"]]
        .sort_values("Cumulative", ascending=False)
    )
    order = totals["Resident"].tolist()
    peak = float(totals["Cumulative"].max() or 0.0)
    headroom = max(peak * 1.12, 1.0)
    spec = density_spec(density)

    def _column(group, show_legend: bool):
        bars = (
            alt.Chart(cum_frame[cum_frame["Resident"].isin(group)])
            .mark_bar(
                cornerRadiusEnd=3, height={"band": 0.82},
                stroke=_SURFACE, strokeWidth=1.5,  # gap between stacked segments
            )
            .encode(
                y=_resident_axis(group, spec),
                x=alt.X(
                    "sum(Points):Q",
                    title="Cumulative points (prior blocks + this block)",
                    scale=alt.Scale(domainMin=0, domainMax=headroom, nice=False),
                    axis=alt.Axis(
                        grid=True, gridColor=_GRID, gridDash=[2, 3],
                        domain=False, ticks=False,
                        labelColor=_INK_MUTED, titleColor=_INK_MUTED,
                        labelFontSize=spec["axis_font"],
                        titleFontSize=spec["axis_font"], titlePadding=8,
                    ),
                ),
                color=alt.Color(
                    "Segment:N",
                    scale=alt.Scale(
                        domain=["Prior blocks", "This block"],
                        range=[hues["prior"], hues["main"]],
                    ),
                    legend=_legend(spec) if show_legend else None,
                ),
                order=alt.Order("Segment:N", sort="ascending"),
                tooltip=[
                    alt.Tooltip("Resident:N"),
                    alt.Tooltip("Segment:N"),
                    alt.Tooltip("Points:Q", format=".1f"),
                    alt.Tooltip("Cumulative:Q", format=".1f", title="Cumulative"),
                ],
            )
        )
        labels = (
            alt.Chart(totals[totals["Resident"].isin(group)])
            .mark_text(align="left", dx=4, fontSize=spec["value_font"], color=_INK_MUTED)
            .encode(
                y=_resident_axis(group, spec),
                x=alt.X("Cumulative:Q"),
                text=alt.Text("Cumulative:Q", format=".1f"),
            )
        )
        return alt.layer(bars, labels).properties(
            height=chart_height(len(group), spec["stacked_row"])
        )

    groups = _split_columns(order, spec)
    columns = [_column(group, show_legend=(i == 0)) for i, group in enumerate(groups)]
    body = columns[0] if len(columns) == 1 else alt.hconcat(*columns)
    return _finish(
        body.properties(
            title=_title(
                f"{role} cumulative standing",
                f"{len(order)} {role.lower()}s · level bar ends mean the "
                "history is balancing out"
                + (" · compact, two columns" if len(columns) > 1 else ""),
            )
        )
    )


def standings_chart(ledger: dict, density: str = COMFORTABLE):
    """The ledger panel's carried-in standings (total + weekend side by side)."""
    rows = [
        {"Resident": person, "Kind": kind, "Points": float(entry.get(dim, 0.0))}
        for person, entry in (ledger or {}).items()
        for kind, dim in (("Total", "total"), ("Weekend", "weekend"))
    ]
    frame = pd.DataFrame(rows)
    order = (
        frame[frame["Kind"] == "Total"]
        .sort_values("Points", ascending=False)["Resident"].tolist()
    )
    peak = float(frame["Points"].max() or 0.0)
    spec = density_spec(density)
    kinds = ["Total", "Weekend"]

    def _column(group, show_legend: bool):
        subset = frame[frame["Resident"].isin(group)]
        bars = (
            alt.Chart(subset)
            .mark_bar(cornerRadiusEnd=3, height={"band": 0.86})
            .encode(
                y=_resident_axis(group, spec),
                x=_value_axis(
                    "Cumulative points carried in", max(peak * 1.12, 1.0), spec
                ),
                yOffset=alt.YOffset("Kind:N", sort=kinds),
                color=alt.Color(
                    "Kind:N",
                    scale=alt.Scale(
                        domain=kinds,
                        range=[ROLE_HUES["Junior"]["main"], WEEKEND_HUE],
                    ),
                    legend=_legend(spec) if show_legend else None,
                ),
                tooltip=[
                    alt.Tooltip("Resident:N"),
                    alt.Tooltip("Kind:N", title="Measure"),
                    alt.Tooltip("Points:Q", format=".1f"),
                ],
            )
        )
        labels = (
            alt.Chart(subset)
            .transform_filter(alt.datum.Kind == "Total")
            .mark_text(align="left", dx=4, fontSize=spec["value_font"], color=_INK_MUTED)
            .encode(
                y=_resident_axis(group, spec),
                x=alt.X("Points:Q"),
                yOffset=alt.YOffset("Kind:N", sort=kinds),
                text=alt.Text("Points:Q", format=".1f"),
            )
        )
        return alt.layer(bars, labels).properties(
            height=chart_height(len(group), spec["grouped_row"])
        )

    groups = _split_columns(order, spec)
    columns = [_column(group, show_legend=(i == 0)) for i, group in enumerate(groups)]
    body = columns[0] if len(columns) == 1 else alt.hconcat(*columns)
    return _finish(
        body.properties(
            title=_title(
                "Cumulative standings carried into this block",
                f"{len(order)} resident(s) · heaviest first — the next "
                "Generate gives them lighter targets",
            )
        )
    )
