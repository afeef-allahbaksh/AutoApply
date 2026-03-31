import json
from pathlib import Path
from jsonschema import validate, ValidationError

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_schema(name: str) -> dict:
    schema_path = SCHEMA_DIR / f"{name}_schema.json"
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
