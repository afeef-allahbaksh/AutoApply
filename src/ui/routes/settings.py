import json
import os
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from src.inbox import auth as inbox_auth
from src.inbox.auth import ImapCredentials
from src.profile_loader import PROFILES_DIR, _atomic_write_json
from src.resume.parser import parse_pdf_to_resume
from src.role_expander import expand_roles
from src.schemas import validate_profile, validate_responses, validate_resume

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10MB — keeps a malicious upload from blowing memory


def _read_profile(profile_name: str) -> dict:
    path = PROFILES_DIR / profile_name / "profile.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"profile.json not found for {profile_name}")
    with open(path) as f:
        return json.load(f)


def _resume_info(profile_name: str) -> dict:
    """Compact summary of the profile's resume.json for the Settings card."""
    empty = {"exists": False, "sections": [], "project_count": 0, "project_pool_names": []}
    if not profile_name:
        return empty
    path = PROFILES_DIR / profile_name / "resume.json"
    if not path.exists():
        return empty
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return empty
    pool = data.get("project_pool") or data.get("projects") or []
    return {
        "exists": True,
        "sections": data.get("section_order", []),
        "project_count": len(pool),
        "project_pool_names": [p.get("name", "?") for p in pool],
    }


def _parse_pdf_bytes(pdf_bytes: bytes) -> dict:
    """Write the uploaded bytes to a temp file and run the existing PDF parser.
    Cleans up the temp file regardless of parser outcome."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        return parse_pdf_to_resume(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _import_resume_from_bytes(profile_name: str, pdf_bytes: bytes) -> dict:
    """Parse PDF bytes and overwrite resume.json. Returns the parsed dict.
    Raises ValueError for size limits and propagates parser exceptions.
    Writes are atomic via `_atomic_write_json` — works even before profile.json
    exists (resume import is sometimes the first thing done in setup)."""
    if len(pdf_bytes) == 0:
        raise ValueError("Empty file.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError(f"File too large (>{MAX_PDF_BYTES // (1024 * 1024)}MB).")
    resume_data = _parse_pdf_bytes(pdf_bytes)
    _atomic_write_json(
        PROFILES_DIR / profile_name / "resume.json",
        resume_data,
        validate_resume,
    )
    return resume_data


def _add_projects_from_bytes(profile_name: str, pdf_bytes: bytes) -> dict:
    """Parse a PDF and merge its projects into the existing project_pool.
    Returns {"added": n, "total": N, "added_names": [...], "skipped_names": [...]}.
    Raises if resume.json doesn't exist."""
    if len(pdf_bytes) == 0:
        raise ValueError("Empty file.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError(f"File too large (>{MAX_PDF_BYTES // (1024 * 1024)}MB).")

    resume_path = PROFILES_DIR / profile_name / "resume.json"
    if not resume_path.exists():
        raise FileNotFoundError("No resume.json — import a full resume first.")
    with open(resume_path) as f:
        resume_data = json.load(f)

    base_projects = resume_data.get("projects", [])
    pool = list(resume_data.get("project_pool", base_projects))
    seen_names = {p["name"].lower() for p in pool}

    extra = _parse_pdf_bytes(pdf_bytes)
    extra_projects = extra.get("projects", [])
    added_names = []
    skipped_names = []
    for p in extra_projects:
        if p["name"].lower() not in seen_names:
            pool.append(p)
            seen_names.add(p["name"].lower())
            added_names.append(p["name"])
        else:
            skipped_names.append(p["name"])

    if added_names:
        resume_data["project_pool"] = pool
        _atomic_write_json(resume_path, resume_data, validate_resume)
    return {
        "added": len(added_names),
        "total": len(pool),
        "added_names": added_names,
        "skipped_names": skipped_names,
    }


def _write_profile(profile_name: str, data: dict) -> None:
    """Atomic + validated write to profile.json."""
    _atomic_write_json(
        PROFILES_DIR / profile_name / "profile.json",
        data,
        validate_profile,
    )


def _read_responses(profile_name: str) -> dict:
    path = PROFILES_DIR / profile_name / "responses.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _write_responses(profile_name: str, data: dict) -> None:
    """Atomic + validated write to responses.json."""
    _atomic_write_json(
        PROFILES_DIR / profile_name / "responses.json",
        data,
        validate_responses,
    )


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _settings_response(
    request: Request,
    profile_name: str,
    profile_data: dict | None = None,
    responses_data: dict | None = None,
    *,
    profile_msg: str = "",
    profile_err: str = "",
    responses_msg: str = "",
    responses_err: str = "",
    inbox_msg: str = "",
    inbox_err: str = "",
    resume_msg: str = "",
    resume_err: str = "",
):
    if profile_data is None:
        profile_data = _read_profile(profile_name) if profile_name else {}
    if responses_data is None:
        responses_data = _read_responses(profile_name) if profile_name else {}
    inbox_status = inbox_auth.status(profile_name) if profile_name else {"state": "not_configured", "email": None}
    return templates.TemplateResponse(
        request, "settings.html",
        template_context(
            request,
            page_title="Settings",
            profile=profile_data,
            responses=responses_data,
            profile_msg=profile_msg,
            profile_err=profile_err,
            responses_msg=responses_msg,
            responses_err=responses_err,
            inbox=inbox_status,
            inbox_msg=inbox_msg,
            inbox_err=inbox_err,
            default_imap_server=inbox_auth.DEFAULT_IMAP_SERVER,
            default_imap_port=inbox_auth.DEFAULT_IMAP_PORT,
            resume_info=_resume_info(profile_name),
            resume_msg=resume_msg,
            resume_err=resume_err,
        ),
    )


@router.get("/settings")
def settings_page(
    request: Request,
    profile_msg: str = "",
    profile_err: str = "",
    responses_msg: str = "",
    responses_err: str = "",
    inbox_msg: str = "",
    inbox_err: str = "",
    resume_msg: str = "",
    resume_err: str = "",
):
    return _settings_response(
        request, state.active_profile(),
        profile_msg=profile_msg, profile_err=profile_err,
        responses_msg=responses_msg, responses_err=responses_err,
        inbox_msg=inbox_msg, inbox_err=inbox_err,
        resume_msg=resume_msg, resume_err=resume_err,
    )


@router.post("/settings/inbox/save")
def save_inbox_credentials(
    email: str = Form(...),
    password: str = Form(...),
    server: str = Form(""),
    port: str = Form(""),
):
    profile_name = state.active_profile()
    email = email.strip()
    password = password.strip()
    if not email or not password:
        return RedirectResponse(url="/settings?inbox_err=Email+and+password+are+required.", status_code=303)
    server = server.strip() or inbox_auth.DEFAULT_IMAP_SERVER
    try:
        port_int = int(port.strip()) if port.strip() else inbox_auth.DEFAULT_IMAP_PORT
    except ValueError:
        return RedirectResponse(url="/settings?inbox_err=Port+must+be+a+number.", status_code=303)

    creds = ImapCredentials(email=email, password=password, server=server, port=port_int)
    ok, err = inbox_auth.verify_credentials(creds)
    if not ok:
        return RedirectResponse(url=f"/settings?inbox_err={err[:160].replace(' ', '+')}", status_code=303)
    inbox_auth.save_credentials(profile_name, creds)
    return RedirectResponse(url=f"/settings?inbox_msg=Connected+as+{email}", status_code=303)


@router.post("/settings/inbox/disconnect")
def disconnect_inbox():
    profile_name = state.active_profile()
    inbox_auth.disconnect(profile_name)
    return RedirectResponse(url="/settings?inbox_msg=Disconnected.+Credentials+deleted.", status_code=303)


def _urlencode_msg(msg: str) -> str:
    """RedirectResponse query params don't support spaces; encode the message."""
    from urllib.parse import quote
    return quote(msg, safe="")


@router.post("/settings/resume/import")
async def import_resume(file: UploadFile = File(...)):
    profile_name = state.active_profile()
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return RedirectResponse(
            url=f"/settings?resume_err={_urlencode_msg('Must be a .pdf file.')}",
            status_code=303,
        )
    contents = await file.read()
    lock = state.profile_lock(profile_name)
    with lock:
        try:
            resume_data = _import_resume_from_bytes(profile_name, contents)
        except ValueError as e:
            return RedirectResponse(
                url=f"/settings?resume_err={_urlencode_msg(str(e))}",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/settings?resume_err={_urlencode_msg(f'Parse failed: {str(e)[:160]}')}",
                status_code=303,
            )
    sections = resume_data.get("section_order", [])
    proj_count = len(resume_data.get("projects", []))
    msg = (
        f"Resume imported · sections: {', '.join(sections)} · {proj_count} project(s). "
        "Add more project PDFs below to grow the pool."
    )
    return RedirectResponse(url=f"/settings?resume_msg={_urlencode_msg(msg)}", status_code=303)


@router.post("/settings/resume/projects/add")
async def add_projects(file: UploadFile = File(...)):
    profile_name = state.active_profile()
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return RedirectResponse(
            url=f"/settings?resume_err={_urlencode_msg('Must be a .pdf file.')}",
            status_code=303,
        )
    contents = await file.read()
    lock = state.profile_lock(profile_name)
    with lock:
        try:
            result = _add_projects_from_bytes(profile_name, contents)
        except FileNotFoundError as e:
            return RedirectResponse(
                url=f"/settings?resume_err={_urlencode_msg(str(e))}",
                status_code=303,
            )
        except ValueError as e:
            return RedirectResponse(
                url=f"/settings?resume_err={_urlencode_msg(str(e))}",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/settings?resume_err={_urlencode_msg(f'Parse failed: {str(e)[:160]}')}",
                status_code=303,
            )
    if result["added"] == 0:
        msg = "No new projects found — all names already in pool."
    else:
        msg = (
            f"Added {result['added']} project(s): {', '.join(result['added_names'])}. "
            f"Pool now has {result['total']} total."
        )
    return RedirectResponse(url=f"/settings?resume_msg={_urlencode_msg(msg)}", status_code=303)


@router.post("/settings/profile")
def save_profile(
    request: Request,
    roles: str = Form(""),
    expand: str = Form(""),
    experience_levels: str = Form(""),
    locations: str = Form(""),
    salary_min: str = Form(""),
    industries: str = Form(""),
    auto_submit: str = Form(""),
    rate_limit_seconds: str = Form(""),
):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        data = _read_profile(profile_name)
        prefs = data.setdefault("job_preferences", {})

        roles_list = _split_csv(roles)
        levels_list = _split_csv(experience_levels)
        locations_list = _split_csv(locations)

        if roles_list:
            if expand:
                try:
                    roles_list = expand_roles(roles_list, levels_list or ["Junior"])
                except Exception as e:
                    return _settings_response(
                        request, profile_name, data,
                        profile_err=f"Role expansion failed: {e}",
                    )
            prefs["roles"] = roles_list

        if levels_list:
            prefs["experience_levels"] = levels_list
        if locations_list:
            prefs["locations"] = locations_list
        if salary_min.strip():
            try:
                prefs["salary_min"] = int(salary_min)
            except ValueError:
                return _settings_response(
                    request, profile_name, data,
                    profile_err=f"salary_min must be a number, got {salary_min!r}.",
                )
        elif "salary_min" in prefs:
            prefs.pop("salary_min")
        industries_list = _split_csv(industries)
        if industries_list:
            prefs["industries"] = industries_list
        elif "industries" in prefs:
            prefs.pop("industries")

        settings = data.setdefault("settings", {})
        settings["auto_submit"] = bool(auto_submit)
        if rate_limit_seconds.strip():
            try:
                settings["rate_limit_seconds"] = int(rate_limit_seconds)
            except ValueError:
                return _settings_response(
                    request, profile_name, data,
                    profile_err=f"rate_limit_seconds must be a number, got {rate_limit_seconds!r}.",
                )

        try:
            _write_profile(profile_name, data)
        except Exception as e:
            return _settings_response(
                request, profile_name, data,
                profile_err=f"Save failed: {e}",
            )

    msg = "Profile saved." + (f" Roles expanded to {len(roles_list)}." if expand else "")
    return _settings_response(request, profile_name, data, profile_msg=msg)


@router.post("/settings/responses")
def save_responses(
    request: Request,
    work_authorization: str = Form(""),
    visa_sponsorship: str = Form(""),
    gender: str = Form(""),
    ethnicity: str = Form(""),
    veteran_status: str = Form(""),
    disability: str = Form(""),
):
    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)
    with lock:
        existing = _read_responses(profile_name)
        # Empty submitted fields preserve the existing value rather than wiping
        # it — a textarea you didn't touch shouldn't blank out your stored answer.
        for key, value in [
            ("work_authorization", work_authorization),
            ("visa_sponsorship", visa_sponsorship),
            ("gender", gender),
            ("ethnicity", ethnicity),
            ("veteran_status", veteran_status),
            ("disability", disability),
        ]:
            if value.strip():
                existing[key] = value.strip()
        if not existing:
            return _settings_response(
                request, profile_name, responses_data=existing,
                responses_err="At least one response is required.",
            )
        try:
            _write_responses(profile_name, existing)
        except Exception as e:
            return _settings_response(
                request, profile_name, responses_data=existing,
                responses_err=f"Save failed: {e}",
            )

    return _settings_response(
        request, profile_name, responses_data=existing,
        responses_msg="Responses saved.",
    )
