"""IMAP credential storage and connection helpers — per-profile.

We default to Gmail (`imap.gmail.com:993`, SSL, app password) but the server +
port fields are user-editable so any IMAP-supporting provider works. The
password gives full mailbox access (read, send, delete); we only read, but the
secret stored on disk is more powerful than the OAuth refresh token it replaces.
"""
import imaplib
import json
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path

import certifi

from src.profile_loader import PROFILES_DIR

DEFAULT_IMAP_SERVER = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993
CONNECT_TIMEOUT = 15


def _ssl_context() -> ssl.SSLContext:
    """CA bundle from certifi so the python.org macOS distribution can verify
    Gmail / Outlook / etc. certs without relying on the system keychain
    (which it doesn't read by default)."""
    return ssl.create_default_context(cafile=certifi.where())


@dataclass
class ImapCredentials:
    email: str
    password: str
    server: str = DEFAULT_IMAP_SERVER
    port: int = DEFAULT_IMAP_PORT


def imap_dir(profile_name: str) -> Path:
    d = PROFILES_DIR / profile_name / "imap"
    d.mkdir(parents=True, exist_ok=True)
    return d


def credentials_path(profile_name: str) -> Path:
    return imap_dir(profile_name) / "credentials.json"


def is_connected(profile_name: str) -> bool:
    """Cheap check — credentials file exists. A real login is run on save."""
    return credentials_path(profile_name).exists()


def load_credentials(profile_name: str) -> ImapCredentials | None:
    p = credentials_path(profile_name)
    if not p.exists():
        return None
    with open(p) as f:
        data = json.load(f)
    return ImapCredentials(
        email=data["email"],
        password=data["password"],
        server=data.get("server", DEFAULT_IMAP_SERVER),
        port=int(data.get("port", DEFAULT_IMAP_PORT)),
    )


def save_credentials(profile_name: str, creds: ImapCredentials) -> None:
    payload = {
        "email": creds.email,
        "password": creds.password,
        "server": creds.server,
        "port": creds.port,
    }
    with open(credentials_path(profile_name), "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def disconnect(profile_name: str) -> None:
    p = credentials_path(profile_name)
    if p.exists():
        p.unlink()


def open_imap(creds: ImapCredentials) -> imaplib.IMAP4_SSL:
    """Open and authenticate an IMAP_SSL connection. Caller must `.logout()`."""
    socket.setdefaulttimeout(CONNECT_TIMEOUT)
    conn = imaplib.IMAP4_SSL(creds.server, creds.port, ssl_context=_ssl_context())
    conn.login(creds.email, creds.password)
    return conn


def verify_credentials(creds: ImapCredentials) -> tuple[bool, str]:
    """Try a real login + logout. Returns (ok, error_message)."""
    try:
        conn = open_imap(creds)
    except imaplib.IMAP4.error as e:
        return False, f"Authentication failed: {e}"
    except (socket.gaierror, socket.timeout, OSError) as e:
        return False, f"Could not reach {creds.server}:{creds.port} ({e})"
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        # Best-effort cleanup — if logout fails the connection will time out
        # naturally. Don't surface this to the caller; verification succeeded.
        pass
    return True, ""


def status(profile_name: str) -> dict:
    """Convenience for the Settings card. Returns {state, email}."""
    if not is_connected(profile_name):
        return {"state": "not_configured", "email": None}
    creds = load_credentials(profile_name)
    if not creds:
        return {"state": "not_configured", "email": None}
    return {"state": "connected", "email": creds.email}
