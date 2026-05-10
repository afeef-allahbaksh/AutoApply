from fastapi import APIRouter, Form, HTTPException, Request

from src.discovery import _load_companies, _save_companies, validate_slug

from .. import state
from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


def _all_companies(profile_name: str) -> list:
    return _load_companies(profile_name)


@router.get("/companies")
def companies_page(request: Request, error: str = "", added: str = ""):
    profile_name = state.active_profile()
    companies = _all_companies(profile_name) if profile_name else []
    return templates.TemplateResponse(
        request, "companies.html",
        template_context(
            request,
            page_title="Companies",
            companies=companies,
            error=error,
            added=added,
        ),
    )


@router.post("/companies")
def add_company(
    request: Request,
    slug: str = Form(...),
    ats: str = Form("auto"),
):
    slug = slug.strip().lower()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")

    profile_name = state.active_profile()
    lock = state.profile_lock(profile_name)

    with lock:
        companies = _all_companies(profile_name)
        existing = {c["slug"] for c in companies}
        if slug in existing:
            return templates.TemplateResponse(
                request, "companies.html",
                template_context(
                    request,
                    page_title="Companies",
                    companies=companies,
                    error=f"'{slug}' is already in the list.",
                    added="",
                ),
            )

        if ats == "auto":
            result = validate_slug(slug, "greenhouse")
            detected = "greenhouse"
            if not result:
                result = validate_slug(slug, "lever")
                detected = "lever"
        elif ats in ("greenhouse", "lever"):
            result = validate_slug(slug, ats)
            detected = ats
        else:
            raise HTTPException(status_code=400, detail=f"invalid ats: {ats}")

        if not result:
            target = "Greenhouse or Lever" if ats == "auto" else ats
            return templates.TemplateResponse(
                request, "companies.html",
                template_context(
                    request,
                    page_title="Companies",
                    companies=companies,
                    error=f"Could not find '{slug}' on {target}.",
                    added="",
                ),
            )

        companies.append(result)
        _save_companies(profile_name, companies)

    return templates.TemplateResponse(
        request, "companies.html",
        template_context(
            request,
            page_title="Companies",
            companies=companies,
            error="",
            added=f"Added {result['name']} ({detected}).",
        ),
    )
