"""Business search (any kind of business) for the orchestrator via the DeepSeek Search API.

Docs: https://api-docs.deepseek.com/guides/search/
POST https://api.deepseek.com/v1/tools/search with {"query", "count"} -> {"results": [...]}.
Numbers found here may be called after the user confirms them in a poll (orchestrator place_call);
with SAFE_MODE on the call still rings an allowed stand-in (common/safe_mode.py).
"""
import logging
import time

import httpx

from common.masking import mask_text
from server import settings

log = logging.getLogger("server.search")

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
TIMEOUT_S = 10.0
MAX_QUERY_CHARS = 128   # API limit
SUMMARY_CHARS = 400     # keep tool results small for the orchestrator's context

# One client for the process, so searches reuse the TLS connection.
_client: httpx.AsyncClient | None = None


class SearchError(Exception):
    pass


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT_S)
    return _client


def _text(value: object, limit: int | None = None) -> str:
    s = value if isinstance(value, str) else ""
    return s[:limit] if limit else s


async def web_search(query: str, count: int = 5) -> list[dict]:
    """Top results as [{title, url, site, date, summary}]. Raises SearchError on any failure.
    Only the model's query is sent, never the user's raw message (it may hold names or numbers)."""
    if not settings.DEEPSEEK_API_KEY:
        raise SearchError("DEEPSEEK_API_KEY is not set")
    query = query.strip()[:MAX_QUERY_CHARS]
    if not query:
        raise SearchError("empty query")

    started = time.monotonic()
    try:
        resp = await _http().post(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={"query": query, "count": max(1, min(count, 10))},
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        log.error("deepseek search failed: HTTP %s %s", e.response.status_code, mask_text(e.response.text[:200]))
        raise SearchError(f"search failed with HTTP {e.response.status_code}") from e
    except (httpx.HTTPError, ValueError) as e:
        log.error("deepseek search failed: %s", type(e).__name__)
        raise SearchError(f"search failed: {type(e).__name__}") from e

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        log.error("deepseek search: unexpected response shape %s", type(data).__name__)
        raise SearchError("unexpected response from search")

    log.info("deepseek search %r -> %d results in %.2fs",
             mask_text(query), len(results), time.monotonic() - started)
    return [
        {
            "title": _text(r.get("title")),
            "url": _text(r.get("url")),
            "site": _text(r.get("site_name")),
            "date": _text(r.get("date")),
            "summary": _text(r.get("summary") or r.get("snippet"), SUMMARY_CHARS),
        }
        for r in results
        if isinstance(r, dict)
    ]
