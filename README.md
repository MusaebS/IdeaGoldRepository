# Idea Gold Scheduler

This project builds on OR-Tools to generate fair on-call schedules. Install `ortools` from the requirements file to enable the CP-SAT optimiser.

If `ortools` is missing, the stub solver marks all shifts as **Unfilled** and the app shows a warning so you know optimisation isn't available.

The time limit for solving depends on the environment and problem size. Set the `ENV` variable to
`dev`, `test`, or `prod` (default) for base limits of 10s, 1s or 60s; the code scales these based on the number of participants, days, and shift templates to keep small runs quick.
Enable the **Test mode** checkbox in the app to load example shifts and participant names automatically.
The solver supports fractional fairness targets via `InputData.target_total`, `target_label`, and `target_weekend`. Fillable coverage dominates the objective; among maximum-coverage solutions it minimises the largest deviation from these targets before the smaller fairness gaps. This keeps point totals balanced without dropping a coverable slot to make the numbers look tidier.

Weekend fairness also has a separate safeguard inside that hierarchy. After
total-point fairness, the optimiser minimises the within-role spread of
`actual weekend points − weekend target` before the summed weekend deviation.
Thus equal totals cannot excuse an avoidable 4-vs-0 weekend split. The
guardrail is soft: it never sacrifices coverage, overrides total fairness, or
breaks a hard scheduling rule.

**Night float is a separate coverage overlay, not a balanced dimension.** It runs *before* the regular scheduler: the dates it covers are assigned to their night-float coverer and removed from regular demand, and each coverer is treated like an *uncompensated* leave for their block (blocked from regular shifts, reduced regular target, no future catch-up). See [Night float](#night-float-a-coverage-overlay) below. Night-float work carries no regular points by default, so it does not enter the total/weekend/per-label balance; the fairness log reports each coverer's night-float **duty days** as an informational figure outside the balance.

If `target_total` or `target_weekend` are not provided, `build_schedule` calculates
them automatically — **per role**: juniors can only work Junior-role shifts and
seniors Senior-role ones, so each role's point pool is divided (availability-
weighted) among that role's residents. A single all-roster share is only
meaningful when both roles happen to have the same per-head demand; when they
don't (say 84 senior slots for 22 seniors = 3.8 each vs 112 junior slots for 40
juniors = 2.8 each), the totals are balanced *within* each role and the
structural cross-role difference is reported by the pre-solve advisories
instead of being smeared over everyone as an unreachable target. The resolved
targets ride on the schedule DataFrame's `attrs`, without mutating the
`InputData` object. For example:

```python
data = InputData(
    start_date=date(2025, 1, 1),
    end_date=date(2025, 1, 7),
    shifts=[ShiftTemplate(label="D", role="Junior", night_float=False, thu_weekend=False, points=1.0)],
    juniors=["A", "B"],
    seniors=[],
    nf_juniors=[],
    nf_seniors=[],
    leaves=[],
    rotators=[],
    min_gap=1,
)
schedule = build_schedule(data)
# schedule.attrs["target_total"] and ["target_weekend"] hold the computed shares
```

The results page includes a **Download Fairness Log** button. It is built to be a verification artifact so mistakes can't slip through:

