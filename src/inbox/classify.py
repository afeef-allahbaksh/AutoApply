"""Email classifier — Claude decides if a message is application-related and what it implies."""
import json

from src.api import create_message, reserve_input_tokens

# Haiku — same accuracy on this binary-classification task as Sonnet, 4x cheaper.
# Tier 1 input limit is 50K TPM; the global token bucket in api.py throttles
# concurrent callers to stay under that without 429s.
CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 15
BODY_CHARS = 800

# Single status enum the rest of the system uses.
VALID_STATUSES = {"applied", "screen", "technical", "onsite", "offer", "rejected"}

PROMPT = """You classify emails for a job-application tracker. For each email, decide whether it represents
a direct change in a candidate's application process — submitted an application, got an interview invite,
moved to next round, received an offer, or got rejected.

Strictly IGNORE (set action_type="ignore"):
- LinkedIn / Indeed / job-board "jobs you might like" alerts
- Recruiter cold emails ("I have an opportunity at X")
- "Your application was viewed" / "X people applied to this job" notifications
- Newsletters, digests, marketing
- Mailing list / community traffic
- Account confirmations, password resets, login alerts
- General company comms (RSVPs, all-hands, swag offers) unrelated to the candidate's pipeline

Classify as action_type="new_application" when:
- An ATS (Greenhouse, Lever, Ashby, Workday, etc.) sends a "thanks for applying" / "we received your application" / "your application has been submitted" confirmation.

Classify as action_type="status_update" when an existing application moves stage:
- Phone screen invite / "let's chat" / "schedule a call" with a recruiter → status="screen"
- Coding challenge / take-home / technical screen / "technical interview" → status="technical"
- Onsite / final round / virtual onsite / "interview loop" → status="onsite"
- Offer letter / "we're excited to extend an offer" → status="offer"
- "Unfortunately" / "we won't be moving forward" / "decided not to proceed" / "other candidates" → status="rejected"

For every classified email return:
- action_type: "new_application" | "status_update" | "ignore"
- company: extracted company name (string, "" if ignore)
- role: job title if mentioned (string, "" if not visible)
- status: one of {applied, screen, technical, onsite, offer, rejected} (or "" if ignore)
- confidence: float 0–1 — how sure you are this is the right classification
- reasoning: one short sentence

Output JSON ONLY — an array with the same length and order as the input emails. No markdown, no commentary.

Input emails:
__EMAILS_JSON__

Required output schema (one entry per input email):
[{"id": "<copied from input>", "action_type": "...", "company": "...", "role": "...", "status": "...", "confidence": 0.0, "reasoning": "..."}, ...]
"""


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def _extract_json_array(text: str) -> list:
    """Strip optional code fences and parse a JSON array."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def classify_batch(messages: list[dict]) -> list[dict]:
    """Classify up to BATCH_SIZE messages in one Claude call.

    Each input must have id, subject, from, body_text. Returns one result per input
    in order; entries that fail validation get coerced to action_type="ignore".
    """
    if not messages:
        return []
    payload = [
        {
            "id": m["id"],
            "subject": _truncate(m.get("subject", ""), 300),
            "from": _truncate(m.get("from", ""), 200),
            "body": _truncate(m.get("body_text") or m.get("snippet", ""), BODY_CHARS),
        }
        for m in messages
    ]
    # Plain replace instead of str.format so literal `{...}` in the prompt body
    # (set notation, JSON example) doesn't trigger KeyError on missing fields.
    prompt = PROMPT.replace("__EMAILS_JSON__", json.dumps(payload, ensure_ascii=False))

    response = create_message(
        model=CLASSIFY_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
    try:
        parsed = _extract_json_array(raw)
    except Exception as e:
        # Fall back to per-message classification so a single malformed token in
        # the batch response doesn't drop ten emails into the ignore bucket.
        if len(messages) > 1:
            out = []
            for m in messages:
                out.extend(classify_batch([m]))
            return out
        return [
            {"id": messages[0]["id"], "action_type": "ignore", "company": "", "role": "",
             "status": "", "confidence": 0.0, "reasoning": f"parse error: {e}"}
        ]

    by_id = {entry.get("id"): entry for entry in parsed if isinstance(entry, dict)}
    results = []
    for m in messages:
        entry = by_id.get(m["id"]) or {"action_type": "ignore"}
        action_type = entry.get("action_type", "ignore")
        if action_type not in {"new_application", "status_update", "ignore"}:
            action_type = "ignore"
        status = entry.get("status", "") or ""
        if action_type == "ignore" or status not in VALID_STATUSES:
            if action_type != "ignore" and status not in VALID_STATUSES:
                # Action claimed but status invalid — downgrade to ignore.
                action_type = "ignore"
            status = ""
        confidence = entry.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        results.append({
            "id": m["id"],
            "action_type": action_type,
            "company": (entry.get("company") or "").strip(),
            "role": (entry.get("role") or "").strip(),
            "status": status,
            "confidence": max(0.0, min(1.0, confidence)),
            "reasoning": (entry.get("reasoning") or "").strip(),
        })
    return results


# 2 in flight stays well under Haiku's 100K input TPM with batches sized at
# ~3.4K tokens each; bigger numbers triggered 429s on the deep-sync first wave.
CLASSIFY_PARALLELISM = 2


def classify_messages(messages: list[dict]) -> list[dict]:
    """Classify any number of messages, running batches in parallel.

    Each batch is one Claude call. CLASSIFY_PARALLELISM batches run concurrently
    (HTTP I/O releases the GIL, so threading is enough — no asyncio needed).
    """
    if not messages:
        return []
    from concurrent.futures import ThreadPoolExecutor

    batches = [messages[i:i + BATCH_SIZE] for i in range(0, len(messages), BATCH_SIZE)]
    if len(batches) == 1:
        return classify_batch(batches[0])
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=CLASSIFY_PARALLELISM) as pool:
        # `map` preserves order, so concatenated results match input message order.
        for result in pool.map(classify_batch, batches):
            out.extend(result)
    return out
