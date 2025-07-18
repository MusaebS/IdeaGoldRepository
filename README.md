# Idea Gold Scheduler

This project builds on OR-Tools to generate fair on-call schedules. Install `ortools` from the requirements file to enable the CP-SAT optimiser.

If `ortools` is missing, the stub solver marks all shifts as **Unfilled**. This ensures the app still runs but signals that optimisation isn't available.

The time limit for solving depends on the environment. Set the `ENV` variable to
`dev`, `test`, or `prod` (default) for 10s, 1s or 60s respectively.
Enable the **Test mode** checkbox in the app to load example shifts and participant names automatically.
The solver supports fractional fairness targets via `InputData.target_total`, `target_label`, and `target_weekend`. It minimises the largest deviation from these targets before minimising smaller gaps and unfilled shifts. This keeps point totals balanced whenever possible.

Fair schedules require these targets. When any of them is left as `None`, the solver now derives default values from the configured shifts and participants. For example:

```python
data = InputData(
    ...,  # shift templates and participant lists
    target_total=None,
    target_weekend=None,
    target_label=None,
)
```

With these defaults each resident receives their proportional share of overall points, weekend load and per-label assignments unless explicit numbers are supplied.

The results page includes a **Download Fairness Log** button. It saves a text file summarising each resident's role, night float points, total and weekend points, along with any deviations from the targets you entered.

### Progress 2025-07
- Automatic fairness target defaults added.

