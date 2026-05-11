import hashlib
import json
import re

from src.api import create_message, strip_code_fences
from src.profile_loader import PROFILES_DIR
from src.resume.renderer import render_resume_pdf
from src.schemas import validate_resume


def _optimization_hash(resume: dict, job_content: str) -> str:
    """Deterministic hash of resume + job content for cache lookup."""
    blob = json.dumps(resume, sort_keys=True) + "\n---\n" + job_content
    return hashlib.sha256(blob.encode()).hexdigest()[:16]

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


OPTIMIZE_PROMPT = """You are an expert resume optimizer. Given a structured resume (JSON) and a job description, complete TWO phases:

PHASE 1 — KEYWORD EXTRACTION:
Extract 10-15 high-value keywords from the job description (technical skills, tools, frameworks, methodologies, domain terms). These are the terms an ATS will scan for.

PHASE 2 — RESUME OPTIMIZATION:
Tailor the resume to incorporate the extracted keywords naturally.

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

Return ONLY valid JSON in this exact format — no markdown fences, no commentary:
{{"keywords": ["keyword1", "keyword2", ...], "resume": {{<full optimized resume object>}}}}

Base resume:
{resume_json}

Job description:
{job_description}

Return the JSON:"""


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

    raw_json = strip_code_fences(message.content[0].text)

    try:
        result = json.loads(raw_json)
    except json.JSONDecodeError as e:
        print(f"  Warning: optimizer returned invalid JSON ({e}). Falling back to base resume.")
        return base_resume

    # New format: {"keywords": [...], "resume": {...}}
    # Old format fallback: flat resume dict with "contact" key
    if "keywords" in result and "resume" in result:
        keywords = result["keywords"]
        optimized = result["resume"]
        if keywords:
            print(f"  Keywords: {', '.join(keywords)}")
    else:
        optimized = result

    try:
        validate_resume(optimized)
    except Exception as e:
        print(f"  Warning: optimizer returned invalid resume schema ({e}). Falling back to base resume.")
        return base_resume

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

    raw = strip_code_fences(message.content[0].text)

    try:
        result = json.loads(raw)
        selected_names_list = result["selected"]
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"  Warning: project selector returned invalid response ({e}). Keeping current projects.")
        return {"projects": current_projects, "reasoning": [], "had_pool": True}

    selected_names = set(selected_names_list)

    # Build the projects list in selection order
    pool_by_name = {p["name"]: p for p in pool}
    selected_projects = [pool_by_name[name] for name in selected_names_list if name in pool_by_name]

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


# NOTE: batch_select_projects() and its prompt were deleted in Phase 29's
# CLI-retirement pass — they were only called by the CLI batch-apply path
# (apply_to_jobs in src/applicant.py, now gone). If multi-job batch optimize
# returns from the UI someday, mirror the existing batch_generate_outreach
# pattern in src/cold_email.py — it has better fallback semantics than the
# old function did.


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:50]


def find_cached_resume(profile_name: str, base_resume: dict, job_content: str, company: str) -> dict | None:
    """Check if a tailored resume already exists for this resume+job combo.

    Returns {"json": ..., "pdf": ...} if cached, None otherwise.
    """
    resumes_dir = PROFILES_DIR / profile_name / "resumes"
    if not resumes_dir.exists():
        return None

    target_hash = _optimization_hash(base_resume, job_content)
    name = base_resume.get("contact", {}).get("name", "resume")
    slug = f"{_slugify(name)}_{_slugify(company)}"
    json_path = resumes_dir / f"{slug}.json"
    pdf_path = resumes_dir / f"{slug}.pdf"

    if not json_path.exists() or not pdf_path.exists():
        return None

    with open(json_path) as f:
        cached = json.load(f)
    if cached.get("_optimization_hash") == target_hash:
        return {"json": str(json_path), "pdf": str(pdf_path)}
    return None


def save_tailored_resume(
    profile_name: str,
    optimized_resume: dict,
    company: str,
    role: str,
    optimization_hash: str = "",
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

    save_data = dict(optimized_resume)
    if optimization_hash:
        save_data["_optimization_hash"] = optimization_hash

    with open(json_path, "w") as f:
        json.dump(save_data, f, indent=2)
        f.write("\n")

    render_resume_pdf(optimized_resume, str(pdf_path))

    return {"json": str(json_path), "pdf": str(pdf_path)}
