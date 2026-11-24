"""Playwright helper for alingo_video_story — reusable browser automation.

Usage:
    uvx --from playwright python pw.py screenshot [url_path] [output_path]
    uvx --from playwright python pw.py run <script.py>

Or import in other scripts:
    from pw import get_page
    with get_page() as page:
        page.goto(...)
"""

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

BASE_URL = "http://localhost:5002"
USERNAME = "test"
PASSWORD = "test"
VIEWPORT = {"width": 1400, "height": 900}
SCREENSHOT_DIR = Path("/tmp")


@contextmanager
def get_page(headless=True):
    """Launch browser, log in, yield authenticated page, then close."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        # Log in
        page.goto(f"{BASE_URL}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button:has-text("Log in")')
        page.wait_for_load_state("networkidle")

        yield page

        browser.close()


def screenshot(page: Page, name: str = "screenshot") -> str:
    """Take a screenshot and return the path."""
    path = SCREENSHOT_DIR / f"pw_{name}.png"
    page.screenshot(path=str(path))
    return str(path)


def screenshot_element(page: Page, selector: str, name: str = "element") -> str:
    """Screenshot a specific element."""
    path = SCREENSHOT_DIR / f"pw_{name}.png"
    el = page.query_selector(selector)
    if el:
        el.screenshot(path=str(path))
    else:
        page.screenshot(path=str(path))
        print(f"Warning: selector '{selector}' not found, took full page screenshot")
    return str(path)


def navigate(page: Page, path: str) -> None:
    """Navigate to a path within the app."""
    url = f"{BASE_URL}{path}" if path.startswith("/") else path
    page.goto(url)
    page.wait_for_load_state("networkidle")


def cli():
    """Simple CLI for quick operations."""
    if len(sys.argv) < 2:
        print("Usage: python pw.py screenshot [/path] [name]")
        print("       python pw.py storyboards")
        return

    cmd = sys.argv[1]

    if cmd == "screenshot":
        url_path = sys.argv[2] if len(sys.argv) > 2 else "/"
        name = sys.argv[3] if len(sys.argv) > 3 else "screenshot"
        with get_page() as page:
            navigate(page, url_path)
            path = screenshot(page, name)
            print(f"Saved: {path}")

    elif cmd == "storyboards":
        # List storyboard titles and IDs
        with get_page() as page:
            navigate(page, "/")
            cards = page.query_selector_all(".project-card")
            for card in cards:
                title = card.query_selector(".project-card-title")
                link = card.query_selector("a")
                t = title.inner_text() if title else "?"
                href = link.get_attribute("href") if link else "?"
                print(f"  {href}: {t}")
            if not cards:
                print("No storyboards found")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    cli()
