"""Cold outreach routes — list, create, bulk import, generate, edit, status,
send, delete. Thin handler layer: every helper of substance lives in
`src/outreach/`. The route file's job is form-handling, lock acquisition,
template rendering, and HTTP-shaped error surfaces."""
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.inbox import auth as inbox_auth
from src.outreach import (
    STATUS_BADGE,
    STATUSES,
    batch_generate_outreach,
    clean_domain,
    generate_outreach,
    gmail_compose_url,
    load_outreach,
    now_iso,
    parse_csv_rows,
    save_outreach,
    send_email,
)
from src.profile_loader import PROFILES_DIR

from .. import state
from ..deps import template_context
from ..pipeline import load_jobs
from ..templates_loader import templates

router = APIRouter()


def _load_profile_data(profile_name: str) -> tuple[dict, dict | None]:
    """Return (profile.json, resume.json or None). Used to feed the generator.

    Stays in the route layer because it touches profile-level files
    (profile.json, resume.json) that are not outreach-specific — outreach
    is *consumer* of these, not owner.
    """
    import json
    profile_path = PROFILES_DIR / profile_name / "profile.json"
    resume_path = PROFILES_DIR / profile_name / "resume.json"
    profile_data = {}
    resume_data = None
    if profile_path.exists():
        with open(profile_path) as f:
            profile_data = json.load(f)
    if resume_path.exists():
        with open(resume_path) as f:
            resume_data = json.load(f)
    return profile_data, resume_data


def _load_companies(profile_name: str) -> list[dict]:
    """For the create-form's company autocomplete."""
    import json
    p = PROFILES_DIR / profile_name / "companies.json"
    if not p.exists():
        return []
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


_load_jobs = load_jobs


def _jobs_grouped_by_company(jobs: list[dict]) -> list[tuple[str, list[dict]]]:
    """Return [(company, [job, …]), …] sorted by company. Each job carries
    its `_idx` (position in jobs.json) so the form posts an integer that
    survives across reloads."""
    by_co: dict[str, list[dict]] = {}
    for i, j in enumerate(jobs):
        co = j.get("company") or "?"
        by_co.setdefault(co, []).append({**j, "_idx": i})
    return sorted(by_co.items(), key=lambda kv: kv[0].lower())


def _job_at(profile_name: str, idx) -> dict | None:
    """Fetch a job by index. Returns None on out-of-range or bad input so the
    generator can fall back to no-JD mode rather than crash."""
    if idx is None or idx == "":
        return None
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return None
    jobs = _load_jobs(profile_name)
    if not 0 <= i < len(jobs):
        return None
    return jobs[i]


def _decorate_record(profile_name: str, r: dict) -> dict:
    """Mutate-in-place: add gmail_url + linked_job_label for the template."""
    r["gmail_url"] = gmail_compose_url(r) if r.get("contact_email") else ""
    linked_job = _job_at(profile_name, r.get("linked_job_idx"))
    r["linked_job_label"] = (
        f"{linked_job.get('title', '?')} at {linked_job.get('company', '?')}"
        if linked_job else ""
    )
    return r


