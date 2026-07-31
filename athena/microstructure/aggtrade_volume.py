"""Shared read-only crypto trade-bucket helpers.

The helpers deliberately only query already-collected trade buckets. They do
not start streams, write buckets, or contact exchanges.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable

log = logging.getLogger("sentinel")


def normalize_trade_symbol(display: str | None) -> str:
    return str(display or "").replace("/", "").upper()


def resolve_trade_bucket_exchange(cfg: dict | None, default: str = "bybit") -> str:
    """Resolve the exchange namespace used for stored crypto trade buckets."""
    cfg = cfg or {}
    raw = str(cfg.get("TRADE_BUCKET_EXCHANGE") or default or "bybit").strip().lower()
    if raw in {"bybit", "binance"}:
        return raw
    return "bybit"


def trade_bucket_source_name(exchange: str | None) -> str:
    exchange_key = str(exchange or "").strip().lower()
    if exchange_key == "bybit":
        return "bybit_public_trade"
    if exchange_key == "binance":
        return "binance_aggtrade"
    return "trade_buckets"


def coerce_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = float(value)
        if abs(ts) > 1_000_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def trade_bucket_session_id(reference_ts: Any = None) -> str | None:
    dt = coerce_utc_datetime(reference_ts)
    return dt.strftime("%Y-%m-%d") if dt else None


def trade_bucket_max_last_ts(reference_ts: Any = None, require_fresh: bool = True) -> float | None:
    if reference_ts is None or require_fresh:
        return None
    dt = coerce_utc_datetime(reference_ts)
    return dt.timestamp() if dt else None


def _cfg_lookup(cfg: dict, key: str, default: Any = None, *, asset_type: str = "crypto") -> Any:
    asset = str(asset_type or "").strip().lower()
    for map_key in (f"{key}_CLASS", f"{key}_BY_ASSET"):
        values = cfg.get(map_key, {}) or {}
        if asset and isinstance(values, dict) and asset in values:
            return values[asset]
    return cfg.get(key, default)


def _as_fraction(value: Any, default: float, *, clamp_minmax: tuple[float, float]) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v > 1.0:
        v = v / 100.0
    lo, hi = clamp_minmax
    return max(lo, min(hi, v))


def _as_value_area_pct(value: Any, default: float = 0.70) -> float:
    return _as_fraction(value, default, clamp_minmax=(0.1, 0.95))


def _as_lvn_threshold(value: Any, default: float = 0.15) -> float:
    return _as_fraction(value, default, clamp_minmax=(0.0, 0.99))


def _iso_from_ts(raw: Any) -> str | None:
    dt = coerce_utc_datetime(raw)
    return dt.isoformat() if dt else None


def query_trade_buckets(
    display: str,
    cfg: dict | None,
    *,
    reference_ts: Any = None,
    require_fresh: bool = True,
    now_fn: Callable[[], float] | None = None,
) -> list[dict]:
    cfg = cfg or {}
    symbol = normalize_trade_symbol(display)
    exchange = resolve_trade_bucket_exchange(cfg)
    max_age_sec = int(cfg.get("TRADE_BUCKET_MAX_AGE_SEC", 300))
    now = now_fn or time.time
    from athena.microstructure import trade_bucket_store

    return trade_bucket_store.query_session_buckets(
        symbol,
        exchange=exchange,
        session_id=trade_bucket_session_id(reference_ts),
        min_last_ts=(float(now()) - max_age_sec) if require_fresh else None,
        max_last_ts=trade_bucket_max_last_ts(reference_ts, require_fresh=require_fresh),
    )


def build_trade_bucket_volume_profile(
    display: str,
    cfg: dict | None,
    *,
    reference_ts: Any = None,
    require_fresh: bool = True,
    now_fn: Callable[[], float] | None = None,
) -> dict:
    cfg = cfg or {}
    exchange = resolve_trade_bucket_exchange(cfg)
    source_name = trade_bucket_source_name(exchange)
    min_buckets = int(cfg.get("TRADE_BUCKET_MIN_LEVELS", 8))
    min_volume = float(cfg.get("TRADE_BUCKET_MIN_VOLUME", 0.0))
    va_pct = _as_value_area_pct(
        _cfg_lookup(cfg, "VP_VALUE_AREA_PCT", cfg.get("VP_VA_PCT", 0.70)),
        0.70,
    )
    lvn_factor = _as_lvn_threshold(
        _cfg_lookup(cfg, "VP_LVN_THRESHOLD", cfg.get("VP_LVN_FACTOR", 0.15)),
        0.15,
    )
    try:
        from volume_profile import compute_bucketed_volume_profile

        rows = query_trade_buckets(
            display,
            cfg,
            reference_ts=reference_ts,
            require_fresh=require_fresh,
            now_fn=now_fn,
        )
        if len(rows) < min_buckets:
            return {"valid": False, "reason": "insufficient_trade_buckets", "bucket_count": len(rows)}
        vp = compute_bucketed_volume_profile(rows, value_area_pct=va_pct, lvn_threshold=lvn_factor)
        if not vp.get("profile_valid"):
            return {"valid": False, "reason": "trade_bucket_profile_invalid", "bucket_count": len(rows)}
        if float(vp.get("total_volume") or 0.0) < min_volume:
            return {"valid": False, "reason": "trade_bucket_volume_too_low", "bucket_count": len(rows)}
        out = dict(vp)
        out["valid"] = True
        out["volume_source"] = source_name
        out["volume_exchange"] = exchange
        out["bucket_count"] = len(rows)
        out["session_start"] = _iso_from_ts(min((r.get("first_ts") or r.get("last_ts") or 0) for r in rows))
        out["session_end"] = _iso_from_ts(max((r.get("last_ts") or r.get("first_ts") or 0) for r in rows))
        out["balance_ratio"] = _balance_ratio(out)
        return out
    except Exception as exc:
        log.debug("[AggTradeVolume] VP unavailable for %s: %s", display, exc)
        return {"valid": False, "reason": "trade_bucket_error"}


def trade_bucket_profile_price_consistent(
    vp: dict | None,
    candles: list | None,
    *,
    tolerance_pct: float = 0.005,
    lookback: int = 48,
) -> tuple[bool, str | None]:
    """True when a trade-bucket VP's POC sits inside the caller's own price range.

    Trade buckets and OHLCV can come from different venues: buckets follow
    ``SCALP_ENGINE.TRADE_BUCKET_EXCHANGE`` while Engine B's crypto candles,
    zones and ATR follow ``ENGINE_AB_CRYPTO_SIGNAL_FEED``. Normal perpetual
    basis is a few bps, so a venue-consistent POC stays well inside the candle
    range; a venue, symbol or session mismatch puts it outside.

    Returns ``(ok, reason)``; ``reason`` is ``None`` when ok. Unevaluable inputs
    return ok — the caller's own candle-sufficiency gates cover that case, and
    failing here would attribute an unrelated data gap to a venue mismatch.
    """
    def _num(value: Any) -> float | None:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    if not isinstance(vp, dict) or not vp.get("valid"):
        return True, None
    poc = _num(vp.get("poc"))
    if poc is None or poc <= 0 or not candles:
        return True, None

    highs: list[float] = []
    lows: list[float] = []
    for candle in list(candles)[-max(1, int(lookback)):]:
        if not isinstance(candle, dict):
            continue
        high = _num(candle.get("high"))
        low = _num(candle.get("low"))
        if high is not None and low is not None and high > 0 and low > 0:
            highs.append(high)
            lows.append(low)
    if not highs or not lows:
        return True, None

    tol = max(0.0, float(tolerance_pct))
    upper = max(highs) * (1.0 + tol)
    lower = min(lows) * (1.0 - tol)
    if lower <= poc <= upper:
        return True, None
    return False, "aggtrade_vp_venue_price_mismatch"


def check_trade_bucket_cvd(
    display: str,
    cfg: dict | None,
    *,
    reference_ts: Any = None,
    require_fresh: bool = True,
    now_fn: Callable[[], float] | None = None,
) -> dict:
    cfg = cfg or {}
    exchange = resolve_trade_bucket_exchange(cfg)
    source_name = trade_bucket_source_name(exchange)
    min_buckets = int(cfg.get("TRADE_BUCKET_MIN_LEVELS", 8))
    try:
        rows = query_trade_buckets(
            display,
            cfg,
            reference_ts=reference_ts,
            require_fresh=require_fresh,
            now_fn=now_fn,
        )
        if len(rows) < min_buckets:
            return {
                "direction": None,
                "cvd_value": 0,
                "cvd_slope": 0,
                "source": "unavailable",
                "reason": "insufficient_trade_buckets",
                "bucket_count": len(rows),
            }
        ordered = sorted(rows, key=lambda r: float(r.get("last_ts") or r.get("price_bucket") or 0.0))
        deltas = [float(r.get("delta") or 0.0) for r in ordered]
        cvd = sum(deltas)
        slope = (sum(deltas[-3:]) - sum(deltas[:3])) if len(deltas) >= 6 else cvd
        min_slope = float(_cfg_lookup(cfg, "CVD_MIN_SLOPE", 0.0))
        direction = "LONG" if slope > min_slope else "SHORT" if slope < -min_slope else None
        return {
            "direction": direction,
            "cvd_value": round(cvd, 2),
            "cvd_slope": round(slope, 4),
            "source": source_name,
            "exchange": exchange,
            "bucket_count": len(rows),
        }
    except Exception as exc:
        log.debug("[AggTradeVolume] CVD unavailable for %s: %s", display, exc)
        return {"direction": None, "cvd_value": 0, "cvd_slope": 0, "source": "error", "reason": "trade_bucket_error"}


def source_fidelity(source: Any, *, domain: str) -> dict:
    raw = str(source or "unknown").strip().lower()
    aliases = {
        "binance_aggtrade": "binance_aggtrade",
        "bybit_public_trade": "bybit_public_trade",
        "bybit_publictrade": "bybit_public_trade",
        "bybit_trade": "bybit_public_trade",
        "bybit": "bybit_public_trade",
        "trade_buckets": "trade_buckets",
        "candle": "candles",
        "candle_volume": "candle_volume",
        "candles": "candles",
        "range_proxy": "range_proxy",
        "mixed_range_proxy": "mixed_range_proxy",
        "mt5_tick": "mt5_tick",
        "mt5_tick_proxy": "mt5_tick",
        "binance_ws": "binance_candle",
        "binance_candle": "binance_candle",
        "disabled": "disabled",
        "error": "error",
        "unavailable": "unavailable",
        "unknown": "unknown",
    }
    normalized = aliases.get(raw, raw)
    real_trade_flow = normalized in {"binance_aggtrade", "bybit_public_trade", "trade_buckets"}
    unavailable = normalized in {"disabled", "error", "unavailable", "unknown"}
    if real_trade_flow:
        fidelity = "real_trade_bucket"
    elif normalized in {"candle_volume", "binance_candle"}:
        fidelity = f"{domain}_candle_volume_proxy"
    elif normalized in {"range_proxy", "mixed_range_proxy", "mt5_tick", "candles"}:
        fidelity = f"{domain}_{normalized}_proxy"
    else:
        fidelity = f"{domain}_proxy"
    return {
        "raw_source": raw,
        "source": normalized,
        "fidelity": fidelity,
        "uses_real_trade_buckets": real_trade_flow,
        "uses_real_order_flow": real_trade_flow,
        "is_proxy": not real_trade_flow,
        "is_delayed_real": False,
        "is_unavailable": unavailable,
    }


def _balance_ratio(vp: dict) -> float | None:
    try:
        vah = float(vp.get("vah"))
        val = float(vp.get("val"))
        high = float(vp.get("session_high"))
        low = float(vp.get("session_low"))
    except (TypeError, ValueError):
        return None
    span = high - low
    if span <= 0:
        return None
    return round(max(0.0, min(1.0, (vah - val) / span)), 4)
