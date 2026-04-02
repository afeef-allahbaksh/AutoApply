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


def create_message(retries: int = 3, **kwargs) -> anthropic.types.Message:
    """Call client.messages.create with exponential backoff on transient errors.

    Retries on overloaded (529), rate limit (429), and connection errors.
    All keyword arguments are passed through to messages.create.
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
            delay = (2 ** attempt)  # 1s, 2s, 4s
            print(f"  API error (attempt {attempt + 1}/{retries}), retrying in {delay}s: {e}")
            time.sleep(delay)
