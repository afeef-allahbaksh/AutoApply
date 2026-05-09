import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from jsonschema import ValidationError

from src.schemas import validate_profile, validate_responses, validate_applications

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def normalize_posting_url(url: str) -> str:
    """Strip query string, fragment, and trailing slash for stable dedup keys.

    Handles tracking params (utm_source, gh_src, etc.) that vary between scrapes
    of the same job posting.
    """
    if not url:
        return url
    try:
        p = urlparse(url)
        path = p.path.rstrip("/")
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))
    except Exception:
        return url


class ProfileLoadError(Exception):
    pass


class Profile:
    def __init__(self, name: str):
        self.profile_name = name
        self.profile_dir = PROFILES_DIR / name

        if not self.profile_dir.is_dir():
            raise ProfileLoadError(
                f"Profile '{name}' not found. Expected directory: {self.profile_dir}"
            )

        self.data = self._load_json("profile.json", validate_profile)
        self.responses = self._load_json_optional("responses.json", validate_responses, default={})
        self.applications = self._load_json_optional("applications.json", validate_applications, default=[])

    def _load_json(self, filename: str, validator) -> dict | list:
        path = self.profile_dir / filename
        if not path.exists():
            raise ProfileLoadError(f"Missing required file: {path}")

        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ProfileLoadError(f"Invalid JSON in {path}: {e}")

        try:
            validator(data)
        except ValidationError as e:
            raise ProfileLoadError(f"Validation error in {path}: {e.message}")

        return data

    def _load_json_optional(self, filename: str, validator, default):
        path = self.profile_dir / filename
        if not path.exists() or path.stat().st_size == 0:
            return default
        try:
            return self._load_json(filename, validator)
        except ProfileLoadError:
            return default

    @property
    def job_preferences(self) -> dict:
        return self.data["job_preferences"]

    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @property
    def auto_submit(self) -> bool:
        return self.settings.get("auto_submit", False)

    @property
    def rate_limit_seconds(self) -> int:
        return self.settings.get("rate_limit_seconds", 30)

    def is_already_applied(self, company: str, role: str, posting_url: str) -> bool:
        """Check if an application already exists by composite key.

        Compares URLs after stripping query/fragment so tracking params don't bypass dedup.
        """
        target = normalize_posting_url(posting_url)
        for app in self.applications:
            if (app["company"] == company
                    and app["role"] == role
                    and normalize_posting_url(app["posting_url"]) == target):
                return True
        return False

    def __repr__(self) -> str:
        return f"Profile(name='{self.profile_name}')"
