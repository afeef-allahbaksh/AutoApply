"""Job discovery package.

External callers should import `discover_jobs` from here:

    from src.jobs import discover_jobs

Submodules:
  - clients: per-ATS HTTP fetch (greenhouse, lever)
  - snippet: HTML-stripping + requirements-section extraction
  - filter: keyword + location matching + dedup
  - classify: LLM-driven country / level / fit-score filtering
  - discover: top-level pipeline orchestrator
"""
from .clients.greenhouse import fetch_greenhouse_jobs
from .clients.lever import fetch_lever_jobs
from .discover import discover_jobs

__all__ = ["discover_jobs", "fetch_greenhouse_jobs", "fetch_lever_jobs"]
