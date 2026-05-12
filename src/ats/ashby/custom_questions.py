"""Claude prompts tuned to Ashby's widget shapes.

Four entry points, each shaped for one widget family:
  - `answer_text` — free-text answers (text input, textarea, datepicker fallback)
  - `pick_radio` — choose exactly one option from a list
  - `pick_checkboxes` — choose one (single-select) or several (multi-select) options
  - `answer_yesno` — Yes/No toggle and boolean confirmation checkboxes

All four call through `src.api.create_message` (shared singleton + retry).
"""
import json

from src.api import create_message, strip_code_fences
from src.ats.applicant_context import build_applicant_context


def answer_text(question: str, profile_data: dict, resume_data: dict | None,
                responses: dict, job_content: str) -> str:
    """Free-text answer. Returns "" for SKIP."""
    context = build_applicant_context(profile_data, resume_data, responses or {})
    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": f"""Answer this job application question using the applicant's real data.

Question: {question}

Applicant data:
{context}

Job description:
{job_content[:2000]}

Rules:
- BE CONCISE. Shortest accurate answer possible.
- For dates / yes-no / factual / URL questions: only the value, no preamble.
- For open-ended: 1-2 sentences MAX.
- Use EXACT data from the applicant profile — never fabricate.
- Avoid AI tells: "excited", "passionate", "this aligns perfectly".
- If you lack the data or it isn't a real question, return ONLY SKIP.
- Return ONLY the answer text, no quotes or labels."""}],
    )
    result = message.content[0].text.strip()
    return "" if result.upper() == "SKIP" else result


def pick_radio(question: str, options: list[str], profile_data: dict,
               resume_data: dict | None, responses: dict, job_content: str) -> str:
    """Choose exactly one option. Returns the option label verbatim."""
    context = build_applicant_context(profile_data, resume_data, responses or {})
    options_str = "\n".join(f"- {opt}" for opt in options)
    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        messages=[{"role": "user", "content": f"""Pick the best option for this radio-button question.

Question: {question}

Available options:
{options_str}

Applicant data:
{context}

Job description (excerpt):
{job_content[:1000]}

Rules:
- Return EXACTLY ONE option, copied verbatim from the list above.
- Pick the option best matching the applicant's data.
- If unsure, pick the most neutral / safe option.
- Return ONLY the option text — no quotes, no labels, no explanation."""}],
    )
    return message.content[0].text.strip()


def pick_checkboxes(question: str, options: list[str], multi: bool,
                    profile_data: dict, resume_data: dict | None,
                    responses: dict, job_content: str) -> list[str]:
    """Pick one option (multi=False) or several (multi=True). Returns the
    list of option labels."""
    context = build_applicant_context(profile_data, resume_data, responses or {})
    options_str = "\n".join(f"- {opt}" for opt in options)
    instruction = (
        "Pick one or more options that apply. Return a JSON array of strings."
        if multi else
        "Pick EXACTLY ONE option. Return a JSON array with one string."
    )
    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": f"""Pick the best option(s) for this checkbox question.

Question: {question}

Available options:
{options_str}

Applicant data:
{context}

Job description (excerpt):
{job_content[:1000]}

{instruction}
- Each option must be copied verbatim from the list above.
- Return ONLY a JSON array — e.g. ["LinkedIn"] or ["Product", "Platform"].
- No commentary outside the array."""}],
    )
    raw = strip_code_fences(message.content[0].text.strip())
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return []


def answer_yesno(question: str, profile_data: dict, resume_data: dict | None,
                 responses: dict, job_content: str) -> bool:
    """Return True for Yes, False for No."""
    context = build_applicant_context(profile_data, resume_data, responses or {})
    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=20,
        messages=[{"role": "user", "content": f"""Answer this yes/no application question.

Question: {question}

Applicant data:
{context}

Job description (excerpt):
{job_content[:1000]}

Rules:
- Return EXACTLY "Yes" or "No" — nothing else.
- For sponsorship questions, match the applicant's visa_sponsorship value.
- For work-authorization questions, match work_authorization.
- For relocation / in-person / work-policy questions, assume Yes unless contradicted by profile data."""}],
    )
    return message.content[0].text.strip().lower().startswith("y")
