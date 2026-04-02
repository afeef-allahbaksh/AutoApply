import json
import os
import re
from datetime import date
from pathlib import Path

from src.api import create_message
from src.profile_loader import PROFILES_DIR
from src.resume_renderer import render_resume_pdf
from src.schemas import validate_resume

SELECT_PROJECTS_PROMPT = """You are an expert resume strategist. Given a pool of projects and a job description, select the {num_projects} most relevant projects for this specific role.

Rules:
1. Pick exactly {num_projects} projects — no more, no less
2. Choose projects whose technologies, domain, or demonstrated skills best match the job requirements
3. Consider both direct keyword matches AND transferable skills
4. For EVERY project in the pool, provide a one-sentence reason for selecting or skipping it

Return ONLY valid JSON in this exact format — no markdown fences, no commentary:
{{
  "selected": ["Project Name 1", "Project Name 2"],
  "reasoning": [
    {{"project": "Project Name", "selected": true, "reason": "One sentence why"}},
    ...
  ]
}}

Project pool:
{project_pool_json}

Job description:
{job_description}

Return the selection JSON:"""


OPTIMIZE_PROMPT = """You are an expert resume optimizer. Given a structured resume (JSON) and a job description, tailor the resume for this specific role.

Rules:
1. REWRITE bullet points to mirror the job description's language and keywords where truthful
2. REORDER skills within each category to surface the most relevant ones first
3. ADD relevant skills the candidate likely has based on their experience that appear in the JD (only if genuinely plausible)
4. PRESERVE all metrics, numbers, company names, dates, and factual claims exactly — never fabricate
5. PRESERVE the candidate's voice and tone — don't make it sound generic
6. PRESERVE section_order and contact info unchanged
7. DO NOT add or remove experience entries, project entries, or education entries
8. DO NOT change company names, job titles, dates, institution names, or degrees
9. Keep bullets concise — each bullet should be 1-2 lines max when rendered
10. Return ONLY the modified resume as valid JSON — no markdown fences, no commentary

Base resume:
{resume_json}

Job description:
{job_description}

Return the optimized resume JSON:"""


def optimize_resume(base_resume: dict, job_description: str) -> dict:
    """Use Claude to tailor a resume for a specific job description.

    Returns the optimized resume dict (validated against schema).
    """
    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": OPTIMIZE_PROMPT.format(
                resume_json=json.dumps(base_resume, indent=2),
                job_description=job_description,
            ),
        }],
    )

    raw_json = message.content[0].text.strip()
    if raw_json.startswith("```"):
        raw_json = raw_json.split("\n", 1)[1]
        raw_json = raw_json.rsplit("```", 1)[0]

    optimized = json.loads(raw_json)
    validate_resume(optimized)
    return optimized


def select_projects(base_resume: dict, job_description: str) -> dict:
    """Select the most relevant projects from project_pool for a job.

    Returns dict with:
      - "projects": list of selected project dicts (ready to inject into resume)
      - "reasoning": list of {project, selected, reason} for diff display
      - "had_pool": whether project_pool existed (False means no selection needed)
    """
    pool = base_resume.get("project_pool", [])
    current_projects = base_resume.get("projects", [])
    num_projects = len(current_projects)

    if not pool or len(pool) <= num_projects:
        return {"projects": current_projects, "reasoning": [], "had_pool": False}

    pool_json = json.dumps(
        [{"name": p["name"], "technologies": p.get("technologies", ""), "bullets": p["bullets"]} for p in pool],
        indent=2,
    )

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": SELECT_PROJECTS_PROMPT.format(
                num_projects=num_projects,
                project_pool_json=pool_json,
                job_description=job_description,
            ),
        }],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    result = json.loads(raw)
    selected_names = set(result["selected"])

    # Build the projects list in selection order
    pool_by_name = {p["name"]: p for p in pool}
    selected_projects = [pool_by_name[name] for name in result["selected"] if name in pool_by_name]

    # Fallback: if Claude returned wrong count, pad or trim
    if len(selected_projects) < num_projects:
        for p in pool:
            if p["name"] not in selected_names and len(selected_projects) < num_projects:
                selected_projects.append(p)
    selected_projects = selected_projects[:num_projects]

    return {
        "projects": selected_projects,
        "reasoning": result.get("reasoning", []),
        "had_pool": True,
    }


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:50]


def save_tailored_resume(
    profile_name: str,
    optimized_resume: dict,
    company: str,
    role: str,
) -> dict:
    """Save a tailored resume as JSON and PDF under the profile's resumes/ directory.

    Returns dict with paths: {"json": ..., "pdf": ...}
    """
    resumes_dir = PROFILES_DIR / profile_name / "resumes"
    resumes_dir.mkdir(exist_ok=True)

    name = optimized_resume.get("contact", {}).get("name", "resume")
    slug = f"{_slugify(name)}_{_slugify(company)}"

    json_path = resumes_dir / f"{slug}.json"
    pdf_path = resumes_dir / f"{slug}.pdf"

    with open(json_path, "w") as f:
        json.dump(optimized_resume, f, indent=2)
        f.write("\n")

    render_resume_pdf(optimized_resume, str(pdf_path))

    return {"json": str(json_path), "pdf": str(pdf_path)}
