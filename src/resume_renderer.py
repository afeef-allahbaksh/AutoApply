import os
import platform
import subprocess
from pathlib import Path

# Ensure weasyprint can find Homebrew libs on macOS
if platform.system() == "Darwin":
    try:
        brew_prefix = subprocess.check_output(["brew", "--prefix"], text=True).strip()
        lib_path = os.path.join(brew_prefix, "lib")
        os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", lib_path)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

from weasyprint import HTML

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "config" / "resume_template"


def _render_contact(contact: dict) -> str:
    name = contact["name"]
    parts = []
    if contact.get("phone"):
        parts.append(contact["phone"])
    if contact.get("email"):
        parts.append(contact["email"])
    if contact.get("linkedin"):
        parts.append(f'<a href="https://linkedin.com/in/{contact["linkedin"]}">{contact["linkedin"]}</a>')
    if contact.get("github"):
        parts.append(f'<a href="https://github.com/{contact["github"]}">{contact["github"]}</a>')
    if contact.get("website"):
        parts.append(f'<a href="{contact["website"]}">{contact["website"]}</a>')
    separator = " | "
    return f'<div class="contact"><h1>{name}</h1><p>{separator.join(parts)}</p></div>'


def _render_summary(summary: str) -> str:
    return f'<div class="section"><h2>Summary</h2><p>{summary}</p></div>'


def _render_skills(skills: list) -> str:
    rows = []
    for group in skills:
        items = ", ".join(group["items"])
        rows.append(f'<p><strong>{group["category"]}:</strong> {items}</p>')
    return f'<div class="section"><h2>Skills</h2>{"".join(rows)}</div>'


def _render_experience(experience: list) -> str:
    entries = []
    for exp in experience:
        end = exp.get("end_date", "Present")
        location = exp.get("location", "")
        header = (
            f'<div class="entry-header">'
            f'<span class="org">{exp["company"]}</span>'
            f'<span class="location">{location}</span>'
            f'</div>'
            f'<div class="entry-subheader">'
            f'<span class="title">{exp["title"]}</span>'
            f'<span class="dates">{exp["start_date"]} – {end}</span>'
            f'</div>'
        )
        bullets = "".join(f"<li>{b}</li>" for b in exp["bullets"])
        entries.append(f'{header}<ul>{bullets}</ul>')
    return f'<div class="section"><h2>Experience</h2>{"".join(entries)}</div>'


def _render_projects(projects: list) -> str:
    entries = []
    for proj in projects:
        tech = f' | <em>{proj["technologies"]}</em>' if proj.get("technologies") else ""
        url = f' | <a href="{proj["url"]}">{proj["url"]}</a>' if proj.get("url") else ""
        header = f'<div class="entry-header"><span class="org">{proj["name"]}{tech}{url}</span></div>'
        bullets = "".join(f"<li>{b}</li>" for b in proj["bullets"])
        entries.append(f'{header}<ul>{bullets}</ul>')
    return f'<div class="section"><h2>Projects</h2>{"".join(entries)}</div>'


def _render_education(education: list) -> str:
    entries = []
    for edu in education:
        end = edu.get("end_date", "")
        start = edu.get("start_date", "")
        dates = f"{start} – {end}" if start else end
        degree = edu["degree"]
        if edu.get("field"):
            degree += f' in {edu["field"]}'
        if edu.get("gpa"):
            degree += f' (Cumulative GPA: {edu["gpa"]})'

        header = (
            f'<div class="entry-header">'
            f'<span class="org">{edu["institution"]}</span>'
            f'</div>'
            f'<div class="entry-subheader">'
            f'<span class="title">{degree}</span>'
            f'<span class="dates">{dates}</span>'
            f'</div>'
        )

        extra = ""
        if edu.get("coursework"):
            courses = ", ".join(edu["coursework"])
            extra += f'<p class="coursework"><strong>Relevant Coursework:</strong> {courses}</p>'
        if edu.get("highlights"):
            hl = "".join(f"<li>{h}</li>" for h in edu["highlights"])
            extra += f"<ul>{hl}</ul>"

        entries.append(f'{header}{extra}')
    return f'<div class="section"><h2>Education</h2>{"".join(entries)}</div>'


def _render_certifications(certifications: list) -> str:
    entries = []
    for cert in certifications:
        line = f'<strong>{cert["name"]}</strong>'
        if cert.get("issuer"):
            line += f' — {cert["issuer"]}'
        if cert.get("date"):
            line += f' ({cert["date"]})'
        entries.append(f"<p>{line}</p>")
    return f'<div class="section"><h2>Certifications</h2>{"".join(entries)}</div>'


def _render_custom(custom: list) -> str:
    sections = []
    for section in custom:
        items = "".join(f"<li>{c}</li>" for c in section["content"])
        sections.append(f'<div class="section"><h2>{section["heading"]}</h2><ul>{items}</ul></div>')
    return "".join(sections)


SECTION_RENDERERS = {
    "summary": lambda data: _render_summary(data.get("summary", "")),
    "skills": lambda data: _render_skills(data.get("skills", [])),
    "experience": lambda data: _render_experience(data.get("experience", [])),
    "projects": lambda data: _render_projects(data.get("projects", [])),
    "education": lambda data: _render_education(data.get("education", [])),
    "certifications": lambda data: _render_certifications(data.get("certifications", [])),
    "custom": lambda data: _render_custom(data.get("custom", [])),
}


def render_resume_html(resume_data: dict) -> str:
    """Convert resume.json data into a full HTML document."""
    css_path = TEMPLATE_DIR / "resume.css"
    css = css_path.read_text() if css_path.exists() else ""

    contact_html = _render_contact(resume_data["contact"])

    body_sections = []
    for section_name in resume_data.get("section_order", []):
        renderer = SECTION_RENDERERS.get(section_name)
        if renderer and resume_data.get(section_name):
            body_sections.append(renderer(resume_data))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{contact_html}{"".join(body_sections)}</body></html>"""


def render_resume_pdf(resume_data: dict, output_path: str) -> None:
    """Render resume.json to a PDF file."""
    html_str = render_resume_html(resume_data)
    HTML(string=html_str).write_pdf(output_path)
