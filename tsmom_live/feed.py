"""Live daily-bar feed + freshness proof for the TSMOM bridge.

Pulls D1 bars from the platform candle cache, drops the forming (in-progress) bar via
the platform's own confirmed/forming split (no bespoke bucket math = no parity drift),
and proves freshness with the canonical diagnostic. Fail-closed everywhere."""
from __future__ import annotations

import pandas as pd

from tsmom_live.signal import TsmomConfig


def pair_dict(cfg: TsmomConfig) -> dict:
    return {"symbol": cfg.broker_symbol, "display": cfg.display, "type": cfg.asset_type, "source": cfg.venue}


def load_closed_daily_bars(cfg: TsmomConfig, limit: int = 400, time_now: float | None = None) -> pd.DataFrame | None:
    """CLOSED D1 OHLC bars (forming bar excluded), oldest first. None on any failure."""
    try:
        from candle_feeds import fetch_candles_live
        from athena_app.services.market_state import (
            candle_timestamp_epoch, market_state_offset_hours, split_market_state,
        )
    except Exception:
        return None
    res = fetch_candles_live(cfg.display, "D1", limit)
    if not res or res.get("error"):
        return None
    candles = res.get("candles") or []
    if not candles:
        return None
    offset = market_state_offset_hours(pair_dict(cfg), "D1")
    confirmed = (split_market_state(candles, "D1", cfg.display, time_now=time_now, offset_hours=offset)
                 .get("confirmed") or [])
    rows = []
    for c in confirmed:
        ep = candle_timestamp_epoch(c)
        if not ep:
            continue
        try:
            rows.append({"time": pd.Timestamp(ep, unit="s", tz="UTC"),
                         "open": float(c["open"]), "high": float(c["high"]),
                         "low": float(c["low"]), "close": float(c["close"])})
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def _severity_ok(severity: str | None) -> bool:
    """A closed-bar D1 system is healthy at 'fresh' or one-bucket lag; calendar-gap
    policy passes count as ok. Anything multi-bucket / missing fails closed."""
    s = str(severity or "")
    return s in {"fresh", "stale_1_bucket"} or "policy_ok" in s


def freshness_ok(cfg: TsmomConfig, exec_dict: dict, time_now: float | None = None) -> tuple[bool, str]:
    """Real freshness proof for bridge.default_deps — fail-closed on any error/no-data."""
    try:
        from candle_feeds import fetch_candles_live
        from athena_app.services.market_state import candle_freshness_diagnostic
        res = fetch_candles_live(cfg.display, "D1", 400)
        if not res or res.get("error"):
            return False, f"no_candles:{(res or {}).get('detail', '')}"
        diag = candle_freshness_diagnostic(pair_dict(cfg), "D1", res.get("candles") or [], time_now=time_now)
        sev = diag.get("stalenessSeverity")
        exec_dict["candleFreshness"] = {"D1": dict(diag)}
        return _severity_ok(sev), str(sev)
    except Exception as exc:
        return False, f"freshness_error:{exc}"
