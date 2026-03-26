"""External research skill — reference implementation using Tavily.

Swappable — any search backend that returns {"title", "url", "snippet"} works.
This module provides two functions:

  web_search(query, max_results=5) -> list[dict]
  web_read(url) -> dict

Both degrade gracefully: missing API key logs a warning and returns empty results
(never crashes). Network and rate-limit errors are caught and surfaced in the
response's "error" field.

Backend: Tavily (https://tavily.com)
  Free tier: 1,000 searches/month.
  Set TAVILY_API_KEY in your .env to enable.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy client – instantiated on first call, not at import time
# ---------------------------------------------------------------------------
_client = None
_client_checked = False


def _get_client():
    """Return a TavilyClient instance, or None if unavailable."""
    global _client, _client_checked  # noqa: PLW0603
    if _client_checked:
        return _client

    _client_checked = True
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "TAVILY_API_KEY not set — external.research.* skills are disabled. "
            "Get a free key at https://tavily.com"
        )
        return None

    try:
        from tavily import TavilyClient  # type: ignore[import-untyped]

        _client = TavilyClient(api_key=api_key)
    except ImportError:
        logger.warning(
            "tavily-python is not installed — external.research.* skills are disabled. "
            "Install with: pip install tavily-python"
        )
    return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for *query*. Returns a list of result dicts.

    Each dict: {"title": str, "url": str, "snippet": str}

    If the search backend is unavailable (missing key, not installed, network
    error, rate limit), returns an empty list and logs the reason.
    """
    if time.time() < _BACKOFF_UNTIL:
        logger.info("Rate-limit backoff active — skipping web_search until %.0f", _BACKOFF_UNTIL)
        return []

    client = _get_client()
    if client is None:
        return []

    try:
        response = client.search(query=query, max_results=max_results)
        results: list[dict] = []
        for item in response.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
            )
        return results
    except Exception as exc:
        _handle_error("web_search", exc)
        return []


def web_read(url: str) -> dict:
    """Read the content of *url*. Returns a dict:

    {"url": str, "content": str, "error": str | None}

    On failure, "content" is empty and "error" describes what went wrong.
    """
    if time.time() < _BACKOFF_UNTIL:
        logger.info("Rate-limit backoff active — skipping web_read until %.0f", _BACKOFF_UNTIL)
        return {"url": url, "content": "", "error": "Rate-limit backoff active"}

    client = _get_client()
    if client is None:
        return {"url": url, "content": "", "error": "Research skill unavailable (no API key or tavily-python not installed)"}

    try:
        response = client.extract(urls=[url])
        extracted = response.get("results", [])
        if extracted:
            raw = extracted[0].get("raw_content", "") or extracted[0].get("text", "")
            return {"url": url, "content": raw, "error": None}
        return {"url": url, "content": "", "error": "No content extracted from URL"}
    except Exception as exc:
        _handle_error("web_read", exc)
        return {"url": url, "content": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# Error handling helper
# ---------------------------------------------------------------------------

_BACKOFF_UNTIL: float = 0.0


def _handle_error(fn_name: str, exc: Exception) -> None:
    """Log errors and apply simple backoff for rate limits."""
    global _BACKOFF_UNTIL  # noqa: PLW0603

    exc_str = str(exc).lower()
    if "rate" in exc_str or "429" in exc_str:
        _BACKOFF_UNTIL = time.time() + 60  # back off 60 seconds
        logger.warning("Rate limited in %s — backing off 60s: %s", fn_name, exc)
    else:
        logger.warning("Error in %s: %s", fn_name, exc)
