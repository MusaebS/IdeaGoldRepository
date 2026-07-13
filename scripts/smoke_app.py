"""End-to-end smoke test for the Streamlit app in a real browser.

Launches ``app.py`` headless, drives it with Playwright/Chromium, and checks the
key flows: the app loads, Test mode + Generate renders a schedule with the
quality metric and export buttons, an invalid configuration shows a validation
error, and an infeasible configuration offers the relax-and-retry recovery.

Unit tests cover the model and the pure functions; this exercises the Streamlit
glue (``session_state``/``st.rerun``, widget wiring, the result rendering) that
unit tests cannot reach.

Usage::

    pip install playwright          # browser binary is expected to be present
    python scripts/smoke_app.py

It is intentionally NOT part of the default ``pytest`` run: it needs a browser
and a live server, so it stays an on-demand check. Exits non-zero on failure.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
import urllib.request

PORT = int(os.environ.get("SMOKE_PORT", "8503"))
URL = f"http://localhost:{PORT}"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _chromium_executable() -> str | None:
    """Find a pre-installed Chromium so we don't download one."""
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for pattern in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux/headless_shell"):
        hits = sorted(glob.glob(os.path.join(base, pattern)))
        if hits:
            return hits[-1]
    return None


def _wait_healthy(timeout: int = 40) -> bool:
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(f"{URL}/_stcore/health", timeout=2) as r:
                if r.read().strip() == b"ok":
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _run_checks() -> list[str]:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    exe = _chromium_executable()
    launch_kwargs = {"headless": True}
    if exe:
        launch_kwargs["executable_path"] = exe

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)

        def check(cond: bool, msg: str) -> None:
            print(f"{'OK  ' if cond else 'FAIL'}: {msg}")
            if not cond:
                failures.append(msg)

        def open_result_workspace(name: str) -> None:
            """Open Results and one of its nested workspaces after any rerun."""
            page.get_by_role("tab", name="⑥ Results").click()
            page.get_by_role("tab", name=name, exact=True).click()

        def open_customise() -> None:
            open_result_workspace("Schedule")
            colour_label = page.get_by_text("Colour cells by")
            if not colour_label.is_visible():
                page.get_by_text("Customise the schedule", exact=False).first.click()
                colour_label.wait_for(timeout=10000)

        # --- success path ---
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_selector("text=Idea Gold Scheduler", timeout=60000)
        check(True, "app loads")
        page.get_by_text("Test mode (preload example data)").click()
        page.wait_for_timeout(2500)
        page.get_by_role("tab", name="⑤ Review & run").click()
        page.locator('button:has-text("Generate schedule")').click()
        # Generation triggers a Streamlit rerun, which recreates the tab set.
        # Wait for the solve result on Review & run before selecting Results;
        # clicking Results immediately races the rerun and leaves it hidden.
        page.get_by_text("Schedule generated (", exact=False).wait_for(
            timeout=120000
        )
        open_result_workspace("Overview")
        page.get_by_text("Schedule quality", exact=True).wait_for(timeout=20000)
        check(True, "quality metric shown")

        open_result_workspace("Schedule")
        page.locator(
            '[data-testid="stDataFrame"]:visible, '
            '[data-testid="stDataFrameResizable"]:visible'
        ).first.wait_for(timeout=120000)
        check(True, "Test mode + Generate renders a schedule")

        open_result_workspace("Export")
        for label in [
            "Download CSV (schedule)",
            "Download Excel (schedule + fairness)",
            "Download PDF (schedule + fairness)",
            "Download updated ledger (for next block)",
        ]:
            try:
                page.get_by_role("button", name=label).wait_for(timeout=20000)
                check(True, f"export button {label!r}")
            except Exception:
                check(False, f"export button {label!r}")

        open_result_workspace("Fairness")
        try:
            page.get_by_text("Workload by resident").first.wait_for(timeout=10000)
            check(page.locator('[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"]').count() > 0,
                  "fairness bar chart renders")
        except Exception:
            check(False, "fairness bar chart renders")
        # New result surfaces: the fairness table download and per-call audit.
        try:
            page.get_by_role("button", name="Download fairness table (CSV)").wait_for(timeout=10000)
            check(True, "fairness table with CSV download renders")
        except Exception:
            check(False, "fairness table with CSV download renders")

        open_result_workspace("Audit trail")
        try:
            page.get_by_text("Per-call detail (audit)").first.click()
            page.get_by_role("button", name="Download per-call CSV").wait_for(timeout=10000)
            check(True, "per-call audit expander renders")
        except Exception:
            check(False, "per-call audit expander renders")

        open_result_workspace("Schedule")
        # Open the "Customise" expander to reach the colour + column controls.
        try:
            page.get_by_text("Customise the schedule", exact=False).first.click()
            page.wait_for_timeout(1000)
            page.get_by_text("Colour cells by").wait_for(timeout=10000)
            check(True, "colour-mode selector shown")
        except Exception:
            check(False, "colour-mode selector shown")
        # One-click theming: apply shades from the theme colour, grid survives.
        try:
            page.get_by_role("button", name="Apply theme shades").click()
            page.wait_for_timeout(2500)
            open_customise()
            check(page.locator('[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"]').count() > 0
                  and page.locator('[data-testid="stException"]').count() == 0,
                  "theme shades apply without breaking the grid")
        except Exception:
            check(False, "theme shades apply without breaking the grid")
        # Add a cosmetic custom column and confirm it lands on the schedule.
        try:
            page.locator('[data-testid="stTextInput"]').filter(
                has_text="New column name"
            ).locator("input").fill("On-call team")
            page.get_by_role("button", name="Add column").click()
            page.wait_for_timeout(2500)
            open_customise()
            check(page.get_by_text("On-call team").count() > 0,
                  "custom cosmetic column added")
        except Exception:
            check(False, "custom cosmetic column added")
        # Auto-fill the new column with a two-name daily cycle.
        try:
            page.locator('[data-testid="stTextArea"]').filter(
                has_text="Names (comma or newline separated)"
            ).locator("textarea").fill("Dr Alpha, Dr Beta")
            page.get_by_role("button", name="Fill", exact=True).click()
            page.wait_for_timeout(2500)
            open_customise()
            check(page.get_by_text("Dr Alpha").count() > 0
                  and page.get_by_text("Dr Beta").count() > 0,
                  "auto-fill pattern populates the column")
        except Exception:
            check(False, "auto-fill pattern populates the column")
        # Toggling a control triggers a Streamlit rerun without re-solving; the
        # schedule must persist (results now render from session_state).
        open_result_workspace("Export")
        page.get_by_text("Show Fairness Log").click()
        page.wait_for_timeout(2500)
        open_result_workspace("Export")
        check(page.locator('[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"]').count() > 0,
              "results persist across a rerun")
        # Clicking a download must NOT blank the results (exports are cached).
        try:
            with page.expect_download(timeout=20000):
                page.get_by_role("button", name="Download CSV (schedule)").click()
        except Exception:
            pass  # capturing the file is best-effort; persistence below is the point
        page.wait_for_timeout(1500)
        check(page.locator('[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"]').count() > 0,
              "results persist after a download click")
        # Manual-edit persistence: Apply flags the schedule as edited (badge +
        # revert button), Revert restores the solver result and clears both.
        try:
            open_result_workspace("Schedule")
            page.get_by_text("Manual edit & revalidate").click()
            page.get_by_role("button", name="Apply edits").wait_for(timeout=10000)
            page.get_by_role("button", name="Apply edits").click()
            revert = page.get_by_role("button", name="Revert to solver result")
            revert.wait_for(state="attached", timeout=30000)
            open_result_workspace("Schedule")
            check(revert.count() > 0, "Apply edits marks the schedule as edited")
            # Streamlit keeps inactive tab trees in the DOM; force targets the
            # now-attached control even when Playwright still considers an old
            # tab wrapper hidden during the transition.
            revert.click(force=True)
            revert.wait_for(state="detached", timeout=30000)
            open_result_workspace("Schedule")
            check(page.get_by_role("button", name="Revert to solver result").count() == 0,
                  "Revert restores the solver result")
            open_result_workspace("Export")
            check(page.get_by_role("button", name="Download CSV (schedule)").count() > 0,
                  "exports still available after edit round-trip")
        except Exception as exc:
            print(f"Manual-edit smoke detail: {exc}")
            check(False, "manual edit apply/revert round-trip")
        check(page.locator('[data-testid="stException"]').count() == 0,
              "no uncaught exception on the page")
        page.close()

        # --- validation-error path ---
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_selector("text=Idea Gold Scheduler", timeout=60000)
        page.get_by_role("tab", name="⑤ Review & run").click()
        page.locator('button:has-text("Generate schedule")').click()
        page.wait_for_timeout(2000)
        check(page.get_by_text("Fix the configuration before generating").count() > 0,
              "invalid config shows a validation error")
        page.close()

        # --- infeasible -> recovery path ---
        # Load a valid roster, then impose an impossible mandatory penalty (no
        # resident can earn 999 points in a block) so the solve is genuinely
        # infeasible and the relax-and-retry recovery appears. (Night float is
        # a coverage overlay now, so an NF shift no longer causes infeasibility.)
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_selector("text=Idea Gold Scheduler", timeout=60000)
        page.get_by_text("Test mode (preload example data)").click()
        page.wait_for_timeout(2500)
        page.get_by_role("tab", name="③ Policies").click()
        page.get_by_role("tab", name="Fairness controls").click()
        page.wait_for_timeout(1000)
        extra = page.locator('[data-testid="stNumberInput"]').filter(has_text="Extra points")
        extra.locator("input").fill("999")
        extra.locator('xpath=following::button[normalize-space()="Add"][1]').click()
        page.wait_for_timeout(1500)
        page.get_by_role("tab", name="⑤ Review & run").click()
        page.locator('button:has-text("Generate schedule")').click()
        page.wait_for_timeout(8000)
        check(page.get_by_role("button", name="Retry with min_gap 0").count() > 0,
              "infeasible config offers a relax-and-retry button")
        page.close()

        browser.close()
    return failures


def main() -> int:
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("SKIP: playwright is not installed (pip install playwright).")
        return 0

    env = dict(os.environ, ENV=os.environ.get("ENV", "dev"))
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", os.path.join(ROOT, "app.py"),
            "--server.port", str(PORT), "--server.headless", "true",
            "--server.enableCORS", "false", "--server.enableXsrfProtection", "false",
        ],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_healthy():
            print("FAIL: Streamlit server did not become healthy.")
            return 1
        failures = _run_checks()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if failures:
        print(f"\n{len(failures)} check(s) failed.")
        return 1
    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
