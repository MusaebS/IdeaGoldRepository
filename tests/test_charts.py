import sys, os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pd = pytest.importorskip("pandas")
pytest.importorskip("altair")

from ui.charts import (
    GROUPED_ROW_PX,
    STACKED_ROW_PX,
    chart_height,
    cumulative_chart,
    standings_chart,
    workload_chart,
)


def _workload_frame(n):
    return pd.DataFrame({
        "Resident": [f"R{i:02d}" for i in range(n)],
        # The last resident has zero points: they must still get a labelled row
        # rather than silently vanishing.
        "Total points": [float(n - i) for i in range(n - 1)] + [0.0],
        "Weekend points": [float((n - i) / 3) for i in range(n - 1)] + [0.0],
    })


def _cumulative_frame(n):
    return pd.DataFrame([
        {"Resident": f"R{i:02d}", "Segment": seg, "Points": pts,
         "Cumulative": 6.0 + i}
        for i in range(n)
        for seg, pts in (("Prior blocks", 6.0), ("This block", float(i)))
    ])


def test_chart_height_gives_every_resident_a_readable_row():
    # The old charts fixed 16px per resident for the WHOLE plot; with two bars
    # per resident that left ~8px each, so bars became hairlines and the names
    # were dropped. Each resident must now get a full row.
    assert chart_height(40, GROUPED_ROW_PX) == 40 * GROUPED_ROW_PX
    assert chart_height(40, GROUPED_ROW_PX) > 40 * 16 * 2
    # Two bars share a grouped row, so each still clears ~15px.
    assert GROUPED_ROW_PX / 2 >= 15
    # Small rosters keep a sane minimum; huge ones are capped (and scroll)
    # rather than squeezing.
    assert chart_height(1, STACKED_ROW_PX) >= 170
    assert chart_height(10_000, GROUPED_ROW_PX) <= 4200


def test_workload_chart_keeps_every_resident_and_never_drops_names():
    frame = _workload_frame(45)
    spec = workload_chart(frame, "Junior", 7.5).to_dict()
    assert spec["height"] == chart_height(45, GROUPED_ROW_PX)
    bars = spec["layer"][0]
    # Names are never silently hidden by Vega's overlap heuristics.
    assert bars["encoding"]["y"]["axis"]["labelOverlap"] is False
    # Every resident is present, including the zero-point one, and the sort
    # order lists them all.
    assert len(bars["encoding"]["y"]["sort"]) == 45
    assert "R44" in bars["encoding"]["y"]["sort"]
    data = spec["datasets"][bars["data"]["name"]]
    assert {row["Resident"] for row in data} == set(frame["Resident"])


def test_workload_chart_without_target_has_no_rule_layer():
    spec = workload_chart(_workload_frame(6), "Senior", None).to_dict()
    # bars + value labels only
    assert len(spec["layer"]) == 2


def test_workload_chart_with_target_labels_the_rule():
    spec = workload_chart(_workload_frame(6), "Senior", 5.0).to_dict()
    marks = [layer.get("mark") for layer in spec["layer"]]
    assert any(
        isinstance(m, dict) and m.get("type") == "rule" for m in marks
    ), "fair-share target rule missing"
    # The caption is pinned to the top edge so it never covers a resident row.
    caption = spec["layer"][-1]
    assert caption["encoding"]["y"]["value"] == 0


def test_cumulative_chart_keeps_every_resident():
    frame = _cumulative_frame(30)
    spec = cumulative_chart(frame, "Senior").to_dict()
    assert spec["height"] == chart_height(30, STACKED_ROW_PX)
    bars = spec["layer"][0]
    assert bars["encoding"]["y"]["axis"]["labelOverlap"] is False
    assert len(bars["encoding"]["y"]["sort"]) == 30
    # Stacked segments carry a surface-coloured stroke so they stay separable.
    assert bars["mark"]["strokeWidth"] > 0


def test_standings_chart_builds_for_a_ledger():
    ledger = {f"R{i:02d}": {"total": float(i), "weekend": float(i) / 2} for i in range(12)}
    spec = standings_chart(ledger).to_dict()
    assert spec["height"] == chart_height(12, GROUPED_ROW_PX)
    assert spec["layer"][0]["encoding"]["y"]["axis"]["labelOverlap"] is False


def test_compact_density_shrinks_height_and_keeps_everyone():
    from ui.charts import COMFORTABLE, COMPACT

    frame = _workload_frame(40)
    roomy = workload_chart(frame, "Junior", 7.5, density=COMFORTABLE).to_dict()
    tight = workload_chart(frame, "Junior", 7.5, density=COMPACT).to_dict()
    # Comfortable stays one tall column; compact splits into two short ones.
    assert "hconcat" not in roomy
    assert len(tight["hconcat"]) == 2
    tallest_compact_column = max(col["height"] for col in tight["hconcat"])
    assert tallest_compact_column < roomy["height"] / 2
    # Every resident survives the split — 40 names across the two columns.
    names = [
        name
        for col in tight["hconcat"]
        for name in col["layer"][0]["encoding"]["y"]["sort"]
    ]
    assert sorted(names) == sorted(frame["Resident"].tolist())
    # Names stay visible in compact too.
    for col in tight["hconcat"]:
        assert col["layer"][0]["encoding"]["y"]["axis"]["labelOverlap"] is False
    # One legend only (the second column suppresses its own).
    legends = [
        col["layer"][0]["encoding"]["color"].get("legend") for col in tight["hconcat"]
    ]
    assert legends[1] is None


def test_compact_keeps_one_column_for_a_small_roster():
    from ui.charts import COMPACT

    spec = workload_chart(_workload_frame(8), "Senior", None, density=COMPACT).to_dict()
    assert "hconcat" not in spec  # splitting 8 residents would be silly


def test_compact_cumulative_shares_the_value_scale_across_columns():
    from ui.charts import COMPACT

    spec = cumulative_chart(_cumulative_frame(36), "Junior", density=COMPACT).to_dict()
    domains = [
        col["layer"][0]["encoding"]["x"]["scale"]["domainMax"]
        for col in spec["hconcat"]
    ]
    # Both columns must share one scale or the bars are not comparable.
    assert len(set(domains)) == 1


def test_role_hues_are_distinct_per_role():
    from ui.charts import ROLE_HUES, WEEKEND_HUE

    junior = ROLE_HUES["Junior"]["main"]
    senior = ROLE_HUES["Senior"]["main"]
    assert junior != senior
    # The weekend series must differ from both role hues it sits beside.
    assert WEEKEND_HUE not in (junior, senior)
    # Prior/current steps of one role stay distinguishable.
    for role in ("Junior", "Senior"):
        assert ROLE_HUES[role]["prior"] != ROLE_HUES[role]["main"]
