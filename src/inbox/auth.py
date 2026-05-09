"""Gmail OAuth — read-only scope, per-profile token persistence."""
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.profile_loader import PROFILES_DIR

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def gmail_dir(profile_name: str) -> Path:
    d = PROFILES_DIR / profile_name / "gmail"
    d.mkdir(parents=True, exist_ok=True)
    return d


def credentials_path(profile_name: str) -> Path:
    return gmail_dir(profile_name) / "credentials.json"


def token_path(profile_name: str) -> Path:
    return gmail_dir(profile_name) / "token.json"


def has_credentials(profile_name: str) -> bool:
    return credentials_path(profile_name).exists()


def is_connected(profile_name: str) -> bool:
    return token_path(profile_name).exists()


def load_credentials(profile_name: str) -> Credentials | None:
    """Return refreshed credentials, or None if no token saved."""
    tp = token_path(profile_name)
    if not tp.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tp, "w") as f:
            f.write(creds.to_json())
    return creds if creds and creds.valid else None


def run_oauth_flow(profile_name: str) -> Credentials:
    """Open browser, run OAuth consent, save token. Blocks until user authorizes."""
    cp = credentials_path(profile_name)
    if not cp.exists():
        raise FileNotFoundError(f"credentials.json not found at {cp}")
    flow = InstalledAppFlow.from_client_secrets_file(str(cp), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")
    with open(token_path(profile_name), "w") as f:
        f.write(creds.to_json())
    return creds


def disconnect(profile_name: str) -> None:
    """Delete the saved token. Credentials.json is left intact for reconnection."""
    tp = token_path(profile_name)
    if tp.exists():
        tp.unlink()


def account_email(creds: Credentials) -> str:
    """Authenticated Gmail address — Gmail users.getProfile API."""
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")


def status(profile_name: str) -> dict:
    """Convenience for the Settings card. Returns {state, email}."""
    if not has_credentials(profile_name):
        return {"state": "no_credentials", "email": None}
    if not is_connected(profile_name):
        return {"state": "credentials_only", "email": None}
    try:
        creds = load_credentials(profile_name)
        if not creds:
            return {"state": "credentials_only", "email": None}
        return {"state": "connected", "email": account_email(creds)}
    except Exception as e:
        return {"state": "error", "email": None, "error": str(e)}
