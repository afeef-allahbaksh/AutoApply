"""Ashby ATS form-filling subpackage.

External callers should import `fill_ashby_application` from here:

    from src.ats.ashby import fill_ashby_application

Submodules:
  - fields: CSS selector constants for every standard field
  - selectors: generic fill/upload helpers (reusable Playwright primitives)
  - custom_questions: Claude-backed answers for free-text + dropdown questions
  - application: top-level orchestrator (fill_ashby_application)

Ashby's form HTML is more standardized than Greenhouse's (which varies wildly
by company config) — Ashby uses a React-Hook-Form-based shell with predictable
`_systemfield_*` name attributes for standard fields. Custom questions vary
per company but follow consistent shape patterns.
"""
from .application import fill_ashby_application

__all__ = ["fill_ashby_application"]
