"""Selectors + system-field map for Ashby application forms.

Ashby's iteration unit is the `[data-field-path]` container — one per
question. The path is either a known applicant field (`_systemfield_*`)
or a UUID for a custom question. Custom questions are routed by label-text
keyword match first, then Claude fallback.
"""

# Discovery — one container per question.
FIELD_ENTRY = "[data-field-path]"

# Known applicant-field paths. Stable across all Ashby boards.
SYSTEMFIELD_NAME = "_systemfield_name"
SYSTEMFIELD_EMAIL = "_systemfield_email"
SYSTEMFIELD_LOCATION = "_systemfield_location"
SYSTEMFIELD_RESUME = "_systemfield_resume"
SYSTEMFIELD_EEOC_GENDER = "_systemfield_eeoc_gender"
SYSTEMFIELD_EEOC_RACE = "_systemfield_eeoc_race"
SYSTEMFIELD_EEOC_VETERAN = "_systemfield_eeoc_veteran_status"
SYSTEMFIELD_EEOC_DISABILITY = "_systemfield_eeoc_disability_status"

# Posting-page CTA. Some Ashby boards inline the form; others gate it behind
# this button. Selector is narrow on purpose — bare "Apply" would risk
# matching the form's own "Submit Application" button on inlined-form boards.
APPLY_BUTTON = (
    'a:has-text("Apply for this Job"), '
    'a:has-text("Apply for this job"), '
    'button:has-text("Apply for this Job"), '
    'button:has-text("Apply for this job")'
)

# Form submit button.
SUBMIT_BUTTON = (
    'button[type="submit"]:has-text("Submit"), '
    'button:has-text("Submit Application"), '
    'button:has-text("Apply")'
)

# Label-keyword → (source, key) routing for UUID-id'd standard fields.
# `source` is one of: "profile", "responses", "resume_edu".
# `key` is the dict/attribute key inside that source.
# Order matters — more-specific keywords first.
LABEL_ROUTES = [
    (("phone",), "profile", "phone"),
    (("linkedin",), "profile", "linkedin"),
    (("github",), "profile", "github"),
    (("portfolio", "personal site", "personal website", "website"), "profile", "website"),
    (("graduation date", "grad date", "graduating", "expected graduation"),
     "resume_edu", "end_date"),
    (("school", "university", "college", "institution"),
     "resume_edu", "institution"),
]
