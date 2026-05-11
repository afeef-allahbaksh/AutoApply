"""ATS-specific form-filling subpackages.

Each ATS lives in its own module (or subpackage) — `greenhouse/` is a package
because the Greenhouse form code grew large enough to warrant submodules
(fields, selectors, dates, education, demographics, custom_questions,
application). Lever is a single file. Anything genuinely cross-ATS lives at
this level (e.g. `applicant_context.py`).

Public entry points:
  - `src.ats.greenhouse.fill_greenhouse_application(...)`
  - `src.ats.lever.fill_lever_application(...)`
"""
