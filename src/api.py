import os
import random
import threading
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = None


def get_client() -> anthropic.Anthropic:
    """Return a shared Anthropic client (singleton)."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# Sliding-window token bucket for input-TPM rate limiting. Default 45K is
# conservative under Anthropic Tier 1 Haiku's 50K cap; override with
# AUTOAPPLY_INPUT_TPM_BUDGET if your tier is higher.
INPUT_TPM_BUDGET = int(os.environ.get("AUTOAPPLY_INPUT_TPM_BUDGET", "45000"))

_throttle_lock = threading.Lock()
_token_log: list[tuple[float, int]] = []  # (monotonic_ts, tokens), oldest first


def reserve_input_tokens(tokens: int) -> None:
    """Block until `tokens` can be consumed without exceeding INPUT_TPM_BUDGET.

    Call before sending a message; pass an estimated input-token count. Bursty
    callers from multiple threads share the budget naturally — the lock is held
    only while computing the wait, not during sleep.
    """
    while True:
        with _throttle_lock:
            now = time.monotonic()
            cutoff = now - 60.0
            while _token_log and _token_log[0][0] < cutoff:
                _token_log.pop(0)
            recent_total = sum(n for _, n in _token_log)
            if recent_total + tokens <= INPUT_TPM_BUDGET:
                _token_log.append((now, tokens))
                return
            # Compute the earliest moment the in-flight excess will roll out.
            excess = (recent_total + tokens) - INPUT_TPM_BUDGET
            running = 0
            wait_until = now + 60.0  # fallback if a single batch exceeds budget
            for t, n in _token_log:
                running += n
                if running >= excess:
                    wait_until = t + 60.0
                    break
            sleep_for = max(0.2, wait_until - now)
        # Lock released; sleep, then re-check on the next loop.
        time.sleep(sleep_for)


def strip_code_fences(text: str) -> str:
    """Strip optional ```…``` markdown fences from a Claude response.

    Handles ` ``` `, ` ```json `, ` ```python `, etc. — and the edge cases the
    original inline pattern silently crashed on (no newline after the opening
    fence, no trailing close fence, only whitespace inside).

    Returns the bare content between fences, stripped. If the input doesn't
    start with a fence, returns the input stripped (unchanged shape).
    """
    raw = (text or "").strip()
    if not raw.startswith("```"):
        return raw
    # Drop everything on the opening-fence line (handles ```json, ```python, etc.)
    newline_idx = raw.find("\n")
    if newline_idx == -1:
        # Just a fence opener with no content — give up, return original
        return raw
    raw = raw[newline_idx + 1:]
    # Drop the closing fence if present (otherwise leave the rest as-is)
    if raw.endswith("```"):
        raw = raw[:-3]
    elif "\n```" in raw:
        raw = raw.rsplit("\n```", 1)[0]
    return raw.strip()


def create_message(retries: int = 5, **kwargs) -> anthropic.types.Message:
    """Call client.messages.create with exponential backoff + jitter on transient errors.

    Retries on overloaded (529), rate limit (429), and connection errors. Jitter
    matters when multiple worker threads hit the same rate-limit window — without
    it they all retry on the same second and 429 again.
    """
    client = get_client()
    for attempt in range(retries):
        try:
            return client.messages.create(**kwargs)
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            # Don't retry on client errors (400, 401, 403, 404) — only server/rate issues
            if isinstance(e, anthropic.APIStatusError) and e.status_code < 500 and e.status_code != 429:
                raise
            if attempt == retries - 1:
                raise
            # 1s, 2s, 4s, 8s, 16s base + 0-1s jitter to break worker-thread collisions
            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"  API error (attempt {attempt + 1}/{retries}), retrying in {delay:.1f}s: {e}")
            time.sleep(delay)
