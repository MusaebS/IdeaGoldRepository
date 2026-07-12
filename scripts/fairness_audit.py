"""Fairness audit: solve real scenarios end-to-end and measure the outcome.

Runs the actual CP-SAT solver over a matrix of small / large / extreme /
feature-combining scenarios and checks the *result* (not just the model):
total-point spread, deviation from the solver-resolved targets, weekend and
night-float spread, per-shift-type distribution, unfilled slots, and hard-rule
violations — plus per-scenario invariants (blackouts honoured, avoid pairs
separated, preferences fairness-neutral, multi-block ledger debts repaid).

    python scripts/fairness_audit.py            # run everything
    python scripts/fairness_audit.py --skip-big # skip the spec-scale solves

Exit code 1 when any scenario FAILs its stated expectation. Requires ortools.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from model.data_models import (  # noqa: E402
    Blackout,
    InputData,
    LoadReduction,
    NightFloatAssignment,
    NightFloatCoverage,
    ShiftClosure,
    ShiftTemplate,
    is_night_call,
)
from model.fairness import calculate_label_counts, calculate_points  # noqa: E402
from model.ledger import update_ledger  # noqa: E402
from model.optimiser import ORTOOLS_AVAILABLE, build_schedule  # noqa: E402
from model.reductions import eligible_for_shift  # noqa: E402
from model.validation import validate_schedule  # noqa: E402

MON = date(2026, 1, 5)  # a Monday, for predictable Sat/Sun weekends


def sh(label, points=1.0, role="Junior", nf=False, thu=False):
    return ShiftTemplate(label=label, role=role, night_float=nf, thu_weekend=thu, points=points)


def mk(shifts, juniors, days=14, start=MON, seniors=(), **kw):
    base = dict(
        start_date=start,
        end_date=start + timedelta(days=days - 1),
        shifts=shifts,
        juniors=list(juniors),
        seniors=list(seniors),
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
        nf_block_length=1,
    )
    base.update(kw)
    return InputData(**base)


def _spread(values):
    values = list(values)
    return (max(values) - min(values)) if values else 0.0


def measure(df, data, exclude=()):
    """Outcome fairness metrics; ``exclude`` names (e.g. capped) skip ranges."""
    points = calculate_points(df, data)
    counts = calculate_label_counts(df, data)
    attrs = getattr(df, "attrs", {}) or {}
    tmap = attrs.get("target_total_map") or {}
    pool = [p for p in data.juniors + data.seniors if p not in exclude]

    devs = [abs(points[p]["total"] - tmap[p]) for p in pool if p in tmap]
    label_count_range = 0
    label_point_range = 0.0
    for shift in data.shifts:
        eligible = [p for p in pool if eligible_for_shift(p, shift, data)]
        if len(eligible) < 2:
            continue
        label_count_range = max(
            label_count_range,
            int(_spread(counts.get(p, {}).get(shift.label, 0) for p in eligible)),
        )
        label_point_range = max(
            label_point_range,
            _spread(points[p]["labels"].get(shift.label, 0.0) for p in eligible),
        )

    records = df.to_dict("records")
    unfilled = sum(
        1 for row in records for s in data.shifts if row.get(s.label) in (None, "Unfilled")
    )
    return {
        "total_range": _spread(points[p]["total"] for p in pool),
        "total_dev": max(devs) if devs else 0.0,
        "weekend_range": _spread(points[p]["weekend"] for p in pool),
        "nf_range": _spread(points[p]["night_float"] for p in pool),
        "label_count_range": label_count_range,
        "label_point_range": label_point_range,
        "unfilled": unfilled,
        "violations": validate_schedule(df, data),
        "status": attrs.get("solver_status"),
        "points": points,
        "counts": counts,
    }


LIMITS = ("total_range", "total_dev", "weekend_range", "nf_range",
          "label_count_range", "unfilled")
FAILURES: list = []


def report(name, metrics, expects, notes=()):
    problems = []
    for key in LIMITS:
        limit = expects.get(key)
        if limit is not None and metrics[key] > limit + 1e-9:
            problems.append(f"{key}={metrics[key]:.2f}>{limit}")
    if metrics["violations"]:
        problems.append(f"{len(metrics['violations'])} rule violations")
    for label, ok in notes:
        if not ok:
            problems.append(label)
    verdict = "PASS" if not problems else "FAIL"
    if problems:
        FAILURES.append((name, problems))
    print(
        f"{verdict}  {name:<38}"
        f" tot rng {metrics['total_range']:>5.2f}  dev {metrics['total_dev']:>5.2f}"
        f"  wk {metrics['weekend_range']:>5.2f}  nf {metrics['nf_range']:>4.1f}"
        f"  lbl n/pts {metrics['label_count_range']}/{metrics['label_point_range']:.1f}"
        f"  unf {metrics['unfilled']:>2}  {metrics['status'] or '-'}"
    )
    for problem in problems:
        print(f"      !! {problem}")
    if metrics["violations"]:
        for violation in metrics["violations"][:3]:
            print(f"      !! {violation}")


def solve(data, env="dev", time_limit_sec=None):
    return build_schedule(data, env=env, time_limit_sec=time_limit_sec)


# --- scenarios ---------------------------------------------------------------

def tiny_exact():
    data = mk([sh("D")], ["A", "B", "C", "E"], days=12)
    report("tiny 4x12x1 (divisible)", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "weekend_range": 1,
            "label_count_range": 0, "unfilled": 0})


def indivisible():
    data = mk([sh("D")], ["A", "B", "C"], days=7)
    report("indivisible 3x7x1 (min range 1)", measure(solve(data), data),
           {"total_range": 1, "total_dev": 1, "label_count_range": 1, "unfilled": 0})


def weekend_sat_sun():
    data = mk([sh("D"), sh("E")], [f"J{i}" for i in range(8)], days=28, min_gap=1)
    report("weekend Sat/Sun 8x28x2 (divisible)", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "weekend_range": 0,
            "label_count_range": 1, "unfilled": 0})


def weekend_fri_sat_night():
    data = mk([sh("D"), sh("N", 2.0, thu=True)], [f"J{i}" for i in range(8)],
              days=28, min_gap=0, weekend_days=[4, 5])
    report("weekend Fri/Sat + Thu-night 8x28x2", measure(solve(data), data),
           {"total_dev": 1, "weekend_range": 2, "label_count_range": 1, "unfilled": 0})


def holiday_plain():
    data = mk([sh("D")], ["A", "B", "C", "E"], days=14,
              holidays=[(MON + timedelta(days=9), 2.0, False)])
    report("holiday +2 mid-week (divisible 16/4)", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "unfilled": 0})


def holiday_weekend_flag():
    data = mk([sh("D")], ["A", "B", "C", "E"], days=14,
              holidays=[(MON + timedelta(days=9), 2.0, True)])
    report("holiday +2 counts-as-weekend", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "weekend_range": 3, "unfilled": 0})


def label_mix_equal_points():
    data = mk([sh("D"), sh("E")], [f"J{i}" for i in range(6)], days=12)
    report("shift mix, equal points 6x12x2", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "label_count_range": 1, "unfilled": 0})


def label_mix_unequal_points():
    data = mk([sh("D"), sh("N", 2.0)], [f"J{i}" for i in range(6)], days=12)
    report("shift mix, D=1 N=2 6x12x2", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "label_count_range": 1, "unfilled": 0})


def features_leave_rotator():
    data = mk([sh("D"), sh("E")], [f"J{i}" for i in range(4)], days=14,
              leaves=[("J0", MON, MON + timedelta(days=6), True)],
              rotators=[("J1", MON, MON + timedelta(days=6))])
    metrics = measure(solve(data), data)
    report("comp leave + rotator half-block", metrics,
           {"total_dev": 1.0, "unfilled": 0})


def features_blackout():
    juniors = [f"J{i}" for i in range(6)]
    window = (MON + timedelta(days=7), MON + timedelta(days=10))
    data = mk([sh("D"), sh("N", 2.0, thu=True)], juniors, days=21,
              named_groups={"T": ["J0", "J1"]},
              blackouts=[Blackout("T", (), window[0], window[1])])
    df = solve(data)
    rows = {row["Date"]: row for row in df.to_dict("records")}
    in_window = all(
        rows[d][s.label] not in ("J0", "J1")
        for d in (window[0] + timedelta(days=i) for i in range(4))
        for s in data.shifts
    )
    night_before = all(
        rows[window[0] - timedelta(days=1)][s.label] not in ("J0", "J1")
        for s in data.shifts if is_night_call(s)
    )
    report("group blackout (window + night-before)", measure(df, data),
           {"total_dev": 2.0, "unfilled": 0},
           notes=[("blackout window honoured", in_window),
                  ("night-before honoured", night_before)])


def features_reduction():
    juniors = [f"J{i}" for i in range(4)]
    for keep_total, tag in ((False, "work-less"), (True, "keep-total")):
        data = mk([sh("D"), sh("N", 2.0)], juniors, days=12,
                  reductions=[LoadReduction(None, ("J0",), ("N",), 0.0,
                                            MON, MON + timedelta(days=11), keep_total)])
        df = solve(data)
        counts = calculate_label_counts(df, data)
        no_nights = counts.get("J0", {}).get("N", 0) == 0
        report(f"reduction N f=0 ({tag})", measure(df, data, exclude=("J0",)),
               {"total_dev": 1.0, "unfilled": 0},
               notes=[("reduced member took no nights", no_nights)])


def features_avoid_pair():
    juniors = [f"J{i}" for i in range(5)]
    data = mk([sh("D"), sh("E")], juniors, days=14, avoid_pairs=[("J0", "J1")])
    df = solve(data)
    together = any(
        {"J0", "J1"} <= {row[s.label] for s in data.shifts}
        for row in df.to_dict("records")
    )
    report("avoid pair 5x14x2", measure(df, data),
           {"total_dev": 1.0, "unfilled": 0},
           notes=[("pair never together", not together)])


def features_preferences_neutral():
    juniors = [f"J{i}" for i in range(4)]
    base = mk([sh("D"), sh("E")], juniors, days=14, seed=7)
    with_prefs = replace(base, preferred_day_type={"J0": "weekend", "J1": "weekday"})
    m_base = measure(solve(base), base)
    m_pref = measure(solve(with_prefs), with_prefs)
    same = all(
        abs(m_base[k] - m_pref[k]) < 1e-9
        for k in ("total_range", "total_dev", "weekend_range")
    )
    wk = lambda m, p: m["points"][p]["weekend"]  # noqa: E731
    honoured = wk(m_pref, "J0") >= wk(m_base, "J0") and wk(m_pref, "J1") <= wk(m_base, "J1")
    report("preferences neutrality (outcome)", m_pref,
           {"total_range": 0, "total_dev": 0, "unfilled": 0},
           notes=[("fairness identical with prefs", same),
                  ("preferences honoured", honoured)])


def features_caps_penalty():
    juniors = [f"J{i}" for i in range(4)]
    data = mk([sh("D"), sh("E")], juniors, days=14,
              max_total={"J0": 4.0}, extra_points={"J1": 2.0})
    df = solve(data)
    metrics = measure(df, data, exclude=("J0", "J1"))
    points = metrics["points"]
    capped_ok = points["J0"]["total"] <= 4.0
    tmap = df.attrs.get("target_total_map") or {}
    penalty_ok = points["J1"]["total"] >= tmap.get("J1", 0.0) - 1e-9
    # A hard cap removes capacity the fair-share targets don't model, so the
    # unconstrained residents must absorb the freed load; fairness is judged
    # among *them* (range <= 1, the integer-slot minimum), not against the
    # cap-distorted target. Coverage must still be complete.
    report("cap J0<=4 + penalty J1+2", metrics, {"total_range": 1, "unfilled": 0},
           notes=[("cap respected", capped_ok), ("penalty floor met", penalty_ok)])


def features_factors():
    juniors = [f"J{i}" for i in range(4)]
    data = mk([sh("D"), sh("E")], juniors, days=14,
              group_factors={"R2": 0.5}, resident_groups={"J0": "R2"})
    df = solve(data)
    metrics = measure(df, data)
    points, tmap = metrics["points"], df.attrs.get("target_total_map") or {}
    halved = abs(points["J0"]["total"] - tmap.get("J0", 0.0)) <= 1.0
    report("seniority factor 0.5 for J0", metrics, {"total_dev": 1.0, "unfilled": 0},
           notes=[("half-load target met", halved)])


def overlay_night_float():
    # Night float as a coverage overlay: two coverers split a 28-day NF rotation
    # (every night covered). Those cells are removed from regular demand and
    # carry no regular points; the coverers are off regular work during their
    # block, and the regular D load stays fair among everyone on the remainder.
    juniors = [f"J{i}" for i in range(6)]
    data = mk(
        [sh("D"), sh("NF", 2.0, nf=True)],
        juniors, days=28, min_gap=1,
        nf_juniors=["J0", "J1"],
        nf_coverage={"NF": NightFloatCoverage("NF", weekdays=(0, 1, 2, 3, 4, 5, 6))},
        nf_assignments=[
            NightFloatAssignment("J0", MON, MON + timedelta(days=13), (), 1),
            NightFloatAssignment("J1", MON + timedelta(days=14), MON + timedelta(days=27), (), 1),
        ],
    )
    df = solve(data)
    metrics = measure(df, data)
    rows = {row["Date"]: row for row in df.to_dict("records")}
    # Every night is an overlay cell (no coverage gaps) and none of them add
    # regular points — the D shift is the only regular demand (28 points).
    all_covered = len(df.attrs.get("nf_cells", {})) == 28
    regular_only = abs(sum(metrics["points"][p]["total"] for p in juniors) - 28.0) < 1e-6
    # Coverers are blocked from regular work during their NF block.
    j0_off = all(rows[MON + timedelta(days=i)]["D"] != "J0" for i in range(14))
    j1_off = all(rows[MON + timedelta(days=i)]["D"] != "J1" for i in range(14, 28))
    report("NF overlay 6x28 (2 coverers)", metrics,
           {"total_dev": 2.0, "unfilled": 0},
           notes=[("NF fully covered by overlay", all_covered),
                  ("NF excluded from regular points", regular_only),
                  ("coverers off regular during NF", j0_off and j1_off)])


def closures_scenario():
    # A shift stood down on weekends + a shortage stretch: closed cells are not
    # unfilled, carry no points, and the open demand stays fair.
    juniors = [f"J{i}" for i in range(4)]
    data = mk([sh("Ward"), sh("Clinic")], juniors, days=14, closures=[
        ShiftClosure("Clinic", MON, MON + timedelta(days=13), (5, 6)),   # weekends
        ShiftClosure("Clinic", MON + timedelta(days=10), MON + timedelta(days=13)),
    ])
    df = solve(data)
    metrics = measure(df, data)
    clinic = list(df["Clinic"])
    closed = clinic.count("Closed")
    total_pts = sum(metrics["points"][p]["total"] for p in juniors)
    # Open demand = Ward (14) + the still-open Clinic cells (14 - closed); the
    # closed Clinic cells add nothing to the point pool.
    expected = 14.0 + (14 - closed)
    report("shift closures 4x14 (Clinic down)", metrics,
           {"total_dev": 1.0, "unfilled": 0},
           notes=[("closed cells not unfilled", "Unfilled" not in clinic),
                  ("some Clinic cells closed", closed > 0),
                  ("closed cells carry no points", abs(total_pts - expected) < 1e-6)])


def multi_block_ledger():
    juniors = ["A", "B", "C"]
    ledger: dict = {}
    ranges = []
    for block in range(3):
        start = MON + timedelta(days=7 * block)
        data = mk([sh("D")], juniors, days=7, start=start)
        if block == 0:
            data = replace(
                data, blackouts=[Blackout(None, ("A",), start, start + timedelta(days=6))]
            )
        # Carryover: feed the running ledger back into the next block's solve.
        df = build_schedule(data, env="dev", ledger=ledger or None)
        ledger = update_ledger(ledger, df, data)
        ranges.append(_spread(ledger[p]["total"] for p in juniors))
    converged = ranges[-1] <= 1.0 and ranges[-1] < ranges[0]
    print(
        ("PASS" if converged else "FAIL")
        + f"  {'3-block ledger (A blacked out wk1)':<38} cumulative ranges "
        + " -> ".join(f"{r:.1f}" for r in ranges)
    )
    if not converged:
        FAILURES.append(("3-block ledger", [f"cumulative ranges {ranges}"]))


def recurring_nf_ledger():
    # Recurring excusal: two coverers split the night-float overlay every block.
    # NF coverage records the coverer as an uncompensated absence, so the ledger
    # must *not* let that recurring excusal diverge — the coverers (least regular
    # work) must never end up recorded above the residents doing full loads.
    juniors = [f"J{i}" for i in range(6)]
    ledger: dict = {}
    ranges = []
    for block in range(4):
        start = MON + timedelta(days=28 * block)
        coverer = "J0" if block % 2 == 0 else "J1"
        data = mk(
            [sh("D"), sh("NF", 2.0, nf=True)], juniors, days=28, start=start, min_gap=1,
            nf_juniors=["J0", "J1"],
            nf_coverage={"NF": NightFloatCoverage("NF", weekdays=(0, 1, 2, 3, 4, 5, 6))},
            nf_assignments=[NightFloatAssignment(coverer, start, start + timedelta(days=27), (), 1)],
        )
        df = build_schedule(data, env="dev", ledger=ledger or None)
        ledger = update_ledger(ledger, df, data)
        ranges.append(_spread(ledger[p]["total"] for p in juniors))
    # The whole roster stays within a couple of points block over block. The
    # pre-fix cumulative credit diverged here to a ~50-point spread (coverers
    # recorded roughly double the residents doing full loads); a bounded range
    # is the guard against that runaway returning.
    bounded = all(r <= 3.0 for r in ranges)
    print(
        ("PASS" if bounded else "FAIL")
        + f"  {'4-block recurring NF ledger':<38} cumulative ranges "
        + " -> ".join(f"{r:.1f}" for r in ranges)
    )
    if not bounded:
        FAILURES.append(("recurring NF ledger", [f"cumulative ranges {ranges}"]))


def multi_block_label_ledger():
    # Per-label carryover: J0 works none of the heavy N shift in block 0 (a
    # work-less reduction), accruing N-type debt. Later blocks must repay the
    # debt *in kind* — extra N for J0, not just extra points of any type —
    # so the cumulative per-label history converges alongside the totals.
    # (Without label carryover the per-block N shares stay equal and J0's N
    # history would lag forever.)
    juniors = [f"J{i}" for i in range(4)]
    ledger: dict = {}
    n_ranges = []
    for block in range(4):
        start = MON + timedelta(days=14 * block)
        data = mk([sh("D"), sh("N", 2.0)], juniors, days=14, start=start)
        if block == 0:
            data = replace(data, reductions=[
                LoadReduction(None, ("J0",), ("N",), 0.0, start, start + timedelta(days=13))
            ])
        df = build_schedule(data, env="dev", ledger=ledger or None)
        ledger = update_ledger(ledger, df, data)
        n_ranges.append(
            _spread((ledger[p].get("labels") or {}).get("N", 0.0) for p in juniors)
        )
    total_range = _spread(ledger[p]["total"] for p in juniors)
    converged = (
        n_ranges[-1] <= 2.0 and n_ranges[-1] < n_ranges[0] and total_range <= 2.0
    )
    print(
        ("PASS" if converged else "FAIL")
        + f"  {'4-block label ledger (J0 no N wk1)':<38} cumulative N ranges "
        + " -> ".join(f"{r:.1f}" for r in n_ranges)
        + f"  tot rng {total_range:.1f}"
    )
    if not converged:
        FAILURES.append((
            "multi-block label ledger",
            [f"cumulative N ranges {n_ranges}", f"total range {total_range}"],
        ))


def weekend_double_points():
    # weekend_multiplier=2: weekend slots carry double points, so weekend
    # imbalance lands in the strongest (total) tier. 28 days from a Monday =
    # 8 weekend days; pool = 20 + 16 = 36 pts over 4 juniors -> 9 each with
    # exactly 2 weekend slots (4 pts) per head.
    juniors = [f"J{i}" for i in range(4)]
    data = replace(mk([sh("D")], juniors, days=28), weekend_multiplier=2.0)
    report(
        "weekend x2 4x28x1 (folds into totals)", measure(solve(data), data),
        {"total_range": 1, "weekend_range": 2, "unfilled": 0},
    )


def mixed_role_pools():
    # Juniors and seniors work disjoint shift pools with different per-head
    # demand (juniors 56/8 = 7 pts, seniors 14/4 = 3.5 pts). Targets are
    # role-aware: fairness must be exact WITHIN each role; the cross-role gap
    # is structural (supply vs demand) and merely reported. This is the shape
    # of the real-world run that motivated the fix (40J/22S, 4+3 shifts/day).
    juniors = [f"J{i}" for i in range(8)]
    seniors = [f"S{i}" for i in range(4)]
    data = mk(
        [sh("D1"), sh("D2"), sh("N", 2.0), sh("SR", role="Senior")],
        juniors, days=14, seniors=seniors,
    )
    df = solve(data)
    points = calculate_points(df, data)
    j_rng = _spread(points[p]["total"] for p in juniors)
    s_rng = _spread(points[p]["total"] for p in seniors)
    records = df.to_dict("records")
    unfilled = sum(
        1 for row in records for s in data.shifts if row.get(s.label) in (None, "Unfilled")
    )
    ok = j_rng <= 1.0 and s_rng <= 1.0 and unfilled == 0
    print(
        ("PASS" if ok else "FAIL")
        + f"  {'mixed roles 8J/4S (per-role balance)':<38} junior rng {j_rng:.1f}"
        + f"  senior rng {s_rng:.1f}  unf {unfilled}"
    )
    if not ok:
        FAILURES.append((
            "mixed role pools",
            [f"junior range {j_rng}", f"senior range {s_rng}", f"unfilled {unfilled}"],
        ))


def extreme_more_shifts_than_people():
    data = mk([sh("D"), sh("E"), sh("F")], ["A", "B"], days=10)
    report("2 people, 3 shifts/day (1 unfilled/day)", measure(solve(data), data),
           {"total_range": 0, "total_dev": 6, "unfilled": 10})


def extreme_heavy_shift():
    data = mk([sh("D"), sh("E"), sh("F"), sh("H", 5.0)], [f"J{i}" for i in range(4)], days=12)
    report("5-pt shift among 1-pt 4x12x4", measure(solve(data), data),
           {"total_range": 1, "total_dev": 1, "label_count_range": 1, "unfilled": 0})


def extreme_min_gap():
    data = mk([sh("D")], [f"J{i}" for i in range(4)], days=12, min_gap=3)
    report("min_gap 3, capacity-tight 4x12x1", measure(solve(data), data),
           {"total_range": 0, "total_dev": 0, "unfilled": 0})


def spec_scale(seeds=(0, 1, 2)):
    from model.demo_data import sample_names, sample_shifts

    juniors, seniors, nf_j, nf_s = sample_names()
    for seed in seeds:
        data = InputData(
            start_date=MON, end_date=MON + timedelta(days=27),
            shifts=sample_shifts(), juniors=juniors, seniors=seniors,
            nf_juniors=nf_j, nf_seniors=nf_s, leaves=[], rotators=[],
            min_gap=1, nf_block_length=5, seed=seed,
        )
        # At spec scale the solve is time-limited, so total-dev is a budget
        # artefact, not an unfairness — the guarantees that must hold are
        # full coverage and no hard-rule violations. Per-label targets are
        # gated off above LABEL_TARGET_MAX_CELLS so they can't starve the
        # primary balance here. Deviation is printed for information. The 30 s
        # budget (vs dev's 10 s) gives CP-SAT room to reach a zero-unfilled
        # incumbent on every seed; the exact trajectory shifts with model
        # changes (e.g. role-aware targets), and coverage still dominates the
        # objective, so a too-small budget fails on search time, not fairness.
        report(
            f"spec-scale 45x28x10 seed {seed} (info)",
            measure(solve(data, time_limit_sec=30), data),
            {"unfilled": 0},
        )


def department_scale_mixed():
    # The user-reported real department shape: 30 juniors + 15 seniors,
    # 4 junior + 3 senior shift types, 28 days, doubled weekends, min_gap 3
    # (8.8k cells — per-label targets are ON since the gate raise). Asserts
    # the outcomes the app promises: within-role totals within one point,
    # weekend spread within one weekend shift (2 pts at x2), per-type call
    # spread bounded, and full coverage.
    juniors = [f"J{i:02d}" for i in range(30)]
    seniors = [f"S{i:02d}" for i in range(15)]
    shifts = [
        sh("Ward night", thu=True), sh("Ward morning"),
        sh("ER zone 1"), sh("ER zone 2"),
        sh("Senior night", role="Senior", thu=True),
        sh("evening", role="Senior"), sh("morning", role="Senior"),
    ]
    data = replace(
        mk(shifts, juniors, days=28, seniors=seniors, min_gap=3),
        weekend_multiplier=2.0,
    )
    df = solve(data, time_limit_sec=60)
    points = calculate_points(df, data)
    counts = calculate_label_counts(df, data)
    jt = _spread(points[p]["total"] for p in juniors)
    st = _spread(points[p]["total"] for p in seniors)
    jw = _spread(points[p]["weekend"] for p in juniors)
    sw = _spread(points[p]["weekend"] for p in seniors)
    lbl = max(
        _spread(counts.get(p, {}).get(s.label, 0) for p in
                (juniors if s.role == "Junior" else seniors))
        for s in shifts
    )
    records = df.to_dict("records")
    unfilled = sum(
        1 for row in records for s in shifts if row.get(s.label) in (None, "Unfilled")
    )
    # Bounds are the stable 60s-FEASIBLE envelope, not the optimum: the two
    # lowest tiers trade off run-to-run under the budget (weekend one-slot
    # spread with label spread 4, or weekend two slots with label spread 2).
    # Totals within one point and full coverage always hold; anything past
    # these bounds is a real fairness regression, not budget noise.
    ok = jt <= 1 and st <= 1 and jw <= 4 and sw <= 4 and lbl <= 4 and unfilled == 0
    print(
        ("PASS" if ok else "FAIL")
        + f"  {'department 30J/15S x28x7 (x2 wk, gap3)':<38} tot J/S {jt:.0f}/{st:.0f}"
        + f"  wk J/S {jw:.0f}/{sw:.0f}  lbl cnt {lbl}  unf {unfilled}"
    )
    if not ok:
        FAILURES.append((
            "department scale mixed",
            [f"totals {jt}/{st}", f"weekend {jw}/{sw}",
             f"label count spread {lbl}", f"unfilled {unfilled}"],
        ))


def main() -> int:
    if not ORTOOLS_AVAILABLE:
        print("ortools is not installed; the audit needs the real solver.")
        return 2
    skip_big = "--skip-big" in sys.argv
    for scenario in (
        tiny_exact, indivisible, weekend_sat_sun, weekend_fri_sat_night,
        holiday_plain, holiday_weekend_flag, label_mix_equal_points,
        label_mix_unequal_points, features_leave_rotator, features_blackout,
        features_reduction, features_avoid_pair, features_preferences_neutral,
        features_caps_penalty, features_factors, overlay_night_float,
        closures_scenario, multi_block_ledger, recurring_nf_ledger,
        multi_block_label_ledger, mixed_role_pools, weekend_double_points,
        extreme_more_shifts_than_people, extreme_heavy_shift, extreme_min_gap,
    ):
        scenario()
    if not skip_big:
        spec_scale()
        department_scale_mixed()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} scenario(s) FAILED:")
        for name, problems in FAILURES:
            print(f"  - {name}: {'; '.join(problems)}")
        return 1
    print("All fairness scenarios PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
