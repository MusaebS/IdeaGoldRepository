# Idea Gold Scheduler

This project builds on OR-Tools to generate fair on-call schedules. Install `ortools` from the requirements file to enable the CP-SAT optimiser.

If `ortools` is missing, the stub solver marks all shifts as **Unfilled** and the app shows a warning so you know optimisation isn't available.

The time limit for solving depends on the environment and problem size. Set the `ENV` variable to
`dev`, `test`, or `prod` (default) for base limits of 10s, 1s or 60s; the code scales these based on the number of participants, days, and shift templates to keep small runs quick.
Enable the **Test mode** checkbox in the app to load example shifts and participant names automatically.
The solver supports fractional fairness targets via `InputData.target_total`, `target_label`, and `target_weekend`. It minimises the largest deviation from these targets before minimising smaller gaps and unfilled shifts. This keeps point totals balanced whenever possible.

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

The results page includes a **Download Fairness Log** button. It saves a text file summarising each resident's role, night float points, total and weekend points, along with any deviations from the targets you entered. A Fairness summary also shows the min/max/range for total and weekend points directly in the UI. The schedule and fairness summary can also be exported as **CSV**, **Excel** (`.xlsx`, schedule + fairness sheets) and **PDF**.

## Configuration validation

Before solving, the configuration is checked with `model.validation.validate_input`.
It rejects the misconfigurations that otherwise produce confusing or wrong output —
a backwards date range, no shifts, duplicate or reserved (`Date`/`Day`) shift labels,
night-float eligibility for people not in the roster, a name in both the junior and
senior lists, and leave/rotator windows that reference unknown residents or run
backwards. The app surfaces these as a list to fix; `build_schedule` raises
`ValueError` for the same problems.

`model.validation.config_warnings` adds *non-blocking* advisories for a valid but
risky configuration — a night-float shift with no eligible residents, or more
shifts of a role per day than there are residents of that role — so you know to
expect unfilled slots before solving rather than after.

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

## Changelog
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
