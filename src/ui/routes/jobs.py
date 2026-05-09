from fastapi import APIRouter, Request

from ..deps import template_context
from ..templates_loader import templates

router = APIRouter()


@router.get("/jobs")
def jobs_page(request: Request):
    return templates.TemplateResponse(
        request, "jobs.html",
        template_context(request, page_title="Jobs"),
    )
