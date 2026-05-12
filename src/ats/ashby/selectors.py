"""Generic Playwright helpers — fill, upload, wait. Same pattern as
`src/ats/greenhouse/selectors.py` but scoped to Ashby's form behaviors."""
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .fields import NAME


def wait_for_form(page: Page, timeout: int = 15000) -> bool:
    """Wait for the Ashby application form to load."""
    try:
        page.wait_for_selector(NAME, timeout=timeout)
        return True
    except PlaywrightTimeout:
        return False


def fill_if_exists(page: Page, selector: str, value: str) -> bool:
    """Fill a field if it exists on the page.

    Ashby's React-Hook-Form-managed inputs sometimes don't reconcile a
    `.fill()` — verify the value stuck; on mismatch, fall back to click +
    type so each keystroke fires a real input event that React consumes.
    """
    try:
        el = page.locator(selector).first
        if not el.is_visible(timeout=1000):
            return False
        el.fill(value)
        if el.input_value() == value:
            return True
        # React reconcile fallback — same trick the greenhouse custom-q
        # handler uses for Remix-controlled inputs.
        try:
            el.click()
            el.fill("")
            el.type(value, delay=20)
        except Exception:
            return False
        return el.input_value() == value
    except (PlaywrightTimeout, Exception):
        return False


def upload_if_exists(page: Page, selector: str, file_path: str) -> bool:
    """Upload a file to a hidden file input if it exists."""
    try:
        el = page.locator(selector)
        if el.count() > 0:
            el.set_input_files(file_path)
            return True
    except Exception:
        pass
    return False
