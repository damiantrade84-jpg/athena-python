"""Research/backtest-only TP/SL exit baselines.

This module is intentionally isolated from live execution.  It does not read
live ATR/RR settings, broker adapters, scanner state, risk gates, or timed-exit
runtime state.  Backtests can use it to evaluate entry signal quality under
simple fixed-R, ATR, or triple-barrier exits before comparing against live-style
execution rules.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence


VALID_EXIT_MODES = {"baseline_r", "atr_baseline", "triple_barrier", "live_exit"}

DEFAULT_MAX_HOLD = {"M15": 12, "H1": 24, "H4": 18, "D1": 10}

DEFAULT_BACKTEST_EXIT_CONFIG: dict[str, Any] = {
    "BACKTEST_EXIT_MODE": "triple_barrier",
    "BACKTEST_BASELINE_R": {
        "sl_r": 1.0,
        "tp_r": 1.5,
        "max_hold_bars": dict(DEFAULT_MAX_HOLD),
    },
    "BACKTEST_ATR_BASELINE": {
        "atr_length": 14,
        "sl_atr": 1.0,
        "tp_atr": 1.5,
        "max_hold_bars": dict(DEFAULT_MAX_HOLD),
    },
    "BACKTEST_TRIPLE_BARRIER": {
        "target_source": "atr",
        "atr_length": 14,
        "sl_mult": 1.0,
        "tp_mult": 1.5,
        "max_hold_bars": dict(DEFAULT_MAX_HOLD),
        "same_bar_policy": "sl_first",
    },
}


@dataclass(frozen=True)
class BacktestExitResult:
    exit_price: float | None
    exit_index: int | None
    exit_timestamp: Any = None
    exit_reason: str = "invalid"
    tp_price: float | None = None
    sl_price: float | None = None
    max_hold_bars: int | None = None
    r_multiple: float = 0.0
    same_bar_policy: str = "sl_first"
    backtest_exit_mode: str = "triple_barrier"
    atr_length: int | None = None
    atr_at_entry: float | None = None
    mae: float | None = None
    mfe: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalized_backtest_exit_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a complete research-exit config without reading live settings."""
    merged = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in DEFAULT_BACKTEST_EXIT_CONFIG.items()
    }
    for key in ("BACKTEST_BASELINE_R", "BACKTEST_ATR_BASELINE", "BACKTEST_TRIPLE_BARRIER"):
        if isinstance(merged.get(key), dict):
            merged[key] = {
                **DEFAULT_BACKTEST_EXIT_CONFIG[key],
                **merged[key],
                "max_hold_bars": {
                    **DEFAULT_BACKTEST_EXIT_CONFIG[key].get("max_hold_bars", {}),
                    **(merged[key].get("max_hold_bars") or {}),
                },
            }
    if config:
        for key, value in config.items():
            if key in {"BACKTEST_BASELINE_R", "BACKTEST_ATR_BASELINE", "BACKTEST_TRIPLE_BARRIER"}:
                base = dict(merged.get(key) or {})
                incoming = dict(value or {}) if isinstance(value, Mapping) else {}
                base["max_hold_bars"] = {
                    **(base.get("max_hold_bars") or {}),
                    **(incoming.get("max_hold_bars") or {}),
                }
                for sub_key, sub_value in incoming.items():
                    if sub_key != "max_hold_bars":
                        base[sub_key] = sub_value
                merged[key] = base
            elif key == "backtest_exit_mode":
                merged["BACKTEST_EXIT_MODE"] = value
            else:
                merged[key] = value
    mode = str(merged.get("BACKTEST_EXIT_MODE") or "triple_barrier").strip().lower()
    if mode not in VALID_EXIT_MODES:
        raise ValueError(f"invalid BACKTEST_EXIT_MODE: {mode}")
    merged["BACKTEST_EXIT_MODE"] = mode
    return merged


