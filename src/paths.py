"""Repo-relative path constants — single source of truth.

Lives in its own module to avoid cyclic imports between `profile_loader` and
`schemas` (both need these paths; `profile_loader` already depends on
`schemas` for validators, so `schemas` can't reverse-import).

Use these from anywhere that needs `config/` or `profiles/` paths — never
recompute them inline with `Path(__file__).parent.parent` (that pattern
breaks the moment a file moves into a subpackage, as Phase 34 demonstrated).
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

PROFILES_DIR = _REPO_ROOT / "profiles"
CONFIG_DIR = _REPO_ROOT / "config"  # jsonschemas, seed lists, resume_template/, example_profile/
