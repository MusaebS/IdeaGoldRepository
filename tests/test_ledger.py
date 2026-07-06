import sys, os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pandas as pd
except Exception:  # pragma: no cover
    from model import optimiser as opt
    pd = opt.pd

import pytest

from model.data_models import ShiftTemplate, InputData
from model.ledger import empty_ledger, update_ledger, ledger_to_json, ledger_from_json


def _data():
    shifts = [ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False, points=1.0)]
    return InputData(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 6),
        shifts=shifts,
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=0,
    )


def test_update_ledger_accumulates():
    data = _data()
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S": "A"},
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "B"},
    ])
    prior = {"A": {"total": 5.0, "weekend": 0.0, "night_float": 0.0}}
    updated = update_ledger(prior, df, data)
    assert updated["A"]["total"] == 7.0  # 5 prior + 2 this block
    assert updated["B"]["total"] == 1.0  # new resident this block


def test_ledger_round_trip():
    ledger = {"A": {"total": 7.0, "weekend": 2.0}}
    restored = ledger_from_json(ledger_to_json(ledger))
    assert restored == ledger


def test_ledger_from_json_fills_missing_dimensions():
    restored = ledger_from_json('{"A": {"total": 4}}')
    assert restored == {"A": {"total": 4.0, "weekend": 0.0}}


def test_ledger_from_json_ignores_legacy_night_float():
    # Night float is no longer a balanced dimension; an old file's key is dropped.
    restored = ledger_from_json('{"A": {"total": 4, "weekend": 1, "night_float": 9}}')
    assert restored == {"A": {"total": 4.0, "weekend": 1.0}}


def test_empty_ledger():
    assert empty_ledger() == {}


def test_carryover_shifts_load_to_underloaded_resident():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule
    from model.fairness import calculate_points

    ledger = {
        "A": {"total": 10.0, "weekend": 0.0, "night_float": 0.0},
        "B": {"total": 0.0, "weekend": 0.0, "night_float": 0.0},
    }
    df = build_schedule(_data(), env="test", ledger=ledger)
    pts = calculate_points(df, _data())
    # A was overloaded in prior blocks, so this block should favour B.
    assert pts["A"]["total"] < pts["B"]["total"]


def test_no_ledger_is_unchanged_behaviour():
    pytest.importorskip("ortools")
    from model.optimiser import build_schedule
    from model.fairness import calculate_points

    df = build_schedule(_data(), env="test")
    pts = calculate_points(df, _data())
    assert abs(pts["A"]["total"] - pts["B"]["total"]) <= 1  # even split, no carryover


# --- ledger policy: no auto-compensation --------------------------------------

def _df(*assignments):
    """Rows of (date, resident) for the single shift 'S'."""
    return pd.DataFrame([{"Date": d, "S": who} for d, who in assignments])


def _days(n, start=date(2023, 1, 1)):
    from datetime import timedelta
    return [start + timedelta(days=i) for i in range(n)]


def test_default_policy_without_excusals_matches_legacy():
    from model.ledger import LedgerPolicy

    data = _data()
    df = _df((date(2023, 1, 1), "A"), (date(2023, 1, 2), "B"))
    legacy = update_ledger(None, df, data, policy=LedgerPolicy(False, False))
    default = update_ledger(None, df, data)
    assert default == legacy  # nothing to adjust -> bit-identical


def test_policy_off_is_bit_identical_with_excusals():
    from dataclasses import replace
    from model.ledger import LedgerPolicy

    data = replace(_data(), leaves=[("B", date(2023, 1, 1), date(2023, 1, 3), False)])
    df = _df((date(2023, 1, 4), "A"), (date(2023, 1, 5), "B"))
    off = update_ledger(None, df, data, policy=LedgerPolicy(False, False))
    expected = {"total": 1.0, "weekend": 0.0,
                "labels": {"S": 1.0}, "label_counts": {"S": 1}}
    assert off["A"] == expected
    assert off["B"] == expected


