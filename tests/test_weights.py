"""Tests for model/weights.py — group/perk load factors and fairness weights.

Stub-safe: imports only model.weights / model.data_models (no pandas/ortools).
"""
from datetime import date

import pytest

from model.data_models import ShiftTemplate, InputData, Perk
from model.weights import person_factor, availability_weights, reference_weights

MON = date(2023, 1, 2)
THU = date(2023, 1, 5)
SUN = date(2023, 1, 8)  # 7-day block Mon..Sun


def _data(**kw) -> InputData:
    base = dict(
        start_date=MON,
        end_date=SUN,
        shifts=[ShiftTemplate(label="S", role="Junior", night_float=False, thu_weekend=False)],
        juniors=["A", "B"],
        seniors=[],
        nf_juniors=[],
        nf_seniors=[],
        leaves=[],
        rotators=[],
        min_gap=1,
    )
    base.update(kw)
    return InputData(**base)


# --- person_factor ------------------------------------------------------------

def test_factor_defaults_to_one():
    assert person_factor("A", MON, _data()) == 1.0


def test_group_factor_applies_to_members_only():
    data = _data(group_factors={"R2": 0.9}, resident_groups={"A": "R2"})
    assert person_factor("A", MON, data) == 0.9
    assert person_factor("B", MON, data) == 1.0


def test_perk_factor_respects_window_edges():
    data = _data(perks=[Perk("A", 0.8, MON, THU)])
    assert person_factor("A", MON, data) == 0.8  # inclusive start
    assert person_factor("A", THU, data) == 0.8  # inclusive end
    assert person_factor("A", SUN, data) == 1.0  # after window
    assert person_factor("B", THU, data) == 1.0  # other resident


def test_perk_open_ended_windows():
    forever = _data(perks=[Perk("A", 0.8)])
    assert person_factor("A", MON, forever) == 0.8
    assert person_factor("A", SUN, forever) == 0.8
    from_thu = _data(perks=[Perk("A", 0.8, THU, None)])
    assert person_factor("A", MON, from_thu) == 1.0
    assert person_factor("A", THU, from_thu) == 0.8
    until_thu = _data(perks=[Perk("A", 0.8, None, THU)])
    assert person_factor("A", MON, until_thu) == 0.8
    assert person_factor("A", SUN, until_thu) == 1.0


def test_perk_tuples_accepted_and_overlaps_multiply():
    data = _data(
        group_factors={"R2": 0.9},
        resident_groups={"A": "R2"},
        perks=[("A", 0.5), ("A", 0.8, MON, THU)],
    )
    # group 0.9 x perk 0.5 x perk 0.8 inside the second perk's window
    assert person_factor("A", MON, data) == 0.9 * 0.5 * 0.8
    assert person_factor("A", SUN, data) == 0.9 * 0.5


# --- availability_weights -----------------------------------------------------

def test_weights_without_factors_match_active_day_counts():
    data = _data(
        rotators=[("B", MON, THU)],  # B active 4 of 7 days
        leaves=[("A", MON, MON, False)],  # A loses 1 day (uncompensated)
    )
    assert availability_weights(data) == {"A": 6.0, "B": 4.0}


def test_compensated_leave_keeps_weight():
    data = _data(leaves=[("A", MON, THU, True)])
    assert availability_weights(data) == {"A": 7.0, "B": 7.0}


def test_group_factor_scales_every_day():
    data = _data(group_factors={"R2": 0.9}, resident_groups={"A": "R2"})
    w = availability_weights(data)
    assert w["A"] == pytest.approx(7 * 0.9)
    assert w["B"] == 7.0


def test_perk_scales_only_window_days():
    data = _data(perks=[Perk("A", 0.5, MON, THU)])  # 4 days at 0.5, 3 at 1.0
    assert availability_weights(data)["A"] == pytest.approx(4 * 0.5 + 3)


def test_rotator_and_factor_compose():
    data = _data(
        rotators=[("A", MON, THU)],
        group_factors={"R2": 0.5},
        resident_groups={"A": "R2"},
    )
    assert availability_weights(data)["A"] == pytest.approx(4 * 0.5)


def test_reference_weights_ignore_everything():
    data = _data(
        rotators=[("A", MON, THU)],
        leaves=[("B", MON, SUN, False)],
        group_factors={"R2": 0.5},
        resident_groups={"A": "R2"},
        perks=[Perk("B", 0.1)],
    )
    assert reference_weights(data) == {"A": 7.0, "B": 7.0}
