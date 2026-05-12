"""Smoke test for src.tasks.prompt — exercises the worker/UI two-sided
prompt channel against a temp status file."""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, "/Users/afeef/workspace/Projects/AutoApply")
from src.tasks import prompt, runner


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def test_clean_ask_respond():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")

        result = {"value": None, "error": None}

        def worker():
            try:
                result["value"] = prompt.ask(sf, "Submit?", ["yes", "no"],
                                             prompt_id="p1", poll_interval=0.05)
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.15)

        # Pending prompt should be visible
        pending = prompt.get_pending(sf)
        assert pending is not None, "expected pending prompt, got None"
        assert_eq(pending["id"], "p1", "pending prompt id")
        assert_eq(pending["question"], "Submit?", "pending prompt question")
        assert_eq(pending["choices"], ["yes", "no"], "pending prompt choices")

        # Submit response
        ok = prompt.submit_response(sf, "p1", "yes")
        assert_eq(ok, True, "submit_response returns True for valid response")

        t.join(timeout=2)
        assert_eq(result["error"], None, "worker did not raise")
        assert_eq(result["value"], "yes", "worker received the chosen value")

        # Both fields cleared after consume
        data = runner.read_status_raw(sf)
        assert_eq(data.get("pending_prompt"), None, "pending_prompt cleared")
        assert_eq(data.get("prompt_response"), None, "prompt_response cleared")


def test_cancel_during_wait():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")

        result = {"value": None, "error": None}

        def worker():
            try:
                result["value"] = prompt.ask(sf, "ok?", ["y", "n"], poll_interval=0.05)
            except prompt.PromptError as e:
                result["error"] = e

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.1)

        runner.request_cancel(sf)
        t.join(timeout=2)
        assert isinstance(result["error"], prompt.PromptCancelled), \
            f"expected PromptCancelled, got {result['error']!r}"
        print("  ok: cancel flipped → PromptCancelled raised")
        data = runner.read_status_raw(sf)
        assert_eq(data.get("pending_prompt"), None, "pending_prompt cleared on cancel")


def test_timeout():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")

        result = {"value": None, "error": None}

        def worker():
            try:
                result["value"] = prompt.ask(sf, "ok?", ["y", "n"],
                                             poll_interval=0.05, timeout_seconds=0.3)
            except prompt.PromptError as e:
                result["error"] = e

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2)
        assert isinstance(result["error"], prompt.PromptTimeout), \
            f"expected PromptTimeout, got {result['error']!r}"
        print("  ok: timeout → PromptTimeout raised")
        data = runner.read_status_raw(sf)
        assert_eq(data.get("pending_prompt"), None, "pending_prompt cleared on timeout")


def test_reject_wrong_id():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")

        result = {"value": None}

        def worker():
            result["value"] = prompt.ask(sf, "?", ["a", "b"],
                                         prompt_id="real-id", poll_interval=0.05)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.1)

        # Wrong id → rejected, worker keeps waiting
        ok = prompt.submit_response(sf, "wrong-id", "a")
        assert_eq(ok, False, "submit_response rejects wrong id")
        assert t.is_alive(), "worker still waiting after wrong-id submit"

        # Right id, valid value
        ok = prompt.submit_response(sf, "real-id", "b")
        assert_eq(ok, True, "submit_response accepts right id")
        t.join(timeout=2)
        assert_eq(result["value"], "b", "worker got the right value")


def test_reject_invalid_value():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")

        result = {"value": None}

        def worker():
            result["value"] = prompt.ask(sf, "?", ["a", "b"],
                                         prompt_id="p", poll_interval=0.05)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.1)

        ok = prompt.submit_response(sf, "p", "z")  # not in choices
        assert_eq(ok, False, "submit_response rejects value not in choices")
        assert t.is_alive(), "worker still waiting after invalid-value submit"

        ok = prompt.submit_response(sf, "p", "a")
        assert_eq(ok, True, "submit_response accepts valid value")
        t.join(timeout=2)
        assert_eq(result["value"], "a", "worker got the right value")


def test_no_pending():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")
        assert_eq(prompt.get_pending(sf), None, "no pending → None")
        assert_eq(prompt.submit_response(sf, "any", "any"), False,
                  "submit_response with no pending → False")


def test_two_prompts_in_sequence():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        runner.write_status(sf, state="running")

        results = []

        def worker():
            results.append(prompt.ask(sf, "first?", ["a", "b"],
                                      prompt_id="p1", poll_interval=0.05))
            results.append(prompt.ask(sf, "second?", ["x", "y"],
                                      prompt_id="p2", poll_interval=0.05))

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.1)
        assert_eq(prompt.get_pending(sf)["id"], "p1", "first prompt pending")
        prompt.submit_response(sf, "p1", "a")
        time.sleep(0.2)
        assert_eq(prompt.get_pending(sf)["id"], "p2", "second prompt pending after first resolved")
        prompt.submit_response(sf, "p2", "y")
        t.join(timeout=2)
        assert_eq(results, ["a", "y"], "both prompts resolved in order")


def test_empty_choices_rejected():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        try:
            prompt.ask(sf, "?", [])
        except ValueError as e:
            print(f"  ok: empty choices → ValueError({e})")
            return
        raise AssertionError("expected ValueError for empty choices")


if __name__ == "__main__":
    tests = [
        test_clean_ask_respond,
        test_cancel_during_wait,
        test_timeout,
        test_reject_wrong_id,
        test_reject_invalid_value,
        test_no_pending,
        test_two_prompts_in_sequence,
        test_empty_choices_rejected,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {e}")

    print(f"\n{'=' * 40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
