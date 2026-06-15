from __future__ import annotations

import math
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from athena_research.forex_edge.models import (
    BlockedDataError,
    InvalidResearchInputError,
)
from athena_research.forex_edge.universe import FOREX_PAIRS


SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "token",
    "access_token",
    "secret",
    "client_secret",
    "password",
    "fred_api_key",
}

_QUALITY_KEYS = {
    "portfolio": (
        "spot_staleness_days",
        "rate_staleness_days",
        "reer_staleness_days",
    ),
    "fixing": ("m5_max_spread_bps",),
    "quality-report": (
        "spot_staleness_days",
        "rate_staleness_days",
        "reer_staleness_days",
        "m5_max_spread_bps",
    ),
    "both": (
        "spot_staleness_days",
        "rate_staleness_days",
        "reer_staleness_days",
        "m5_max_spread_bps",
    ),
}


def default_store_root() -> Path:
    override = os.environ.get("ATHENA_FOREX_EDGE_ROOT", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena" / "research" / "forex_edge"


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() in SECRET_KEYS
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        parts = urlsplit(value)
        if parts.query:
            query = [
                (key, "[REDACTED]" if key.lower() in SECRET_KEYS else item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
            value = urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    parts.path,
                    urlencode(query),
                    parts.fragment,
                )
            )
        secret = os.environ.get("FRED_API_KEY", "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
        return value
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise InvalidResearchInputError("forex edge config must be a mapping")
    if raw.get("schema_version") != 1:
        raise InvalidResearchInputError(
            "forex edge config schema_version must be 1"
        )
    if tuple(raw.get("universe", {}).get("pairs", ())) != FOREX_PAIRS:
        raise InvalidResearchInputError(
            "configured forex universe does not match frozen universe"
        )
    if raw.get("production_eligible") is not False:
        raise InvalidResearchInputError(
            "production_eligible must remain false"
        )
    try:
        min_currencies = int(raw["portfolio"]["min_currencies"])
        top_n = int(raw["portfolio"]["top_n"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidResearchInputError(
            "portfolio config is missing required frozen values"
        ) from exc
    if min_currencies < 12:
        raise InvalidResearchInputError(
            "portfolio min_currencies must be at least 12"
        )
    if top_n != 4:
        raise InvalidResearchInputError("portfolio top_n must be 4")
    return deepcopy(dict(raw))


def validate_empirical_config(config: Mapping[str, Any], lane: str) -> None:
    lane_key = {
        "run-portfolio": "portfolio",
        "run-fixing": "fixing",
        "run-both": "both",
    }.get(lane, lane)
    required = _QUALITY_KEYS.get(lane_key)
    if required is None:
        raise InvalidResearchInputError(f"unknown empirical lane: {lane}")
    quality = config.get("quality", {})
    if not isinstance(quality, Mapping):
        raise BlockedDataError("UNREGISTERED_QUALITY_LIMIT")
    for key in required:
        value = quality.get(key)
        if isinstance(value, bool):
            raise BlockedDataError("UNREGISTERED_QUALITY_LIMIT")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise BlockedDataError("UNREGISTERED_QUALITY_LIMIT") from exc
        if not math.isfinite(numeric) or numeric <= 0:
            raise BlockedDataError("UNREGISTERED_QUALITY_LIMIT")
