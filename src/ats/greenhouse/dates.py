"""Date parsing + filling for the structured education section."""
import re


def parse_date_parts(date_str: str) -> tuple[str | None, str | None]:
    """Extract month and year from date strings like 'June 2026' or
    'Expected Graduation: June 2026'."""
    MONTH_MAP = {
        "january": "1", "february": "2", "march": "3", "april": "4",
        "may": "5", "june": "6", "july": "7", "august": "8",
        "september": "9", "october": "10", "november": "11", "december": "12",
    }

    match = re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',
        date_str, re.IGNORECASE,
    )
    if match:
        return MONTH_MAP[match.group(1).lower()], match.group(2)

    match = re.search(r'(\d{1,2})[/-](\d{4})', date_str)
    if match:
        return str(int(match.group(1))), match.group(2)

    match = re.search(r'(\d{4})', date_str)
    if match:
        return None, match.group(1)

    return None, None


def try_select_date(locator, index: int, value: str) -> bool:
    """Try setting a date value in a dropdown or input field.

    Greenhouse education-date fields can be either `<select>` (label/value
    options) or `<input type="number">` — this picks the right strategy.
    """
    try:
        el = locator.nth(index) if locator.count() > index else locator.last
        if not el.is_visible(timeout=500):
            return False

        tag = el.evaluate("el => el.tagName.toLowerCase()")

        if tag == "input":
            el.fill(value)
            return True

        # For <select>, try by value, then label, then zero-padded
        try:
            el.select_option(value=value)
            return True
        except Exception:
            pass
        try:
            el.select_option(label=value)
            return True
        except Exception:
            pass
        if len(value) == 1:
            try:
                el.select_option(value=f"0{value}")
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False
