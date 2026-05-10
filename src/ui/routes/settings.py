import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.inbox import auth as inbox_auth
from src.inbox.auth import ImapCredentials
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
):
    return _settings_response(
        request, state.active_profile(),
        profile_msg=profile_msg, profile_err=profile_err,
        responses_msg=responses_msg, responses_err=responses_err,
        inbox_msg=inbox_msg, inbox_err=inbox_err,
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
