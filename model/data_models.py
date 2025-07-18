from dataclasses import dataclass
from datetime import date
from typing import List, Tuple, Dict


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
    target_label: Dict[tuple[str, str], float] | None = None
    target_total: float | None = None
    target_weekend: Dict[str, float] | None = None

