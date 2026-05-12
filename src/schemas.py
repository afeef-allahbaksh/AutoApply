import json

from jsonschema import validate

from src.paths import CONFIG_DIR

# Back-compat alias; new code should import `CONFIG_DIR` directly from
# `src.paths`.
SCHEMA_DIR = CONFIG_DIR


def _load_schema(name: str) -> dict:
    schema_path = CONFIG_DIR / f"{name}_schema.json"
    with open(schema_path) as f:
        return json.load(f)


def validate_profile(data: dict) -> None:
    """Validate profile data against the profile schema. Raises ValidationError on failure."""
    schema = _load_schema("profile")
    validate(instance=data, schema=schema)


def validate_responses(data: dict) -> None:
    """Validate responses data against the responses schema. Raises ValidationError on failure."""
    schema = _load_schema("responses")
    validate(instance=data, schema=schema)


def validate_applications(data: list) -> None:
    """Validate applications data against the applications schema. Raises ValidationError on failure."""
    schema = _load_schema("applications")
    validate(instance=data, schema=schema)


def validate_companies(data: list) -> None:
    """Validate companies data against the companies schema. Raises ValidationError on failure."""
    schema = _load_schema("companies")
    validate(instance=data, schema=schema)


def validate_resume(data: dict) -> None:
    """Validate resume data against the resume schema. Raises ValidationError on failure."""
    schema = _load_schema("resume")
    validate(instance=data, schema=schema)


def validate_proposals(data: list) -> None:
    """Validate inbox proposals queue. Raises ValidationError on failure."""
    schema = _load_schema("proposals")
    validate(instance=data, schema=schema)


def validate_inbox_state(data: dict) -> None:
    """Validate inbox sync state. Raises ValidationError on failure."""
    schema = _load_schema("inbox_state")
    validate(instance=data, schema=schema)
