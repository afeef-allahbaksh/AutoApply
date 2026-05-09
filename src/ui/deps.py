from fastapi import Request

from . import state


def template_context(request: Request, **extra) -> dict:
    return {
        "request": request,
        "active_profile_name": state.active_profile(),
        "profiles": state.list_profiles(),
        "current_path": request.url.path,
        **extra,
    }
