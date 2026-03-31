import json
import os
import re
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.resume_renderer import render_resume_pdf
from src.schemas import validate_resume

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

load_dotenv()

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
    client = anthropic.Anthropic()
    message = client.messages.create(
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

    slug = f"{_slugify(company)}_{_slugify(role)}_{date.today().isoformat()}"

    json_path = resumes_dir / f"{slug}.json"
    pdf_path = resumes_dir / f"{slug}.pdf"

    with open(json_path, "w") as f:
        json.dump(optimized_resume, f, indent=2)
        f.write("\n")

    render_resume_pdf(optimized_resume, str(pdf_path))

    return {"json": str(json_path), "pdf": str(pdf_path)}
