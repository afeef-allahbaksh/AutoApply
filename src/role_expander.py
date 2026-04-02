import json

from src.api import create_message

EXPAND_PROMPT = """Given these target job roles, generate a comprehensive list of related job title keywords that a job seeker should also match against.

Target roles: {roles}
Experience levels: {levels}

Rules:
1. Include common variations, abbreviations, and synonyms (e.g. SWE, SDE, Software Developer)
2. Include level-prefixed versions (e.g. Junior, Entry-Level, New Grad, Associate, Early Career)
3. Include specialization variants (e.g. Backend Engineer, Full Stack Engineer, Platform Engineer)
4. Keep it to real job titles that appear on Greenhouse/Lever job boards
5. Do NOT include unrelated roles (e.g. don't include "Data Scientist" for a software engineer)
6. Return ONLY a JSON array of strings, no commentary, no markdown fences

Example input: ["Software Engineer"]
Example output: ["Software Engineer", "SWE", "SDE", "Software Developer", "Backend Engineer", "Frontend Engineer", "Full Stack Engineer", "Fullstack Engineer", "Platform Engineer", "Systems Engineer", "Application Engineer", "Junior Software Engineer", "New Grad Software Engineer", "Entry Level Software Engineer", "Associate Software Engineer"]"""


def expand_roles(roles: list[str], experience_levels: list[str]) -> list[str]:
    """Use Claude to expand role titles into comprehensive keyword list.

    One-time call during setup — results are stored in profile.json.
    """
    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": EXPAND_PROMPT.format(
                roles=", ".join(roles),
                levels=", ".join(experience_levels),
            ),
        }],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    expanded = json.loads(raw)

    # Ensure the original roles are included
    for role in roles:
        if role not in expanded:
            expanded.append(role)

    return expanded
