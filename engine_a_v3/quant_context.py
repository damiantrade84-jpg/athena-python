"""Engine A quant scorer — context assembler for volume and subsystem signals."""

from __future__ import annotations

import time
from typing import Any

from engine_a_v3.subsystems import (
    ST_AVAILABLE,
    ST_NA,
    ST_NEUTRAL,
    ST_UNAVAILABLE,
    subsystems_enabled,
    z_to_subsystem_entry,
)

_MICRO_METRIC_BLEND: tuple[tuple[str, float], ...] = (
    ("orderflow_delta", 0.40),
    ("order_book_imbalance", 0.35),
    ("liquidity_wall_detection", 0.15),
    ("liquidity_pressure", 0.10),
)


def microstructure_scoring_enabled() -> bool:
    try:
        from config import CONFIG

        if not bool(CONFIG.get("CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED", False)):
            return False
        micro_cfg = CONFIG.get("ENGINE_A_V3_MICROSTRUCTURE") or {}
        return bool(micro_cfg.get("ENABLED", True))
    except Exception:
        return False


def _microstructure_max_age_sec() -> float:
    try:
        from config import CONFIG

        micro_cfg = CONFIG.get("ENGINE_A_V3_MICROSTRUCTURE") or {}
        return max(1.0, float(micro_cfg.get("MAX_AGE_SEC", 120) or 120))
    except Exception:
        return 120.0


def _microstructure_exchange() -> str:
    try:
        from config import CONFIG

        micro_cfg = CONFIG.get("ENGINE_A_V3_MICROSTRUCTURE") or {}
        exchange = str(micro_cfg.get("EXCHANGE", "binance") or "binance").strip().lower()
        return exchange or "binance"
    except Exception:
        return "binance"


def _normalize_micro_metrics(raw: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for key, _weight in _MICRO_METRIC_BLEND:
        value = raw.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        out[key] = max(-1.0, min(1.0, numeric))
    return out or None


def resolve_live_microstructure_metrics(
    pair: dict[str, Any],
    *,
    runtime_cache: dict[str, Any] | None = None,
) -> dict[str, float] | None:
    """Resolve fresh live microstructure metrics for a crypto pair."""
    if not microstructure_scoring_enabled():
        return None
    if str(pair.get("type") or "").lower() != "crypto":
        return None
    symbol = str(pair.get("symbol") or "").replace("/", "").upper()
    if not symbol:
        return None

    max_age = _microstructure_max_age_sec()
    now = time.time()

    if isinstance(runtime_cache, dict):
        cached = runtime_cache.get(symbol)
        if isinstance(cached, dict):
            age = now - float(cached.get("_updated_ts") or 0)
            if age <= max_age:
                normalized = _normalize_micro_metrics(cached)
                if normalized:
                    return normalized

    try:
        from athena.microstructure.microstructure_store import query_latest

        rows = query_latest(symbol, _microstructure_exchange(), limit=1)
        if rows:
            row = rows[0]
            age = now - float(row.get("timestamp") or 0)
            if age <= max_age:
                return _normalize_micro_metrics(row)
    except Exception:
        pass
    return None


def _carry_entry(display: str, *, as_of_date: str | None = None) -> dict[str, Any]:
    try:
        from carry_feed import _PAIR_CARRY_FORMULA, get_carry_z

        if not _PAIR_CARRY_FORMULA.get(display):
            return {"state": ST_NA}
        return z_to_subsystem_entry(get_carry_z(display, as_of_date=as_of_date))
    except Exception:
        return {"state": ST_UNAVAILABLE}


def _sentiment_entry(display: str, *, as_of_date: str | None = None) -> dict[str, Any]:
    try:
        from cot_feed import _PAIR_FORMULA, get_cot_z

        if not _PAIR_FORMULA.get(display):
            return {"state": ST_NA}
        return z_to_subsystem_entry(get_cot_z(display, as_of_date=as_of_date))
    except Exception:
        return {"state": ST_UNAVAILABLE}


def _microstructure_entry(
    pair_type: str | None,
    metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    if str(pair_type or "").lower() != "crypto":
        return {"state": ST_NA}
    if not microstructure_scoring_enabled():
        return {"state": ST_NA}
    normalized = _normalize_micro_metrics(metrics if isinstance(metrics, dict) else None)
    if not normalized:
        return {"state": ST_UNAVAILABLE}

    terms: list[tuple[float, float]] = []
    for key, weight in _MICRO_METRIC_BLEND:
        value = normalized.get(key)
        if value is None or value == 0.0:
            continue
        terms.append((weight, value))
    if not terms:
        return {"signal": 0.0, "quality": 0.0, "state": ST_NEUTRAL}

    weight_sum = sum(weight for weight, _ in terms)
    blended = sum(weight * value for weight, value in terms) / weight_sum
    magnitude = min(1.0, abs(blended))
    if magnitude < 0.05:
        return {"signal": 0.0, "quality": 0.0, "state": ST_NEUTRAL, "blended": round(blended, 4)}
    return {
        "signal": round(blended, 4),
        "quality": round(magnitude, 4),
        "state": ST_AVAILABLE,
        "blended": round(blended, 4),
        "metrics": normalized,
    }


def build_quant_context(
    *,
    volume_ratio: Any = None,
    display: str | None = None,
    pair_type: str | None = None,
    as_of_date: str | None = None,
    subsystem_entries: dict[str, Any] | None = None,
    microstructure_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the quant scorer context. Only populated keys are returned."""
    context: dict[str, Any] = {}
    if volume_ratio is not None:
        try:
            context["volume_ratio"] = float(volume_ratio)
        except (TypeError, ValueError):
            pass

    if subsystem_entries:
        context.update(subsystem_entries)
        return context

    if not subsystems_enabled() or not display:
        return context

    context["carry"] = _carry_entry(display, as_of_date=as_of_date)
    context["sentiment"] = _sentiment_entry(display, as_of_date=as_of_date)
    context["microstructure"] = _microstructure_entry(pair_type, microstructure_metrics)
    return context
