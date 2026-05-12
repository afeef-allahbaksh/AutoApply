"""CSS selectors for Greenhouse application form fields.

Greenhouse-hosted forms vary a lot in their HTML — some use canonical names
(`name="first_name"`), some use IDs (`#first_name`), some use legacy patterns
(`id*="phone"`). Each selector here is a comma-separated list of fallbacks
tried in order by `page.locator()`.
"""

# Standard applicant fields
FIRST_NAME = 'input[name="first_name"], #first_name'
LAST_NAME = 'input[name="last_name"], #last_name'
PREFERRED_NAME = 'input[name="preferred_name"], #preferred_name, input[id*="preferred" i][id*="name" i]'
CANDIDATE_LOCATION = '#candidate-location, input[id*="candidate-location" i]'
EMAIL = 'input[name="email"], #email'
PHONE = 'input[name="phone"], #phone, input[id*="phone" i]'
PHONE_COUNTRY = 'select[name="phone_country_code"], select[id*="phone" i][id*="country" i]'
LOCATION = 'input[name="location"], #location, input[autocomplete="address-level2"]'
LINKEDIN = 'input[autocomplete="custom-question-linkedin-profile"], input[name*="linkedin" i], input[id*="linkedin" i], input[placeholder*="linkedin" i], input[aria-label*="LinkedIn" i]'

# File uploads
RESUME_UPLOAD = 'input[type="file"][name="resume"]'
COVER_LETTER_UPLOAD = 'input[type="file"][name="cover_letter"]'

# Form-level
SUBMIT_BUTTON = 'button:has-text("Submit")'

# Custom-question container — Greenhouse uses question_ prefix, others use custom_
CUSTOM_QUESTION = '[id^="question_"], [id^="custom_"], [data-field-type="custom"]'

# Education section — Greenhouse uses various naming:
# education[][school_name_id], job_application[education][][school_name_id], etc.
EDU_SCHOOL = 'input[name*="school_name"], input[id*="school" i][type="text"], input[name*="education"][name*="school"]'
EDU_DEGREE = 'select[name*="degree"], select[id*="degree" i], input[id^="degree-"], input[name*="degree"]'
EDU_DISCIPLINE = 'input[name*="discipline"], input[id*="discipline" i], select[name*="discipline"]'

# Education dates — can be <select> or <input type="number"> depending on the form
EDU_START_MONTH = 'select[id^="start-month-"], input[id^="start-month-"], select[name*="start_month"], input[name*="start_month"]'
EDU_START_YEAR = 'select[id^="start-year-"], input[id^="start-year-"], select[name*="start_year"], input[name*="start_year"]'
EDU_END_MONTH = 'select[id^="end-month-"], input[id^="end-month-"], select[name*="end_month"], input[name*="end_month"]'
EDU_END_YEAR = 'select[id^="end-year-"], input[id^="end-year-"], select[name*="end_year"], input[name*="end_year"]'
