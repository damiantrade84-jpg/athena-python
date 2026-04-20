"""Shared TLS context helpers for websocket clients."""

from __future__ import annotations

import logging
import os
import ssl

log = logging.getLogger("sentinel")
_warned_unverified = False


def _certifi_path() -> str | None:
    try:
        import certifi

        return certifi.where()
    except Exception as exc:
        log.debug("[WS-SSL] certifi CA bundle unavailable: %s", exc)
        return None


def configure_process_ca_bundle() -> str | None:
    """Point common Python HTTP clients at certifi when no CA bundle is set."""
    cafile = _certifi_path()
    if not cafile:
        return None
    os.environ.setdefault("SSL_CERT_FILE", cafile)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)
    return cafile


def default_ssl_context() -> ssl.SSLContext:
    """Return the websocket TLS context for market-data feeds."""
    global _warned_unverified
    verify_flag = str(os.environ.get("ATHENA_MARKET_DATA_WS_SSL_VERIFY", "true")).strip().lower()
    if verify_flag in {"0", "false", "no", "off"}:
        if not _warned_unverified:
            log.warning("[WS-SSL] Market-data websocket certificate verification is disabled")
            _warned_unverified = True
        return ssl._create_unverified_context()

    cafile = configure_process_ca_bundle()
    if cafile:
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()
