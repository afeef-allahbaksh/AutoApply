from pathlib import Path

from playwright.sync_api import sync_playwright


def get_browser_context(headless: bool = False, storage_state_path: Path | None = None) -> tuple:
    """Launch a browser context.

    If storage_state_path is provided and the file exists, the new context loads
    cookies + localStorage from it so previously-completed 2FA challenges persist.

    Returns (playwright, browser, context) tuple.
    The caller is responsible for closing: context.close(), browser.close(), pw.stop()
    """
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    context_kwargs = {
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 900},
    }
    if storage_state_path is not None and Path(storage_state_path).exists():
        context_kwargs["storage_state"] = str(storage_state_path)
    context = browser.new_context(**context_kwargs)

    return pw, browser, context
