"""Cold outreach email generator.

Claude-backed. Same shape as `src.cover_letter.generate_cover_letter`, but
tuned for relationship outreach (you → a specific person at a company) rather
than application material (you → the company).

Returns a structured dict so future bulk-generation paths can reuse the output
without re-parsing free-form text.
"""
import json

from src.api import create_message, strip_code_fences

# How many outreach drafts to bundle into a single Claude call. 20 fits
# comfortably in the 8K output budget at ~120 words each + JSON framing.
BATCH_CHUNK_SIZE = 20


OUTREACH_PROMPT = """You are writing a cold outreach email for a job seeker — to a specific person at a company. NOT an application; this is relationship outreach asking for a brief chat, a referral, or a quick question.

Sender:
Name: {sender_name}
Background:
{sender_background}

Recipient:
Name: {contact_name}
Title: {contact_title}
Company: {company}

What the sender knows about this person (or why they're reaching out to them specifically):
{context_notes}

{job_block}
Rules — follow strictly:
1. SUBJECT line: max 8 words. Specific and scannable. Avoid clichés like "Application for...", "Interested in your role", "Quick question about your company".
2. BODY: 3 short paragraphs, MAX 120 words total. Plain text, no HTML.
   - Para 1: WHY this person specifically. Reference the context notes — something real, not generic.
   - Para 2: ONE concrete credibility marker from the sender's background. A metric or shipped project, not a list.
   - Para 3: ONE clear, low-friction ask — 15-minute chat, a single question, a referral. Make it easy to say yes.
3. Tone — genuine and direct. Match what an engineer would say to another engineer (or PM to PM, etc., based on the recipient's title). No corporate-speak.
4. Forbidden phrases: "I am excited to", "I'd love to", "this aligns perfectly", "I hope this email finds you well", "I came across your profile", "I admire your work". Anything that sounds templated.
5. No flattery. No apologies for emailing. No "I know you're busy".
6. Sign off naturally with sender's first name only.

Return ONLY valid JSON in this exact format — no markdown fences, no commentary:
{{"subject": "...", "body": "..."}}"""


def _build_sender_background(resume_data: dict, max_chars: int = 600) -> str:
    """Compact summary of the sender's resume for the prompt — recent experience
    and a couple of projects with metrics where available."""
    parts = []
    for exp in (resume_data or {}).get("experience", [])[:3]:
        bullets = "; ".join(b[:120] for b in exp.get("bullets", [])[:2])
        if bullets:
            parts.append(f"{exp.get('title', '?')} at {exp.get('company', '?')}: {bullets}")
        else:
            parts.append(f"{exp.get('title', '?')} at {exp.get('company', '?')}")
    for proj in (resume_data or {}).get("projects", [])[:2]:
        tech = f" ({proj['technologies']})" if proj.get("technologies") else ""
        bullets = "; ".join(b[:100] for b in proj.get("bullets", [])[:1])
        parts.append(f"{proj.get('name', '?')}{tech}: {bullets}")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text or "(no resume on file — generate based on the context notes alone)"


def generate_outreach(
    profile_data: dict,
    resume_data: dict | None,
    company: str,
    contact_name: str,
    contact_title: str,
    context_notes: str,
    job_content: str = "",
) -> dict:
    """Generate a cold outreach email tailored to the recipient.

    Returns {"subject": str, "body": str}. On parse failure, falls back to a
    minimal hand-crafted draft so the user always has something to edit rather
    than a blank composer.
    """
    sender_name = (profile_data or {}).get("name", "").strip() or "(your name)"
    sender_background = _build_sender_background(resume_data or {})
    job_block = (
        f"Job they may be a fit for at this company:\n{job_content[:1200]}\n\n"
        if job_content.strip() else ""
    )

    prompt = OUTREACH_PROMPT.format(
        sender_name=sender_name,
        sender_background=sender_background,
        contact_name=contact_name or "(unknown)",
        contact_title=contact_title or "(unknown title)",
        company=company or "(unknown company)",
        context_notes=context_notes.strip() or "(no notes — sender has no specific reason in mind)",
        job_block=job_block,
    )

    try:
        message = create_message(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = strip_code_fences(message.content[0].text)
        result = json.loads(raw)
        subject = (result.get("subject") or "").strip()
        body = (result.get("body") or "").strip()
        if subject and body:
            return {"subject": subject, "body": body}
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError):
        pass

    # Fallback: minimal skeleton so the user always has something editable.
    return _fallback_draft(sender_name, contact_name, company)


def _fallback_draft(sender_name: str, contact_name: str, company: str) -> dict:
    """The same skeleton shape used by both single and batch generation when
    Claude returns garbage or the JSON parse fails."""
    return {
        "subject": f"Question about {company or 'your work'}",
        "body": (
            f"Hi {contact_name or 'there'},\n\n"
            f"(Generator failed — fill this in by hand.)\n\n"
            f"— {sender_name.split(' ')[0] if sender_name else 'you'}"
        ),
    }