def test_penalty_debit_removes_extra_from_total_only():
    from dataclasses import replace

    data = replace(_data(), extra_points={"A": 2.0})
    df = _df(*[(d, "A") for d in _days(4)], *[(d, "B") for d in _days(2, date(2023, 1, 5))])
    updated = update_ledger(None, df, data)
    # A earned 4 raw; the +2 penalty is not carried -> countable 2 (equal to B).
    assert updated["A"]["total"] == pytest.approx(2.0)
    assert updated["B"]["total"] == pytest.approx(2.0)
    assert updated["A"]["adjustments"]["penalty_not_carried"] == 2.0
    # The debit touches only the total dimension: no excused credit was issued.
    assert "excused_credit" not in updated["A"]["adjustments"]
    assert "adjustments" not in updated["B"]


def test_excused_credit_exact_numbers_uncomp_leave():
    from dataclasses import replace
    from model.ledger import block_adjustments

    # 6-day block, B uncompensated-excused 3 days: weights (6, 3).
    data = replace(_data(), leaves=[("B", date(2023, 1, 1), date(2023, 1, 3), False)])
    adj = block_adjustments(None, data)
    assert adj["B"]["excused_total"] == pytest.approx(6 * (1 / 2 - 3 / 9))   # +1.0
    assert adj["A"]["excused_total"] == pytest.approx(6 * (1 / 2 - 6 / 9))   # -1.0


def test_excused_credit_applies_for_rotator_perk_and_group():
    from dataclasses import replace
    from model.data_models import Perk
    from model.ledger import block_adjustments

    variants = [
        replace(_data(), rotators=[("B", date(2023, 1, 1), date(2023, 1, 3))]),
        replace(_data(), perks=[Perk("B", 0.5)]),
        replace(_data(), group_factors={"half": 0.5}, resident_groups={"B": "half"}),
    ]
    for data in variants:
        adj = block_adjustments(None, data)
        assert adj["B"]["excused_total"] == pytest.approx(1.0), data
        assert adj["A"]["excused_total"] == pytest.approx(-1.0), data


def test_credits_sum_to_zero_per_dimension():
    from dataclasses import replace
    from model.data_models import Perk
    from model.ledger import block_adjustments

    data = replace(
        _data(),
        perks=[Perk("B", 0.7)],
        leaves=[("A", date(2023, 1, 6), date(2023, 1, 6), False)],
        extra_points={"A": 1.0},
    )
    adj = block_adjustments(None, data)
    for dim in ("excused_total", "excused_weekend"):
        assert sum(v[dim] for v in adj.values()) == pytest.approx(0.0)


def test_excused_credit_is_per_block_not_cumulative():
    """A recurring excusal must not scale the credit by the running total.

    The credit compensates *this block's* excused shortfall only, so a resident
    who is excused every block gets the same credit no matter how large the
    prior ledger has grown. (Scaling it by the cumulative pool made the recorded
    ledger diverge — the resident doing the least work ended up recorded with
    the most.)
    """
    from dataclasses import replace
    from model.data_models import Perk
    from model.ledger import block_adjustments

    data = replace(_data(), perks=[Perk("B", 0.5)])
    fresh = block_adjustments(None, data)
    # A large accumulated history for the same recurring excusal must not
    # inflate this block's credit.
    huge_prior = {"A": {"total": 500.0, "weekend": 0.0},
                  "B": {"total": 500.0, "weekend": 0.0}}
    with_prior = block_adjustments(huge_prior, data)
    assert with_prior["B"]["excused_total"] == pytest.approx(fresh["B"]["excused_total"])
    assert with_prior["A"]["excused_total"] == pytest.approx(fresh["A"]["excused_total"])
    assert fresh["B"]["excused_total"] == pytest.approx(1.0)  # 6 * (1/2 - 3/6)... bounded


