from dataclasses import dataclass
from datetime import date
from typing import List, Tuple, Dict


def normalized_leaves(leaves):
    """Yield ``(name, start, end, compensated)`` for each leave entry.

    Leaves may be stored as 4-tuples carrying a per-leave ``compensated`` flag, or
    as legacy 3-tuples (treated as compensated — full quota, the original
    behaviour). ``compensated`` leaves block the days but keep the resident's fair
    share; ``uncompensated`` leaves additionally scale that share down (like a
    rotator), so the resident is not penalised for the absence.
    """
    for entry in leaves or []:
        name, start, end = entry[0], entry[1], entry[2]
        compensated = bool(entry[3]) if len(entry) > 3 else True
        yield name, start, end, compensated


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
    leaves: List[Tuple[str, date, date]]
    rotators: List[Tuple[str, date, date]]
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
    target_label: Dict[tuple[str, str], float] | None = None
    target_total: float | None = None
    target_weekend: Dict[str, float] | None = None
    target_total_map: Dict[str, float] | None = None
    target_night_float: Dict[str, float] | None = None

