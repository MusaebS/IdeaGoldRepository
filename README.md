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

The results page includes a **Download Fairness Log** button. It saves a text file summarising each resident's role, night float points, total and weekend points, along with any deviations from the targets you entered. A Fairness summary also shows the min/max/range for total and weekend points directly in the UI.

## Changelog
- Removed unused `extra_oncalls` field from `InputData`.
- Added `docs/archive.txt` containing a read-only snapshot of all code and documentation.
- Hardened the scheduler against stubbed CP-SAT implementations to keep tests and fallbacks working.
- Added UI fairness summaries and stub-solver warning banner.
- Scaled solver time limits by rough problem size to balance dev responsiveness with larger runs.
