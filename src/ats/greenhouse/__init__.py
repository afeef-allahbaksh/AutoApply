"""Greenhouse ATS form-filling subpackage.

External callers should import `fill_greenhouse_application` from here:

    from src.ats.greenhouse import fill_greenhouse_application

Submodules:
  - fields: CSS selector constants for every field
  - selectors: generic fill/upload + fuzzy-match helpers
  - dates: month/year parsing + filling
  - location: Google Places autocomplete handling
  - education: structured education section
  - demographics: EEO / "decline to self-identify" defaults
  - custom_questions: Claude-backed answers for free-text + dropdown questions
  - application: top-level orchestrator (fill_greenhouse_application)
"""
from .application import fill_greenhouse_application

__all__ = ["fill_greenhouse_application"]
