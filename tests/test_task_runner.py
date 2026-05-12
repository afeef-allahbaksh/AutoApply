"""Smoke test for src.tasks.runner — exercises every public function against a
temp status file so no real profile data is touched."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.tasks import runner


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")
    print(f"  ok: {msg}")


def test_idle_when_no_file():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        st = runner.read_status(sf, "test:nothing")
        assert_eq(st, {"state": "idle"}, "no file -> idle")


def test_full_lifecycle_clean_completion():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"

        def work():
            for i in range(3):
                if runner.is_cancel_requested(sf):
                    return
                runner.write_status(sf, progress=i)
                time.sleep(0.05)

        def thread_target():
            runner.run_with_terminal_status(
                sf, work=work,
                idle_message="done", cancelled_message="cxl",
                log_prefix="[test]",
            )

        started, msg = runner.start_task(
            task_key="test:clean", status_file=sf,
            target=thread_target, args=(),
            initial_status={"message": "starting"},
            thread_name="test-clean",
        )
        assert_eq(started, True, "start_task returns True")

        # Status should be running immediately (synchronous initial write)
        st = runner.read_status(sf, "test:clean")
        assert_eq(st["state"], "running", "state=running synchronously after start")
        assert_eq(st["message"], "starting", "initial_status fields persist")

        # Wait for completion
        time.sleep(0.5)
        st = runner.read_status(sf, "test:clean")
        assert_eq(st["state"], "idle", "clean completion -> idle")
        assert_eq(st["message"], "done", "idle_message applied")


def test_cancel_path():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"

        def work():
            for _ in range(100):
                if runner.is_cancel_requested(sf):
                    return
                time.sleep(0.02)

        def thread_target():
            runner.run_with_terminal_status(
                sf, work=work,
                idle_message="done", cancelled_message="user cancelled",
                log_prefix="[test]",
            )

        runner.start_task(
            task_key="test:cancel", status_file=sf,
            target=thread_target, args=(),
        )
        time.sleep(0.05)
        flipped = runner.request_cancel(sf)
        assert_eq(flipped, True, "request_cancel returns True while running")
        time.sleep(0.5)
        st = runner.read_status(sf, "test:cancel")
        assert_eq(st["state"], "cancelled", "cancel path -> cancelled")
        assert_eq(st["message"], "user cancelled", "cancelled_message applied")
        # Cancel flag must be cleared so the file is reusable
        assert_eq(st.get("cancel_requested"), False, "cancel_requested cleared on finalize")


def test_error_path():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"

        def thread_target():
            runner.run_with_terminal_status(
                sf, work=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                log_prefix="[test]",
            )

        runner.start_task(
            task_key="test:error", status_file=sf,
            target=thread_target, args=(),
        )
        time.sleep(0.3)
        st = runner.read_status(sf, "test:error")
        assert_eq(st["state"], "error", "exception -> error state")
        assert "boom" in st.get("error", ""), f"error message captured: {st.get('error')}"
        print("  ok: error message captured")


def test_already_running():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"

        def thread_target():
            runner.run_with_terminal_status(sf, work=lambda: time.sleep(0.5))

        runner.start_task(
            task_key="test:dup", status_file=sf, target=thread_target,
            already_running_msg="dup detected",
        )
        started, msg = runner.start_task(
            task_key="test:dup", status_file=sf, target=thread_target,
            already_running_msg="dup detected",
        )
        assert_eq(started, False, "second start blocked while task running")
        assert_eq(msg, "dup detected", "already_running_msg returned")
        time.sleep(0.6)


def test_stuck_state_detection():
    with tempfile.TemporaryDirectory() as td:
        sf = Path(td) / "status.json"
        # Simulate a file left in running state with no live thread
        runner.write_status(sf, state="running", message="zombie")
        st = runner.read_status(sf, "test:zombie", interrupted_message="zombie!")
        assert_eq(st["state"], "interrupted", "stuck running -> interrupted")
        assert_eq(st["message"], "zombie!", "interrupted_message used")


def test_concurrent_kinds_for_one_profile():
    """Same profile, different task kinds, should coexist."""
    with tempfile.TemporaryDirectory() as td:
        sf_a = Path(td) / "a.json"
        sf_b = Path(td) / "b.json"

        def make_target(sf):
            return lambda: runner.run_with_terminal_status(sf, work=lambda: time.sleep(0.2))

        a, _ = runner.start_task(task_key="kindA:user", status_file=sf_a, target=make_target(sf_a))
        b, _ = runner.start_task(task_key="kindB:user", status_file=sf_b, target=make_target(sf_b))
        assert_eq(a and b, True, "two different task kinds for same profile both start")
        time.sleep(0.4)


if __name__ == "__main__":
    tests = [
        test_idle_when_no_file,
        test_full_lifecycle_clean_completion,
        test_cancel_path,
        test_error_path,
        test_already_running,
        test_stuck_state_detection,
        test_concurrent_kinds_for_one_profile,
    ]

    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  FAIL: {e}")

    print(f"\n{'='*40}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
