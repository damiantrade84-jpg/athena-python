from __future__ import annotations

import json
import os
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from athena_research.forex_edge.config import redact_secrets
from athena_research.forex_edge.models import InvalidResearchInputError, ReasonCode
from athena_research.forex_edge.sources.common import HttpGet, requests_get


_NY = ZoneInfo("America/New_York")
_PERCENT_UNITS = {"percent", "percent per annum"}


def percent_to_decimal(value: float, unit: str) -> float:
    if unit.strip().lower() not in _PERCENT_UNITS:
        raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
    return float(value) / 100.0


def _available_time(realtime_start: str, kind: str) -> pd.Timestamp:
    if kind not in {"spot", "rate"}:
        raise InvalidResearchInputError(f"unknown FRED kind: {kind}")
    day = date.fromisoformat(realtime_start)
    release_time = time(16, 15) if kind == "spot" else time(23, 59, 59)
    return pd.Timestamp(
        datetime.combine(day, release_time, tzinfo=_NY)
    ).tz_convert("UTC")


def normalize_fred_observations(
    payload: dict[str, Any],
    *,
    series_id: str,
    currency: str,
    kind: str,
    unit: str,
    usd_per_currency: bool | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for observation in payload.get("observations", []):
        if not isinstance(observation, dict):
            raise InvalidResearchInputError("FRED observations must be objects")
        raw = str(observation.get("value", "")).strip()
        if raw in {"", "."}:
            continue
        realtime_start = str(observation.get("realtime_start", "")).strip()
        if not realtime_start:
            raise ValueError(ReasonCode.UNVERIFIED_AVAILABILITY.value)
        raw_value = float(raw)
        if kind == "spot":
            if usd_per_currency is None or raw_value <= 0:
                raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
            value = raw_value if usd_per_currency else 1.0 / raw_value
            normalized_unit = "USD_PER_CURRENCY"
            availability_verified = True
            availability_reason = ""
        elif kind == "rate":
            percent_to_decimal(raw_value, unit)
            value = raw_value
            normalized_unit = unit
            availability_verified = False
            availability_reason = ReasonCode.UNVERIFIED_AVAILABILITY.value
        else:
            raise InvalidResearchInputError(f"unknown FRED kind: {kind}")
        rows.append(
            {
                "timestamp": pd.Timestamp(observation["date"], tz="UTC"),
                "available_time": _available_time(realtime_start, kind),
                "value": value,
                "raw_value": raw_value,
                "series_id": series_id,
                "currency": currency,
                "unit": normalized_unit,
                "raw_unit": unit,
                "realtime_start": realtime_start,
                "realtime_end": str(observation.get("realtime_end", "")),
                "availability_verified": availability_verified,
                "availability_reason": availability_reason,
            }
        )
    return pd.DataFrame(rows)


def fetch_fred_series(
    series_id: str,
    *,
    api_base: str,
    api_key_env: str,
    observation_start: str = "2000-01-01",
    observation_end: str | None = None,
    http_get: HttpGet = requests_get,
) -> bytes:
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{api_key_env} not configured")
    params: dict[str, object] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "output_type": 4,
        "observation_start": observation_start,
    }
    if observation_end:
        params["observation_end"] = observation_end
    response = None
    try:
        response = http_get(
            f"{api_base.rstrip('/')}/series/observations",
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        json.loads(response.content.decode("utf-8"))
        return response.content
    except Exception as exc:
        safe = redact_secrets(
            {
                "series_id": series_id,
                "url": response.url if response is not None else api_base,
            }
        )
        raise RuntimeError(f"FRED request failed: {safe}") from exc