def test_recurring_excusal_ledger_stays_bounded_and_correct_direction():
    """A resident on a standing half-load factor must not be recorded as the
    heaviest worker: their cumulative total stays the lowest and the spread does
    not blow up block over block (the pre-fix credit diverged the other way)."""
    pytest.importorskip("ortools")
    from datetime import timedelta
    from dataclasses import replace
    from model.optimiser import build_schedule

    juniors = ["A", "B", "C", "D"]
    ledger: dict = {}
    ranges = []
    for b in range(5):
        start = date(2023, 1, 1) + timedelta(days=14 * b)
        data = replace(
            _data(),
            start_date=start,
            end_date=start + timedelta(days=13),
            juniors=juniors,
            group_factors={"half": 0.5},
            resident_groups={"A": "half"},  # A permanently carries half load
        )
        df = build_schedule(data, env="test", ledger=ledger or None)
        ledger = update_ledger(ledger, df, data)
        totals = [ledger[p]["total"] for p in juniors]
        ranges.append(max(totals) - min(totals))
    # The half-load resident accumulates the *least*, never the most — the
    # pre-fix cumulative credit inverted this, recording the under-worked
    # resident as the heaviest. The spread also stays bounded (linear in the
    # honest per-block gap) rather than blowing up block over block.
    assert ledger["A"]["total"] == min(ledger[p]["total"] for p in juniors)
    assert ledger["A"]["total"] < max(ledger[p]["total"] for p in juniors)
    assert ranges[-1] <= 10  # per-block fix ~8; the cumulative runaway exceeds this


def test_nf_duty_days_carried_forward_across_blocks():
    """The informational NF-duty count accumulates instead of resetting."""
    from dataclasses import replace

    data = replace(
        _data(),
        shifts=[ShiftTemplate(label="NF", role="Junior", night_float=True,
                              thu_weekend=False, points=2.0)],
    )
    # Two night-float overlay cells covered by A (column matches the shift label).
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "NF": "A"},
        {"Date": date(2023, 1, 2), "NF": "A"},
    ])
    df.attrs["nf_cells"] = {"2023-01-01": {"NF": "A"}, "2023-01-02": {"NF": "A"}}
    prior = {"A": {"total": 0.0, "weekend": 0.0, "nf_days": 3}}
    updated = update_ledger(prior, df, data)
    assert updated["A"]["nf_days"] == 5  # 3 prior + 2 this block
    assert updated["A"]["total"] == 0.0  # NF cells never add regular points


def _cap_data(start, cap_active, excused, days=14):
    from dataclasses import replace
    shifts = [
        ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0),
        ShiftTemplate(label="E", role="Junior", night_float=False, thu_weekend=False, points=1.0),
    ]
    from datetime import timedelta
    return replace(
        _data(), start_date=start, end_date=start + timedelta(days=days - 1),
        shifts=shifts, juniors=["A", "B", "C", "D"],
        max_total={"A": 2.0} if cap_active else None,
        max_total_excused={"A": True} if (cap_active and excused) else None,
    )


def test_excused_cap_credits_sum_to_zero():
    from model.ledger import block_adjustments

    adj = block_adjustments(None, _cap_data(date(2023, 1, 1), True, True))
    assert sum(v["excused_total"] for v in adj.values()) == pytest.approx(0.0)
    # A (capped at 2, fair share 7) is credited its 5-point shortfall.
    assert adj["A"]["excused_total"] == pytest.approx(5.0)


def test_excused_cap_does_not_diverge_across_blocks():
    """A permanent 'do not compensate later' cap keeps the ledger balanced — the
    capped resident is recorded at fair standing, never caught up — whereas the
    'compensate later' cap builds an ever-growing (unrepayable) debt."""
    pytest.importorskip("ortools")
    from datetime import timedelta
    from model.optimiser import build_schedule

    def final_range(excused):
        ledger: dict = {}
        for b in range(4):
            data = _cap_data(date(2023, 1, 1) + timedelta(days=14 * b), True, excused)
            df = build_schedule(data, env="dev", ledger=ledger or None)
            ledger = update_ledger(ledger, df, data)
        totals = [ledger[p]["total"] for p in ("A", "B", "C", "D")]
        return max(totals) - min(totals)

    excused_range = final_range(True)
    compensate_range = final_range(False)
    assert excused_range <= 4.0  # bounded, near-balanced
    # The excused cap is dramatically tighter than the diverging compensate case.
    assert excused_range < compensate_range / 3


