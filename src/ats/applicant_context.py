"""Profile + resume → context string for Claude prompts.

Shared between Greenhouse and Lever custom-question handlers — the same
applicant data renders identically regardless of which ATS we're answering on.
"""


def build_applicant_context(profile_data: dict, resume_data: dict | None, responses: dict) -> str:
    """Build a comprehensive context string from all available applicant data."""
    sections = []

    sections.append(f"Name: {profile_data.get('name', '')}")
    sections.append(f"Email: {profile_data.get('email', '')}")
    sections.append(f"Phone: {profile_data.get('phone', '')}")
    sections.append(f"Location: {profile_data.get('location', '')}")
    if profile_data.get("zip_code"):
        sections.append(f"Zip Code: {profile_data['zip_code']}")
    if profile_data.get("linkedin"):
        linkedin = profile_data["linkedin"]
        full = linkedin if linkedin.startswith("http") else f"https://linkedin.com/in/{linkedin}"
        sections.append(f"LinkedIn: {full}")
    if profile_data.get("github"):
        github = profile_data["github"]
        full = github if github.startswith("http") else f"https://github.com/{github}"
        sections.append(f"GitHub: {full}")
    if profile_data.get("website"):
        sections.append(f"Website: {profile_data['website']}")

    if resume_data and resume_data.get("education"):
        for edu in resume_data["education"]:
            parts = [f"University: {edu.get('institution', '')}"]
            if edu.get("degree"):
                parts.append(f"Degree: {edu['degree']}")
            if edu.get("field"):
                parts.append(f"Major/Field: {edu['field']}")
            if edu.get("gpa"):
                parts.append(f"GPA: {edu['gpa']}")
            if edu.get("end_date"):
                parts.append(f"Graduation: {edu['end_date']}")
            if edu.get("start_date"):
                parts.append(f"Start: {edu['start_date']}")
            if edu.get("coursework"):
                parts.append(f"Coursework: {', '.join(edu['coursework'])}")
            sections.append("\n".join(parts))

    if resume_data and resume_data.get("experience"):
        exp_lines = []
        for exp in resume_data["experience"]:
            exp_lines.append(f"- {exp['title']} at {exp['company']} ({exp.get('start_date', '')} - {exp.get('end_date', 'Present')})")
        sections.append("Experience:\n" + "\n".join(exp_lines))

    if resume_data and resume_data.get("skills"):
        for skill_group in resume_data["skills"]:
            sections.append(f"{skill_group['category']}: {', '.join(skill_group['items'])}")

    if responses:
        resp_lines = [f"  {k}: {v}" for k, v in responses.items()]
        sections.append("Pre-set responses:\n" + "\n".join(resp_lines))

    return "\n\n".join(sections)
