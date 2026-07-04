# Idea Gold Scheduler

This project builds on OR-Tools to generate fair on-call schedules. Install `ortools` from the requirements file to enable the CP-SAT optimiser.

If `ortools` is missing, the stub solver marks all shifts as **Unfilled** and the app shows a warning so you know optimisation isn't available.

The time limit for solving depends on the environment and problem size. Set the `ENV` variable to
`dev`, `test`, or `prod` (default) for base limits of 10s, 1s or 60s; the code scales these based on the number of participants, days, and shift templates to keep small runs quick.
Enable the **Test mode** checkbox in the app to load example shifts and participant names automatically.
The solver supports fractional fairness targets via `InputData.target_total`, `target_label`, `target_weekend`, and `target_night_float`. It minimises the largest deviation from these targets before minimising smaller gaps and unfilled shifts. This keeps point totals balanced whenever possible.

Night-float load is balanced as a first-class fairness dimension: the burdensome night shifts are spread evenly across the eligible pool (per role, availability-weighted for rotators) rather than only being balanced indirectly through total points. The fairness log and summary report each resident's night-float points, deviation, and min/max range.

If `target_total` or `target_weekend` are not provided, `build_schedule` calculates
them automatically. It divides the total points and weekend points for the block
evenly among all listed residents and assigns these values back on the `InputData`
object before solving. For example:

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
# data.target_total and data.target_weekend now hold the computed shares
```

The results page includes a **Download Fairness Log** button. It is built to be a verification artifact so mistakes can't slip through:

- a **health line** (slots filled / unfilled) and a **points checksum** (assigned + unfilled = available, flagged if they don't reconcile);
- each resident's role, night-float / total / weekend points shown **with their target and deviation**, residents **sorted worst-deviation-first**, and anyone more than a point off their total target flagged `[OVER]` / `[UNDER]`;
- a **Constraint violations** section (it runs `validate_schedule`, so a hand-edited roster's problems surface here too);
- an explicit list of **unfilled slots**.

A Fairness summary and a per-resident bar chart show the min/max/range in the UI, and the Excel/PDF "Fairness" sheet carries the same `Total dev` / `NF dev` columns from the same solver-resolved targets — so the on-screen view, the log, and the exports all agree. The schedule and fairness summary can also be exported as **CSV**, **Excel** (`.xlsx`, schedule + fairness sheets) and **PDF**.

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
solving. Cells are dropdowns restricted to role/night-float-eligible residents
(plus *Unfilled*), a live preview shows the constraint issues and quality score
your changes would cause, and nothing is saved until you click **Apply edits**.
Once applied, the edited schedule *is* the result: the fairness summary, the
log, the ledger, and every download reflect it, and a banner reminds you the
solver output was overridden. **Revert to solver result** restores the pristine
schedule at any time. Constraint violations introduced by hand (a min-gap
break, a role mismatch) are flagged on screen and recorded in the fairness log
rather than silently accepted.

## Configuration validation

Before solving, the configuration is checked with `model.validation.validate_input`.
It rejects the misconfigurations that otherwise produce confusing or wrong output —
a backwards date range, no shifts, duplicate or reserved (`Date`/`Day`) shift labels,
night-float eligibility for people not in the roster, a name in both the junior and
senior lists, and leave/rotator windows that reference unknown residents or run
backwards. The app surfaces these as a list to fix; `build_schedule` raises
`ValueError` for the same problems.

`model.validation.config_warnings` adds *non-blocking* advisories for a valid but
risky configuration — a night-float shift with no eligible residents, more shifts
of a role per day than there are residents of that role, and likely leave/rotator
mistakes (a window outside the schedule dates, a rotator with no active days, a
resident on leave for the whole block, or a leave outside that resident's rotator
window) — so you catch them before solving rather than after.

## Rest spacing and night-float blocks

`min_gap` keeps a resident's regular (non-night-float) shifts more than `min_gap`
days apart. Night-float nights stay consecutive *within* a block, and a block also
gets `min_gap` idle days before it starts and after it ends versus any other shift,
so a resident is never scheduled straight off a night-float block. If the constraints
can't be met, the app distinguishes a genuinely infeasible configuration (with
diagnostic hints) from a solver timeout, and offers to retry with a relaxed `min_gap`
or shorter night-float block.

## Reproducibility

Set a **Random seed** to seed the solver's search. The same seed reproduces the same
schedule when the solver finishes; under a tight time limit the parallel search may
still vary. `build_schedule` does not mutate the `InputData` you pass it — the
resolved fairness targets and solver status are returned on the DataFrame's `attrs`
(`target_total`, `target_total_map`, `target_weekend`, `solver_status`,
`wall_time_sec`).

## Leaves: compensated or uncompensated

Each leave carries a per-leave **compensated** flag (4-tuple
`(resident, start, end, compensated)`; legacy 3-tuples are treated as
compensated). A *compensated* leave blocks the days but keeps the resident's full
fair share (they are not expected to make it up). An *uncompensated* leave also
scales that share down for the days missed, like a rotator, so the resident is
not penalised for the absence. Toggle it per leave in the app's Leaves editor.

## Carryover fairness (cumulative across blocks)

Fairness can span multiple blocks. After generating a schedule, download the
**fairness ledger** (each resident's accumulated total / weekend / night-float
points); upload it before the next block and the optimiser balances *cumulative*
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
Two checkboxes under **④ Save / carryover** restore pure cumulative balancing if
you really want deviations repaid.

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
  points than an R1, across total, weekend, and night-float targets.
- **Perks** (`perks`): an individual load percentage for one resident, bounded to
  a date window or applied forever. Perks stack multiplicatively with the group
  factor, and only affect days inside the window.
- **Shift exemptions** (`exempt_shifts`): a resident never assigned to specific
  shift *types* (hard block). Like night-float eligibility, an exemption keeps
  the resident's targets unchanged — they carry their share on the other shift
  types; combine with a perk if their overall share should also drop.

All three are annotated on the resident's fairness-log line (`[R2 ×0.90]`,
`[perk ×0.80 →2026-08-01]`, `[exempt: NF]`) and the targets already embed the
factors, so deviations stay honest. Configure them under **③ Advanced**.

## Groups & blackouts

Define **named groups** (② *Groups & blackouts*) — plain member lists, e.g. four
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
under **③ Advanced → Exemptions** (the normal hard-block mechanism), where it
stays visible and editable.

## Shift-type load reductions (repaid later)

Sometimes a group should carry **less of specific shift types** for a while —
e.g. few or no night calls during a heavy rotation, with others covering.
Add a **reduction** (③ Advanced): group (or names) + shift types + a **load %
of fair share** (0% = none of those shifts in the window) + a period. It is
enforced as a hard cap that can never make the schedule infeasible.
Per entry, choose what happens to the rest of their load *this* block:

- **Work less now, repay later** (default): their total (and night-float)
  targets drop by the reduced amount, others absorb it, and the whole
  shortfall is carried in the fairness ledger as debt — the next block's
  carryover targets make them repay it.
- **Keep full share**: targets are untouched, so the solver compensates them
  with *other* shift types in the same block; only what cannot fit carries over.

Either way — and unlike perks or group factors — the reduction is **never
excused**: the ledger's no-catch-up policy issues no credit for it, so the
deficit is always repaid through carryover. Caveat: repayment is tracked on
the total / weekend / night-float dimensions, so a reduced label that is
neither night-float nor weekend repays via total points (per-label carryover
targets are future work).

## Fairness table, per-call audit & ledger editor

Beyond the text log, the results page shows a **fairness table**: per resident,
call *counts* and points per shift type, total/weekend/night-float with targets
and deviations, **prior + cumulative** columns when a carryover ledger is
loaded (including cumulative per-shift-type call counts), a `Pref match`
column, and a Notes column with the same annotations as the log — downloadable
as CSV, and mirrored in the Excel/PDF Fairness sheet. A **Per-call detail
(audit)** expander lists every (date, shift) slot with its holder, points, and
weekend/night-float flags — download the CSV each month for future reference.

The carryover ledger is now **editable in-app** (④ Save / carryover): upload
it, adjust any resident's cumulative numbers after a real-world change, add or
remove residents, download the edited JSON without generating — and whatever
the grid shows is what the next Generate balances against. The saved ledger
also accumulates per-shift-type points and call counts across blocks
(informational; carryover balancing stays on the three dimensions; old ledger
files load unchanged).

## Importing availability requests

Collect monthly "I need to be free on these dates" requests with any form tool,
export them as a sheet, and import them in one go (② under Leaves): download
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
**day type** (weekends vs weekdays) — ③ Advanced. Preferences are strictly
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
approval from higher authority, the editor (③ Advanced → *Avoid pairs
(restricted)*) hides behind an **access code** — a deliberate extra step, not
a security measure; pairs loaded from a config file stay active and are shown
as a count even while locked. Both residents' log lines carry an
`[avoids: …]` note.

## Per-resident caps

`InputData.max_total` and `InputData.max_nights` (maps of resident → points) put a
hard ceiling on how much total / night-float load a resident can carry, configurable
in the app's "Per-resident caps" panel. A capped resident simply works less and the
slack falls to `Unfilled`, so a cap never makes the schedule infeasible; the cap
overrides fairness for that resident.

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

## Benchmarking

`python scripts/benchmark.py` times `build_schedule` across a few sizes against the
spec's ≤60s target for 40 residents × 28 days × 10 shifts; pass `people days shifts`
for a single custom run.

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
  and all exports, with a banner and violation flags. (Previously edits were
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
- Fairness log shows targets inline + a points checksum, sorts worst-deviation-first, and folds in constraint violations; Excel/PDF gain matching `Total dev`/`NF dev` columns.
- Hardened the fairness log into a verification artifact: a coverage-health header, `[OVER]`/`[UNDER]` outlier flags, and an explicit unfilled-slots list.
- Added a per-leave compensated/uncompensated toggle (uncompensated leave scales the resident's quota down like a rotator).
- Added leave/rotator sanity advisories (out-of-range windows, fully-excluded rotators, whole-block leave, redundant leave).
- Added cumulative carryover fairness via a save/load fairness ledger (`model/ledger.py`, `build_schedule(..., ledger=...)`).
- Added per-resident hard caps on total and night-float load (`max_total`, `max_nights`).
- Balanced night-float load as a first-class fairness objective (`target_night_float`), with deviation/range reporting; fairness deviations now read solver-resolved targets from `df.attrs`.
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
