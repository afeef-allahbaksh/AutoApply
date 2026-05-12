"""First-run setup wizard — single page that creates profile.json + responses.json
(and optionally resume.json) for a new (or reset) profile."""
import json
import re
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from src.profile_loader import PROFILES_DIR
from src.role_expander import expand_roles
from src.schemas import validate_profile, validate_responses

from .. import state
from ..deps import template_context
from ..templates_loader import templates
from .settings import _import_resume_from_bytes

router = APIRouter()


_SLUG_RE = re.compile(r"[^a-z0-9_]")


def _slugify_profile_name(name: str) -> str:
    """Match the CLI's munging: lowercase, spaces → underscores, strip other punctuation."""
    return _SLUG_RE.sub("", name.strip().replace(" ", "_").lower())


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


@router.get("/setup")
def setup_page(request: Request, msg: str = "", err: str = ""):
    """Render the setup form. Shown on first visit when no profile exists, or
    via direct link to create an additional profile."""
    existing = state.list_profiles()
    return templates.TemplateResponse(
        request, "setup.html",
        template_context(
            request,
            page_title="Setup",
            existing_profiles=existing,
            msg=msg,
            err=err,
        ),
    )


@router.post("/setup")
async def setup_submit(
    request: Request,
    profile_name: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    location: str = Form(...),
    linkedin: str = Form(""),
    github: str = Form(""),
    roles: str = Form(...),
    expand: str = Form(""),  # checkbox value="1" → "1" or ""
    experience_levels: str = Form(...),
    locations: str = Form(...),
    salary_min: str = Form(""),
    industries: str = Form(""),
    auto_submit: str = Form(""),
    rate_limit_seconds: str = Form("30"),
    work_authorization: str = Form(""),
    visa_sponsorship: str = Form(""),
    gender: str = Form(""),
    ethnicity: str = Form(""),
    veteran_status: str = Form(""),
    disability: str = Form(""),
    resume: UploadFile | None = File(default=None),
):
    slug = _slugify_profile_name(profile_name)
    if not slug:
        return RedirectResponse(
            url="/setup?err=Profile+name+is+required.",
            status_code=303,
        )

    profile_dir = PROFILES_DIR / slug
    profile_dir.mkdir(parents=True, exist_ok=True)

    roles_list = _split_csv(roles)
    levels_list = _split_csv(experience_levels)
    locations_list = _split_csv(locations)
    if not roles_list or not levels_list or not locations_list:
        return RedirectResponse(
            url="/setup?err=Roles,+experience+levels,+and+locations+are+all+required.",
            status_code=303,
        )

    # Role expansion is a Claude call (~$0.005, ~5-10s). It runs synchronously
    # here — the user already sees the form-submit spinner.
    final_roles = roles_list
    if expand:
        try:
            final_roles = expand_roles(roles_list, levels_list)
        except Exception as e:
            return RedirectResponse(
                url=f"/setup?err=Role+expansion+failed:+{str(e)[:120].replace(' ', '+')}",
                status_code=303,
            )

    profile_data: dict = {
        "name": name.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "location": location.strip(),
        "job_preferences": {
            "roles": final_roles,
            "experience_levels": levels_list,
            "locations": locations_list,
        },
        "settings": {
            "auto_submit": bool(auto_submit),
        },
    }
    if linkedin.strip():
        profile_data["linkedin"] = linkedin.strip()
    if github.strip():
        profile_data["github"] = github.strip()
    if salary_min.strip():
        try:
            profile_data["job_preferences"]["salary_min"] = int(salary_min.replace(",", "").split(".")[0])
        except ValueError:
            pass
    industries_list = _split_csv(industries)
    if industries_list:
        profile_data["job_preferences"]["industries"] = industries_list
    if rate_limit_seconds.strip():
        try:
            profile_data["settings"]["rate_limit_seconds"] = int(rate_limit_seconds)
        except ValueError:
            profile_data["settings"]["rate_limit_seconds"] = 30
    else:
        profile_data["settings"]["rate_limit_seconds"] = 30

    try:
        validate_profile(profile_data)
    except Exception as e:
        return RedirectResponse(
            url=f"/setup?err=Profile+failed+schema+validation:+{str(e)[:120].replace(' ', '+')}",
            status_code=303,
        )

    with open(profile_dir / "profile.json", "w") as f:
        json.dump(profile_data, f, indent=2)
        f.write("\n")

    # Responses are optional — the user can leave fields blank and edit later
    # from Settings. Only persist non-empty values.
    responses: dict[str, str] = {}
    for key, value in [
        ("work_authorization", work_authorization),
        ("visa_sponsorship", visa_sponsorship),
        ("gender", gender),
        ("ethnicity", ethnicity),
        ("veteran_status", veteran_status),
        ("disability", disability),
    ]:
        if value.strip():
            responses[key] = value.strip()
    if responses:
        try:
            validate_responses(responses)
            with open(profile_dir / "responses.json", "w") as f:
                json.dump(responses, f, indent=2)
                f.write("\n")
        except Exception:
            # Bad response shape shouldn't block profile creation — user can
            # fix on the Settings page.
            pass

    # Optional resume upload — parse synchronously via the Phase 26 helper.
    # If parsing fails or the file is empty, we don't roll back the profile;
    # the user can retry from Settings → Resume.
    resume_status = ""
    if resume is not None and resume.filename:
        if not resume.filename.lower().endswith(".pdf"):
            resume_status = "Resume not imported (must be a .pdf). Add it later from Settings."
        else:
            try:
                contents = await resume.read()
                if contents:  # ignore empty-file submissions from browsers
                    _import_resume_from_bytes(slug, contents)
                    resume_status = "Resume imported."
            except Exception as e:
                resume_status = f"Resume parse failed: {str(e)[:120]}. Add it later from Settings."

    # Activate the new profile and bounce to dashboard
    state.set_active_profile(slug)
    target = "/" if not resume_status else f"/?setup_msg={quote(resume_status, safe='')}"
    return RedirectResponse(url=target, status_code=303)
