import os
import threading

from src.profile_loader import PROFILES_DIR

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def list_profiles() -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir())


def active_profile() -> str:
    name = os.environ.get("AUTOAPPLY_PROFILE", "")
    if name:
        return name
    profiles = list_profiles()
    return profiles[0] if profiles else ""


def set_active_profile(name: str) -> None:
    os.environ["AUTOAPPLY_PROFILE"] = name


def profile_lock(name: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _locks[name] = lock
        return lock
