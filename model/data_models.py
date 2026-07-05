from dataclasses import dataclass
from datetime import date, timedelta
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


class Blackout(NamedTuple):
    """A no-call window for a named group (or an ad-hoc set) of residents.

    Everyone covered is blocked from every non-night-float shift in the
    window, and — when ``night_before`` is set — from the *night on-calls* of
    the day before it, so nobody enters the period post-call. Night on-call
    means a shift flagged "Thu counts as weekend" (``thu_weekend``), the
    roster's existing marker for shifts whose morning after is post-call;
    night-float duty is a separate rotation and is never touched by
    blackouts. Unlike a leave the entry is per group and reported
    separately. ``compensated`` keeps each member's full fair share (the
    default): missed load is made up on other days, or carried in the
    fairness ledger as repayable debt. Uncompensated scales the share down
    like uncompensated leave.
    """

    group: str | None          # named-group reference, resolved at use time
    members: Tuple[str, ...]   # ad-hoc names, used when group is None
    start: date
    end: date
    night_before: bool = True
    compensated: bool = True


def is_night_call(shift) -> bool:
    """The roster's night on-call marker: "Thu counts as weekend", non-NF.

    Thursday-counts-as-weekend is set exactly on the night on-calls (the
    morning after is post-call, so a Thursday night is weekend load); night
    float is a separate rotation, not an on-call.
    """
    return bool(shift.thu_weekend) and not shift.night_float


def normalized_blackouts(blackouts):
    """Yield a :class:`Blackout` for each entry (typed or 4/5/6-tuple)."""
    for entry in blackouts or []:
        group = entry[0]
        members = tuple(entry[1] or ())
        start, end = entry[2], entry[3]
        night_before = bool(entry[4]) if len(entry) > 4 else True
        compensated = bool(entry[5]) if len(entry) > 5 else True
        yield Blackout(group, members, start, end, night_before, compensated)


class LoadReduction(NamedTuple):
    """Less of specific shift types for a group inside a window.

    Each covered member carries at most ``factor`` (0..1) of their fair share
    of the ``labels`` inside the window — 0 means none of those shifts at all.
    The availability weights are deliberately untouched, so the shortfall is
    never excused: it carries in the fairness ledger as debt the member
    repays in later blocks. ``keep_total`` picks the in-block behaviour:
    False (default) also lowers the member's total/night-float targets so
    they genuinely work less this block and repay everything later; True
    keeps their full share, so the solver compensates them with other shift
    types now and only what cannot fit carries over.
    """

    group: str | None          # named-group reference, resolved at use time
    members: Tuple[str, ...]   # ad-hoc names, used when group is None
    labels: Tuple[str, ...]
    factor: float
    start: date
    end: date
    keep_total: bool = False


def normalized_reductions(reductions):
    """Yield a :class:`LoadReduction` for each entry (typed or 6/7-tuple)."""
    for entry in reductions or []:
        group = entry[0]
        members = tuple(entry[1] or ())
        labels = tuple(entry[2] or ())
        factor = float(entry[3])
        start, end = entry[4], entry[5]
        keep_total = bool(entry[6]) if len(entry) > 6 else False
        yield LoadReduction(group, members, labels, factor, start, end, keep_total)


def blackout_person_windows(blackouts, named_groups):
    """Per-person blackout windows: ``{name: [(start, end, compensated)]}``.

    Group references resolve to the *current* members, so editing a group
    updates every blackout that uses it. This is the window proper; the
    night-before rule is a partial (night-on-calls-only) block on a single
    day and lives in :func:`blackout_night_before_dates`.
    """
    groups = named_groups or {}
    out: Dict[str, List[Tuple[date, date, bool]]] = {}
    for b in normalized_blackouts(blackouts):
        people = groups.get(b.group, ()) if b.group is not None else b.members
        for person in people:
            out.setdefault(person, []).append((b.start, b.end, b.compensated))
    return out


def blackout_night_before_dates(blackouts, named_groups):
    """Per-person dates whose *night on-calls* are blocked: ``{name: {date}}``.

    For every blackout with ``night_before``, the day before the window: the
    member must not take a night on-call there (or they would be post-call on
    their first off day). Only shifts matching :func:`is_night_call` are
    blocked on these dates — day shifts and night float are unaffected.
    """
    groups = named_groups or {}
    out: Dict[str, set] = {}
    for b in normalized_blackouts(blackouts):
        if not b.night_before:
            continue
        people = groups.get(b.group, ()) if b.group is not None else b.members
        for person in people:
            out.setdefault(person, set()).add(b.start - timedelta(days=1))
    return out


class NightFloatCoverage(NamedTuple):
    """Which dates a night-float-eligible shift is actually covered by NF.

    A shift with ``night_float`` set is *eligible* for the night-float overlay;
    this pattern says which of its dates the overlay really covers (the rest
    stay ordinary regular shifts). ``weekdays`` (Mon=0..Sun=6) is the recurring
    pattern; ``include_dates`` add one-off dates; ``exclude_dates`` remove
    specific dates even if their weekday matches. No pattern ⇒ never NF-covered
    (the shift is scheduled entirely as a regular shift).
    """

    label: str
    weekdays: Tuple[int, ...] = ()
    include_dates: Tuple[date, ...] = ()
    exclude_dates: Tuple[date, ...] = ()


