import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from src.inbox import auth as gmail_auth
from src.profile_loader import PROFILES_DIR
from src.role_expander import expand_roles
from src.schemas import validate_profile, validate_responses

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


def _read_profile(profile_name: str) -> dict:
    path = PROFILES_DIR / profile_name / "profile.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"profile.json not found for {profile_name}")
    with open(path) as f:
        return json.load(f)


def _write_profile(profile_name: str, data: dict) -> None:
    validate_profile(data)
    path = PROFILES_DIR / profile_name / "profile.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _read_responses(profile_name: str) -> dict:
    path = PROFILES_DIR / profile_name / "responses.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _write_responses(profile_name: str, data: dict) -> None:
    validate_responses(data)
    path = PROFILES_DIR / profile_name / "responses.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


@router.get("/settings")
def settings_page(
    request: Request,
    profile_msg: str = "",
    profile_err: str = "",
    responses_msg: str = "",
    responses_err: str = "",
    gmail_msg: str = "",
    gmail_err: str = "",
):
    profile_name = state.active_profile()
    profile_data = _read_profile(profile_name) if profile_name else {}
    responses_data = _read_responses(profile_name) if profile_name else {}
    gmail_status = gmail_auth.status(profile_name) if profile_name else {"state": "no_credentials", "email": None}
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
            gmail=gmail_status,
            gmail_msg=gmail_msg,
            gmail_err=gmail_err,
        ),
    )


@router.post("/settings/gmail/credentials")
async def upload_gmail_credentials(
    request: Request,
    file: UploadFile = File(...),
):
    profile_name = state.active_profile()
    contents = await file.read()
    try:
        parsed = json.loads(contents.decode())
        if not (parsed.get("installed") or parsed.get("web")):
            return RedirectResponse(
                url="/settings?gmail_err=Not+a+valid+OAuth+client+JSON+(missing+'installed'+or+'web'+key).",
                status_code=303,
            )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return RedirectResponse(
            url="/settings?gmail_err=Could+not+parse+credentials.json+(invalid+JSON).",
            status_code=303,
        )
    cp = gmail_auth.credentials_path(profile_name)
    cp.write_bytes(contents)
    return RedirectResponse(url="/settings?gmail_msg=credentials.json+saved.+Click+Connect+to+authorize.", status_code=303)


@router.post("/settings/gmail/connect")
def connect_gmail():
    profile_name = state.active_profile()
    if not gmail_auth.has_credentials(profile_name):
        return RedirectResponse(url="/settings?gmail_err=Upload+credentials.json+first.", status_code=303)
    try:
        creds = gmail_auth.run_oauth_flow(profile_name)
        email = gmail_auth.account_email(creds)
        return RedirectResponse(url=f"/settings?gmail_msg=Connected+as+{email}", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/settings?gmail_err=Auth+failed:+{str(e)[:120]}", status_code=303)


@router.post("/settings/gmail/disconnect")
def disconnect_gmail():
    profile_name = state.active_profile()
    gmail_auth.disconnect(profile_name)
    return RedirectResponse(url="/settings?gmail_msg=Disconnected.+Token+deleted.", status_code=303)


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
                    return templates.TemplateResponse(
                        request, "settings.html",
                        template_context(
                            request,
                            page_title="Settings",
                            profile=data,
                            responses=_read_responses(profile_name),
                            profile_msg="",
                            profile_err=f"Role expansion failed: {e}",
                            responses_msg="", responses_err="",
                        ),
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
                pass
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
                pass

        try:
            _write_profile(profile_name, data)
        except Exception as e:
            return templates.TemplateResponse(
                request, "settings.html",
                template_context(
                    request,
                    page_title="Settings",
                    profile=data,
                    responses=_read_responses(profile_name),
                    profile_msg="",
                    profile_err=f"Save failed: {e}",
                    responses_msg="", responses_err="",
                ),
            )

    return templates.TemplateResponse(
        request, "settings.html",
        template_context(
            request,
            page_title="Settings",
            profile=data,
            responses=_read_responses(profile_name),
            profile_msg="Profile saved." + (f" Roles expanded to {len(roles_list)}." if expand else ""),
            profile_err="",
            responses_msg="", responses_err="",
        ),
    )


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
            elif key in existing and not value:
                pass
        if not existing:
            return templates.TemplateResponse(
                request, "settings.html",
                template_context(
                    request,
                    page_title="Settings",
                    profile=_read_profile(profile_name),
                    responses=existing,
                    profile_msg="", profile_err="",
                    responses_msg="",
                    responses_err="At least one response is required.",
                ),
            )
        try:
            _write_responses(profile_name, existing)
        except Exception as e:
            return templates.TemplateResponse(
                request, "settings.html",
                template_context(
                    request,
                    page_title="Settings",
                    profile=_read_profile(profile_name),
                    responses=existing,
                    profile_msg="", profile_err="",
                    responses_msg="", responses_err=f"Save failed: {e}",
                ),
            )

    return templates.TemplateResponse(
        request, "settings.html",
        template_context(
            request,
            page_title="Settings",
            profile=_read_profile(profile_name),
            responses=existing,
            profile_msg="", profile_err="",
            responses_msg="Responses saved.",
            responses_err="",
        ),
    )
