from __future__ import annotations

import math
import os
import re
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
    "apikey",
    "authorization",
    "token",
    "accesstoken",
    "secret",
    "clientsecret",
    "password",
    "fredapikey",
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


def _normalized_secret_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_secret_key(key: object) -> bool:
    return _normalized_secret_key(key) in SECRET_KEYS


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _is_secret_key(key)
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        if re.fullmatch(r"\s*Bearer\s+\S+\s*", value, flags=re.IGNORECASE):
            return "Bearer [REDACTED]"
        parts = urlsplit(value)
        if parts.scheme and parts.netloc:
            netloc = parts.netloc
            if "@" in netloc:
                netloc = f"[REDACTED]@{netloc.rsplit('@', 1)[1]}"
            query = [
                (key, "[REDACTED]" if _is_secret_key(key) else item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
            value = urlunsplit(
                (
                    parts.scheme,
                    netloc,
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
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise InvalidResearchInputError("forex edge config must be a mapping")
    if raw.get("schema_version") != 1:
        raise InvalidResearchInputError(
            "forex edge config schema_version must be 1"
        )
    sections: dict[str, Mapping[str, Any]] = {}
    for name in (
        "universe",
        "sources",
        "portfolio",
        "fixing",
        "quality",
        "validation",
    ):
        section = raw.get(name)
        if not isinstance(section, Mapping):
            raise InvalidResearchInputError(
                f"forex edge config {name} must be a mapping"
            )
        sections[name] = section
    universe = sections["universe"]
    portfolio = sections["portfolio"]
    if tuple(universe.get("pairs", ())) != FOREX_PAIRS:
        raise InvalidResearchInputError(
            "configured forex universe does not match frozen universe"
        )
    if raw.get("production_eligible") is not False:
        raise InvalidResearchInputError(
            "production_eligible must remain false"
        )
    try:
        min_currencies = portfolio["min_currencies"]
        top_n = portfolio["top_n"]
    except KeyError as exc:
        raise InvalidResearchInputError(
            "portfolio config is missing required frozen values"
        ) from exc
    if type(min_currencies) is not int:
        raise InvalidResearchInputError(
            "portfolio min_currencies must be an integer"
        )
    if type(top_n) is not int:
        raise InvalidResearchInputError("portfolio top_n must be an integer")
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
