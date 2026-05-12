"""Apply route subpackage — single-job + batch.

`src/ui/app.py` registers `apply.router`, which combines the routers from
`single.py` and `batch.py`. External callers (mostly tests) can keep importing
`_apply_worker`, `_partition_selected`, etc. directly from `src.ui.routes.apply`
— the re-exports below preserve that surface.
"""
from fastapi import APIRouter

# Re-export `get_browser_context` for direct-import callers. Note: this is
# NOT a usable patch target — `mock.patch` rewrites a name in a module, but
# both `single.py` and `batch.py` import their own copy via
# `from src.browser import get_browser_context`. Tests must patch the
# submodule names (`src.ui.routes.apply.single.get_browser_context`, etc.)
# to affect the worker.
from src.browser import get_browser_context  # noqa: F401

from .batch import _batch_apply_worker
from .batch import router as _batch_router
from .shared import (
    _append_completed,
    _apply_status_path,
    _apply_task_key,
    _load_jobs,
    _partition_selected,
)
from .single import _apply_worker
from .single import router as _single_router

router = APIRouter()
router.include_router(_single_router)
router.include_router(_batch_router)

__all__ = [
    "router",
    # Public-ish helpers (used by tests and potentially by other routes)
    "_apply_worker",
    "_batch_apply_worker",
    "_apply_status_path",
    "_apply_task_key",
    "_load_jobs",
    "_partition_selected",
    "_append_completed",
    "get_browser_context",
]
