"""Interactive prompt channel for background tasks.

When a worker hits a decision boundary (Submit y/n, CAPTCHA pause, dupe
overwrite, etc.) it calls `ask()` — that writes a `pending_prompt` field to
the task's status file and blocks polling for a `prompt_response`. The UI
polls the status file like normal, surfaces the prompt as a modal, and posts
back to a route that calls `submit_response()`. The worker reads the response,
clears both fields, and returns the value to its caller.

The pending_prompt + prompt_response live alongside the runner's other status
fields in the same JSON file, so the existing 4s UI poll picks them up
without extra plumbing.
"""
import time
import uuid
from pathlib import Path

from . import runner


class PromptError(Exception):
    """Base class for prompt-channel errors."""


class PromptCancelled(PromptError):
    """Raised when the task's cancel flag flipped while waiting on a prompt."""


class PromptTimeout(PromptError):
    """Raised when timeout_seconds elapsed without a response."""


def ask(
    status_file: Path,
    question: str,
    choices: list[str],
    screenshot_path: Path | None = None,
    extra: dict | None = None,
    prompt_id: str | None = None,
    poll_interval: float = 0.5,
    timeout_seconds: float | None = None,
) -> str:
    """Worker-side: write a pending prompt to `status_file` and block until the
    UI submits a response, the cancel flag flips, or `timeout_seconds` elapses.

    `choices` is the closed set of acceptable response values. The UI is
    expected to render one button per choice; `submit_response` rejects values
    outside this set.

    Returns the chosen value. Raises `PromptCancelled` or `PromptTimeout`.
    """
    if not choices:
        raise ValueError("ask() requires a non-empty choices list")
    pid = prompt_id or str(uuid.uuid4())
    pending = {"id": pid, "question": question, "choices": list(choices)}
    if screenshot_path is not None:
        pending["screenshot_path"] = str(screenshot_path)
    if extra:
        pending["extra"] = extra
    runner.write_status(status_file, pending_prompt=pending, message=question)

    started = time.monotonic()
    while True:
        if runner.is_cancel_requested(status_file):
            runner.write_status(status_file, pending_prompt=None)
            raise PromptCancelled(f"Prompt {pid} cancelled before response")
        if timeout_seconds is not None and (time.monotonic() - started) > timeout_seconds:
            runner.write_status(status_file, pending_prompt=None)
            raise PromptTimeout(f"Prompt {pid} timed out after {timeout_seconds:.1f}s")
        data = runner.read_status_raw(status_file)
        response = data.get("prompt_response")
        if response and response.get("id") == pid:
            value = response.get("value")
            # Clear both fields so the status file returns to a "no pending
            # interaction" shape before the worker continues. This matters for
            # the UI: the modal disappears as soon as pending_prompt is None.
            runner.write_status(status_file, pending_prompt=None, prompt_response=None)
            if value not in pending["choices"]:
                # submit_response is supposed to enforce this, but defend in
                # depth: a stale or hand-crafted response shouldn't slip
                # through. Loop and wait for a valid response instead.
                continue
            return value
        time.sleep(poll_interval)


def get_pending(status_file: Path) -> dict | None:
    """UI-side: returns the pending prompt dict if a worker is waiting, else None."""
    return runner.read_status_raw(status_file).get("pending_prompt")


def submit_response(status_file: Path, prompt_id: str, value: str) -> bool:
    """UI-side: record the user's choice. Returns True iff the prompt is still
    pending with the given id AND the value is in its choices."""
    data = runner.read_status_raw(status_file)
    pending = data.get("pending_prompt")
    if not pending or pending.get("id") != prompt_id:
        return False
    if value not in pending.get("choices", []):
        return False
    runner.write_status(status_file, prompt_response={"id": prompt_id, "value": value})
    return True
