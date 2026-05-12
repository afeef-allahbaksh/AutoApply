"""Detect what kind of widget lives inside an Ashby [data-field-path] container.

Ashby renders one container per question, but the widget inside varies:
text input, combobox, react-datepicker, radio group, checkbox group, yes/no
button toggle, single confirmation checkbox, textarea, or file input. The
form-fill orchestrator dispatches on the shape returned here.

Shape detection keys off DOM structure (input types, role attributes,
sibling buttons) — not Notion-specific hashed class names like
`_yesno_17tft_149`. Those differ across Ashby boards.
"""
from playwright.sync_api import Locator

FILE_UPLOAD = "file"
COMBOBOX = "combobox"           # role=combobox autocomplete (Location)
DATEPICKER = "datepicker"       # react-datepicker
RADIO_GROUP = "radio_group"     # 2+ <input type=radio>
CHECKBOX_GROUP = "checkbox_group"  # 2+ <input type=checkbox>
YESNO_TOGGLE = "yesno_toggle"   # <button>Yes</button> <button>No</button> + hidden checkbox
SINGLE_CHECKBOX = "single_checkbox"  # one checkbox (confirmation)
TEXTAREA = "textarea"
TEXT_INPUT = "text"
UNKNOWN = "unknown"


def detect_shape(container: Locator) -> str:
    """Inspect the DOM inside a [data-field-path] container. Order matters —
    more-specific shapes checked first."""
    try:
        if container.locator('input[type="file"]').count() > 0:
            return FILE_UPLOAD

        if container.locator(".react-datepicker-wrapper").count() > 0:
            return DATEPICKER

        # Yes/No toggle: both buttons present AND a hidden checkbox to track state.
        cb_count = container.locator('input[type="checkbox"]').count()
        if cb_count == 1:
            try:
                btn_texts = container.locator("button").all_inner_texts()
                btn_texts = [t.strip() for t in btn_texts]
                if "Yes" in btn_texts and "No" in btn_texts:
                    return YESNO_TOGGLE
            except Exception:
                pass

        if container.locator('input[role="combobox"]').count() > 0:
            return COMBOBOX

        if container.locator('input[type="radio"]').count() >= 2:
            return RADIO_GROUP

        if cb_count >= 2:
            return CHECKBOX_GROUP

        if cb_count == 1:
            return SINGLE_CHECKBOX

        if container.locator("textarea").count() > 0:
            return TEXTAREA

        if container.locator("input").count() > 0:
            return TEXT_INPUT
    except Exception:
        pass

    return UNKNOWN


def is_multi_select(label_text: str) -> bool:
    """True if a checkbox-group label suggests picking multiple options."""
    ll = label_text.lower()
    return any(kw in ll for kw in (
        "all that apply", "select all", "check all", "multiple",
    ))


def is_required(container: Locator) -> bool:
    """Check if the container's question is required.

    Ashby marks required via the `required=""` attribute on the input,
    `aria-required="true"`, or a `_required_*` class on the question label.
    """
    try:
        if container.locator("input[required], textarea[required], select[required]").count() > 0:
            return True
        if container.locator('[aria-required="true"]').count() > 0:
            return True
        cls = container.locator("label").first.get_attribute("class") or ""
        if "_required_" in cls:
            return True
    except Exception:
        pass
    return False
