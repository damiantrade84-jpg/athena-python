"""Authoritative MT5 daily-bar feed for the TSMOM/OX Book scanner.

The live book must read the same broker-owned OHLC path as the rest of Athena.
It never treats CandleBuilder/cache history as live D1 authority. The forming bar
is removed with Athena's canonical market-state helpers and freshness fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from tsmom_live.signal import TsmomConfig


@dataclass(frozen=True)
class DailyBars:
    frame: pd.DataFrame | None
    source: str
    freshness_ok: bool
    freshness_reason: str
    freshness: dict[str, Any]
    error: str | None = None


# Test seam. Production resolution stays lazy so importing this small module never
# imports the athena.py monolith or initializes a broker connection.
_authoritative_candle_fetcher: Callable[[dict, str, int], dict] | None = None


def _runtime_candle_fetcher() -> Callable[[dict, str, int], dict] | None:
    try:
        from athena_runtime import rt

        fetcher = getattr(rt(), "fetch_mt5", None)
        return fetcher if callable(fetcher) else None
    except Exception:
        return None


def pair_dict(cfg: TsmomConfig) -> dict:
    return {
        "symbol": cfg.broker_symbol,
        "display": cfg.display,
        "type": cfg.asset_type,
        "source": cfg.venue,
    }


def _failed(reason: str, *, diagnostic: dict[str, Any] | None = None) -> DailyBars:
    return DailyBars(
        frame=None,
        source="mt5",
        freshness_ok=False,
        freshness_reason=reason,
        freshness=diagnostic or {},
        error=reason,
    )


def load_daily_bars(
    cfg: TsmomConfig,
    limit: int = 400,
    time_now: float | None = None,
) -> DailyBars:
    """Return confirmed D1 OHLC plus provenance/freshness from live MT5.

    This is read-only. Missing runtime wiring, broker failures, malformed candles,
    and stale data are surfaced rather than replaced by cached/provider history.
    """
    fetcher = _authoritative_candle_fetcher
    if not callable(fetcher):
        fetcher = _runtime_candle_fetcher()
    if not callable(fetcher):
        return _failed("authoritative_mt5_fetcher_unavailable")

    pair = pair_dict(cfg)
    try:
        result = fetcher(pair, "D1", int(limit))
    except Exception as exc:
        return _failed(f"mt5_fetch_error:{exc}")

    if not isinstance(result, dict):
        return _failed("mt5_fetch_invalid_response")
    if result.get("error"):
        detail = str(result.get("detail") or result.get("error") or "unknown").strip()
        return _failed(f"mt5_fetch_failed:{detail}")

    raw_candles = result.get("candles")
    if not isinstance(raw_candles, list) or not raw_candles:
        return _failed("mt5_no_candles")

    try:
        from athena_app.services.market_state import (
            candle_freshness_diagnostic,
            candle_timestamp_epoch,
            market_state_offset_hours,
            split_market_state,
            trim_mt5_d1_broker_session_ahead_tail,
        )

        candles, _ = trim_mt5_d1_broker_session_ahead_tail(
            pair,
            "D1",
            raw_candles,
            time_now=time_now,
        )
        diagnostic = candle_freshness_diagnostic(
            pair,
            "D1",
            candles,
            time_now=time_now,
            source="mt5",
        )
        offset = market_state_offset_hours(pair, "D1")
        confirmed = (
            split_market_state(
                candles,
                "D1",
                cfg.display,
                time_now=time_now,
                offset_hours=offset,
                provider="mt5",
                provider_symbol=cfg.broker_symbol,
            ).get("confirmed")
            or []
        )
    except Exception as exc:
        return _failed(f"market_state_error:{exc}")

    rows = []
    for candle in confirmed:
        epoch = candle_timestamp_epoch(candle)
        if not epoch:
            continue
        try:
            rows.append({
                "time": pd.Timestamp(epoch, unit="s", tz="UTC"),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    severity = str(diagnostic.get("stalenessSeverity") or "missing_current_bucket")
    if not rows:
        return _failed("no_confirmed_daily_bars", diagnostic=diagnostic)

    frame = (
        pd.DataFrame(rows)
        .sort_values("time")
        .drop_duplicates(subset=["time"], keep="last")
        .reset_index(drop=True)
    )
    return DailyBars(
        frame=frame,
        source="mt5",
        freshness_ok=_severity_ok(severity),
        freshness_reason=severity,
        freshness=diagnostic,
        error=None,
    )


def load_closed_daily_bars(
    cfg: TsmomConfig,
    limit: int = 400,
    time_now: float | None = None,
) -> pd.DataFrame | None:
    """Compatibility wrapper returning only the confirmed D1 frame."""
    return load_daily_bars(cfg, limit=limit, time_now=time_now).frame


def _severity_ok(severity: str | None) -> bool:
    """Accept canonical fresh/one-bucket D1 states and calendar-gap policy passes."""
    value = str(severity or "")
    return value in {"fresh", "stale_1_bucket"} or "policy_ok" in value


def freshness_ok(
    cfg: TsmomConfig,
    exec_dict: dict,
    time_now: float | None = None,
) -> tuple[bool, str]:
    """Execution-time authoritative freshness proof; fail closed on every error."""
    snapshot = load_daily_bars(cfg, time_now=time_now)
    exec_dict["candleFreshness"] = {"D1": dict(snapshot.freshness)}
    if snapshot.error:
        return False, snapshot.error
    return bool(snapshot.freshness_ok), snapshot.freshness_reason
