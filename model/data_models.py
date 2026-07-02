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
    target_label: Dict[tuple[str, str], float] | None = None
    target_total: float | None = None
    target_weekend: Dict[str, float] | None = None
    target_total_map: Dict[str, float] | None = None
    target_night_float: Dict[str, float] | None = None

