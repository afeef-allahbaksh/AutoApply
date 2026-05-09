import random
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
