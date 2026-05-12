"""HTML-stripping + requirements-section extraction.

The classify + fit-score pass want a compact snippet that emphasizes
qualifications/requirements over marketing copy, so the LLM has enough signal
to judge level fit without paying for the whole job description.
"""
import html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_YEARS_RE = re.compile(r"\d+\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_REQUIREMENTS_HEADERS = re.compile(
    r"(?:qualifications?|requirements?|who you are|what you.?ll need|what we.?re looking for|minimum qualifications?|must have|years of.{0,20}experience)",
    re.IGNORECASE,
)


def extract_job_snippet(content: str, max_len: int = 500) -> str:
    """Extract the most relevant snippet from a job description.

    Prioritizes requirements/qualifications sections and years-of-experience
    mentions over intro marketing copy.
    """
    clean = html.unescape(content)
    clean = _HTML_TAG_RE.sub(" ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""

    match = _REQUIREMENTS_HEADERS.search(clean)
    if match:
        section = clean[match.start():match.start() + max_len]
        return section + "..." if len(clean) > match.start() + max_len else section

    years_match = _YEARS_RE.search(clean)
    if years_match:
        start = max(0, years_match.start() - 100)
        section = clean[start:start + max_len]
        return section + "..." if len(clean) > start + max_len else section

    return clean[:max_len] + ("..." if len(clean) > max_len else "")
