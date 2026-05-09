from fastapi import APIRouter, Request

from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


@router.get("/applications")
def applications_page(request: Request):
    return templates.TemplateResponse(
        request, "applications.html",
        template_context(request, page_title="Applications"),
    )