def calculate_backtest_exit(
    candles: Sequence[Mapping[str, Any]] | Any,
    entry_index: int,
    entry_price: float,
    direction: str,
    timeframe: str,
    mode: str | None = None,
    config: Mapping[str, Any] | None = None,
    atr_series: Sequence[float | None] | Any | None = None,
) -> BacktestExitResult:
    """Calculate a conservative research/backtest-only exit.

    The entry candle is not inspected for TP/SL hits; the first exit check is
    `entry_index + 1`.  If both TP and SL are touched on the same OHLC bar,
    `same_bar_policy="sl_first"` exits at SL for both long and short trades.
    """
    cfg = normalized_backtest_exit_config(config)
    exit_mode = (mode or cfg.get("BACKTEST_EXIT_MODE") or "triple_barrier").lower()
    if exit_mode == "live_exit":
        return _invalid(
            exit_mode,
            "live_exit_delegates_to_existing_backtest_path",
            same_bar_policy="sl_first",
        )
    if exit_mode not in VALID_EXIT_MODES:
        return _invalid(exit_mode, f"invalid_mode:{exit_mode}")

    rows = _normalise_candles(candles)
    try:
        entry_i = int(entry_index)
        entry = float(entry_price)
    except (TypeError, ValueError):
        return _invalid(exit_mode, "invalid_entry")
    if entry_i < 0 or entry_i >= len(rows) or not math.isfinite(entry) or entry <= 0:
        return _invalid(exit_mode, "invalid_entry")

    side = str(direction or "").upper()
    if side not in {"LONG", "SHORT"}:
        return _invalid(exit_mode, "invalid_direction")

    tf = str(timeframe or "").upper()
    mode_cfg_key = {
        "baseline_r": "BACKTEST_BASELINE_R",
        "atr_baseline": "BACKTEST_ATR_BASELINE",
        "triple_barrier": "BACKTEST_TRIPLE_BARRIER",
    }[exit_mode]
    mode_cfg = cfg.get(mode_cfg_key, {}) or {}
    same_bar_policy = str(mode_cfg.get("same_bar_policy") or "sl_first").lower()
    max_hold_bars = _max_hold_for_timeframe(mode_cfg, tf)

    atr_length: int | None = None
    atr_at_entry: float | None = None
    if exit_mode == "baseline_r":
        sl_dist = float(mode_cfg.get("sl_r", 1.0) or 1.0)
        tp_dist = float(mode_cfg.get("tp_r", 1.5) or 1.5)
    else:
        atr_length = int(mode_cfg.get("atr_length", 14) or 14)
        atr_at_entry = _atr_at_entry(rows, entry_i, atr_length, atr_series)
        if atr_at_entry is None or atr_at_entry <= 0:
            return _invalid(
                exit_mode,
                "invalid_atr_at_entry",
                same_bar_policy=same_bar_policy,
                atr_length=atr_length,
                atr_at_entry=atr_at_entry,
                max_hold_bars=max_hold_bars,
            )
        if exit_mode == "atr_baseline":
            sl_dist = atr_at_entry * float(mode_cfg.get("sl_atr", 1.0) or 1.0)
            tp_dist = atr_at_entry * float(mode_cfg.get("tp_atr", 1.5) or 1.5)
        else:
            if str(mode_cfg.get("target_source") or "atr").lower() != "atr":
                return _invalid(exit_mode, "unsupported_target_source", same_bar_policy=same_bar_policy)
            sl_dist = atr_at_entry * float(mode_cfg.get("sl_mult", 1.0) or 1.0)
            tp_dist = atr_at_entry * float(mode_cfg.get("tp_mult", 1.5) or 1.5)

    if not all(math.isfinite(v) and v > 0 for v in (sl_dist, tp_dist)):
        return _invalid(exit_mode, "invalid_barrier_distance", same_bar_policy=same_bar_policy)

    if side == "LONG":
        sl_price = entry - sl_dist
        tp_price = entry + tp_dist
    else:
        sl_price = entry + sl_dist
        tp_price = entry - tp_dist

    mfe = 0.0
    mae = 0.0
    last_i = min(len(rows) - 1, entry_i + max_hold_bars)
    for idx in range(entry_i + 1, last_i + 1):
        row = rows[idx]
        try:
            high = float(row["high"])
            low = float(row["low"])
        except (KeyError, TypeError, ValueError):
            return _invalid(exit_mode, f"invalid_candle:{idx}", same_bar_policy=same_bar_policy)
        fav_r, adv_r = _bar_excursions_r(side, entry, sl_dist, high, low)
        mfe = max(mfe, fav_r)
        mae = min(mae, adv_r)

        reason = _resolve_bar(side, low, high, sl_price, tp_price, same_bar_policy)
        if reason:
            px = sl_price if reason == "sl" else tp_price
            return _result(
                exit_mode,
                reason,
                px,
                idx,
                rows[idx],
                entry,
                side,
                sl_price,
                tp_price,
                sl_dist,
                max_hold_bars,
                same_bar_policy,
                atr_length,
                atr_at_entry,
                mae,
                mfe,
            )

    if last_i > entry_i:
        exit_px = float(rows[last_i].get("close", entry))
        reason = "timeout"
        return _result(
            exit_mode,
            reason,
            exit_px,
            last_i,
            rows[last_i],
            entry,
            side,
            sl_price,
            tp_price,
            sl_dist,
            max_hold_bars,
            same_bar_policy,
            atr_length,
            atr_at_entry,
            mae,
            mfe,
        )
    return _invalid(exit_mode, "no_future_candles", same_bar_policy=same_bar_policy)


