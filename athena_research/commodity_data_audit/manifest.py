from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from athena_research.commodity_data_audit.registry import recommended_acquisition_batch
from athena_research.reproducibility import hash_stable_json


def build_acquisition_manifest(
    *,
    symbols: list[str],
    timeframes: list[str],
    source: str = "mt5",
    normalized_version: str = "commodity_ohlcv_v1",
    date_range_policy: str = "max_available_from_mt5_terminal",
    limits: dict[str, int] | None = None,
    min_bars: dict[str, int] | None = None,
) -> dict[str, Any]:
    defaults = recommended_acquisition_batch()
    manifest = {
        "schema": "engine_a_v4_commodity_acquisition_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "symbols": list(symbols),
        "timeframes": [tf.upper() for tf in timeframes],
        "date_range_policy": date_range_policy,
        "limits": dict(limits or defaults["limits"]),
        "min_bars": dict(min_bars or defaults["min_bars"]),
        "normalized_version": normalized_version,
        "raw_preservation": defaults["raw_preservation"],
        "incremental": defaults["incremental"],
        "rate_limits": defaults["rate_limits"],
        "quality_checks": [
            "duplicate_timestamps",
            "missing_bar_estimate",
            "future_bar_rejection",
            "contract_type_tag",
            "volume_type_tag",
            "manifest_content_hash",
        ],
        "failure_recovery": "retry per symbol/timeframe; persist partial manifest; never mutate raw payloads",
    }
    manifest["content_hash"] = hash_stable_json(
        {
            key: value
            for key, value in manifest.items()
            if key not in {"content_hash", "generated_at"}
        }
    )
    return manifest
