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

        # --- success path ---
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_selector("text=Idea Gold Scheduler", timeout=60000)
        check(True, "app loads")
        page.get_by_text("Test mode (preload example data)").click()
        page.wait_for_timeout(2500)
        page.get_by_role("button", name="Generate Schedule").click()
        page.wait_for_selector(
            '[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"]',
            timeout=120000,
        )
        check(True, "Test mode + Generate renders a schedule")
        check(page.get_by_text("Schedule quality").count() > 0, "quality metric shown")
        for label in [
            "Download CSV (schedule)",
            "Download Excel (schedule + fairness)",
            "Download PDF (schedule + fairness)",
        ]:
            try:
                page.get_by_role("button", name=label).wait_for(timeout=20000)
                check(True, f"export button {label!r}")
            except Exception:
                check(False, f"export button {label!r}")
        check(page.locator('[data-testid="stException"]').count() == 0,
              "no uncaught exception on the page")
        page.close()

        # --- validation-error path ---
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_selector("text=Idea Gold Scheduler", timeout=60000)
        page.get_by_role("button", name="Generate Schedule").click()
        page.wait_for_timeout(2000)
        check(page.get_by_text("Fix the configuration before generating").count() > 0,
              "invalid config shows a validation error")
        page.close()

        # --- infeasible -> recovery path ---
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_selector("text=Idea Gold Scheduler", timeout=60000)
        page.locator('[data-testid="stTextArea"] textarea').first.fill("A")
        page.locator('[data-testid="stTextInput"] input').first.fill("NF")
        page.get_by_text("Night Float", exact=True).click()
        page.get_by_role("button", name="Add Shift").click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Generate Schedule").click()
        page.wait_for_timeout(6000)
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
