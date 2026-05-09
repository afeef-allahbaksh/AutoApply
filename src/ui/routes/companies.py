from fastapi import APIRouter, Request

from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


@router.get("/companies")
def companies_page(request: Request):
    return templates.TemplateResponse(
        request, "companies.html",
        template_context(request, page_title="Companies"),
    )
