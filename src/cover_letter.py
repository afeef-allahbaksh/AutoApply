from src.api import create_message

COVER_LETTER_PROMPT = """Write a concise cover letter for this job application.

Applicant:
Name: {name}
Location: {location}
Background: {background}

Job:
Company: {company}
Role: {role}
Description: {job_content}

Rules:
1. Keep it to 3 short paragraphs (opening, body, closing)
2. Mirror language from the job description naturally
3. Highlight 2-3 specific achievements from the applicant's background that are most relevant
4. Include concrete metrics where available
5. Sound genuine — not templated or overly formal
6. Do NOT use "I am excited to apply" or similar cliches
7. Do NOT include addresses, dates, or "Dear Hiring Manager" — just the letter body
8. Return ONLY the cover letter text, no labels or commentary"""


def generate_cover_letter(
    profile_data: dict,
    resume_data: dict,
    company: str,
    role: str,
    job_content: str,
) -> str:
    """Generate a tailored cover letter using Claude."""
    # Build a brief background summary from resume
    background_parts = []
    for exp in resume_data.get("experience", [])[:3]:
        bullets = "; ".join(b[:120] for b in exp.get("bullets", [])[:2])
        background_parts.append(f"{exp['title']} at {exp['company']}: {bullets}")
    for proj in resume_data.get("projects", [])[:2]:
        tech = f" ({proj['technologies']})" if proj.get("technologies") else ""
        background_parts.append(f"Project: {proj['name']}{tech}")

    background = "\n".join(background_parts)

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": COVER_LETTER_PROMPT.format(
                name=profile_data.get("name", ""),
                location=profile_data.get("location", ""),
                background=background,
                company=company,
                role=role,
                job_content=job_content[:3000],
            ),
        }],
    )
    return message.content[0].text.strip()