def test_compensate_later_cap_catches_up_when_lifted():
    """A temporary 'compensate later' cap: the shortfall is repaid once the cap
    lifts, so cumulative totals converge."""
    pytest.importorskip("ortools")
    from datetime import timedelta
    from model.optimiser import build_schedule

    ledger: dict = {}
    for b in range(4):
        # Cap A for the first two blocks only, then lift it.
        data = _cap_data(date(2023, 1, 1) + timedelta(days=14 * b), b < 2, False)
        df = build_schedule(data, env="dev", ledger=ledger or None)
        ledger = update_ledger(ledger, df, data)
    totals = [ledger[p]["total"] for p in ("A", "B", "C", "D")]
    assert max(totals) - min(totals) <= 1.0  # caught up after the cap lifted


def test_max_total_excused_config_round_trip():
    from model.config_io import input_data_from_json, input_data_to_json

    data = _cap_data(date(2023, 1, 1), True, True)
    restored = input_data_from_json(input_data_to_json(data))
    assert restored.max_total == {"A": 2.0}
    assert restored.max_total_excused == {"A": True}


def test_invariant_excused_shortfall_not_repaid_next_block():
    """The flagship guarantee: after a perk/group reduction, the saved ledger

    yields *equal* targets in a clean identical next block — no catch-up."""
    from dataclasses import replace
    from model.optimiser import resolve_targets

    # Block 1: B carries half load (weights 6 vs 3) -> targets 4.0 / 2.0.
    data1 = replace(_data(), group_factors={"half": 0.5}, resident_groups={"B": "half"})
    resolved1 = resolve_targets(data1)
    assert resolved1.target_total_map == pytest.approx({"A": 4.0, "B": 2.0})
    # The block meets its targets exactly (A works 4 days, B works 2).
    df = _df(*[(d, "A") for d in _days(4)], *[(d, "B") for d in _days(2, date(2023, 1, 5))])
    ledger = update_ledger(None, df, data1)
    countable = {p: ledger[p]["total"] for p in ("A", "B")}
    assert countable["A"] == pytest.approx(countable["B"])  # equal standing recorded
    # Block 2: identical block, the group factor is gone -> equal targets.
    resolved2 = resolve_targets(_data(), ledger=ledger_from_json(ledger_to_json(ledger)))
    tmap = resolved2.target_total_map
    assert tmap["A"] == pytest.approx(tmap["B"])
    assert tmap["A"] == pytest.approx(3.0)


def test_invariant_penalty_not_refunded_next_block():
    from dataclasses import replace
    from model.optimiser import resolve_targets

    data1 = replace(_data(), extra_points={"A": 2.0})
    resolved1 = resolve_targets(data1)
    assert resolved1.target_total_map == pytest.approx({"A": 4.0, "B": 2.0})
    df = _df(*[(d, "A") for d in _days(4)], *[(d, "B") for d in _days(2, date(2023, 1, 5))])
    ledger = update_ledger(None, df, data1)
    resolved2 = resolve_targets(_data(), ledger=ledger_from_json(ledger_to_json(ledger)))
    tmap = resolved2.target_total_map
    assert tmap["A"] == pytest.approx(tmap["B"])  # the punishment is not repaid


def test_invariant_algebra_with_penalty_and_excusal_combined():
    """f < 1 (penalty scaling) + an excused reduction, checked algebraically:
    prior + met-target - debit + credit is identical across residents."""
    from dataclasses import replace
    from model.ledger import block_adjustments
    from model.optimiser import resolve_targets

    data = replace(
        _data(),
        extra_points={"A": 2.0},
        group_factors={"half": 0.5},
        resident_groups={"B": "half"},
    )
    resolved = resolve_targets(data)
    adj = block_adjustments(None, data)
    extras = {"A": 2.0, "B": 0.0}
    updated = {
        p: resolved.target_total_map[p] - extras[p] + adj[p]["excused_total"]
        for p in ("A", "B")
    }
    assert updated["A"] == pytest.approx(updated["B"])


