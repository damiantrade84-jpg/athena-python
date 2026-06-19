from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from athena_research.commodity_data_audit.capture_state import CaptureState

ALLOWED_BROKER_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "company",
        "server",
        "terminal_build",
        "terminal_name",
    }
)

ALLOWED_SYMBOL_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "terminal_symbol",
        "digits",
        "point",
        "trade_contract_size",
        "currency_base",
        "currency_profit",
        "currency_margin",
        "swap_long",
        "swap_short",
        "swap_mode",
        "session_sample_monday",
        "metadata_note",
    }
)

FORBIDDEN_PERSISTENCE_KEYS: frozenset[str] = frozenset(
    {
        "login",
        "account_login",
        "account_number",
        "account_holder",
        "account_name",
        "holder_name",
        "balance",
        "equity",
        "margin",
        "terminal_path",
        "data_path",
        "commondata_path",
        "password",
        "credential",
        "credentials",
        "token",
        "secret",
        "api_key",
        "MT5_PATH",
        "ATHENA_REAL_ORDERS_CONFIRM",
        "account_currency",
    }
)

ALLOWED_RAW_BAR_KEYS: frozenset[str] = frozenset(
    {
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
        "capture_state",
        "capture_as_of",
    }
)


def validate_raw_bar_for_persistence(bar: dict[str, Any]) -> None:
    from athena_research.commodity_data_audit.freeze_store import FreezeStoreError

    unexpected = sorted(set(bar) - ALLOWED_RAW_BAR_KEYS)
    if unexpected:
        raise FreezeStoreError(f"unexpected raw bar keys: {unexpected}")

    known_states = {member.value for member in CaptureState}
    if bar.get("capture_state") not in known_states:
        raise FreezeStoreError("capture_state is missing or not a known CaptureState value")

    raw_as_of = bar.get("capture_as_of")
    if not isinstance(raw_as_of, str):
        raise FreezeStoreError("capture_as_of must be a valid timezone-aware UTC timestamp")
    try:
        parsed_as_of = datetime.fromisoformat(raw_as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreezeStoreError("capture_as_of must be a valid timezone-aware UTC timestamp") from exc
    if parsed_as_of.tzinfo is None or parsed_as_of.utcoffset() != timedelta(0):
        raise FreezeStoreError("capture_as_of must be a valid timezone-aware UTC timestamp")


def validate_raw_bars_for_persistence(bars: list[dict[str, Any]]) -> None:
    for bar in bars:
        validate_raw_bar_for_persistence(bar)


def sanitize_broker_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Return whitelisted broker/terminal identity fields only."""
    company = raw.get("terminal_company") or raw.get("company")
    server = raw.get("account_server") or raw.get("server")
    build = raw.get("terminal_build")
    name = raw.get("terminal_name")
    out: dict[str, Any] = {}
    if company:
        out["company"] = str(company)
    if server:
        out["server"] = str(server)
    if build is not None:
        out["terminal_build"] = int(build)
    if name:
        out["terminal_name"] = str(name)
    return {key: out[key] for key in ALLOWED_BROKER_METADATA_KEYS if key in out}


def sanitize_symbol_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Return whitelisted symbol contract metadata only."""
    return {key: raw[key] for key in ALLOWED_SYMBOL_METADATA_KEYS if key in raw}


def _leaf_key(path: str) -> str:
    segment = path.rsplit(".", maxsplit=1)[-1]
    if "[" in segment:
        segment = segment.split("[", maxsplit=1)[0]
    return segment


def find_forbidden_persistence_keys(obj: Any, *, prefix: str = "") -> list[str]:
    """Return dotted paths of forbidden MT5/account/credential keys in JSON-like data."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            leaf = _leaf_key(path)
            if leaf in FORBIDDEN_PERSISTENCE_KEYS and leaf != "currency_margin":
                found.append(path)
            found.extend(find_forbidden_persistence_keys(value, prefix=path))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            found.extend(find_forbidden_persistence_keys(item, prefix=f"{prefix}[{index}]"))
    return found
