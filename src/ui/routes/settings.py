from fastapi import APIRouter, Request

from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(
        request, "settings.html",
        template_context(request, page_title="Settings"),
    )
