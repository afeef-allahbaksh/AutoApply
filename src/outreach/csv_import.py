"""Bulk CSV/TSV → outreach rows.

Accepts comma-, tab-, or semicolon-separated values with an optional header
row. Returns parsed row dicts ready to be assembled into `outreach.json`
records by the caller (the route layer adds id/timestamps/status defaults).
"""
import csv
import io


def parse_csv_rows(raw: str) -> tuple[list[dict], list[str]]:
    """Parse a pasted CSV/TSV block into outreach row dicts.

    - Accepts comma-, tab-, or semicolon-separated values (sniffed by csv).
    - Supports an optional header row. If the first row's first cell looks like
      a column name (any of name/email/title/company/domain/context), treat it
      as a header; otherwise positional defaults.
    - Skips blank rows.

    Returns (rows, errors). Each row dict has keys: name, email, title,
    company, domain, context (all strings; missing → ''). `errors` is a list
    of human-readable validation messages (one per skipped row).
    """
    text = (raw or "").strip()
    if not text:
        return [], ["No CSV pasted."]

    POSITIONAL = ["name", "email", "title", "company", "domain", "context"]
    HEADER_TOKENS = {"name", "email", "title", "company", "domain", "context",
                     "contact", "contact_name", "contact_email", "contact_title",
                     "company_domain", "context_notes", "notes"}
    # Normalize line endings and strip BOM
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")

    # Explicit delimiter detection — csv.Sniffer is unreliable on short or
    # sparse inputs (especially tab-separated with trailing empty cells, which
    # is exactly what spreadsheet pastes look like). Check the first line for
    # the strongest signal.
    first_line = text.split("\n", 1)[0]
    if "\t" in first_line:
        delim = "\t"
    elif ";" in first_line and "," not in first_line:
        delim = ";"
    else:
        delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any((cell or "").strip() for cell in r)]
    if not rows:
        return [], ["No usable rows."]

    # Detect header by checking if the first row's first cell looks like a column name
    first_row_first = (rows[0][0] or "").strip().lower()
    if first_row_first in HEADER_TOKENS:
        header = [(c or "").strip().lower() for c in rows[0]]
        alias = {
            "contact": "name", "contact_name": "name",
            "contact_email": "email",
            "contact_title": "title",
            "company_domain": "domain",
            "context_notes": "context", "notes": "context",
        }
        header = [alias.get(c, c) for c in header]
        data_rows = rows[1:]
    else:
        header = POSITIONAL[:max(len(r) for r in rows)]
        data_rows = rows

    out: list[dict] = []
    errors: list[str] = []
    for i, raw_row in enumerate(data_rows, start=1 if first_row_first in HEADER_TOKENS else 0):
        row = dict.fromkeys(POSITIONAL, "")
        for j, cell in enumerate(raw_row):
            if j >= len(header):
                break
            key = header[j]
            if key in POSITIONAL:
                row[key] = (cell or "").strip()
        if not row["name"] or not row["company"]:
            errors.append(f"Row {i+1}: skipped — missing required name or company.")
            continue
        out.append(row)
    return out, errors
