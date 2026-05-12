import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from jsonschema import ValidationError

from src.schemas import (
    validate_applications, validate_companies, validate_profile,
    validate_resume, validate_responses,
)

# Re-exported from `src.paths` so legacy `from src.profile_loader import
# PROFILES_DIR` callers keep working. New code should import directly from
# `src.paths`.
from src.paths import CONFIG_DIR, PROFILES_DIR  # noqa: F401


def _atomic_write_json(path: Path, data, validator=None) -> None:
    """Validate (if validator provided) + atomic write to `path`.

    Filesystem-level crash safety: writes to a `.tmp` sibling first, then
    `os.rename()`s into place. A mid-write crash leaves the previous file
    intact instead of a half-written JSON.

    Validation runs BEFORE the tmp file is written — a schema failure raises
    `ValidationError` and the on-disk file is untouched.
    """
    if validator is not None:
        validator(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


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

    # ------------------------------------------------------------------------
    # Atomic-write data layer
    #
    # Every top-level profile-file mutation should go through one of these
    # methods. They validate (where a schema exists), write atomically via
    # tmp+rename, and update the cached attribute so reads-after-write see
    # the new state without re-instantiating.
    #
    # Callers remain responsible for locking the read-modify-write sequence
    # (via `src.ui.state.profile_lock`) — the lock and atomicity are
    # separate concerns: locks serialize cross-thread RMW, atomic writes
    # protect against filesystem-level crashes.
    # ------------------------------------------------------------------------

    @classmethod
    def create(cls, name: str, data: dict) -> "Profile":
        """First-time profile creation. Used by the setup wizard.

        Validates `data` against the profile schema, creates the profile
        directory, writes `profile.json` atomically, then returns a
        loaded Profile instance.
        """
        profile_dir = PROFILES_DIR / name
        _atomic_write_json(profile_dir / "profile.json", data, validate_profile)
        return cls(name)

    def save_profile_data(self, data: dict) -> None:
        _atomic_write_json(self.profile_dir / "profile.json", data, validate_profile)
        self.data = data

    def save_responses(self, responses: dict) -> None:
        _atomic_write_json(self.profile_dir / "responses.json", responses, validate_responses)
        self.responses = responses

    def save_applications(self, applications: list) -> None:
        _atomic_write_json(
            self.profile_dir / "applications.json", applications, validate_applications,
        )
        self.applications = applications

    def save_companies(self, companies: list) -> None:
        _atomic_write_json(self.profile_dir / "companies.json", companies, validate_companies)

    def save_jobs(self, jobs: list) -> None:
        # No jobs.json schema in src/schemas.py today; add a validator later
        # if we want to enforce shape.
        _atomic_write_json(self.profile_dir / "jobs.json", jobs)

    def save_resume(self, resume: dict) -> None:
        _atomic_write_json(self.profile_dir / "resume.json", resume, validate_resume)

    def save_outreach(self, records: list) -> None:
        # Outreach schema is UI-driven (deliberate — fields evolve with the
        # cold-email feature). Skip jsonschema validation here; the route
        # layer is the source of truth for the record shape.
        _atomic_write_json(self.profile_dir / "outreach.json", records)

    def __repr__(self) -> str:
        return f"Profile(name='{self.profile_name}')"
