"""Server-resolved session/regime context for Engine D AI scalp review."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from config import CONFIG

_TZ_NY = ZoneInfo("America/New_York")
_TZ_LONDON = ZoneInfo("Europe/London")

_CURRENT_SESSIONS = (
    "ASIA",
    "LONDON",
    "NY_PREMARKET",
    "NY_OPEN",
    "NY_MIDDAY",
    "NY_AFTERNOON",
    "NY_CLOSE",
    "OVERNIGHT",
)

_VOLATILITY_REGIMES = ("LOW", "NORMAL", "HIGH", "EXTREME")
_DELIVERY_WINDOWS = (
    "CLEAN_EXPANSION_WINDOW",
    "COMPRESSION_WINDOW",
    "LATE_SESSION_RISK",
    "OVERNIGHT_AVOID",
)
_PREFERRED_MODELS = (
    "NY_TREND_SQUEEZE",
    "LONDON_MEAN_REVERSION",
    "LOW_VOL_FAILED_BREAKOUT",
    "NO_PREFERENCE",
)


def _session_cfg() -> dict[str, Any]:
    scalp_cfg = CONFIG.get("AI_SCALP_CHART_REVIEW") or {}
    ctx_cfg = scalp_cfg.get("SESSION_CONTEXT") or {}
    return ctx_cfg if isinstance(ctx_cfg, dict) else {}


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    raw = str(value or "").strip()
    if not raw or ":" not in raw:
        return default
    parts = raw.split(":", 1)
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return default


def _coerce_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def _minutes_in_window(now_local: datetime, start: time, end: time) -> tuple[int, int]:
    """Return (minutesFromOpen, minutesToClose) for now_local within [start, end)."""
    open_dt = now_local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    close_dt = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if close_dt <= open_dt:
        close_dt = close_dt.replace(day=close_dt.day + 1)
    elapsed = int((now_local - open_dt).total_seconds() // 60)
    remaining = int((close_dt - now_local).total_seconds() // 60)
    return max(0, elapsed), max(0, remaining)


def _in_time_range(now_local: datetime, start: time, end: time) -> bool:
    t = now_local.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def _resolve_ny_subsession(now_ny: datetime, cfg: dict[str, Any]) -> tuple[str, time, time]:
    pre_start = time(*_parse_hhmm(cfg.get("NY_PREMARKET_START_ET"), (8, 0)))
    open_start = time(*_parse_hhmm(cfg.get("NY_OPEN_START_ET"), (9, 30)))
    open_end = time(*_parse_hhmm(cfg.get("NY_OPEN_END_ET"), (10, 30)))
    midday_end = time(*_parse_hhmm(cfg.get("NY_MIDDAY_END_ET"), (14, 0)))
    afternoon_end = time(*_parse_hhmm(cfg.get("NY_AFTERNOON_END_ET"), (15, 30)))
    close_end = time(*_parse_hhmm(cfg.get("NY_CLOSE_END_ET"), (16, 0)))

    if _in_time_range(now_ny, pre_start, open_start):
        return "NY_PREMARKET", pre_start, open_start
    if _in_time_range(now_ny, open_start, open_end):
        return "NY_OPEN", open_start, open_end
    if _in_time_range(now_ny, open_end, midday_end):
        return "NY_MIDDAY", open_end, midday_end
    if _in_time_range(now_ny, midday_end, afternoon_end):
        return "NY_AFTERNOON", midday_end, afternoon_end
    if _in_time_range(now_ny, afternoon_end, close_end):
        return "NY_CLOSE", afternoon_end, close_end
    return "OVERNIGHT", time(0, 0), time(23, 59)


def _resolve_current_session(
    now_utc: datetime,
    asset_type: str,
    cfg: dict[str, Any],
) -> tuple[str, time, time]:
    asset = str(asset_type or "forex").strip().lower()
    now_ny = now_utc.astimezone(_TZ_NY)
    now_london = now_utc.astimezone(_TZ_LONDON)

    if asset == "crypto":
        asia_start_h = int(cfg.get("ASIA_START_UTC_HOUR", 1))
        asia_end_h = int(cfg.get("ASIA_END_UTC_HOUR", 9))
        if asia_start_h <= now_utc.hour < asia_end_h:
            start = time(asia_start_h, 0)
            end = time(asia_end_h, 0)
            return "ASIA", start, end

    ny_session, ny_start, ny_end = _resolve_ny_subsession(now_ny, cfg)
    if ny_session != "OVERNIGHT":
        return ny_session, ny_start, ny_end

    lon_start = time(*_parse_hhmm(cfg.get("LONDON_START_LOCAL"), (7, 0)))
    lon_end = time(*_parse_hhmm(cfg.get("LONDON_END_LOCAL"), (16, 0)))
    if _in_time_range(now_london, lon_start, lon_end):
        return "LONDON", lon_start, lon_end

    if asset == "crypto":
        return "OVERNIGHT", time(0, 0), time(23, 59)
    return "OVERNIGHT", time(0, 0), time(23, 59)


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_volatility_regime(signal: dict | None, cfg: dict[str, Any]) -> str:
    sig = signal if isinstance(signal, dict) else {}
    market_state = str(sig.get("market_state") or sig.get("marketState") or "").lower()
    atr = _to_float(sig.get("atr"))
    price = _to_float(sig.get("price") or sig.get("entry"))
    if price and atr and price > 0:
        atr_pct = (atr / price) * 100.0
        low_max = float(cfg.get("ATR_PCT_LOW_MAX", 0.08))
        normal_max = float(cfg.get("ATR_PCT_NORMAL_MAX", 0.15))
        high_max = float(cfg.get("ATR_PCT_HIGH_MAX", 0.30))
        if atr_pct <= low_max:
            regime = "LOW"
        elif atr_pct <= normal_max:
            regime = "NORMAL"
        elif atr_pct <= high_max:
            regime = "HIGH"
        else:
            regime = "EXTREME"
    else:
        regime = "NORMAL"

    if market_state == "balance" and regime in ("HIGH", "EXTREME"):
        regime = "NORMAL"
    elif market_state == "imbalance" and regime == "LOW":
        regime = "NORMAL"
    return regime


def _resolve_delivery_window(
    current_session: str,
    volatility_regime: str,
    market_state: str,
    *,
    profit_lock_active: bool,
) -> str:
    if profit_lock_active:
        return "LATE_SESSION_RISK"
    if current_session in ("OVERNIGHT", "ASIA"):
        return "OVERNIGHT_AVOID"
    if current_session in ("NY_CLOSE", "NY_AFTERNOON"):
        return "LATE_SESSION_RISK"
    if current_session == "NY_OPEN" and volatility_regime in ("NORMAL", "HIGH", "EXTREME"):
        return "CLEAN_EXPANSION_WINDOW"
    if current_session in ("NY_MIDDAY",) or market_state == "balance":
        return "COMPRESSION_WINDOW"
    if current_session == "LONDON" and volatility_regime in ("LOW", "NORMAL"):
        return "COMPRESSION_WINDOW"
    if current_session == "NY_PREMARKET":
        return "COMPRESSION_WINDOW"
    return "CLEAN_EXPANSION_WINDOW"


def _resolve_preferred_model(
    current_session: str,
    volatility_regime: str,
    market_state: str,
    delivery_window: str,
) -> str:
    if delivery_window == "OVERNIGHT_AVOID":
        return "NO_PREFERENCE"
    if current_session in ("NY_OPEN", "NY_PREMARKET") and volatility_regime in ("NORMAL", "HIGH", "EXTREME"):
        return "NY_TREND_SQUEEZE"
    if current_session == "LONDON" and volatility_regime in ("LOW", "NORMAL"):
        return "LONDON_MEAN_REVERSION"
    if volatility_regime == "LOW" and market_state == "balance":
        return "LOW_VOL_FAILED_BREAKOUT"
    if current_session == "NY_MIDDAY" and market_state == "balance":
        return "LONDON_MEAN_REVERSION"
    return "NO_PREFERENCE"


def _resolve_session_conviction_hint(
    current_session: str,
    delivery_window: str,
    *,
    profit_lock_active: bool,
) -> dict[str, str]:
    if profit_lock_active:
        return {
            "sessionQuality": "NO_TRADE_WINDOW",
            "sessionConvictionAdjustment": "HARD_REJECT",
        }
    if delivery_window == "OVERNIGHT_AVOID":
        return {
            "sessionQuality": "NO_TRADE_WINDOW",
            "sessionConvictionAdjustment": "HARD_REJECT",
        }
    if delivery_window == "LATE_SESSION_RISK":
        return {
            "sessionQuality": "CHOP_RISK",
            "sessionConvictionAdjustment": "DOWNGRADE",
        }
    if current_session == "NY_MIDDAY" or delivery_window == "COMPRESSION_WINDOW":
        return {
            "sessionQuality": "CHOP_RISK",
            "sessionConvictionAdjustment": "DOWNGRADE",
        }
    if current_session == "NY_OPEN" and delivery_window == "CLEAN_EXPANSION_WINDOW":
        return {
            "sessionQuality": "CLEAN_DELIVERY",
            "sessionConvictionAdjustment": "UPGRADE",
        }
    return {
        "sessionQuality": "ACCEPTABLE",
        "sessionConvictionAdjustment": "NEUTRAL",
    }


def resolve_engine_d_session_context(
    *,
    when: datetime | None = None,
    asset_type: str = "forex",
    signal: dict | None = None,
    session_risk_state: dict | None = None,
) -> dict[str, Any]:
    """Resolve server-trusted session/regime context for AI review (not from screenshot)."""
    cfg = _session_cfg()
    if cfg.get("ENABLED", True) is False:
        return {
            "currentSession": "OVERNIGHT",
            "minutesFromSessionOpen": None,
            "minutesToSessionClose": None,
            "volatilityRegime": "NORMAL",
            "deliveryWindow": "OVERNIGHT_AVOID",
            "preferredModel": "NO_PREFERENCE",
            "profitLockActive": False,
            "sessionQuality": "ACCEPTABLE",
            "sessionConvictionAdjustment": "NEUTRAL",
            "resolverDisabled": True,
        }

    now_utc = (_coerce_utc_datetime(when) or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sig = signal if isinstance(signal, dict) else {}
    risk = session_risk_state if isinstance(session_risk_state, dict) else {}
    if not risk and isinstance(sig.get("session_risk_state"), dict):
        risk = sig["session_risk_state"]

    net_r = _to_float(risk.get("net_r_today")) or 0.0
    profit_lock = bool(risk.get("size_cut_active")) or net_r >= float(cfg.get("PROFIT_LOCK_NET_R", 2.0))

    current_session, win_start, win_end = _resolve_current_session(
        now_utc,
        asset_type,
        cfg,
    )

    if current_session == "ASIA":
        now_local = now_utc
    elif current_session == "LONDON":
        now_local = now_utc.astimezone(_TZ_LONDON)
    elif current_session.startswith("NY_") or current_session == "OVERNIGHT":
        now_local = now_utc.astimezone(_TZ_NY)
    else:
        now_local = now_utc.astimezone(_TZ_NY)

    mins_open, mins_close = _minutes_in_window(now_local, win_start, win_end)
    market_state = str(sig.get("market_state") or sig.get("marketState") or "").lower()
    volatility_regime = _resolve_volatility_regime(sig, cfg)
    delivery_window = _resolve_delivery_window(
        current_session,
        volatility_regime,
        market_state,
        profit_lock_active=profit_lock,
    )
    preferred_model = _resolve_preferred_model(
        current_session,
        volatility_regime,
        market_state,
        delivery_window,
    )
    hint = _resolve_session_conviction_hint(
        current_session,
        delivery_window,
        profit_lock_active=profit_lock,
    )

    return {
        "currentSession": current_session,
        "minutesFromSessionOpen": mins_open,
        "minutesToSessionClose": mins_close,
        "volatilityRegime": volatility_regime,
        "deliveryWindow": delivery_window,
        "preferredModel": preferred_model,
        "profitLockActive": profit_lock,
        "sessionQuality": hint["sessionQuality"],
        "sessionConvictionAdjustment": hint["sessionConvictionAdjustment"],
        "resolvedAtUtc": now_utc.isoformat(),
        "assetType": str(asset_type or "forex").lower(),
    }


def resolve_session_conviction_hint(session_context: dict[str, Any]) -> dict[str, str]:
    """Return advisory sessionQuality + sessionConvictionAdjustment from resolved context."""
    if not isinstance(session_context, dict):
        return {"sessionQuality": "ACCEPTABLE", "sessionConvictionAdjustment": "NEUTRAL"}
    return {
        "sessionQuality": str(session_context.get("sessionQuality") or "ACCEPTABLE"),
        "sessionConvictionAdjustment": str(
            session_context.get("sessionConvictionAdjustment") or "NEUTRAL"
        ),
    }
