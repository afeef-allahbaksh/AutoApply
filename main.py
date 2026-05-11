"""AutoApply launcher.

The dashboard is the user surface — there are no CLI subcommands here anymore.
This script just starts the FastAPI app and (optionally) opens your browser.
Bootstrapping a fresh install? Just run `python main.py`; if no profile exists,
the dashboard redirects to the setup wizard.

Flags:
    --profile <name>    Activate a specific profile on launch.
    --port <n>          Port to bind (default 8000).
    --host <addr>       Host to bind (default 127.0.0.1).
    --no-browser        Don't auto-open the dashboard.

Env vars:
    AUTOAPPLY_PROFILE   Same as --profile (the flag overrides if both are set).
    AUTOAPPLY_DEV=1     Run with uvicorn auto-reload — for development only.
                        Background tasks get killed on file change.
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

import uvicorn


def _open_browser_after_delay(url: str, delay: float = 1.0) -> None:
    """Wait briefly so uvicorn has time to bind, then open the dashboard."""
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — browser failure must not abort the server
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="autoapply",
        description="Launch the AutoApply dashboard.",
    )
    parser.add_argument(
        "--profile", default=None,
        help=(
            "Profile to activate on launch. If omitted, the dashboard auto-picks "
            "one (or redirects to setup if none exist)."
        ),
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Port to bind (default 8000).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help=(
            "Host to bind (default 127.0.0.1). Use 0.0.0.0 to expose on the LAN — "
            "remember there's no auth."
        ),
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Don't auto-open the dashboard in a browser.",
    )
    args = parser.parse_args()

    if args.profile:
        os.environ["AUTOAPPLY_PROFILE"] = args.profile

    # Print a clickable URL using a loopback alias when bound to 0.0.0.0
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{display_host}:{args.port}/"
    profile_label = args.profile or os.environ.get("AUTOAPPLY_PROFILE") or "auto-detect"

    print()
    if args.host != "127.0.0.1":
        print(f"⚠️  Binding to {args.host} — reachable from other machines, no auth.")
    print(f"Dashboard: {url}  (profile: {profile_label})")

    if not args.no_browser:
        threading.Thread(
            target=_open_browser_after_delay, args=(url,), daemon=True,
        ).start()

    dev = os.environ.get("AUTOAPPLY_DEV", "").lower() in ("1", "true", "yes")
    if dev:
        print("AUTOAPPLY_DEV=1 → auto-reload ON. Ctrl-C to stop.")
        print()
        uvicorn.run(
            "src.ui.app:app",
            host=args.host, port=args.port, log_level="info",
            reload=True, reload_dirs=["src"],
        )
    else:
        from src.ui.app import app
        print("Ctrl-C to stop.")
        print()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")

    return 0


if __name__ == "__main__":
    sys.exit(main())
