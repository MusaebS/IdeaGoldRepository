from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import List

from .data_models import (
    Blackout,
    Leave,
    LoadReduction,
    NightFloatAssignment,
    NightFloatCoverage,
    Perk,
    RotatorWindow,
    ShiftClosure,
    ShiftTemplate,
    InputData,
    normalized_blackouts,
    normalized_closures,
    normalized_leaves,
    normalized_nf_assignments,
    normalized_nf_coverage,
    normalized_perks,
    normalized_reductions,
    normalized_rotators,
)

__all__ = [
    "input_data_to_json",
    "input_data_from_json",
    "display_from_json",
    "config_compatibility_warnings",
]


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _finite_number(
    value, field: str, *, positive: bool = False, nonnegative: bool = False
) -> float:
    """Parse a finite JSON number with a useful configuration error."""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if (
        not math.isfinite(parsed)
        or (positive and parsed <= 0)
        or (nonnegative and parsed < 0)
    ):
        requirement = (
            "a positive finite number"
            if positive
            else "a non-negative finite number" if nonnegative else "finite"
        )
        raise ValueError(f"{field} must be {requirement}")
    return parsed


def _integer(value, field: str, *, minimum: int | None = None) -> int:
    """Parse an integer without silently truncating floats or accepting bools."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not math.isfinite(parsed) or not parsed.is_integer():
        raise ValueError(f"{field} must be an integer")
    integer = int(parsed)
    if minimum is not None and integer < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return integer


def _require_bool(value, field: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")


def _validate_raw_config(raw) -> None:
    """Reject unsafe scalar/schema values before building ``InputData``.

    Cross-field operational validation remains in ``model.validation`` so an
    incomplete but structurally safe file can still be loaded and repaired in
    the UI. This boundary catches values that otherwise crash much later.
    """
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a JSON object")
    shifts = raw.get("shifts", [])
    if not isinstance(shifts, list):
        raise ValueError("shifts must be a list")
    seen_labels: set[str] = set()
    for index, shift in enumerate(shifts):
        if not isinstance(shift, dict):
            raise ValueError(f"shifts[{index}] must be an object")
        label = str(shift.get("label", "")).strip()
        if not label:
            raise ValueError(f"shifts[{index}].label must not be empty")
        key = label.casefold()
        if key in seen_labels:
            raise ValueError(f"Duplicate shift label '{label}'")
        seen_labels.add(key)
        role = shift.get("role")
        if role not in ("Junior", "Senior"):
            raise ValueError(
                f"shifts[{index}].role must be 'Junior' or 'Senior', not {role!r}"
            )
        _finite_number(
            shift.get("points", 1.0),
            f"shifts[{index}].points",
            nonnegative=True,
        )
        for bool_field in ("night_float", "thu_weekend"):
            if bool_field in shift:
                _require_bool(shift[bool_field], f"shifts[{index}].{bool_field}")

    _finite_number(raw.get("weekend_multiplier", 1.0), "weekend_multiplier", positive=True)
    weekend_days = raw.get("weekend_days")
    if weekend_days is not None:
        if not isinstance(weekend_days, list):
            raise ValueError("weekend_days must be a list")
        parsed_days = []
        for value in weekend_days:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("weekend_days values must be integers 0..6")
            day = value
            if not 0 <= day <= 6:
                raise ValueError(f"weekend_days contains invalid weekday {day}; expected 0..6")
            parsed_days.append(day)
        if len(set(parsed_days)) != len(parsed_days):
            raise ValueError("weekend_days must not contain duplicates")

    _integer(raw.get("min_gap", 1), "min_gap", minimum=0)
    for index, entry in enumerate(raw.get("weekday_points") or []):
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise ValueError(f"weekday_points[{index}] must be [shift, weekday, points]")
        weekday = _integer(entry[1], f"weekday_points[{index}].weekday")
        if not 0 <= weekday <= 6:
            raise ValueError(f"weekday_points[{index}] has invalid weekday {weekday}")
        _finite_number(
            entry[2], f"weekday_points[{index}].points", nonnegative=True
        )

    for name, value in (raw.get("max_total_excused") or {}).items():
        _require_bool(value, f"max_total_excused[{name!r}]")
    for index, entry in enumerate(raw.get("leaves") or []):
        if not isinstance(entry, (list, tuple)) or len(entry) not in (3, 4):
            raise ValueError(f"leaves[{index}] must have 3 or 4 values")
        if len(entry) == 4:
            _require_bool(entry[3], f"leaves[{index}].compensated")
    for index, entry in enumerate(raw.get("holidays") or []):
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise ValueError(f"holidays[{index}] must have 3 values")
        _require_bool(entry[2], f"holidays[{index}].counts_as_weekend")
    for index, entry in enumerate(raw.get("blackouts") or []):
        if not isinstance(entry, (list, tuple)) or len(entry) not in (4, 5, 6):
            raise ValueError(f"blackouts[{index}] must have 4 to 6 values")
        for position, field in ((4, "night_before"), (5, "compensated")):
            if len(entry) > position:
                _require_bool(entry[position], f"blackouts[{index}].{field}")
    for index, entry in enumerate(raw.get("reductions") or []):
        if not isinstance(entry, (list, tuple)) or len(entry) not in (6, 7):
            raise ValueError(f"reductions[{index}] must have 6 or 7 values")
        if len(entry) == 7:
            _require_bool(entry[6], f"reductions[{index}].keep_total")


def config_compatibility_warnings(data: InputData) -> list[str]:
    """Warnings for legacy fields retained in JSON but no longer operational."""
    warnings: list[str] = []
    if data.max_nights:
        warnings.append(
            "Legacy 'max_nights' values were loaded but are not used by the "
            "night-float overlay. Configure NF coverage/assignments instead."
        )
    if data.nf_block_length != 5:
        warnings.append(
            "Legacy 'nf_block_length' was loaded but is not used by the current "
            "date-range night-float overlay."
        )
    return warnings


def _windows_to_json(windows) -> List[list]:
    return [
        [name, start.isoformat(), end.isoformat()]
        for name, start, end in normalized_rotators(windows)
    ]


def _windows_from_json(items) -> List[RotatorWindow]:
    out: List[RotatorWindow] = []
    for name, start, end in items or []:
        out.append(RotatorWindow(name, date.fromisoformat(start), date.fromisoformat(end)))
    return out


def _leaves_to_json(leaves) -> List[list]:
    return [
        [name, start.isoformat(), end.isoformat(), compensated]
        for name, start, end, compensated in normalized_leaves(leaves)
    ]


def _leaves_from_json(items) -> List[Leave]:
    out: List[Leave] = []
    for entry in items or []:
        name, start, end = entry[0], entry[1], entry[2]
        compensated = bool(entry[3]) if len(entry) > 3 else True
        out.append(Leave(name, date.fromisoformat(start), date.fromisoformat(end), compensated))
    return out


def input_data_to_json(data: InputData, display: dict | None = None) -> str:
    """Serialise an :class:`InputData` configuration to a JSON string.

    Solver-derived fields (the ``target_*`` values) are intentionally omitted;
    only user-entered configuration is saved. ``display`` (optional) is a
    cosmetic section — palette colours, custom columns and their values,
    column order — stored under a ``"display"`` key so a saved config restores
    the look as well as the maths. Loaders that predate it ignore the key.
    """
    payload = {
        "start_date": data.start_date.isoformat(),
        "end_date": data.end_date.isoformat(),
        "shifts": [
            {
                "label": s.label,
                "role": s.role,
                "night_float": s.night_float,
                "thu_weekend": s.thu_weekend,
                "points": s.points,
            }
            for s in data.shifts
        ],
        "juniors": list(data.juniors),
        "seniors": list(data.seniors),
        "nf_juniors": list(data.nf_juniors),
        "nf_seniors": list(data.nf_seniors),
        "leaves": _leaves_to_json(data.leaves),
        "rotators": _windows_to_json(data.rotators),
        "min_gap": data.min_gap,
        "nf_block_length": data.nf_block_length,
        "seed": data.seed,
        "weekend_days": data.weekend_days,
        "weekend_multiplier": data.weekend_multiplier,
        "max_total": data.max_total,
        "max_total_excused": data.max_total_excused or None,
        "max_nights": data.max_nights,
        "extra_points": data.extra_points,
        "weekday_points": (
            [[label, wd, pts] for (label, wd), pts in data.weekday_points.items()]
            if data.weekday_points
            else None
        ),
        "holidays": (
            [[d.isoformat(), bonus, weekend] for d, bonus, weekend in data.holidays]
            if data.holidays
            else None
        ),
        "group_factors": data.group_factors or None,
        "resident_groups": data.resident_groups or None,
        "perks": (
            [
                [p.name, p.factor,
                 p.start.isoformat() if p.start else None,
                 p.end.isoformat() if p.end else None]
                for p in normalized_perks(data.perks)
            ]
            if data.perks
            else None
        ),
        "exempt_shifts": (
            {name: sorted(labels) for name, labels in data.exempt_shifts.items()}
            if data.exempt_shifts
            else None
        ),
        "named_groups": (
            {group: list(members) for group, members in data.named_groups.items()}
            if data.named_groups
            else None
        ),
        "blackouts": (
            [
                [b.group, list(b.members), b.start.isoformat(), b.end.isoformat(),
                 b.night_before, b.compensated]
                for b in normalized_blackouts(data.blackouts)
            ]
            if data.blackouts
            else None
        ),
        "reductions": (
            [
                [r.group, list(r.members), list(r.labels), r.factor,
                 r.start.isoformat(), r.end.isoformat(), r.keep_total]
                for r in normalized_reductions(data.reductions)
            ]
            if data.reductions
            else None
        ),
        "preferred_shifts": (
            {name: sorted(labels) for name, labels in data.preferred_shifts.items()}
            if data.preferred_shifts
            else None
        ),
        "preferred_day_type": (
            dict(data.preferred_day_type) if data.preferred_day_type else None
        ),
        "avoid_pairs": (
            [[pair[0], pair[1]] for pair in data.avoid_pairs]
            if data.avoid_pairs
            else None
        ),
        "nf_coverage": (
            [
                [c.label, list(c.weekdays),
                 [d.isoformat() for d in c.include_dates],
                 [d.isoformat() for d in c.exclude_dates]]
                for c in normalized_nf_coverage(data.nf_coverage)
            ]
            if data.nf_coverage
            else None
        ),
        "nf_assignments": (
            [
                [a.name, a.start.isoformat(), a.end.isoformat(), list(a.labels), a.rest_days]
                for a in normalized_nf_assignments(data.nf_assignments, default_rest=data.nf_rest_days)
            ]
            if data.nf_assignments
            else None
        ),
        "nf_rest_days": data.nf_rest_days,
        "closures": (
            [
                [c.label, c.start.isoformat(), c.end.isoformat(), list(c.weekdays)]
                for c in normalized_closures(data.closures)
            ]
            if data.closures
            else None
        ),
    }
    if display:
        payload["display"] = display
    return json.dumps(payload, indent=2)


def display_from_json(text: str, reserved_columns=()) -> dict | None:
    """Extract and sanitise the cosmetic ``"display"`` section of a config.

    Returns ``None`` for configs without one (or anything malformed) — the
    caller simply skips the display restore in that case.
    """
    from .coloring import DEFAULT_PALETTE  # local: keeps module deps minimal

    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    display = raw.get("display") if isinstance(raw, dict) else None
    if not isinstance(display, dict):
        return None
    out: dict = {}
    palette = display.get("palette")
    if isinstance(palette, dict):
        cleaned = {
            str(k): str(v)
            for k, v in palette.items()
            if k in DEFAULT_PALETTE and isinstance(v, str) and _HEX_COLOR.fullmatch(v)
        }
        if cleaned:
            out["palette"] = cleaned
    cols = display.get("extra_cols")
    if isinstance(cols, list):
        reserved = {str(c).strip().casefold() for c in reserved_columns}
        cleaned_cols = []
        seen = set(reserved)
        for value in cols:
            col = str(value).strip()
            key = col.casefold()
            if not col or key in seen:
                continue
            seen.add(key)
            cleaned_cols.append(col)
        out["extra_cols"] = cleaned_cols
    vals = display.get("extra_vals")
    if isinstance(vals, dict):
        allowed = set(out.get("extra_cols", ()))
        out["extra_vals"] = {
            str(col): {str(d): str(v) for d, v in per_day.items()}
            for col, per_day in vals.items()
            if isinstance(per_day, dict) and str(col) in allowed
        }
    order = display.get("col_order")
    if isinstance(order, list):
        out["col_order"] = [str(c) for c in order]
    return out or None


def input_data_from_json(text: str) -> InputData:
    """Rebuild an :class:`InputData` from a JSON string produced by
    :func:`input_data_to_json`."""
    raw = json.loads(text)
    _validate_raw_config(raw)
    shifts = [
        ShiftTemplate(
            label=s["label"],
            role=s["role"],
            night_float=bool(s.get("night_float", False)),
            thu_weekend=bool(s.get("thu_weekend", False)),
            points=float(s.get("points", 1.0)),
        )
        for s in raw.get("shifts", [])
    ]
    return InputData(
        start_date=date.fromisoformat(raw["start_date"]),
        end_date=date.fromisoformat(raw["end_date"]),
        shifts=shifts,
        juniors=list(raw.get("juniors", [])),
        seniors=list(raw.get("seniors", [])),
        nf_juniors=list(raw.get("nf_juniors", [])),
        nf_seniors=list(raw.get("nf_seniors", [])),
        leaves=_leaves_from_json(raw.get("leaves")),
        rotators=_windows_from_json(raw.get("rotators")),
        min_gap=int(raw.get("min_gap", 1)),
        weekend_multiplier=float(raw.get("weekend_multiplier", 1.0)),
        nf_block_length=int(raw.get("nf_block_length", 5)),
        seed=int(raw.get("seed", 0)),
        weekend_days=(
            [int(d) for d in raw["weekend_days"]]
            if raw.get("weekend_days") is not None
            else None
        ),
        max_total=(
            {str(k): float(v) for k, v in raw["max_total"].items()}
            if raw.get("max_total")
            else None
        ),
        max_total_excused=(
            {str(k): bool(v) for k, v in raw["max_total_excused"].items()}
            if raw.get("max_total_excused")
            else None
        ),
        max_nights=(
            {str(k): float(v) for k, v in raw["max_nights"].items()}
            if raw.get("max_nights")
            else None
        ),
        extra_points=(
            {str(k): float(v) for k, v in raw["extra_points"].items()}
            if raw.get("extra_points")
            else None
        ),
        weekday_points=(
            {(str(label), int(wd)): float(pts) for label, wd, pts in raw["weekday_points"]}
            if raw.get("weekday_points")
            else None
        ),
        holidays=(
            [
                (date.fromisoformat(d), float(bonus), bool(weekend))
                for d, bonus, weekend in raw["holidays"]
            ]
            if raw.get("holidays")
            else None
        ),
        group_factors=(
            {str(k): float(v) for k, v in raw["group_factors"].items()}
            if raw.get("group_factors")
            else None
        ),
        resident_groups=(
            {str(k): str(v) for k, v in raw["resident_groups"].items()}
            if raw.get("resident_groups")
            else None
        ),
        perks=(
            [
                Perk(
                    str(name),
                    float(factor),
                    date.fromisoformat(start) if start else None,
                    date.fromisoformat(end) if end else None,
                )
                for name, factor, start, end in raw["perks"]
            ]
            if raw.get("perks")
            else None
        ),
        exempt_shifts=(
            {str(k): [str(x) for x in v] for k, v in raw["exempt_shifts"].items()}
            if raw.get("exempt_shifts")
            else None
        ),
        named_groups=(
            {str(g): [str(m) for m in v] for g, v in raw["named_groups"].items()}
            if raw.get("named_groups")
            else None
        ),
        blackouts=(
            [
                Blackout(
                    None if entry[0] is None else str(entry[0]),
                    tuple(str(m) for m in entry[1] or ()),
                    date.fromisoformat(entry[2]),
                    date.fromisoformat(entry[3]),
                    bool(entry[4]) if len(entry) > 4 else True,
                    bool(entry[5]) if len(entry) > 5 else True,
                )
                for entry in raw["blackouts"]
            ]
            if raw.get("blackouts")
            else None
        ),
        reductions=(
            [
                LoadReduction(
                    None if entry[0] is None else str(entry[0]),
                    tuple(str(m) for m in entry[1] or ()),
                    tuple(str(lbl) for lbl in entry[2] or ()),
                    float(entry[3]),
                    date.fromisoformat(entry[4]),
                    date.fromisoformat(entry[5]),
                    bool(entry[6]) if len(entry) > 6 else False,
                )
                for entry in raw["reductions"]
            ]
            if raw.get("reductions")
            else None
        ),
        preferred_shifts=(
            {str(k): [str(x) for x in v] for k, v in raw["preferred_shifts"].items()}
            if raw.get("preferred_shifts")
            else None
        ),
        preferred_day_type=(
            {str(k): str(v) for k, v in raw["preferred_day_type"].items()}
            if raw.get("preferred_day_type")
            else None
        ),
        avoid_pairs=(
            [(str(a), str(b)) for a, b in raw["avoid_pairs"]]
            if raw.get("avoid_pairs")
            else None
        ),
        nf_coverage=(
            {
                str(entry[0]): NightFloatCoverage(
                    str(entry[0]),
                    tuple(int(w) for w in entry[1] or ()),
                    tuple(date.fromisoformat(d) for d in (entry[2] if len(entry) > 2 else []) or []),
                    tuple(date.fromisoformat(d) for d in (entry[3] if len(entry) > 3 else []) or []),
                )
                for entry in raw["nf_coverage"]
            }
            if raw.get("nf_coverage")
            else None
        ),
        nf_assignments=(
            [
                NightFloatAssignment(
                    str(entry[0]),
                    date.fromisoformat(entry[1]),
                    date.fromisoformat(entry[2]),
                    tuple(str(lbl) for lbl in (entry[3] if len(entry) > 3 else []) or []),
                    int(entry[4]) if len(entry) > 4 else int(raw.get("nf_rest_days", 1)),
                )
                for entry in raw["nf_assignments"]
            ]
            if raw.get("nf_assignments")
            else None
        ),
        nf_rest_days=int(raw.get("nf_rest_days", 1)),
        closures=(
            [
                ShiftClosure(
                    str(entry[0]),
                    date.fromisoformat(entry[1]),
                    date.fromisoformat(entry[2]),
                    tuple(int(w) for w in (entry[3] if len(entry) > 3 else []) or []),
                )
                for entry in raw["closures"]
            ]
            if raw.get("closures")
            else None
        ),
    )
