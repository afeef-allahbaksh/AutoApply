import json
from pathlib import Path

import pdfplumber

from src.api import create_message
from src.schemas import validate_resume

RESUME_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "resume_schema.json"

PARSE_PROMPT = """You are a resume parser. Given raw text extracted from a PDF resume, convert it into a structured JSON object.

Here is the JSON schema to follow:
{schema}

Rules:
- Only `contact` and `section_order` are required. Include other sections only if they exist in the resume.
- `section_order` must list the sections in the exact order they appear in the resume.
- For `skills`: split each category line into a category name and an array of individual skill strings.
- For `experience`: each entry needs company, title, location, start_date, end_date, and bullets. If the title looks like a team/platform name rather than a job title, still use it as the title field.
- For `projects`: extract the project name, technologies (the part after "|"), and bullet points.
- For `education`: extract institution, degree, field, GPA, dates, coursework (from "Relevant Coursework" lines), and any highlights.
- Preserve all metrics, numbers, and specific details exactly as written. Do not paraphrase or summarize bullets.
- Join bullet points that wrap across multiple lines into single strings.
- For contact info: extract name, email, phone, and any LinkedIn/GitHub/website handles.
- Return ONLY valid JSON, no markdown fences, no commentary.

Resume text:
{resume_text}"""


def parse_pdf_to_resume(pdf_path: str) -> dict:
    """Parse a PDF resume into structured resume.json format using Claude."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    if not text.strip():
        raise ValueError(f"No text extracted from {pdf_path}")

    with open(RESUME_SCHEMA_PATH) as f:
        schema = f.read()

    message = create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": PARSE_PROMPT.format(schema=schema, resume_text=text),
        }],
    )

    raw_json = message.content[0].text.strip()
    # Strip markdown fences if present
    if raw_json.startswith("```"):
        raw_json = raw_json.split("\n", 1)[1]
        raw_json = raw_json.rsplit("```", 1)[0]

    resume_data = json.loads(raw_json)
    validate_resume(resume_data)
    return resume_data