def _normalise_candles(candles: Sequence[Mapping[str, Any]] | Any) -> list[Mapping[str, Any]]:
    if hasattr(candles, "to_dict"):
        return list(candles.to_dict("records"))
    return list(candles or [])


def _max_hold_for_timeframe(mode_cfg: Mapping[str, Any], timeframe: str) -> int:
    max_hold = mode_cfg.get("max_hold_bars") or DEFAULT_MAX_HOLD
    if isinstance(max_hold, Mapping):
        value = max_hold.get(timeframe) or max_hold.get(timeframe.upper()) or max_hold.get("default")
    else:
        value = max_hold
    try:
        return max(1, int(value or DEFAULT_MAX_HOLD.get(timeframe, 24)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_HOLD.get(timeframe, 24)


def _atr_at_entry(
    candles: list[Mapping[str, Any]],
    entry_index: int,
    atr_length: int,
    atr_series: Sequence[float | None] | Any | None,
) -> float | None:
    if atr_series is not None:
        try:
            value = atr_series.iloc[entry_index] if hasattr(atr_series, "iloc") else atr_series[entry_index]
            value_f = float(value)
            return value_f if math.isfinite(value_f) and value_f > 0 else None
        except (IndexError, KeyError, TypeError, ValueError):
            return None
    if entry_index < atr_length:
        return None
    trs: list[float] = []
    start = entry_index - atr_length + 1
    for idx in range(start, entry_index + 1):
        try:
            high = float(candles[idx]["high"])
            low = float(candles[idx]["low"])
            prev_close = float(candles[idx - 1]["close"]) if idx > 0 else float(candles[idx]["close"])
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(trs) != atr_length:
        return None
    atr = sum(trs) / atr_length
    return atr if math.isfinite(atr) and atr > 0 else None


def _resolve_bar(
    direction: str,
    low: float,
    high: float,
    sl_price: float,
    tp_price: float,
    same_bar_policy: str,
) -> str | None:
    if direction == "LONG":
        hit_sl = low <= sl_price
        hit_tp = high >= tp_price
    else:
        hit_sl = high >= sl_price
        hit_tp = low <= tp_price
    if hit_sl and hit_tp:
        return "sl" if same_bar_policy == "sl_first" else "sl"
    if hit_sl:
        return "sl"
    if hit_tp:
        return "tp"
    return None


def _bar_excursions_r(direction: str, entry: float, risk: float, high: float, low: float) -> tuple[float, float]:
    if risk <= 0:
        return 0.0, 0.0
    if direction == "LONG":
        return (high - entry) / risk, (low - entry) / risk
    return (entry - low) / risk, (entry - high) / risk


def _result(
    mode: str,
    reason: str,
    exit_price: float,
    exit_index: int,
    row: Mapping[str, Any],
    entry: float,
    direction: str,
    sl_price: float,
    tp_price: float,
    risk: float,
    max_hold_bars: int,
    same_bar_policy: str,
    atr_length: int | None,
    atr_at_entry: float | None,
    mae: float,
    mfe: float,
) -> BacktestExitResult:
    if reason == "sl":
        r_multiple = -1.0
    else:
        r_multiple = ((exit_price - entry) / risk) if direction == "LONG" else ((entry - exit_price) / risk)
    return BacktestExitResult(
        exit_price=float(exit_price),
        exit_index=int(exit_index),
        exit_timestamp=row.get("time") or row.get("timestamp") or row.get("date"),
        exit_reason=reason,
        tp_price=float(tp_price),
        sl_price=float(sl_price),
        max_hold_bars=int(max_hold_bars),
        r_multiple=round(float(r_multiple), 6),
        same_bar_policy=same_bar_policy,
        backtest_exit_mode=mode,
        atr_length=atr_length,
        atr_at_entry=atr_at_entry,
        mae=round(float(mae), 6),
        mfe=round(float(mfe), 6),
        metadata={},
    )


def _invalid(
    mode: str,
    reason: str,
    *,
    same_bar_policy: str = "sl_first",
    atr_length: int | None = None,
    atr_at_entry: float | None = None,
    max_hold_bars: int | None = None,
) -> BacktestExitResult:
    return BacktestExitResult(
        exit_price=None,
        exit_index=None,
        exit_reason="invalid",
        same_bar_policy=same_bar_policy,
        backtest_exit_mode=mode,
        atr_length=atr_length,
        atr_at_entry=atr_at_entry,
        max_hold_bars=max_hold_bars,
        metadata={"failure_reason": reason},
    )
