"""Shared HTTP client for enrichment feeds (COT, carry, duka)."""

from __future__ import annotations

import logging
from typing import Iterable

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter

log = logging.getLogger("sentinel")

_CONNECT_TIMEOUT_SEC = 5
_READ_TIMEOUT_SEC = 30
FEED_HTTP_TIMEOUT = (_CONNECT_TIMEOUT_SEC, _READ_TIMEOUT_SEC)

_session: Session | None = None


def get_feed_session() -> Session:
    """Process-wide requests.Session with bounded connect/read timeouts."""
    global _session
    if _session is None:
        sess = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        _session = sess
    return _session


def feed_get(url: str, **kwargs) -> Response:
    """GET with default feed timeouts (5s connect, 30s read)."""
    timeout = kwargs.pop("timeout", FEED_HTTP_TIMEOUT)
    return get_feed_session().get(url, timeout=timeout, **kwargs)


def fetch_texts_concurrent(urls: Iterable[str]) -> dict[str, str | None]:
    """Fetch multiple URLs concurrently via httpx (non-critical enrichment batch)."""
    url_list = list(urls)
    if not url_list:
        return {}

    try:
        import httpx
    except ImportError:
        log.warning("[FEED_HTTP] httpx unavailable; falling back to sequential requests")
        return {url: _fetch_text_sequential(url) for url in url_list}

    results: dict[str, str | None] = {}

    try:
        timeout = httpx.Timeout(_READ_TIMEOUT_SEC, connect=_CONNECT_TIMEOUT_SEC)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for url in url_list:
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    results[url] = resp.text
                except Exception as exc:
                    log.debug("[FEED_HTTP] concurrent fetch failed %s: %s", url, exc)
                    results[url] = None
    except Exception as exc:
        log.warning("[FEED_HTTP] httpx client failed: %s", exc)
        return {url: _fetch_text_sequential(url) for url in url_list}

    return results


def _fetch_text_sequential(url: str) -> str | None:
    try:
        resp = feed_get(url)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        log.debug("[FEED_HTTP] sequential fetch failed %s: %s", url, exc)
        return None
