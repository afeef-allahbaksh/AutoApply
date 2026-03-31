from pathlib import Path
from playwright.sync_api import sync_playwright

BROWSER_DATA_DIR = Path(__file__).resolve().parent.parent / "browser_data"


def get_browser_context(headless: bool = False) -> tuple:
    """Launch a persistent browser context.

    Returns (playwright, browser, context) tuple.
    The caller is responsible for closing: context.close(), browser.close(), pw.stop()
    """
    BROWSER_DATA_DIR.mkdir(exist_ok=True)

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
    )

    return pw, browser, context
