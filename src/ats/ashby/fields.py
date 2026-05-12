"""CSS selectors for Ashby application form fields.

Ashby uses a React Hook Form shell with `_systemfield_*` name attributes for
the standard applicant fields. Custom-question fields use auto-generated UUIDs
in their names — we match them by the presence of a label rather than an ID
prefix (unlike Greenhouse's `id^="question_"` pattern).
"""

# Standard applicant fields — Ashby's `_systemfield_*` convention is stable
# across boards.
NAME = 'input[name="_systemfield_name"], input[id*="name" i][type="text"]'
EMAIL = 'input[name="_systemfield_email"], input[type="email"]'
PHONE = 'input[name="_systemfield_phone"], input[type="tel"]'
LOCATION = 'input[name="_systemfield_location"], input[name*="location" i][type="text"]'
LINKEDIN = (
    'input[name="_systemfield_linkedin"], '
    'input[name*="linkedin" i], '
    'input[placeholder*="linkedin" i], '
    'input[aria-label*="LinkedIn" i]'
)

# File uploads — Ashby renders these as visible drop zones backed by a hidden file input.
RESUME_UPLOAD = (
    'input[type="file"][name="_systemfield_resume"], '
    'input[type="file"][accept*="pdf"], '
    'input[type="file"]'
)

# Form-level
SUBMIT_BUTTON = (
    'button[type="submit"]:has-text("Submit"), '
    'button:has-text("Submit Application"), '
    'button:has-text("Apply")'
)

# Custom-question container — Ashby renders each question inside a wrapper
# with a label. The shape is generally:
#   <label for="...">Question text</label>
#   <input | select | textarea ... />
# We discover them by walking all input/textarea/select elements that aren't
# in the standard `_systemfield_*` set and aren't file uploads.
SYSTEMFIELD_NAMES = {
    "_systemfield_name", "_systemfield_email", "_systemfield_phone",
    "_systemfield_location", "_systemfield_linkedin", "_systemfield_resume",
}