def _render_main(
    request: Request,
    profile_name: str,
    *,
    msg: str = "",
    err: str = "",
    open_id: str = "",
) -> HTMLResponse:
    records = load_outreach(profile_name) if profile_name else []
    companies = _load_companies(profile_name) if profile_name else []
    jobs = _load_jobs(profile_name) if profile_name else []
    jobs_by_company = _jobs_grouped_by_company(jobs)
    inbox_status = inbox_auth.status(profile_name) if profile_name else {"state": "not_configured", "email": None}
    records = sorted(records, key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
    for r in records:
        _decorate_record(profile_name, r)
    ungenerated_count = sum(1 for r in records if not r.get("draft_subject"))
    return templates.TemplateResponse(
        request, "_cold_email_main.html",
        {
            "request": request,
            "records": records,
            "companies": companies,
            "jobs_by_company": jobs_by_company,
            "inbox_status": inbox_status,
            "ungenerated_count": ungenerated_count,
            "statuses": STATUSES,
            "status_badge": STATUS_BADGE,
            "msg": msg,
            "err": err,
            "open_id": open_id,
        },
    )


@router.get("/cold-email")
def cold_email_page(request: Request, msg: str = "", err: str = "", open_id: str = ""):
    profile_name = state.active_profile()
    records = load_outreach(profile_name) if profile_name else []
    companies = _load_companies(profile_name) if profile_name else []
    jobs = _load_jobs(profile_name) if profile_name else []
    jobs_by_company = _jobs_grouped_by_company(jobs)
    inbox_status = inbox_auth.status(profile_name) if profile_name else {"state": "not_configured", "email": None}
    records = sorted(records, key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
    for r in records:
        _decorate_record(profile_name, r)
    ungenerated_count = sum(1 for r in records if not r.get("draft_subject"))
    return templates.TemplateResponse(
        request, "cold_email.html",
        template_context(
            request,
            page_title="Cold email",
            records=records,
            companies=companies,
            jobs_by_company=jobs_by_company,
            inbox_status=inbox_status,
            ungenerated_count=ungenerated_count,
            statuses=STATUSES,
            status_badge=STATUS_BADGE,
            msg=msg,
            err=err,
            open_id=open_id,
        ),
    )


@router.post("/cold-email")
def create_outreach(
    request: Request,
    company: str = Form(...),
    contact_name: str = Form(...),
    contact_email: str = Form(""),
    contact_title: str = Form(""),
    company_domain: str = Form(""),
    context_notes: str = Form(""),
    linked_job_idx: str = Form(""),
):
    profile_name = state.active_profile()
    company = company.strip()
    contact_name = contact_name.strip()
    if not company or not contact_name:
        return _render_main(request, profile_name, err="Company and contact name are required.")
    parsed_job_idx: int | None = None
    if linked_job_idx.strip():
        if _job_at(profile_name, linked_job_idx) is None:
            return _render_main(request, profile_name, err="Selected job no longer exists. Refresh the page and try again.")
        parsed_job_idx = int(linked_job_idx)
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        record = {
            "id": str(uuid.uuid4())[:12],
            "company": company,
            "company_domain": clean_domain(company_domain),
            "contact_name": contact_name,
            "contact_email": contact_email.strip(),
            "contact_title": contact_title.strip(),
            "context_notes": context_notes.strip(),
            "draft_subject": "",
            "draft_body": "",
            "status": "draft",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "sent_at": None,
            "linked_application_idx": None,
            "linked_job_idx": parsed_job_idx,
        }
        records.append(record)
        save_outreach(profile_name, records)
    return _render_main(request, profile_name, msg=f"Created outreach to {contact_name}.", open_id=record["id"])


@router.post("/cold-email/bulk-import")
def bulk_import(request: Request, csv_text: str = Form(...)):
    """Parse a pasted CSV/TSV and create one outreach record per row."""
    profile_name = state.active_profile()
    rows, parse_errors = parse_csv_rows(csv_text)
    if not rows:
        msg = "No rows imported. " + " ".join(parse_errors[:3]) if parse_errors else "No rows imported."
        return _render_main(request, profile_name, err=msg.strip())
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        now = now_iso()
        for row in rows:
            records.append({
                "id": str(uuid.uuid4())[:12],
                "company": row["company"],
                "company_domain": clean_domain(row["domain"]),
                "contact_name": row["name"],
                "contact_email": row["email"],
                "contact_title": row["title"],
                "context_notes": row["context"],
                "draft_subject": "",
                "draft_body": "",
                "status": "draft",
                "created_at": now,
                "updated_at": now,
                "sent_at": None,
                "linked_application_idx": None,
                "linked_job_idx": None,
            })
        save_outreach(profile_name, records)
    summary = f"Imported {len(rows)} outreach record{'s' if len(rows) != 1 else ''}."
    if parse_errors:
        summary += f" ({len(parse_errors)} row{'s' if len(parse_errors) != 1 else ''} skipped — see details.)"
    return _render_main(request, profile_name, msg=summary)


@router.post("/cold-email/generate-all")
def generate_all_drafts(request: Request):
    """Find every record with an empty draft_subject and batch-generate drafts
    for them in one (or a few chunked) Claude calls. Drafts already written
    by hand are left alone."""
    profile_name = state.active_profile()
    profile_data, resume_data = _load_profile_data(profile_name)
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        ungenerated_ids = [r["id"] for r in records if not r.get("draft_subject")]
        if not ungenerated_ids:
            return _render_main(request, profile_name, msg="No drafts to generate.")
        targets = []
        for r in records:
            if r["id"] not in ungenerated_ids:
                continue
            linked_job = _job_at(profile_name, r.get("linked_job_idx"))
            targets.append({
                "company": r.get("company", ""),
                "contact_name": r.get("contact_name", ""),
                "contact_title": r.get("contact_title", ""),
                "context_notes": r.get("context_notes", ""),
                "job_content": (linked_job or {}).get("content", "") if linked_job else "",
            })
        drafts = batch_generate_outreach(profile_data, resume_data, targets)
        for rid, draft in zip(ungenerated_ids, drafts):
            for r in records:
                if r["id"] == rid:
                    r["draft_subject"] = draft["subject"]
                    r["draft_body"] = draft["body"]
                    r["updated_at"] = now_iso()
                    break
        save_outreach(profile_name, records)
    return _render_main(
        request, profile_name,
        msg=f"Generated {len(ungenerated_ids)} draft{'s' if len(ungenerated_ids) != 1 else ''}.",
    )


@router.post("/cold-email/{rid}/generate")
def generate_draft(request: Request, rid: str):
    profile_name = state.active_profile()
    profile_data, resume_data = _load_profile_data(profile_name)
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        record = next((r for r in records if r["id"] == rid), None)
        if not record:
            raise HTTPException(status_code=404, detail="outreach record not found")
        # If the record links to a job, feed its content into the generator —
        # the JD is the single best context for personalizing outreach.
        linked_job = _job_at(profile_name, record.get("linked_job_idx"))
        job_content = (linked_job or {}).get("content", "") if linked_job else ""
        result = generate_outreach(
            profile_data=profile_data,
            resume_data=resume_data,
            company=record["company"],
            contact_name=record["contact_name"],
            contact_title=record.get("contact_title", ""),
            context_notes=record.get("context_notes", ""),
            job_content=job_content,
        )
        record["draft_subject"] = result["subject"]
        record["draft_body"] = result["body"]
        record["updated_at"] = now_iso()
        save_outreach(profile_name, records)
    msg = "Draft generated."
    if linked_job:
        msg += f" Used the JD for {linked_job.get('title')} as context."
    return _render_main(request, profile_name, msg=msg, open_id=rid)


@router.post("/cold-email/{rid}/edit")
def edit_outreach(
    request: Request,
    rid: str,
    contact_email: str = Form(""),
    contact_title: str = Form(""),
    company_domain: str = Form(""),
    context_notes: str = Form(""),
    draft_subject: str = Form(""),
    draft_body: str = Form(""),
    linked_job_idx: str = Form(""),
):
    profile_name = state.active_profile()
    new_link: int | None = None
    if linked_job_idx.strip():
        if _job_at(profile_name, linked_job_idx) is None:
            return _render_main(request, profile_name, err="Selected job no longer exists.", open_id=rid)
        new_link = int(linked_job_idx)
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        record = next((r for r in records if r["id"] == rid), None)
        if not record:
            raise HTTPException(status_code=404, detail="outreach record not found")
        record["contact_email"] = contact_email.strip()
        record["contact_title"] = contact_title.strip()
        record["company_domain"] = clean_domain(company_domain)
        record["context_notes"] = context_notes.strip()
        record["draft_subject"] = draft_subject.strip()
        record["draft_body"] = draft_body.strip()
        record["linked_job_idx"] = new_link
        record["updated_at"] = now_iso()
        save_outreach(profile_name, records)
    return _render_main(request, profile_name, msg="Saved.", open_id=rid)


@router.post("/cold-email/{rid}/status")
def change_status(request: Request, rid: str, status: str = Form(...)):
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        record = next((r for r in records if r["id"] == rid), None)
        if not record:
            raise HTTPException(status_code=404, detail="outreach record not found")
        record["status"] = status
        record["updated_at"] = now_iso()
        # Stamp sent_at the first time the record flips to sent
        if status == "sent" and not record.get("sent_at"):
            record["sent_at"] = now_iso()
        save_outreach(profile_name, records)
    return _render_main(request, profile_name, msg=f"Status updated to {status}.")


@router.post("/cold-email/{rid}/send")
def send_outreach(request: Request, rid: str):
    """Send the email directly via SMTP using the connected inbox account.
    On success, status flips to sent and sent_at is stamped."""
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        record = next((r for r in records if r["id"] == rid), None)
        if not record:
            raise HTTPException(status_code=404, detail="outreach record not found")
        ok, err = send_email(
            profile_name=profile_name,
            to=record.get("contact_email", ""),
            subject=record.get("draft_subject", ""),
            body=record.get("draft_body", ""),
        )
        if not ok:
            return _render_main(request, profile_name, err=err, open_id=rid)
        record["status"] = "sent"
        record["updated_at"] = now_iso()
        if not record.get("sent_at"):
            record["sent_at"] = now_iso()
        save_outreach(profile_name, records)
    return _render_main(
        request, profile_name,
        msg=f"Sent to {record.get('contact_name', '?')} at {record.get('contact_email', '?')}.",
        open_id=rid,
    )


@router.post("/cold-email/{rid}/delete")
def delete_outreach(request: Request, rid: str):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        records = load_outreach(profile_name)
        before = len(records)
        records = [r for r in records if r["id"] != rid]
        if len(records) == before:
            raise HTTPException(status_code=404, detail="outreach record not found")
        save_outreach(profile_name, records)
    return _render_main(request, profile_name, msg="Outreach deleted.")
