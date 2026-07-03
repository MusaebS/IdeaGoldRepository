from dataclasses import dataclass
from datetime import date
from typing import List, NamedTuple, Sequence, Tuple, Dict


class Leave(NamedTuple):
    """A leave window. Being a NamedTuple it compares equal to, unpacks like,
    and serialises exactly as the plain tuples used historically."""

    name: str
    start: date
    end: date
    compensated: bool = True


class RotatorWindow(NamedTuple):
    """An active window for a rotating resident (only available inside it)."""

    name: str
    start: date
    end: date


class Perk(NamedTuple):
    """An individual load reduction (or increase) for a resident.

    ``factor`` multiplies the resident's fair-share weight on days the perk is
    active (e.g. 0.8 = 20% fewer points). ``start``/``end`` bound the window;
    ``None`` means unbounded on that side, so a perk with neither date applies
    forever. Overlapping perks multiply.
    """

    name: str
    factor: float
    start: date | None = None
    end: date | None = None


def normalized_perks(perks):
    """Yield a :class:`Perk` for each entry (typed or 2/3/4-tuple)."""
    for entry in perks or []:
        name, factor = entry[0], float(entry[1])
        start = entry[2] if len(entry) > 2 else None
        end = entry[3] if len(entry) > 3 else None
        yield Perk(name, factor, start, end)


def normalized_leaves(leaves):
    """Yield a :class:`Leave` for each leave entry.

    Leaves may be stored as :class:`Leave`, 4-tuples carrying a per-leave
    ``compensated`` flag, or legacy 3-tuples (treated as compensated — full
    quota, the original behaviour). ``compensated`` leaves block the days but
    keep the resident's fair share; ``uncompensated`` leaves additionally scale
    that share down (like a rotator), so the resident is not penalised for the
    absence.
    """
    for entry in leaves or []:
        name, start, end = entry[0], entry[1], entry[2]
        compensated = bool(entry[3]) if len(entry) > 3 else True
        yield Leave(name, start, end, compensated)


def normalized_rotators(rotators):
    """Yield a :class:`RotatorWindow` for each rotator entry (tuple or typed)."""
    for entry in rotators or []:
        yield RotatorWindow(entry[0], entry[1], entry[2])


@dataclass
class ShiftTemplate:
    label: str
    role: str  # 'Junior' or 'Senior'
    night_float: bool
    thu_weekend: bool
    points: float = 1.0


@dataclass
class InputData:
    start_date: date
    end_date: date
    shifts: List[ShiftTemplate]
    juniors: List[str]
    seniors: List[str]
    nf_juniors: List[str]
    nf_seniors: List[str]
    # Preferred element type is Leave / RotatorWindow; plain 3- or 4-tuples are
    # accepted everywhere for backward compatibility (see normalized_leaves /
    # normalized_rotators).
    leaves: Sequence[Leave | Tuple[str, date, date] | Tuple[str, date, date, bool]]
    rotators: Sequence[RotatorWindow | Tuple[str, date, date]]
    min_gap: int = 1
    nf_block_length: int = 5
    seed: int = 0
    # Weekday numbers (Mon=0 .. Sun=6) that count as the weekend. ``None`` keeps
    # the default Saturday/Sunday. The per-shift ``thu_weekend`` flag still adds
    # Thursday for individual shifts on top of this.
    weekend_days: List[int] | None = None
    # Per-resident hard caps (in points). A resident in the map works at most that
    # many total / night-float points; uncovered slots fall to ``Unfilled``.
    max_total: Dict[str, float] | None = None
    max_nights: Dict[str, float] | None = None
    # Per-resident mandatory extra points (e.g. a penalty). A resident in the map
    # must carry this many points above their share: their total target is raised
    # by it (others' lowered to reconcile) and a hard floor enforces it.
    extra_points: Dict[str, float] | None = None
    # Point-value overrides. ``weekday_points`` maps (shift label, weekday 0=Mon..
    # 6=Sun) to the exact points that shift is worth on that weekday (e.g. a night
    # worth 2 on Tuesdays). ``holidays`` is a list of (date, bonus, count_as_weekend)
    # — every shift on that date gets ``bonus`` extra points, and if the flag is set
    # the date also counts toward weekend balance.
    weekday_points: Dict[Tuple[str, int], float] | None = None
    holidays: List[Tuple[date, float, bool]] | None = None
    # Seniority groups: a named group (e.g. "R2") maps to a load factor and
    # residents are assigned to groups. An R2 at 0.9 fairly carries ~10% fewer
    # points than an R1 at 1.0; the reduction flows through every fairness
    # target (total / weekend / night-float) via the availability weights.
    group_factors: Dict[str, float] | None = None
    resident_groups: Dict[str, str] | None = None
    # Individual perks: per-resident load factors, optionally time-bounded
    # (see Perk). Composes multiplicatively with the group factor.
    perks: Sequence[Perk | Tuple] | None = None
    # Shift-type exemptions: resident -> shift labels never assigned to them
    # (a hard block). Follows the night-float-eligibility precedent: the
    # resident's targets are UNCHANGED — they carry their full share on the
    # remaining shift types. Combine with a perk to also lower the share.
    exempt_shifts: Dict[str, List[str]] | None = None
    # Named resident groups: group name -> member names. A pure roster grouping
    # used for bulk entry (group blackouts / load reductions); it carries no
    # load factor — that is what group_factors/resident_groups are for. A
    # resident may belong to several groups.
    named_groups: Dict[str, List[str]] | None = None
    target_label: Dict[tuple[str, str], float] | None = None
    target_total: float | None = None
    target_weekend: Dict[str, float] | None = None
    target_total_map: Dict[str, float] | None = None
    target_night_float: Dict[str, float] | None = None

