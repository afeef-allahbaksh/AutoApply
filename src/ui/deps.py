from fastapi import HTTPException, Request

from src.profile_loader import Profile

from . import state


def get_profile() -> Profile:
    name = state.active_profile()
    if not name:
        raise HTTPException(status_code=500, detail="No profile configured")
    try:
        return Profile(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def template_context(request: Request, **extra) -> dict:
    """Common template variables — sidebar profile dropdown + active path highlighting."""
    return {
        "request": request,
        "active_profile_name": state.active_profile(),
        "profiles": state.list_profiles(),
        "current_path": request.url.path,
        **extra,
    }
