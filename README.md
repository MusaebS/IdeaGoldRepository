# Idea Gold Scheduler

This project builds on OR-Tools to generate fair on-call schedules. Install `ortools` from the requirements file to enable the CP-SAT optimiser.

If `ortools` is missing, the stub solver marks all shifts as **Unfilled**. This ensures the app still runs but signals that optimisation isn't available.

The time limit for solving depends on the environment. Set the `ENV` variable to
`dev`, `test`, or `prod` (default) for 10s, 1s or 60s respectively.