BATCH_OUTREACH_PROMPT = """You are writing N cold outreach emails for a job seeker, to N different people. NOT applications; these are relationship outreach asking for a brief chat, referral, or quick question.

Sender:
Name: {sender_name}
Background:
{sender_background}

Below are N targets (0-indexed). Generate one outreach per target.

{targets_block}

Rules — follow strictly for EVERY target:
1. SUBJECT line: max 8 words. Specific and scannable. Avoid "Application for…", "Interested in your role", clichés.
2. BODY: 3 short paragraphs, MAX 120 words total. Plain text.
   - Para 1: WHY THIS PERSON specifically. Use their context_notes — something real, not generic.
   - Para 2: ONE concrete credibility marker from the sender's background. A metric or shipped project.
   - Para 3: ONE clear, low-friction ask (15-min chat / single question / referral).
3. Tone — match the recipient's likely tone (engineer vs PM vs recruiter, based on title). No corporate-speak.
4. FORBIDDEN phrases for all bodies: "I am excited to", "I'd love to", "this aligns perfectly", "I hope this email finds you well", "I came across your profile", "I admire your work". Anything templated.
5. No flattery, no apologies, no "I know you're busy".
6. Sign off with sender's first name only.
7. Each draft must be distinct — DO NOT reuse subject lines or opening sentences across targets.

Return ONLY valid JSON in this exact format — no markdown fences, no commentary:
[
  {{"index": 0, "subject": "...", "body": "..."}},
  {{"index": 1, "subject": "...", "body": "..."}},
  ...
]"""


def _format_target_block(index: int, target: dict) -> str:
    """Render a single target's context into the batched prompt."""
    company = target.get("company") or "(unknown company)"
    contact_name = target.get("contact_name") or "(unknown)"
    contact_title = target.get("contact_title") or "(unknown title)"
    context_notes = (target.get("context_notes") or "").strip() or "(no notes)"
    job_content = (target.get("job_content") or "").strip()
    job_line = f"\n  Related job at this company: {job_content[:600]}" if job_content else ""
    return (
        f"--- Target {index} ---\n"
        f"  Contact: {contact_name}\n"
        f"  Title: {contact_title}\n"
        f"  Company: {company}\n"
        f"  What sender knows about them: {context_notes}"
        f"{job_line}"
    )


def batch_generate_outreach(
    profile_data: dict,
    resume_data: dict | None,
    targets: list[dict],
) -> list[dict]:
    """Generate cold outreach drafts for many recipients in batched Claude calls.

    `targets` is a list of dicts, each with at least `company` + `contact_name`.
    Optional per-target keys: `contact_title`, `context_notes`, `job_content`
    (the linked job's description, when applicable).

    Returns a list of `{subject, body}` dicts the same length as `targets`,
    in the same order. On any failure for a given target, that slot is filled
    with the same fallback skeleton as the single-record generator — the
    caller never gets a list with `None` entries.

    Chunked at `BATCH_CHUNK_SIZE` to stay under the 8K output budget at ~120
    words per draft + JSON framing.
    """
    if not targets:
        return []

    sender_name = (profile_data or {}).get("name", "").strip() or "(your name)"
    sender_background = _build_sender_background(resume_data or {})

    results: list[dict] = [None] * len(targets)  # type: ignore[list-item]

    for chunk_start in range(0, len(targets), BATCH_CHUNK_SIZE):
        chunk = targets[chunk_start:chunk_start + BATCH_CHUNK_SIZE]
        targets_block = "\n\n".join(
            _format_target_block(i, t) for i, t in enumerate(chunk)
        )
        prompt = BATCH_OUTREACH_PROMPT.format(
            sender_name=sender_name,
            sender_background=sender_background,
            targets_block=targets_block,
        )

        # Reserve enough output for ~200 words per draft (safety margin over 120).
        max_tokens = min(8192, len(chunk) * 350 + 256)
        try:
            message = create_message(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = strip_code_fences(message.content[0].text)
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("expected a JSON array")
        except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError, ValueError):
            # Whole chunk failed — fallback every slot in the chunk
            for i, t in enumerate(chunk):
                results[chunk_start + i] = _fallback_draft(
                    sender_name,
                    t.get("contact_name", ""),
                    t.get("company", ""),
                )
            continue

        # Map parsed items by their "index" field; Claude usually returns them
        # in order but we don't bet on it. Fallback for missing/garbled slots.
        by_index = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            subject = (item.get("subject") or "").strip()
            body = (item.get("body") or "").strip()
            if subject and body and 0 <= idx < len(chunk):
                by_index[idx] = {"subject": subject, "body": body}

        for i, t in enumerate(chunk):
            if i in by_index:
                results[chunk_start + i] = by_index[i]
            else:
                results[chunk_start + i] = _fallback_draft(
                    sender_name,
                    t.get("contact_name", ""),
                    t.get("company", ""),
                )

    return results  # type: ignore[return-value]
