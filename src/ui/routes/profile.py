import os
import shutil
from urllib.parse import quote

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from src.profile_loader import PROFILES_DIR

from .. import state

router = APIRouter()


@router.post("/profile")
def switch_profile(name: str = Form(...)):
    if name in state.list_profiles():
        state.set_active_profile(name)
    return RedirectResponse("/", status_code=303)


@router.post("/profile/delete")
def delete_profile(name: str = Form(...), confirm: str = Form("")):
    """Hard-delete a profile directory. Requires `confirm=name` to guard
    against accidental form submissions / replay. After delete, unsets the
    active-profile env var if it pointed here; the dashboard's first-visit
    redirect handles the rest (picks another profile or goes to /setup)."""
    name = name.strip()
    if not name or name not in state.list_profiles():
        msg = quote(f"Profile {name!r} not found.", safe="")
        return RedirectResponse(f"/settings?inbox_err={msg}", status_code=303)
    # Double-entry confirmation: the form must echo the profile name back so a
    # stray POST can't nuke an unrelated profile.
    if confirm != name:
        msg = quote("Delete cancelled — confirmation text did not match.", safe="")
        return RedirectResponse(f"/settings?inbox_err={msg}", status_code=303)

    target = PROFILES_DIR / name
    # Refuse to delete anything outside profiles/ (defense against a path-traversal
    # slug somehow getting past the slugifier).
    target_resolved = target.resolve()
    profiles_resolved = PROFILES_DIR.resolve()
    if profiles_resolved not in target_resolved.parents and target_resolved != profiles_resolved:
        msg = quote("Refusing to delete: path escapes profiles/.", safe="")
        return RedirectResponse(f"/settings?inbox_err={msg}", status_code=303)

    shutil.rmtree(target, ignore_errors=False)

    # Clear the active-profile env var if it pointed at the deleted profile.
    # The next request's dashboard route will redirect to /setup if no profiles
    # remain, or pick another one.
    if os.environ.get("AUTOAPPLY_PROFILE") == name:
        del os.environ["AUTOAPPLY_PROFILE"]

    msg = quote(f"Profile {name!r} deleted.", safe="")
    return RedirectResponse(f"/?setup_msg={msg}", status_code=303)
