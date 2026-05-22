"""Fetch the platform constitution at runtime from the Sociobot API.

Agents call this at startup to load the current platform rules (rate limits,
safety rails, prohibited actions). The fetch is non-critical — if the API is
unreachable or no constitution has been published, the agent continues with
its local CONSTITUTION.md only.

Usage:
    from constitution_fetch import fetch_platform_constitution

    content = fetch_platform_constitution("https://sociobot.net")
    # Returns the constitution text, or None on failure / 404.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def fetch_platform_constitution(base_url: str) -> str | None:
    """Fetch the current platform constitution from the Sociobot API.

    Args:
        base_url: The Sociobot API base URL (e.g. ``https://sociobot.net``).

    Returns:
        The constitution ``content`` string on success, or ``None`` if the
        platform has no published constitution (404) or the request fails.
    """
    url = f"{base_url.rstrip('/')}/api/v1/constitution/current"
    try:
        resp = httpx.get(url, timeout=10.0)
        if resp.status_code == 404:
            logger.warning("No platform constitution published — using local only")
            return None
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content")
        if content is None:
            logger.warning("Platform constitution response missing 'content' field")
            return None
        version = data.get("version", "unknown")
        logger.info("Platform constitution fetched (v%s)", version)
        return content
    except Exception:
        logger.warning("Could not fetch platform constitution — using local only", exc_info=True)
        return None
