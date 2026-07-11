"""On-demand performance diagnostics for the scheduler UI."""

from __future__ import annotations

import streamlit as st

from model.benchmarking import (
    SAFE_BENCHMARK_PRESETS,
    BenchmarkCase,
    benchmark_available,
    run_benchmark,
)
from ui.state import Keys
from ui.theme import card_container, render_section_header, render_status


def _preset_label(case: BenchmarkCase) -> str:
    scale = "Quick" if case.people <= 10 else "Department" if case.people <= 20 else "Stress"
    return f"{scale} · {case.dimensions}"


def render_diagnostics() -> None:
    """Render a bounded benchmark lab that never touches the live configuration."""
    render_section_header(
        "Performance lab",
        "Measure the real OR-Tools scheduler on synthetic workloads. Nothing here "
        "changes the roster, rules, fairness history, or generated schedule.",
        eyebrow="Diagnostics",
    )
    render_status(
        "Benchmarks can occupy the current app worker while they run. Start with "
        "Quick; the largest case is intentionally protected by a confirmation.",
        tone="warning",
        title="Run on demand",
        label="Heads-up",
    )

    if not benchmark_available():
        st.error("OR-Tools is not installed, so benchmark timings would be meaningless.")
        return

    with card_container("Choose a workload", "Use a preset or define one bounded custom case."):
        mode = st.radio(
            "Benchmark mode",
            ["Preset", "Custom"],
            horizontal=True,
            key="benchmark_mode",
        )
        if mode == "Preset":
            labels = {_preset_label(case): case for case in SAFE_BENCHMARK_PRESETS}
            selected = st.selectbox("Workload", list(labels), key="benchmark_preset")
            case = labels[selected]
        else:
            cols = st.columns(3)
            people = cols[0].number_input(
                "People", min_value=2, max_value=45, value=10, step=1, key="benchmark_people"
            )
            days = cols[1].number_input(
                "Days", min_value=1, max_value=31, value=14, step=1, key="benchmark_days"
            )
            shifts = cols[2].number_input(
                "Shifts", min_value=1, max_value=10, value=5, step=1, key="benchmark_shifts"
            )
            case = BenchmarkCase(int(people), int(days), int(shifts))

        estimated_cells = case.people * case.days * case.shifts
        stats = st.columns(4)
        stats[0].metric("People", case.people)
        stats[1].metric("Days", case.days)
        stats[2].metric("Shifts", case.shifts)
        stats[3].metric("Assignment cells", f"{estimated_cells:,}")

        large_case = estimated_cells >= 8_000
        confirmed = not large_case or st.checkbox(
            "I understand this large benchmark can run for about a minute",
            key="benchmark_confirm_large",
        )
        if st.button(
            "Run benchmark",
            type="primary",
            key="benchmark_run",
            disabled=not confirmed,
            width="stretch",
        ):
            try:
                with st.spinner(f"Solving {case.dimensions}…"):
                    st.session_state[Keys.BENCHMARK_RESULT] = run_benchmark(case, env="prod")
            except Exception as exc:
                st.session_state[Keys.BENCHMARK_RESULT] = None
                st.error(f"Benchmark failed: {exc}")

    result = st.session_state.get(Keys.BENCHMARK_RESULT)
    if result is None:
        st.caption("For repeatable command-line measurements, run `python scripts/benchmark.py`.")
        return

    tone = "success" if result.within_target else "warning"
    render_status(
        f"{result.case.dimensions} completed in {result.elapsed_seconds:.2f}s "
        f"with solver status {result.solver_status or 'unknown'}.",
        tone=tone,
        title=f"{result.flag} · target ≤ {result.case.target_seconds:g}s",
        label="Result",
    )
    metrics = st.columns(3)
    metrics[0].metric("Elapsed", f"{result.elapsed_seconds:.2f}s")
    metrics[1].metric("Target", f"≤ {result.case.target_seconds:g}s")
    metrics[2].metric("Solver status", result.solver_status or "Unknown")


__all__ = ["render_diagnostics"]
