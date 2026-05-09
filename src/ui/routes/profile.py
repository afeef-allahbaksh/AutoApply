from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from .. import state

router = APIRouter()


@router.post("/profile")
def switch_profile(name: str = Form(...)):
    if name in state.list_profiles():
        state.set_active_profile(name)
    return RedirectResponse("/", status_code=303)