- a **health line** (slots filled / unfilled) and a **points checksum** (assigned + unfilled = available, flagged if they don't reconcile);
- each resident's role, total / weekend points shown **with their target and deviation** (plus their night-float **duty days** as an informational figure outside the balance), residents **sorted worst-deviation-first**, and anyone more than a point off their total target flagged `[OVER]` / `[UNDER]`;
- a **Constraint violations** section (it runs `validate_schedule`, so a hand-edited roster's problems surface here too);
- an explicit list of **unfilled slots**.

A Fairness summary and per-resident charts show targets, deviations, and
cumulative standings in the UI. Exports deliberately separate presentation
from evidence: the displayed schedule follows cosmetic column choices, while
fairness, validation, policy, and per-call calculations always use the complete
authoritative solved (or validated-edited) schedule. Excel contains Schedule,
Fairness, Per-call, and Policy & validation sheets; the PDF contains the same
policy/validation evidence. Per-call rows distinguish regular, unfilled,
closed, and NF-overlay cells and show awarded separately from nominal points.
CSV/XLSX text is neutralised against spreadsheet-formula execution.

## Guided workspace

The Streamlit app is organised as one stable six-step journey. Every editor is
still available on the page, but long collections of controls are grouped into
smaller in-step tabs so it is easier to find the right mechanism without losing
context:

1. **Setup** — block dates and rest, shift templates, and the resident roster.
2. **Coverage** — leaves and rotators, night-float coverage, and shift closures.
3. **Policies** — team restrictions, fairness controls, preferences, and point rules.
4. **History** — portable config files and the optional carryover fairness ledger.
5. **Review & run** — an at-a-glance summary, blocking validation issues,
   non-blocking advisories, and schedule generation.
6. **Results** — the solved schedule, fairness evidence, audit detail, and exports.

The explanatory captions and help text remain beside their controls; the new
structure changes navigation and presentation, not the meaning of a rule. A
separate **Diagnostics** workspace contains the on-demand Performance lab and
never changes the live roster, rules, ledger, or generated schedule.

### Results workspaces

After a solve, Results is split into five focused views:

- **Overview** — solver status, quality, coverage, and preference outcomes.
- **Schedule** — display customisation, the schedule grid, and manual edit/revalidation.
- **Fairness** — fairness ranges, the per-resident table, chart, and fairness CSV.
- **Audit** — per-call detail and the assignment-rationale explorer.
- **Export** — fairness log, schedule CSV, Excel, PDF, and updated ledger downloads.

All five views come from the same result in session state, so cosmetic changes
do not rerun the optimiser and manual edits continue to flow through validation,
fairness reporting, and exports.

If solver-relevant configuration or carryover history changes after generation,
Results warns that the saved solve is stale and should be regenerated before it
is published or carried into the next ledger.

### Optional canonical name matching

The resident-roster editor can optionally treat names that differ only by case,
Unicode compatibility forms, or repeated whitespace as the same person. This
mode uses NFKC normalisation, collapses whitespace, and compares with Unicode
`casefold`, while preserving the first-entered spelling for display and exports.
It is off by default, so existing exact-name workflows remain unchanged. Ledger
reconciliation remains an explicit review-and-apply step; enabling canonical
matching never silently merges historical records.

### Privacy and persistence

The app has no database or server-side resident-data store. Uploaded configs,
availability files, and ledgers are processed in the active Streamlit session;
generated schedules and downloads are likewise held in session memory rather
than persisted by the application. Save the config and updated fairness ledger
locally before the session or app restarts. This no-retention model keeps hosting
simple and makes the user-owned JSON files the portable source of history; adding
durable automatic persistence would require an explicitly configured external
store and credentials.

## Customising the results (cosmetic)

Everything under **🎨 Customise the schedule** and the column controls is *purely
cosmetic* — it changes how the finished schedule looks and what the downloads
contain, never the assignments, fairness, or validation.

**Colour-coding.** A **Colour cells by** selector shades the grid, and the same
colours flow into the Excel and PDF downloads (`model/coloring.py` returns one
`{(row, shift) -> #rrggbb}` map used on-screen, in openpyxl fills, and in reportlab
backgrounds, so all three agree cell-for-cell). Modes:

- **Weekend + points** (default) — weekends get one shade, weekdays another, with the
  intensity rising for higher-point shifts, so a heavy weekend night stands out.
- **Weekend only** — just flags weekend cells.
- **Point value** — shades every cell by how many points it's worth.
- **Role (junior/senior)** — senior shifts one hue, junior another.
- **None** — no shading.

The five role colours (weekend / points / senior / junior / unfilled) are editable
with colour pickers, with a **Reset colours** button. Or pick one **Theme colour**
and click **Apply theme shades** — the four role colours are derived from it
automatically (hue-rotated so they stay distinct); *unfilled* keeps its warning
red either way. Unfilled slots are always flagged regardless of the mode.

**Custom columns.** Add extra columns to the final schedule for labelling that has
nothing to do with the maths — on-call team, consultant on service, a notes column,
whatever a given month needs. Add/remove them by name and fill a value per day; they
ride along into the on-screen grid and every download but are ignored by the
scheduler, fairness, and validation. An **Auto-fill** helper populates a whole
column from a pasted name list with a pattern — *repeat daily* (cycle the list),
*weekly* (each name covers 7 days — consultant-of-the-week), or *same every day* —
and you can still hand-edit cells afterwards.

**Saved with the config.** The downloaded config JSON includes a `display` section
(colours, custom columns and their values, column order), so loading a config also
restores the look. Older config files without it load unchanged.

**Column order & visibility.** Reorder columns (the selection order in the *Columns
to show* control is the display order) and hide any you don't want — again, display
only.

Downloads are cached per (result + colours + columns), so clicking a download is
instant and no longer momentarily blanks the results while a large Excel/PDF rebuilds.

## Manual edits (hand-tweaking the schedule)

The **Manual edit & revalidate** panel lets you change any assignment after
solving. Ordinary demand cells are dropdowns restricted to role-eligible
residents plus *Unfilled*. Configured closure and night-float overlay cells are
authoritative and protected: manual edits cannot create, reopen, or replace
them. A live preview shows constraint issues and quality; **Apply edits** stays
disabled until every hard rule passes, and the mutation revalidates defensively.
Once applied, the edited schedule *is* the result: the fairness summary, log,
ledger, and every download reflect it. The PDF marks it `MANUALLY EDITED (not
solver-certified)`. **Revert to solver result** restores the pristine schedule.

## Configuration validation

Before solving, the configuration is checked with `model.validation.validate_input`.
It rejects the misconfigurations that otherwise produce confusing or wrong output —
a backwards date range, no shifts, duplicate or reserved (`Date`/`Day`) shift labels,
night-float eligibility for people not in the roster, a name in both the junior and
senior lists, and leave/rotator windows that reference unknown residents or run
backwards. Point policies must be finite and in range; overlapping night-float
ownership is rejected. JSON imports also reject unsafe scalar types instead of
silently coercing them, sanitise display columns/colours, and apply the complete
configuration atomically. The same selected file can be reloaded intentionally.
Legacy `max_nights`/`nf_block_length` values load with an explicit warning
because the current overlay does not use them. The app surfaces issues as a list
to fix; `build_schedule` raises `ValueError` for the same operational problems.

`model.validation.config_warnings` adds *non-blocking* advisories for a valid but
risky configuration — a night-float shift with no eligible residents, more shifts
of a role per day than there are residents of that role, and likely leave/rotator
mistakes (a window outside the schedule dates, a rotator with no active days, a
resident on leave for the whole block, or a leave outside that resident's rotator
window) — so you catch them before solving rather than after.

## Night float (a coverage overlay)

Night float is a dedicated coverage rotation — a stretch of nights one resident
owns — not an on-call point balanced against everyone else. It is modelled as an
**overlay that runs before the regular scheduler**, so it never distorts the
regular point balance:

1. **Eligibility.** Tick **Night-float eligible (overlay)** on a shift in the
   shift editor (`ShiftTemplate.night_float`). This only makes the shift
   *eligible* — a plain regular shift until you say which of its dates the
   overlay actually covers.
2. **Coverage pattern** (`nf_coverage`, ② *Coverage → Night float*). Per eligible shift,
   choose the weekdays (and one-off include/exclude dates) the overlay covers.
   Uncovered dates stay ordinary regular shifts with their usual points,
   weekend logic, eligibility and fairness. No pattern ⇒ the shift is scheduled
   entirely as a regular shift.
3. **Coverers** (`nf_assignments`, ② *Coverage → Night float*). Assign an explicit period
   to each covering resident (reusing the date-range editor) plus a configurable
   **1–2 rest days** after the block. There is no auto-assignment.

For each covered date with a coverer, the overlay:

- assigns the coverer straight into the schedule and **removes that cell from
  regular demand** — it carries no regular points and no fairness weight;
- feeds the coverer's period **+ rest days to the regular scheduler as an
  *uncompensated* leave** — so they are blocked from regular shifts during (and
  just after) their block, their regular target drops for the absence, and the
  ledger's no-catch-up policy never makes them repay the missed regular work.

A covered date with **no** assigned coverer **falls back to regular scheduling**
(a pre-solve advisory flags it). Because coverage is date-aware, the blackout
"night before" rule also protects regular residents on an NF-eligible night's
*uncovered* dates (`is_regular_night_call`). Overlay cells are marked in
`df.attrs["nf_cells"]`, so fairness, validation, exports, and the manual-edit
dropdowns all treat them as coverage, not regular assignments. Each coverer's
**night-float duty days** are tracked separately (fairness log, table, exports,
and the ledger's `nf_days`) as an informational cross-block record — outside the
regular balance. The overlay lives in `model/night_float.py`
(`resolve_night_float` → cells / coverage-gaps / rest leaves).

## Rest spacing

`min_gap` keeps a resident's regular shifts more than `min_gap` days apart.
Night-float coverage is not a regular shift, so it is spaced by the overlay's own
rest days (see [Night float](#night-float-a-coverage-overlay)) rather than by
`min_gap`. If the constraints can't be met, the app distinguishes a genuinely
infeasible configuration (with diagnostic hints) from a solver timeout, and offers
to retry with a relaxed `min_gap`.

## Reproducibility

Set a **Random seed** to seed the solver's search. The same seed reproduces the same
schedule when the solver finishes; under a tight time limit the parallel search may
still vary. `build_schedule` does not mutate the `InputData` you pass it — the
resolved fairness targets and solver status are returned on the DataFrame's `attrs`
(`target_total`, `target_total_map`, `target_weekend`, `solver_status`,
`target_label`, `time_limit_sec`, `wall_time_sec`).

## Leaves: compensated or uncompensated

Each leave carries a per-leave **compensated** flag (4-tuple
`(resident, start, end, compensated)`; legacy 3-tuples are treated as
compensated). A *compensated* leave blocks the days but keeps the resident's full
fair share (they are not expected to make it up). An *uncompensated* leave also
scales that share down for the days missed, like a rotator, so the resident is
not penalised for the absence. Toggle it per leave in the app's Leaves editor.

## Carryover fairness (cumulative across blocks)

Fairness can span multiple blocks. After generating a schedule, download the
**fairness ledger** (each resident's accumulated total / weekend points, plus an
informational night-float duty-day count); upload it before the next block and the optimiser balances *cumulative*
load — whoever carried extra last time gets lighter targets now. `build_schedule`
takes an optional `ledger` argument; `model/ledger.py` handles save/load
(`ledger_to_json` / `ledger_from_json`) and accumulation (`update_ledger`). A large
prior imbalance is corrected over several blocks rather than all at once.

**No auto-compensation (default).** Real-world events shouldn't be "balanced
away": by default the saved ledger records *fairness-countable* points — penalty
extra points are **not** carried (a punishment is never refunded with a lighter
later block), and load excused by uncompensated leave, rotator windows, perks, or
group factors is credited so the resident is **not** made to catch it up later
(e.g. after a perk expires). Adjusted entries carry a transparent `adjustments`
audit note in the ledger JSON, and the download caption lists what was applied.
Two checkboxes under **④ History → Fairness ledger** restore pure cumulative balancing if
you really want deviations repaid.

**Per-shift-type debt is repaid in kind (default).** The ledger also carries each
resident's accumulated points *per shift type*. When "Repay shift-type debt in the
same shift type" is ticked (the default, in **④ History → Fairness ledger**), someone who
worked more than their share of, say, nights last block gets a lighter *night*
target now — not just fewer points overall. This sits at the lowest fairness tier,
so it never overrides the total or weekend balance, and on very large blocks (where
per-type targets are skipped for solver speed) the history is still recorded but not
auto-repaid. Untick it to fall back to repaying imbalance through total/weekend
points only. No change to the ledger file format — old ledgers just start each
shift type at zero history.

**Reconciling a ledger after names or shifts change.** Names and shift labels are
matched exactly, so a fixed misspelling, a renamed shift, a retired shift, or a new
joiner would otherwise silently orphan or restart history. After you upload a
ledger, a **reconcile step** (under the grid in ④ History) lists every ledger name/shift
that doesn't match the current setup, with likely matches suggested first, and lets
you **merge** it into the current entry (keeping its history), **keep** it as
dormant history, or **remove** it — nothing changes until you click Apply, and you
can dismiss the panel to use the ledger as-is.

**Where is it kept?** The app is stateless — nothing is stored server-side, which
matters on Streamlit Community Cloud where the filesystem is wiped on every
restart. The ledger is *your* file: download it after each block (named
`fairness_ledger_through_<end-date>.json`) and re-upload it next block. This works
on any host with zero setup and keeps resident data off third-party servers.
Durable automatic persistence would require an external store (a database or cloud
bucket) with credentials in `st.secrets`. Leaving the ledger empty produces a
standalone, one-off schedule unrelated to history.

## Seniority groups, perks & shift exemptions

Load doesn't have to be equal:

- **Seniority groups** (`group_factors` + `resident_groups`): define groups like
  R1/R2/R3/R4 with a load percentage — every R2 at 90% fairly carries ~10% fewer
  points than an R1, across the total and weekend targets.
- **Perks** (`perks`): an individual load percentage for one resident, bounded to
  a date window or applied forever. Perks stack multiplicatively with the group
  factor, and only affect days inside the window.
- **Shift exemptions** (`exempt_shifts`): a resident never assigned to specific
  shift *types* (hard block). Like night-float eligibility, an exemption keeps
  the resident's targets unchanged — they carry their share on the other shift
  types; combine with a perk if their overall share should also drop.

All three are annotated on the resident's fairness-log line (`[R2 ×0.90]`,
`[perk ×0.80 →2026-08-01]`, `[exempt: NF]`) and the targets already embed the
factors, so deviations stay honest. Configure them under **③ Policies**.

## Groups & blackouts

Define **named groups** (③ *Policies → Teams & restrictions*) — plain member lists, e.g. four
teams of three, fully editable — and add **blackout periods** per group (or for
ad-hoc names): nobody covered is on call during the window, and by default they
are also blocked from the **night calls of the day before**, so no one is
post-call on their first off day. "Night call" uses the roster's existing
marker: the shifts flagged **"Thu counts as weekend"** (a Thursday night makes
Friday post-call, hence weekend load) — day shifts on the day before stay
allowed. **Night float is never touched by blackouts** (not in the window, not
the day before): it is a separate rotation, not an on-call; a personal leave
still blocks it. A blackout is not a leave: it is entered in bulk, reported
separately (`[blackout Team A …]` in the log and Notes), and **compensated by
default** — each member keeps their full fair share, so the missed load is made
up on other days of the block or, with a carryover ledger, carried as debt and
repaid next block (never excused). Untick *Compensated* for
uncompensated-leave semantics instead. Group membership is resolved when the
schedule is generated, so editing a group updates every blackout that
references it — and **rotators are ordinary roster members**, so groups,
blackouts, reductions, and preferences all apply to them while they are
active. Pre-solve advisories flag empty groups, out-of-block windows, and days
where blackouts + leaves + rotators leave fewer available residents than
shifts ("expect unfilled slots"). Note: the manual-edit dropdowns can't hide
people per-day, but assigning someone inside their blackout — or on a night
call the day before it — is flagged by the validator and the log.

When adding a **rotator** you can also pick which shift types they cover
("Covers only these shift types"): anything left out is added to their entry
under **③ Policies → Teams & restrictions** (the normal hard-block mechanism), where it
stays visible and editable.

## Shift-type load reductions (repaid later)

Sometimes a group should carry **less of specific shift types** for a while —
e.g. few or no night calls during a heavy rotation, with others covering.
Add a **reduction** (③ Policies → Teams & restrictions): group (or names) + shift types + a **load %
of fair share** (0% = none of those shifts in the window) + a period. It is
enforced as a hard cap that can never make the schedule infeasible.
Per entry, choose what happens to the rest of their load *this* block:

- **Work less now, repay later** (default): their total target drops by the
  reduced amount, others absorb it, and the whole
  shortfall is carried in the fairness ledger as debt — the next block's
  carryover targets make them repay it.
- **Keep full share**: targets are untouched, so the solver compensates them
  with *other* shift types in the same block; only what cannot fit carries over.

Either way — and unlike perks or group factors — the reduction is **never
excused**: the ledger's no-catch-up policy issues no credit for it, so the
deficit is always repaid through carryover. With per-shift-type carryover on
(the default), a reduced shift type is repaid in *that* shift type; the
total / weekend dimensions still balance overall load on top.

## Fairness table, per-call audit & ledger editor

Beyond the text log, the results page shows a **fairness table**: per resident,
call *counts*, points, targets, and deviations per shift type; total/weekend
targets and deviations; and an informational `NF duty (days)`. With a carryover
ledger it shows both actual prior + cumulative values and the policy-adjusted
standing that will be saved for the next block (including cumulative per-shift-
type call counts), plus a `Pref match`
column, and a Notes column with the same annotations as the log — downloadable
as CSV, and mirrored in the Excel/PDF Fairness sheet. A **Per-call detail
(audit)** expander lists every (date, shift) slot with its holder, explicit
status, awarded and nominal points, and weekend/NF-overlay flags — download the
CSV each month for future reference.

The carryover ledger is now **editable in-app** (④ History → Fairness ledger): upload
it, adjust any resident's cumulative numbers after a real-world change, add or
remove residents, download the edited JSON without generating — and whatever
the grid shows is what the next Generate balances against. Uploading also runs
the **reconcile step** (see [Carryover fairness](#carryover-fairness-cumulative-across-blocks))
so a renamed resident or shift keeps its history instead of restarting at zero.
The saved ledger accumulates per-shift-type points and call counts across
blocks; these feed both the cumulative "which calls" view and, by default, the
next block's per-shift-type targets. Old ledger files load unchanged.

## Importing availability requests

Collect monthly "I need to be free on these dates" requests with any form tool,
export them as a sheet, and import them in one go (② Coverage → Leaves & rotators): download
the **template** (CSV or Excel; columns `Name`, `Start`, `End`), upload the
responses (`.xlsx` or `.csv`), review the **preview** — every row gets an OK or
a per-row error (unknown name, unreadable date, backwards range), so one bad
answer never blocks the rest — then **Apply**: each valid row becomes a
compensated leave, exactly as if entered by hand (deduplicated against
existing entries). Header synonyms (`Resident`, `From`/`To`…), several rows
per person, an empty End for a single day, and typed date cells / ISO /
`DD/MM/YYYY` strings are all accepted.

## Shift preferences (soft)

Residents can prefer **specific shift types** (e.g. nights vs mornings) and a
**day type** (weekends vs weekdays) — ③ Policies → Preferences & points. Preferences are strictly
quality-of-life: they only choose *among equally fair schedules* and never
change anyone's fair share, deviations, the log, or the ledger. Mechanically,
when preferences exist every fairness weight in the objective is multiplied by
`K = 2·days·shifts + 1` while each matched assignment earns a reward of 1–2;
since the total preference reward can never reach `K`, no preference can buy an
unfilled slot or a single scaled point of any deviation. Two residents wanting
opposite things simply swap slots — no harm, no unfairness. The results page
shows per-person match ratios and the fairness table a `Pref match` column;
advisories flag preferences for shifts a person can never work.

## Avoid pairs (restricted)

Two residents with a personal conflict can be kept apart: an **avoid pair** is
never on call on the same day (any shift types). It is a hard constraint that
never makes the schedule infeasible (uncoverable slots fall to *Unfilled*, and
an advisory warns when the pair are the only two of a role) and it does not
change anyone's fairness target. Because separating people usually needs
approval from higher authority, the editor (③ Policies → Preferences & points → *Avoid pairs
(restricted)*) hides behind an **access code** — a deliberate extra step, not
a security measure. Pairs loaded from a config remain visible but inactive; an
authorised user must unlock the panel, review the names, and click **Confirm
imported avoid pairs** before they affect a solve. Both residents' log lines
carry an `[avoids: …]` note.

## Per-resident caps

`InputData.max_total` (map of resident → points) puts a
hard ceiling on how much total load a resident can carry, configurable
in the app's "Per-resident caps" panel. A capped resident simply works less and the
slack falls to `Unfilled`, so a cap never makes the schedule infeasible; the cap
overrides fairness for that resident. (`max_nights` is retained for config
compatibility but has no effect now that night float is a coverage overlay
outside the regular point system.)

## Extra points (mandatory penalties)

`InputData.extra_points` (resident → points) imposes mandatory extra workload on a
resident, e.g. as a penalty. The resident's total fairness target is raised by the
amount (everyone else's lowered proportionally so the totals still reconcile) and a
hard floor forces the solver to actually assign them at least that much — so a
resident given `+2` ends up two points above their peers' fair share. It is
enforced, not merely preferred: if it can't fit (availability / min-gap, or a
conflicting `max_total` cap), the schedule is reported infeasible with diagnostics
rather than silently dropping the penalty. Configure it in the app's "Per-resident
caps & extra points" panel. The fairness log tags that resident's line with
`[+N penalty applied]` so it's clear why they carry more.

## Point overrides & holidays (advanced)

A shift can be worth different points depending on the day:

- **Weekday overrides** (`weekday_points`, a map of `(shift label, weekday 0=Mon..6=Sun) → points`) set a shift's exact value on a given weekday — e.g. a night worth 2 on Tuesdays.
- **Holidays** (`holidays`, a list of `(date, bonus, count_as_weekend)`) add bonus points to every shift on a specific date, for the occasional mid-week holiday. Each holiday can optionally count toward weekend balance; by default it's just worth more points.

Both feed a single effective-points value (`utils.effective_points`) used everywhere points are counted, so a heavier day automatically counts more toward fairness — whoever works it carries more load and does fewer other shifts. Configure them in the app's "Point overrides & holidays (advanced)" panel.

## Weekend definition

Weekend fairness defaults to Saturday/Sunday. Set **Weekend days** in the app (or
`InputData.weekend_days`, a list of weekday numbers Mon=0..Sun=6) to use a different
weekend, e.g. `[4, 5]` for Friday/Saturday. The per-shift "Thu counts as weekend"
flag still adds Thursday for individual shifts on top of this.

**Weekend shift points (×)** — `InputData.weekend_multiplier` — makes every
weekend slot worth a multiple of its points (applied after weekday overrides
and holiday bonuses, in the single `effective_points` source, so the solver,
targets, fairness table/log, exports, and ledger all agree). New sessions
default to **×2**, making weekend work materially count in the strongest
total-load tier. A separate target-relative weekend residual-spread guardrail
then discourages concentrating weekends on one resident when an equally good
total-point schedule can redistribute them. A saved config without the field
keeps the old behaviour (×1). Neither mechanism can overcome an eligibility,
availability, cap, or `min_gap` structural lock — see the advisories below.

## Pre-solve capacity advisories

Before generating, **⑤ Review & run** lists structural facts that no solver
setting can overcome, so an uneven result is never a mystery:

- **min_gap ceiling** — with a gap of *g*, a resident fits at most
  ⌈days/(g+1)⌉ shifts in the block. If a role's roster × ceiling falls short
  of its slots, the shortfall is *guaranteed* unfilled; within ~10% it's
  flagged as very tight (expect unfilled slots or uneven spread).
- **Weekly-rhythm lock** — when (min_gap+1) is divisible by 7 on a 3+-week
  block, every resident repeats the same weekday all block: whoever starts on
  a Saturday works *every* Saturday, and weekend fairness is mathematically
  impossible. Use a smaller gap and let the point balance spread the load.
- **Cross-role workload gap** — when the two roles' per-head point averages
  differ by more than a point, the difference is a roster/shift-mix fact;
  totals are balanced within each role.

## How fairness is verified

Four guarantees underpin the "is the result actually fair?" question:

- **Coverage before fairness.** The solver never leaves a *fillable* on-call
  slot empty to make point totals look tidier — an uncovered on-call is never
  an acceptable price for equality. Unfilled slots appear only when coverage
  is genuinely impossible (caps, blackouts, eligibility, too few residents).
- **Balance within each role.** Targets split each role's own point pool
  among that role's residents (the `mixed_role_pools` audit scenario holds
  both roles to a ≤1-point spread). A cross-role difference reflects the
  roster/shift mix — it is reported by the pre-solve advisories, not treated
  as solver unfairness the model could never fix.
- **No hidden weekend concentration.** After total fairness is settled, the
  weekend residual-spread guardrail prefers the schedule whose actual-minus-
  target weekend load is closest together within each role. Equal totals do
  not make an avoidable 4-vs-0 weekend split acceptable.
- **Per-shift-type balance.** Equal *total* points is not enough if one
  resident works all the heavy nights and another only day shifts. On top of
  total / weekend balance, each shift type's points are split
  fairly among the residents eligible for it (the spec's per-label share), so
  the *mix* is even too — auto-enabled for departments up to
  `LABEL_TARGET_MAX_CELLS` (residents × days × shifts); very large rosters
  skip it so the extra variables can't starve the primary balance under a
  solve-time limit. Load reductions and shift preferences deliberately opt a
  resident out of this pin (the whole point of those features is a chosen mix).
  Across blocks, a carryover ledger extends this to the *cumulative* mix: the
  `multi_block_label_ledger` audit scenario checks that a shift type someone
  was kept off in one block is repaid to them in later blocks, not just as
  generic points.

`python scripts/fairness_audit.py` solves ~20 scenarios end-to-end (small,
large, extreme, and every feature) and checks the *outcome*: total/weekend
spread, night-float overlay ownership/rest, per-shift-type distribution, coverage, hard-rule
violations, multi-block ledger convergence, and preference neutrality. It exits
non-zero if any scenario fails its stated fairness expectation.

## Benchmarking

The **Diagnostics → Performance lab** runs bounded synthetic cases on demand,
without reading or changing the active schedule setup. Start with a preset or
choose a custom case; the largest workloads require an explicit confirmation
because they can occupy the app worker for about a minute. The result reports
elapsed time, solver status, and whether the case met its target.

For repeatable command-line measurements, `python scripts/benchmark.py` times the
same benchmark model across several sizes against the spec's ≤60s target for
40 residents × 28 days × 10 shifts; pass `people days shifts` for one custom run.

## App smoke test

`python scripts/smoke_app.py` launches the app headless and drives it in a real
browser (Playwright/Chromium) to check the end-to-end UI: load, Test mode +
Generate rendering a schedule with the quality metric and CSV/Excel/PDF exports,
the validation-error path, and the infeasible relax-and-retry recovery. It needs
`pip install playwright` and a Chromium binary; it is an on-demand check, not part
of the `pytest` run.

## Development

```bash
pip install -e .[dev]   # app + pytest, pytest-cov, ruff, mypy, playwright
pytest                  # unit + headless AppTest suite
ruff check .            # lint
mypy                    # type check (model/)
python scripts/smoke_app.py   # real-browser end-to-end smoke test
```

The code is laid out as `model/` (solver, fairness, validation, exports — no
Streamlit imports), `ui/` (session state, editors, tabs, results rendering),
and `app.py` (the thin `streamlit run` entry point). `model/points.py` is the
single source of per-slot point values shared by the solver and all reporting.
CI runs ruff, mypy, and pytest on Python 3.11/3.12 — plus a stub-only job with
no pandas/OR-Tools installed to guard the graceful-degradation path.

## Changelog
- **Weekend concentration and integrity hardening.** Added a target-relative,
  within-role weekend residual-spread guardrail below total fairness and above
  summed weekend deviation; quality now scores target residuals rather than
  misleading raw equality. Manual edits can no longer redefine configured
  demand, validation covers the solver's hard rules, exports calculate from the
  authoritative result and include policy/validation evidence, and config
  imports are strict and atomic. Reduction targets now normalise duplicate and
  overlapping windows per cell and per eligible role pool. The automatic
  optimiser timing function and UI override are deliberately unchanged; no
  shorter or additional cutoff was introduced.
- **Reporting overhaul: per-role fairness views, cumulative ledger charts, and
  a redesigned report.** The Fairness workspace now shows Juniors and Seniors
  as separate tabs with sorted, role-coloured workload charts (dashed
  fair-share target line) and — when a ledger is loaded — stacked
  prior + this-block cumulative charts; the ledger panel gained a standings
  chart. Long rule annotations moved out of the tables into an expander
  (screen) and numbered footnotes (PDF), with compact date ranges. The PDF
  became a real report: title block with solver status and quality, colour
  legend, friendly dates with weekend-shaded rows, explicit Unfilled/Closed
  markers, per-role fairness tables with footnote markers, and page footers.
  Excel gained frozen panes, styled headers, real date formats, sensible
  column widths, and a Per-call audit sheet.
- **Shift-type fairness at department scale, honest quality score, and a
  "why isn't quality higher?" diagnosis.** The per-shift-type balancing gate
  rose 6k → 20k cells (re-measured under role-aware targets: the label tier
  now costs the primary balance nothing and halves per-type call spread), so
  equal Ward/ER/night distribution applies to real rosters — and per-type
  ledger carryover with it; the ledger panel now shows the cumulative
  calls-per-type history it tracks. The automatic solver budget scales with
  problem size (up to 300 s) instead of a flat 60 s that stranded big rosters
  on early uneven incumbents. `schedule_quality` grants an integrality
  allowance (an unavoidable one-shift difference no longer costs points — a
  proven-optimal schedule can score 100), the Overview shows the 50/30/20
  score breakdown, and a diagnosis expander explains low scores in plain
  language with the fix (raise the time limit, relax min_gap, capacity
  advisories). The min_gap slider became a number input (its filled track
  could render out of sync with the value).
- **Role-aware fairness, weekend ×2, capacity advisories, solver budget, and
  upload fixes.** Auto targets now split each role's point pool within that
  role (juniors and seniors work disjoint shift pools; a single global share
  was unreachable whenever their per-head demand differed, showing up as
  systematic cross-role inequality) — ledger carryover, extra points, and
  reductions reconcile within the role too, and `schedule_quality` scores
  within-role balance. New `weekend_multiplier` (UI default ×2; old configs
  ×1) makes weekend shifts count double so weekend fairness rides the
  strongest tier. New pre-solve capacity advisories explain structural limits
  (min_gap shift ceiling, the weekly-rhythm weekend lock, cross-role workload
  gaps). The solver time limit is now a UI control, and a FEASIBLE solve that
  exhausted its budget shows a prominent "raise the limit" warning instead of
  a quiet caption. Fixed: config upload crashed (keyed-widget writes after
  render; now queued via `PENDING_CONFIG` and applied before widgets), the
  infeasible-retry path silently solving with a different min_gap than the
  slider showed, and Generate giving no success feedback.
- **Guided workspace and visual polish.** Reorganised the complete UI into a
  six-step Setup → Coverage → Policies → History → Review & run → Results flow,
  with smaller nested editor tabs and the existing mechanism explanations kept
  beside their controls. Results now has focused Overview, Schedule, Fairness,
  Audit, and Export workspaces; a separate on-demand Diagnostics / Performance
  lab runs bounded synthetic benchmarks without touching live state. Added
  optional display-preserving canonical name matching, shared upload guards,
  reusable status/card treatments, and a cohesive Streamlit theme. Raised the
  minimum supported Streamlit version to 1.58.
- **Per-shift-type carryover, ledger reconciliation, and config repopulation.**
  The carryover ledger's per-shift-type history now feeds the next block's
  per-label targets (default on; toggle in ④ History → Fairness ledger), so shift-type
  debt is repaid in kind rather than as generic points — at the lowest fairness
  tier, so total/weekend balance is untouched, and gated on large rosters (no
  ledger-format change). Uploading a ledger now offers a **reconcile step** for
  names/shifts that don't match the current setup (merge keeping history, keep
  as dormant history, or remove; likely matches suggested via `difflib`), so a
  fixed misspelling or a renamed shift no longer silently restarts history.
  Uploading a config now **repopulates every editor tab** for review instead of
  being consumed invisibly at Generate. Plus success toasts on uploads, applies,
  and the main editor adds. New helpers: `model/ledger.py` `reconcile_report` /
  `rename_person` / `rename_label` / `drop_person` / `drop_label`; `optimiser`
  `label_carryover` flag; `ui/config_tabs.py` `populate_editors_from_config`;
  `ui/state.py` `flash` / `show_flash`.
- Night float reworked into a **separate coverage overlay** instead of a shift
  type inside the regular scheduler. Shifts are marked *night-float-eligible*
  with a per-shift coverage pattern (`nf_coverage`) and explicit coverer periods
  (`nf_assignments`, +configurable rest days); covered cells are removed from
  regular demand and carry no regular points, while
  each coverer's period is fed to the regular solve as an uncompensated leave
  (no future catch-up). Covered dates without a coverer fall back to regular
  scheduling. **Breaking:** night float is no longer a balanced fairness
  dimension — `target_night_float`/`dev_night_float`, the NF-block/rest solver
  constraints, and `max_nights` enforcement are gone (old configs/ledgers load
  unchanged; NF fields become no-ops), and the ledger's `night_float` points
  dimension is replaced by an informational `nf_days` duty-day count. New
  `model/night_float.py` overlay resolver; `is_regular_night_call` makes the
  blackout "night before" rule date-aware. A coverer only ever covers their own
  role's NF shifts (a junior is never written onto a senior night-float shift);
  the overlay cells ride on `df.attrs` as a serializable `{date-iso: {label:
  name}}` map (no more tuple-key serialization warnings); night-float
  eligibility is one roster picker (role taken from the roster); and the option
  to count NF in the regular point system was removed. Obsolete UI for the
  retired NF-block-length / max-nights concepts is gone.
- Fairness audit + two outcome fixes: the solver no longer leaves a fillable
  slot unfilled to shrink point deviation (coverage now dominates the fairness
  objective), and per-shift-type balance is enforced via auto per-label targets
  (gated by roster size) so equal totals no longer hide an unequal night/day
  mix; added `scripts/fairness_audit.py` to check the outcome across scenarios.
- Blackouts refined: the day-before rule now blocks only the night on-calls
  (the shifts flagged "Thu counts as weekend"), and night float is never
  touched by blackouts (semantics change for the former "block day before"
  flag in saved configs); rotators can declare covered shift types at entry
  (the rest lands in Exemptions); added access-code-gated avoid pairs (two
  residents never on call the same day, fairness untouched).
- Added named groups with group blackout periods (off call during the window
  and, by default, the day before; compensated by default so the shortfall is
  repaid via the ledger, not excused), shift-type load reductions (a group
  carries a set % of its fair share of chosen shift types for a window, with
  per-entry "work less now, repay later" / "keep full share" modes), a
  per-resident fairness table (counts + points per shift type, targets,
  prior/cumulative ledger columns, notes) with CSV download, a per-call audit
  table, an in-app editable carryover ledger (with per-shift-type history in
  the ledger JSON), Excel/CSV import of monthly availability requests
  (template + per-row validated preview → compensated leaves), and soft shift
  preferences (preferred shift types and weekend/weekday) that only break
  ties between equally fair schedules.
- Added seniority groups (R1/R2… load percentages), windowed per-resident perks,
  and shift-type exemptions — all flowing through fairness targets and annotated
  in the log; the ledger no longer auto-compensates penalties or excused
  shortfalls by default (toggleable); custom columns gained pattern auto-fill
  (daily / weekly / constant); one-click theme shades derive the palette from a
  single colour; the config JSON now saves the display setup (colours, custom
  columns, column order) and restores it on load.
- Manual edits now persist: an Apply/Revert flow with eligibility-restricted
  dropdown cells; the edited schedule flows into fairness, the log, the ledger
  and all exports after hard-rule validation passes. (Previously edits were
  silently discarded.)
- Split the monolithic `app.py` into a `ui/` package; names de-duplicate on
  entry, dual-role names and duplicate shift labels warn before Generate, and
  leave/rotator/holiday date pickers default to the schedule block.
- Single points source (`model/points.py`) shared by solver and reporting;
  faster model construction (precomputed availability); typed `Leave` /
  `RotatorWindow`; installable package with bounded pins; mypy in CI.
- Added editable colour pickers for the five schedule colours, cosmetic custom columns (e.g. on-call team / consultant, labelled per day and carried into the downloads without touching the maths), and column reorder/hide. Exports are now cached per result+display so a download no longer blanks the results while a large Excel/PDF rebuilds.
- Added customisable results colour-coding (`model/coloring.py`): weekend/points/role/none modes shading the on-screen grid, with matching colours in the Excel and PDF exports; results now render from `session_state` so changing the colour mode doesn't re-solve.
- Reorganised the app UI into tabs (Shifts & people / Dates & rules / Advanced / Save & carryover), with a primary Generate button, summary metrics, and unfilled-slot highlighting.
- Added per-weekday shift point overrides and holiday bonus dates (optionally weekend-counting), via one `effective_points` value threaded through the solver and fairness.
- Added mandatory per-resident extra points (`extra_points`, e.g. a penalty): the target is raised and a hard floor enforces it.
- Fairness log shows targets inline + a points checksum, sorts worst-deviation-first, and folds in constraint violations; Excel/PDF carry matching total/weekend/per-label deviations plus informational NF duty days.
- Hardened the fairness log into a verification artifact: a coverage-health header, `[OVER]`/`[UNDER]` outlier flags, and an explicit unfilled-slots list.
- Added a per-leave compensated/uncompensated toggle (uncompensated leave scales the resident's quota down like a rotator).
- Added leave/rotator sanity advisories (out-of-range windows, fully-excluded rotators, whole-block leave, redundant leave).
- Added cumulative carryover fairness via a save/load fairness ledger (`model/ledger.py`, `build_schedule(..., ledger=...)`).
- Added per-resident hard caps on total load (`max_total`); legacy `max_nights` remains file-compatible but is not used by the later NF overlay model.
- Historical (superseded): night-float load was once balanced as a fairness objective. The current coverage overlay tracks NF duty days informationally and keeps them outside regular point fairness.
- Added non-blocking pre-solve configuration warnings (`config_warnings`).
- Added a configurable weekend definition (`weekend_days`) and a `scripts/benchmark.py` solve-time benchmark.
- Added pre-solve configuration validation (`validate_input`) surfaced in the app and enforced by `build_schedule`.
- Enforced rest before/after night-float blocks and split solver-timeout from true infeasibility, with a one-click relax-and-retry recovery.
- Added a solver random seed, CSV export, and solver status / wall-time reporting; `build_schedule` no longer mutates its `InputData` (targets are returned on `df.attrs`).
- Added continuous integration (pytest with and without OR-Tools/pandas) and a `ruff` lint configuration.
- Removed unused `extra_oncalls` field from `InputData`.
- Removed the stale `docs/archive.txt` source snapshot (git history is the source of truth).
- Hardened the scheduler against stubbed CP-SAT implementations to keep tests and fallbacks working.
- Added UI fairness summaries and stub-solver warning banner.
- Scaled solver time limits by rough problem size to balance dev responsiveness with larger runs.