def normalized_nf_coverage(coverage):
    """Yield ``NightFloatCoverage`` for each entry (typed or dict/tuple)."""
    items = coverage.items() if isinstance(coverage, dict) else (coverage or [])
    for entry in items:
        if isinstance(entry, tuple) and len(entry) == 2 and not isinstance(entry[1], (int, date)):
            label, spec = entry  # (label, NightFloatCoverage|tuple|dict)
            if isinstance(spec, NightFloatCoverage):
                yield spec._replace(label=str(label))
                continue
            if isinstance(spec, dict):
                yield NightFloatCoverage(
                    str(label),
                    tuple(int(w) for w in spec.get("weekdays", ())),
                    tuple(spec.get("include_dates", ())),
                    tuple(spec.get("exclude_dates", ())),
                )
                continue
            yield NightFloatCoverage(str(label), *spec)
            continue
        yield entry if isinstance(entry, NightFloatCoverage) else NightFloatCoverage(*entry)


class NightFloatAssignment(NamedTuple):
    """A resident covering the night-float overlay for a period.

    During ``[start, end]`` the resident covers the NF-eligible shifts in
    ``labels`` (empty = all NF shifts) on the dates those shifts are NF-covered.
    They are removed from the regular scheduler for the window plus
    ``rest_days`` recovery days afterwards (a leave-like buffer so nobody goes
    straight from nights to a regular shift). Fed to the regular scheduler as an
    *uncompensated* leave, so their regular target drops for the period and the
    ledger never makes them catch it up.
    """

    name: str
    start: date
    end: date
    labels: Tuple[str, ...] = ()
    rest_days: int = 1


def normalized_nf_assignments(assignments, default_rest: int = 1):
    """Yield ``NightFloatAssignment`` for each entry (typed or 3/4/5-tuple)."""
    for entry in assignments or []:
        if isinstance(entry, NightFloatAssignment):
            yield entry
            continue
        name, start, end = entry[0], entry[1], entry[2]
        labels = tuple(entry[3] or ()) if len(entry) > 3 else ()
        rest = int(entry[4]) if len(entry) > 4 else default_rest
        yield NightFloatAssignment(name, start, end, labels, rest)


def nf_covered(day: date, shift, data) -> bool:
    """True if ``shift`` on ``day`` is covered by the night-float overlay."""
    if not shift.night_float:
        return False
    cov = None
    for entry in normalized_nf_coverage(data.nf_coverage):
        if entry.label == shift.label:
            cov = entry
            break
    if cov is None:
        return False  # eligible but no coverage configured → scheduled as regular
    if day in cov.exclude_dates:
        return False
    return day in cov.include_dates or day.weekday() in cov.weekdays


def is_regular_night_call(day: date, shift, data) -> bool:
    """A night on-call worked by *regular* residents on ``day``.

    A "Thu counts as weekend" shift is a night on-call; on dates the night-float
    overlay covers it there is no regular assignment, so it is only a regular
    night call on its *uncovered* dates. Used for the blackout "night before"
    rule (protect regular residents from being post-call).
    """
    return bool(shift.thu_weekend) and not nf_covered(day, shift, data)


@dataclass
class ShiftTemplate:
    label: str
    role: str  # 'Junior' or 'Senior'
    night_float: bool  # night-float-ELIGIBLE (covered by the NF overlay on covered dates)
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
    # Group blackouts (see Blackout): whole groups off call for a window and,
    # by default, off the *night on-calls* of the day before it. Night float
    # is never touched. Compensated by default — not an excusal.
    blackouts: Sequence[Blackout | Tuple] | None = None
    # Shift-type load reductions (see LoadReduction): a group carries at most
    # ``factor`` of its fair share of specific shift types inside a window;
    # the shortfall is carried in the ledger and repaid, never excused.
    reductions: Sequence[LoadReduction | Tuple] | None = None
    # Soft shift preferences: resident -> preferred shift labels, and resident
    # -> preferred day type ("weekend" / "weekday"). Quality-of-life only:
    # they order otherwise EQUALLY-FAIR schedules and never change targets,
    # deviations, or the ledger (see optimiser.objective_weights).
    preferred_shifts: Dict[str, List[str]] | None = None
    preferred_day_type: Dict[str, str] | None = None
    # Avoid pairs: two residents never on call on the same day (any shifts).
    # A hard constraint that never causes infeasibility on its own (slots fall
    # to Unfilled) and leaves fairness targets untouched. The UI gates the
    # editor behind an access code because using this needs sign-off.
    avoid_pairs: Sequence[Tuple[str, str]] | None = None
    # Night-float overlay: which dates each NF-eligible shift is actually
    # covered (nf_coverage), and who covers the overlay when (nf_assignments).
    # Covered slots are removed from regular demand; the coverers are blocked
    # in the regular scheduler (like uncompensated leave) for the period plus
    # a rest buffer. NF is deliberately outside the regular point/fairness
    # system — count_nf_points optionally counts NF work as regular points.
    nf_coverage: Dict[str, NightFloatCoverage] | None = None
    nf_assignments: Sequence[NightFloatAssignment | Tuple] | None = None
    count_nf_points: bool = False
    nf_rest_days: int = 1
    target_label: Dict[tuple[str, str], float] | None = None
    target_total: float | None = None
    target_weekend: Dict[str, float] | None = None
    target_total_map: Dict[str, float] | None = None
    target_night_float: Dict[str, float] | None = None

