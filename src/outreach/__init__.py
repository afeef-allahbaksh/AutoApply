"""Cold outreach feature package.

Submodules:
  - generator: Claude-backed draft generation (single + batched)
  - sender: SMTP send via the inbox app password
  - store: outreach.json persistence + status enum
  - csv_import: bulk CSV/TSV → row dicts
  - gmail: compose URL + domain normalization helpers

External callers (route layer, tests) should import from here:

    from src.outreach import generate_outreach, send_email, load_outreach
"""
from .csv_import import parse_csv_rows
from .generator import batch_generate_outreach, generate_outreach
from .gmail import clean_domain, gmail_compose_url
from .sender import send_email
from .store import (
    STATUS_BADGE,
    STATUSES,
    load_outreach,
    now_iso,
    outreach_path,
    save_outreach,
)

__all__ = [
    # generator
    "generate_outreach", "batch_generate_outreach",
    # sender
    "send_email",
    # store
    "load_outreach", "save_outreach", "outreach_path", "now_iso",
    "STATUSES", "STATUS_BADGE",
    # csv_import
    "parse_csv_rows",
    # gmail
    "gmail_compose_url", "clean_domain",
]
