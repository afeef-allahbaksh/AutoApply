from fastapi import APIRouter, Request

from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


@router.get("/cold-email")
def cold_email_page(request: Request):
    return templates.TemplateResponse(
        request, "cold_email.html",
        template_context(request, page_title="Cold email"),
    )
