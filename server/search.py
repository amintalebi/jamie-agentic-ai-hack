"""Web search for the orchestrator via the DeepSeek Search API.

Docs: https://api-docs.deepseek.com/guides/search/
POST https://api.deepseek.com/v1/tools/search with {"query", "count", ...} -> {"results": [...]}.
"""
import logging
import time

import httpx

from server import settings

log = logging.getLogger("server.search")

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
TIMEOUT_S = 10.0
MAX_QUERY_CHARS = 128   # API limit for query and ori_question
SUMMARY_CHARS = 400     # keep tool results small for the orchestrator's context


class SearchError(Exception):
    pass


async def web_search(query: str, count: int = 5, ori_question: str | None = None) -> list[dict]:
    """Top results as [{title, url, site, date, summary}]. Raises SearchError on any failure."""
    if not settings.DEEPSEEK_API_KEY:
        raise SearchError("DEEPSEEK_API_KEY is not set")
    query = query.strip()[:MAX_QUERY_CHARS]
    if not query:
        raise SearchError("empty query")
    body: dict = {"query": query, "count": max(1, min(count, 10))}
    if ori_question and ori_question.strip():
        body["ori_question"] = ori_question.strip()[:MAX_QUERY_CHARS]

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(
                SEARCH_URL,
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json=body,
            )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except httpx.HTTPStatusError as e:
        log.error("deepseek search failed: HTTP %s %s", e.response.status_code, e.response.text[:200])
        raise SearchError(f"search failed with HTTP {e.response.status_code}") from e
    except (httpx.HTTPError, ValueError) as e:
        log.error("deepseek search failed: %s", e)
        raise SearchError(f"search failed: {type(e).__name__}") from e

    log.info("deepseek search %r -> %d results in %.2fs", query, len(results), time.monotonic() - started)
    return [
        {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "site": r.get("site_name") or "",
            "date": r.get("date") or "",
            "summary": (r.get("summary") or r.get("snippet") or "")[:SUMMARY_CHARS],
        }
        for r in results
    ]
