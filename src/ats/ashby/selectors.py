"""Playwright primitives for each Ashby widget shape.

Each filler takes a container Locator (the [data-field-path] wrapper) and
the value(s) to fill. Returns bool: True if the field was actually filled,
False if the locator didn't resolve or fill verification failed.
"""
import time

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout


def wait_for_form(page: Page, timeout: int = 15000) -> bool:
    """Wait for the application form to load. Detected by the first
    [data-field-path] container appearing in the DOM."""
    try:
        page.wait_for_selector("[data-field-path]", timeout=timeout)
        return True
    except PlaywrightTimeout:
        return False


def get_label_text(container: Locator) -> str:
    """Return the question text for a container (the first <label>)."""
    try:
        return container.locator("label").first.inner_text().strip()
    except Exception:
        return ""


def get_option_labels(container: Locator) -> list[str]:
    """Return visible option labels of a radio or checkbox group, skipping
    the question-title label (always the first <label>)."""
    try:
        labels = container.locator("label").all_inner_texts()
        return [t.strip() for t in labels[1:] if t.strip()]
    except Exception:
        return []


def fill_text(container: Locator, value: str) -> bool:
    """Fill the first <input> or <textarea> with `value`. Verify React
    reconciled the change; on mismatch fall back to click+type so each
    keystroke fires an input event the controlled component consumes."""
    if not value:
        return False
    try:
        el = container.locator("input, textarea").first
        if not el.is_visible(timeout=1000):
            return False
        el.fill(value)
        if el.input_value() == value:
            return True
        el.click()
        el.fill("")
        el.type(value, delay=20)
        return el.input_value() == value
    except Exception:
        return False


def fill_combobox(page: Page, container: Locator, value: str) -> bool:
    """Type into a role=combobox input, wait for the listbox to populate,
    click the best matching option. The listbox is portaled to <body>, not
    nested inside the container."""
    if not value:
        return False
    try:
        el = container.locator('input[role="combobox"]').first
        if not el.is_visible(timeout=1000):
            return False
        el.click()
        el.fill("")
        el.type(value, delay=30)
        time.sleep(0.8)

        options = page.locator('[role="option"]:visible')
        n = options.count()
        if n == 0:
            return False

        target = value.lower().strip()
        for i in range(n):
            try:
                txt = options.nth(i).inner_text().strip().lower()
                if txt == target or target in txt:
                    options.nth(i).click()
                    time.sleep(0.3)
                    return True
            except Exception:
                continue
        # Fallback: first option (better than blank for required combobox)
        options.first.click()
        time.sleep(0.3)
        return True
    except Exception:
        return False


def fill_date(container: Locator, value: str) -> bool:
    """Type into a react-datepicker input. Expected value format: MM/DD/YYYY.
    Press Enter to commit (some variants don't commit on blur alone)."""
    if not value:
        return False
    try:
        el = container.locator(".react-datepicker-wrapper input").first
        if not el.is_visible(timeout=1000):
            return False
        el.click()
        el.fill("")
        el.type(value, delay=30)
        el.press("Enter")
        time.sleep(0.3)
        return True
    except Exception:
        return False


def fill_yesno(container: Locator, yes: bool) -> bool:
    """Click the Yes or No button in a yes/no toggle widget."""
    try:
        label = "Yes" if yes else "No"
        btn = container.locator(f'button:has-text("{label}")').first
        if not btn.is_visible(timeout=1000):
            return False
        btn.click()
        return True
    except Exception:
        return False


def fill_single_checkbox(container: Locator, yes: bool) -> bool:
    """Check (or leave unchecked) a single boolean checkbox.

    Ashby's checkboxes are visually-hidden inputs styled via wrapper. Clicking
    the associated <label> works; clicking the hidden input directly does not.
    """
    try:
        if not yes:
            return True  # leave unchecked, success
        cb = container.locator('input[type="checkbox"]').first
        try:
            cb_id = cb.get_attribute("id") or ""
            if cb_id:
                lbl = container.locator(f'label[for="{cb_id}"]').first
                if lbl.count() > 0:
                    lbl.click()
                    return cb.is_checked()
        except Exception:
            pass
        cb.check(force=True)
        return cb.is_checked()
    except Exception:
        return False


def fill_radio_group(container: Locator, target_label: str) -> bool:
    """Click the radio option whose label matches `target_label`."""
    if not target_label:
        return False
    try:
        labels = container.locator("label").all()
        target = target_label.lower().strip()
        # Exact match first (skip the question-title label at [0])
        for lbl in labels[1:]:
            try:
                if lbl.inner_text().strip().lower() == target:
                    lbl.click()
                    return True
            except Exception:
                continue
        # Substring fallback
        for lbl in labels[1:]:
            try:
                txt = lbl.inner_text().strip().lower()
                if target in txt or txt in target:
                    lbl.click()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def fill_checkbox_group(container: Locator, target_labels: list[str]) -> bool:
    """Click each checkbox label that matches one of `target_labels`."""
    if not target_labels:
        return False
    try:
        targets = [t.lower().strip() for t in target_labels if t.strip()]
        labels = container.locator("label").all()
        clicked = 0
        for lbl in labels[1:]:  # skip question-title label
            try:
                txt = lbl.inner_text().strip().lower()
            except Exception:
                continue
            matched = False
            if txt in targets:
                matched = True
            else:
                for t in targets:
                    if t in txt or txt in t:
                        matched = True
                        break
            if matched:
                try:
                    lbl.click()
                    clicked += 1
                except Exception:
                    continue
        return clicked > 0
    except Exception:
        return False


def upload_file(page: Page, container: Locator, file_path: str) -> bool:
    """Upload a file to the <input type="file"> inside this container.

    Two strategies, in order:
      1. Direct `set_input_files` on the (visually-hidden) input. Works for
         most React forms — Playwright dispatches the change event itself.
      2. `expect_file_chooser` + click the visible upload button as fallback.
         Some boards have stricter event handling that the direct approach
         doesn't satisfy.

    Scoping to the container avoids the Notion-style multi-match collision
    where the top "autofill resume parser" pane and the actual resume input
    both match `input[type="file"]` at page level.
    """
    try:
        el = container.locator('input[type="file"]').first
        if el.count() == 0:
            print("  [ashby] upload_file: no file input found in container")
            return False
        try:
            el.set_input_files(file_path)
            print(f"  [ashby] upload_file: set_input_files({file_path}) ok")
            return True
        except Exception as e:
            print(f"  [ashby] upload_file: set_input_files failed ({type(e).__name__}: {e}); trying file_chooser")
    except Exception as e:
        print(f"  [ashby] upload_file: locator resolution failed ({type(e).__name__}: {e})")
        return False

    try:
        upload_btn = container.locator(
            'button:has-text("Upload File"), button:has-text("Upload file"), '
            'button:has-text("Choose file"), button:has-text("Browse")'
        ).first
        if upload_btn.count() == 0:
            print("  [ashby] upload_file: no upload button to fall back on")
            return False
        with page.expect_file_chooser() as fc_info:
            upload_btn.click()
        fc_info.value.set_files(file_path)
        print("  [ashby] upload_file: file_chooser fallback ok")
        return True
    except Exception as e:
        print(f"  [ashby] upload_file: file_chooser fallback failed ({type(e).__name__}: {e})")
        return False
