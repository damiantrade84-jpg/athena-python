"""etoro_executor.py — eToro Public API adapter (scaffold, default-safe).

This module is intentionally non-invasive:
- no execution path wiring
- no risk bypasses
- no live-order side effects unless called explicitly

It provides request helpers plus instrument-resolution utilities so the rest of
Athena can integrate eToro in a controlled, paper-first workflow.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from urllib import error, parse, request

from config import CONFIG

log = logging.getLogger("sentinel")

_API_BASE = "https://public-api.etoro.com"


@dataclass(frozen=True)
class EtoroCredentials:
    api_key: str
    user_key: str


def etoro_enabled(cfg: dict | None = None) -> bool:
    resolved = cfg or CONFIG
    etoro_cfg = resolved.get("ETORO", {}) or {}
    return bool(etoro_cfg.get("ENABLED", False))


def etoro_is_demo_mode(cfg: dict | None = None) -> bool:
    resolved = cfg or CONFIG
    etoro_cfg = resolved.get("ETORO", {}) or {}
    return bool(etoro_cfg.get("DEMO_MODE", True))


def etoro_base_url(cfg: dict | None = None) -> str:
    resolved = cfg or CONFIG
    etoro_cfg = resolved.get("ETORO", {}) or {}
    return str(etoro_cfg.get("BASE_URL", _API_BASE) or _API_BASE).rstrip("/")


def etoro_get_credentials() -> EtoroCredentials | None:
    api_key = str(os.environ.get("ETORO_API_KEY", "") or "").strip()
    user_key = str(os.environ.get("ETORO_USER_KEY", "") or "").strip()
    if not api_key or not user_key:
        return None
    return EtoroCredentials(api_key=api_key, user_key=user_key)


def _headers(creds: EtoroCredentials, request_id: str | None = None) -> dict[str, str]:
    return {
        "x-api-key": creds.api_key,
        "x-user-key": creds.user_key,
        "x-request-id": request_id or str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def _request_json(
    method: str,
    url: str,
    creds: EtoroCredentials,
    payload: dict | None = None,
    timeout_sec: float = 10.0,
    request_id: str | None = None,
) -> dict:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=body,
        headers=_headers(creds, request_id=request_id),
        method=method.upper(),
    )
    try:
        with request.urlopen(req, timeout=float(timeout_sec)) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8") if hasattr(exc, "read") else str(exc)
        log.error(f"[ETORO] HTTP {exc.code} {method} {url} -> {detail}")
        raise RuntimeError(f"ETORO_HTTP_{exc.code}: {detail}") from exc
    except error.URLError as exc:
        log.error(f"[ETORO] Network error {method} {url}: {exc}")
        raise RuntimeError(f"ETORO_NETWORK_ERROR: {exc}") from exc


def resolve_instrument_id(
    symbol: str,
    creds: EtoroCredentials,
    *,
    timeout_sec: float = 10.0,
    base_url: str | None = None,
) -> int:
    """Resolve symbol to immutable eToro instrumentId.

    Enforces exact-match resolution on `internalSymbolFull` so callers never guess
    or hardcode IDs.
    """
    requested_symbol = str(symbol or "").strip().upper()
    if not requested_symbol:
        raise ValueError("symbol is required")
    root = (base_url or etoro_base_url()).rstrip("/")
    qs = parse.urlencode({"internalSymbolFull": requested_symbol})
    url = f"{root}/api/v1/market-data/search?{qs}"
    data = _request_json("GET", url, creds=creds, timeout_sec=timeout_sec)
    for item in data.get("items", []) or []:
        if str(item.get("internalSymbolFull", "")).upper() == requested_symbol:
            try:
                return int(item["instrumentId"])
            except (KeyError, TypeError, ValueError) as exc:
                break
    raise LookupError(f"Instrument not found for symbol={requested_symbol}")


def build_market_open_by_amount_payload(
    *,
    instrument_id: int,
    amount: float,
    is_buy: bool,
    leverage: int = 1,
) -> dict:
    if int(instrument_id) <= 0:
        raise ValueError("instrument_id must be positive")
    if float(amount) <= 0:
        raise ValueError("amount must be positive")
    return {
        "InstrumentId": int(instrument_id),
        "Amount": float(amount),
        "Leverage": int(leverage),
        "IsBuy": bool(is_buy),
    }


def signal_direction_to_is_buy(direction: str) -> bool:
    norm = str(direction or "").strip().upper()
    if norm == "LONG":
        return True
    if norm == "SHORT":
        return False
    raise ValueError(f"Unsupported direction: {direction}")

