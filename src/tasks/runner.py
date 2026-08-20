"""Generic background-task runner — daemon thread, status file, cooperative cancel.

Extracted from `src/inbox/sync.py` so apply, discover, optimize, pipeline, and the
setup wizard can share one mechanism. Callers own their status file path and the
worker function; this module owns thread registration, atomic status IO, and
stuck-state detection.

Task keys take the form `f"{kind}:{profile}"` so different kinds can run
concurrently for one profile (inbox-sync alongside apply) while the same kind
can't double-start.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_active_threads: dict[str, threading.Thread] = {}
_thread_registry_lock = threading.Lock()


def _read_status_raw(status_file: Path) -> dict:
    """Read status file as-is, no liveness check. Empty dict if missing/corrupt."""
    if not status_file.exists():
        return {}
    try:
        with open(status_file) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def read_status_raw(status_file: Path) -> dict:
    """Public raw-read helper for callers that need a single field without
    stuck-state detection (e.g. `src/tasks/prompt.py` polling for a response)."""
    return _read_status_raw(status_file)


def read_status(
    status_file: Path,
    task_key: str,
    interrupted_message: str = "Task interrupted (worker stopped). Re-start to retry.",
) -> dict:
    """Status with stuck-state detection.

    If the file says running but no live worker thread exists for `task_key` in
    this process (e.g. uvicorn restarted mid-task), surface that as
    `interrupted` so the UI stops polling.
    """
    data = _read_status_raw(status_file)
    if not data:
        return {"state": "idle"}
    if data.get("state") == "running":
        with _thread_registry_lock:
            t = _active_threads.get(task_key)
            if t is None or not t.is_alive():
                return {**data, "state": "interrupted", "message": interrupted_message}
    return data


def write_status(status_file: Path, **fields) -> None:
    """Atomic read-modify-write. Tmp file + rename so a mid-write crash leaves
    the previous status intact rather than a half-written JSON."""
    current = _read_status_raw(status_file)
    current.update(fields)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_file.with_suffix(status_file.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(current, f, indent=2)
        f.write("\n")
    tmp.replace(status_file)


def request_cancel(status_file: Path, cancelling_message: str = "Cancelling…") -> bool:
    """Flip the cancel flag if a task is running. Workers poll
    `is_cancel_requested()` at safe boundaries and exit cleanly."""
    if _read_status_raw(status_file).get("state") != "running":
        return False
    write_status(status_file, cancel_requested=True, message=cancelling_message)
    return True


def is_cancel_requested(status_file: Path) -> bool:
    """Poll from inside a worker at loop/chunk boundaries to exit cleanly."""
    return bool(_read_status_raw(status_file).get("cancel_requested"))


def start_task(
    task_key: str,
    status_file: Path,
    target: Callable,
    args: tuple = (),
    initial_status: dict | None = None,
    thread_name: str | None = None,
    already_running_msg: str = "Task already running.",
) -> tuple[bool, str]:
    """Spawn a daemon thread for `target(*args)`.

    Writes initial `state="running"` status synchronously *before* the thread
    starts so the response that just spawned the worker can render an
    in-progress banner without racing the first worker update.

    Returns (started, message). If a live thread for `task_key` already exists,
    returns (False, already_running_msg).
    """
    with _thread_registry_lock:
        existing = _active_threads.get(task_key)
        if existing is not None and existing.is_alive():
            return False, already_running_msg
        # Drop dead Thread references so the dict doesn't accumulate over weeks.
        if existing is not None:
            _active_threads.pop(task_key, None)
        base_status = {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "error": None,
            "cancel_requested": False,
        }
        if initial_status:
            base_status.update(initial_status)
        write_status(status_file, **base_status)
        t = threading.Thread(
            target=target,
            args=args,
            daemon=True,
            name=thread_name or f"task-{task_key}",
        )
        _active_threads[task_key] = t
        t.start()
    return True, "Started."


def run_with_terminal_status(
    status_file: Path,
    work: Callable[[], str | None],
    idle_message: str = "Task complete.",
    cancelled_message: str = "Task cancelled.",
    log_prefix: str = "[tasks]",
) -> None:
    """Wrap a worker function with the standard error-trap + terminal-status pattern.

    Call this from inside your thread target. `work` is the no-arg callable that
    runs the pipeline; returning cleanly means success (or cancellation, which is
    detected from the cancel flag). Exceptions become terminal `error` state.

    `work` may return a string to override `idle_message` — for workers whose
    completion banner depends on what they found (e.g. "…N still to scan").
    """
    try:
        outcome = work()
        if isinstance(outcome, str) and outcome:
            idle_message = outcome
        final = _read_status_raw(status_file)
        if final.get("cancel_requested"):
            write_status(
                status_file,
                state="cancelled",
                completed_at=datetime.now(timezone.utc).isoformat(),
                message=cancelled_message,
                cancel_requested=False,
            )
        else:
            write_status(
                status_file,
                state="idle",
                completed_at=datetime.now(timezone.utc).isoformat(),
                message=idle_message,
            )
    except Exception as e:  # noqa: BLE001 — top-of-thread catch-all is intentional
        # Status file gets a short error for the UI banner; the full traceback
        # goes to stderr so debugging is actually possible (the truncated
        # version often elides the root cause).
        import sys
        import traceback
        write_status(
            status_file,
            state="error",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(e)[:240],
            cancel_requested=False,
        )
        print(f"{log_prefix} worker crashed: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