def test_adjustments_audit_written_and_stripped_on_load():
    from dataclasses import replace

    data = replace(_data(), extra_points={"A": 1.0})
    df = _df((date(2023, 1, 1), "A"))
    updated = update_ledger(None, df, data)
    assert "adjustments" in updated["A"]
    restored = ledger_from_json(ledger_to_json(updated))
    assert "adjustments" not in restored["A"]  # audit is per-update, not history
    assert restored["A"]["total"] == updated["A"]["total"]


def test_prior_with_adjustments_key_tolerated():
    prior = {
        "A": {"total": 3.0, "weekend": 0.0, "night_float": 0.0,
              "adjustments": {"penalty_not_carried": 1.0}},
    }
    df = _df((date(2023, 1, 1), "A"))
    updated = update_ledger(prior, df, _data())
    assert updated["A"]["total"] == 4.0
    assert "adjustments" not in updated["A"]  # old audit dropped, dims kept


def test_update_ledger_accumulates_label_history():
    data = _data()
    df = pd.DataFrame([
        {"Date": date(2023, 1, 1), "S": "A"},
        {"Date": date(2023, 1, 2), "S": "A"},
        {"Date": date(2023, 1, 3), "S": "B"},
    ])
    prior = {"A": {"total": 4.0, "weekend": 0.0, "night_float": 0.0,
                   "labels": {"S": 4.0}, "label_counts": {"S": 4}}}
    ledger = update_ledger(prior, df, data)
    assert ledger["A"]["labels"] == {"S": 6.0}
    assert ledger["A"]["label_counts"] == {"S": 6}
    assert ledger["B"]["labels"] == {"S": 1.0}
    assert ledger["B"]["label_counts"] == {"S": 1}


def test_ledger_json_round_trips_label_history_and_legacy_loads():
    ledger = {"A": {"total": 3.0, "weekend": 1.0,
                    "labels": {"S": 3.0}, "label_counts": {"S": 3}}}
    restored = ledger_from_json(ledger_to_json(ledger))
    assert restored == ledger
    # Legacy files without the history keys load unchanged.
    legacy = ledger_from_json('{"A": {"total": 2.0}}')
    assert legacy == {"A": {"total": 2.0, "weekend": 0.0}}


def test_ledger_rows_round_trip_preserves_history_via_base():
    from model.ledger import ledger_to_rows, rows_to_ledger

    ledger = {
        "A": {"total": 3.0, "weekend": 1.0,
              "labels": {"S": 3.0}, "label_counts": {"S": 3}},
        "B": {"total": 2.0, "weekend": 0.0},
    }
    rows = ledger_to_rows(ledger)
    assert rows == [
        {"Resident": "A", "Total": 3.0, "Weekend": 1.0},
        {"Resident": "B", "Total": 2.0, "Weekend": 0.0},
    ]
    rebuilt, problems = rows_to_ledger(rows, base=ledger)
    assert problems == []
    assert rebuilt == ledger


def test_rows_to_ledger_reports_problems_and_allows_negatives():
    from model.ledger import rows_to_ledger

    rows = [
        {"Resident": " A ", "Total": "abc", "Weekend": -1.5},
        {"Resident": "A", "Total": 2.0, "Weekend": 0.0},
        {"Resident": "", "Total": 5.0, "Weekend": 0.0},
        {"Resident": None, "Total": 0.0, "Weekend": 0.0},
    ]
    ledger, problems = rows_to_ledger(rows)
    assert ledger["A"]["total"] == 2.0  # duplicate: the last row wins
    assert any("non-numeric" in p for p in problems)
    assert any("last one wins" in p for p in problems)
    assert any("no resident name" in p for p in problems)
    ledger2, _ = rows_to_ledger([{"Resident": "B", "Total": 1.0, "Weekend": -2.0}])
    assert ledger2["B"]["weekend"] == -2.0
