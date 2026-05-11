"""Google Places autocomplete handling for the location field."""
import time

from playwright.sync_api import Page

from .fields import LOCATION
from .selectors import fill_if_exists


def fill_location_autocomplete(page: Page, location: str) -> bool:
    """Fill location field and handle Google Places autocomplete dropdown.

    If no autocomplete suggestion appears, leave the typed text in place rather
    than pressing Enter — on some forms (Calendly) Enter against an empty
    suggestion list clears the input and the field fails required validation.
    """
    try:
        el = page.locator(LOCATION).first
        if not el.is_visible(timeout=1000):
            return False

        el.fill("")
        el.type(location, delay=50)
        time.sleep(1.0)

        autocomplete_selectors = [
            '.pac-item',
            '.pac-container .pac-item',
            '[role="option"]',
            '.autocomplete-suggestions li',
        ]

        for sel in autocomplete_selectors:
            try:
                suggestion = page.locator(sel).first
                if suggestion.is_visible(timeout=500):
                    suggestion.click()
                    return True
            except Exception:
                continue

        # No suggestion appeared — leave typed text as the value.
        return True

    except Exception:
        return fill_if_exists(page, LOCATION, location)
