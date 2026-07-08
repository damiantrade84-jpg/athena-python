"""Diagnostic V3 backtest with decoupled signal/entry/exit TFs (research only)."""

from __future__ import annotations

import bisect
from typing import Any

from engine_a_v3.backtest import _cost_r, _simulate_exit, _summarize, confirmed_cutoff_open_epoch
from engine_a_v3.diagnostics import reverse_direction_result_r, trade_path_diagnostics
from engine_a_v3.evaluator import evaluate_engine_a_v3, _parse_time
from athena_app.services.market_state import candle_timestamp_epoch


def _walk_tf_for_signal(signal_tf: str) -> str:
    return signal_tf if signal_tf in ("H1", "H4", "D1") else "H4"


def _candles_for_tf(candles: dict[str, list], tf: str) -> list[dict]:
    return list(candles.get(tf) or [])


def _find_entry_index(entry_candles: list[dict], signal_epoch: int) -> int | None:
    epochs = [candle_timestamp_epoch(c) for c in entry_candles]
    idx = bisect.bisect_right(epochs, signal_epoch)
    if idx >= len(entry_candles) - 1:
        return None
    return idx + 1


def run_diagnostic_v3_backtest(
    pair: dict,
    *,
    signal_tf: str,
    entry_tf: str,
    exit_tf: str,
    execution_tf: str,
    horizon: str,
    candles: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """Research-only V3 backtest with separated signal/entry/exit timeframes."""
    import backtest_runner
    from config import CONFIG

    if candles is None:
        candles = _fetch_engine_a_candles(pair)
        if not candles:
            return {"error": "Could not fetch candles", "totalTrades": 0, "trades": []}

    walk_tf = _walk_tf_for_signal(signal_tf)
    walk = _candles_for_tf(candles, walk_tf)
    entry_rows = _candles_for_tf(candles, entry_tf)
    exit_rows = _candles_for_tf(candles, exit_tf)
    if not walk or not entry_rows or not exit_rows:
        return {
            "error": f"Missing candles walk={walk_tf} entry={entry_tf} exit={exit_tf}",
            "blocked": "BLOCKED_DATA",
            "totalTrades": 0,
            "trades": [],
        }

    _v3_costs = CONFIG.get("ENGINE_A_V3_BACKTEST") or {}
    spread_bps = float(_v3_costs.get("SPREAD_BPS", 2.0))
    commission_bps = float(_v3_costs.get("COMMISSION_BPS", 1.0))
    slippage_bps = float(_v3_costs.get("SLIPPAGE_BPS", 1.0))
    swap_bps = float(_v3_costs.get("SWAP_BPS_PER_DAY", 0.5))
    max_hold = int(_v3_costs.get("MAX_HOLD_BARS", 24))
    round_trip = bool(_v3_costs.get("ROUND_TRIP_COSTS", True))

    _open_epochs: dict[str, list[int]] = {}
    for tf, rows in candles.items():
        _open_epochs[tf] = [candle_timestamp_epoch(c) for c in rows]
    walk_epochs = _open_epochs.get(walk_tf, [])

    def build_prefix(primary_open_epoch: int) -> dict[str, list[dict]]:
        prefix: dict[str, list[dict]] = {}
        for tf, rows in candles.items():
            cutoff = confirmed_cutoff_open_epoch(walk_tf, primary_open_epoch, tf)
            eps = _open_epochs[tf]
            split = bisect.bisect_right(eps, cutoff)
            prefix[tf] = rows[:split]
        return prefix

    eval_horizon = horizon if walk_tf != "D1" else "swing"
    min_index = max(80, 50)
    trades: list[dict] = []
    same_bar = 0
    next_available = min_index

    for index in range(min_index, len(walk) - 1):
        if index < next_available:
            continue
        prefix = build_prefix(walk_epochs[index])
        signal = evaluate_engine_a_v3(pair, prefix, horizon=eval_horizon)
        if signal.decision != "TRADE" or not signal.qualified:
            continue

        signal_epoch = walk_epochs[index]
        entry_idx = _find_entry_index(entry_rows, signal_epoch)
        if entry_idx is None:
            continue
        entry_bar = entry_rows[entry_idx]
        if signal.entryZone is None:
            continue

        zone_low = float(signal.entryZone.low)
        zone_high = float(signal.entryZone.high)
        bar_low = float(entry_bar["low"])
        bar_high = float(entry_bar["high"])
        if bar_high < zone_low or bar_low > zone_high:
            bar_open = float(entry_bar["open"])
            entry = min(max(bar_open, zone_low), zone_high) if signal.direction == "LONG" else max(min(bar_open, zone_high), zone_low)
        else:
            bar_open = float(entry_bar["open"])
            entry = min(max(bar_open, zone_low), zone_high) if signal.direction == "LONG" else max(min(bar_open, zone_high), zone_low)

        sl = float(signal.sl)
        tp1 = float(signal.tp1) if signal.tp1 is not None else None
        tp2 = float(signal.tp2 or signal.tp1)
        direction = str(signal.direction)
        if direction == "LONG" and not (sl < entry < tp2):
            continue
        if direction == "SHORT" and not (sl > entry > tp2):
            continue

        entry_epoch = candle_timestamp_epoch(entry_bar)
        exit_epochs = _open_epochs.get(exit_tf, [])
        exit_start = bisect.bisect_right(exit_epochs, entry_epoch)
        exit_end = min(len(exit_rows), exit_start + max_hold * _tf_bars_multiplier(exit_tf, walk_tf))
        exit_window = exit_rows[exit_start:exit_end]
        if not exit_window:
            continue

        exit_result = _simulate_exit(
            exit_window,
            direction=direction,
            entry=entry,
            sl=sl,
            tp1=float(tp1),
            tp2=tp2,
            exit_policy=signal.exitPolicy,
        )
        exit_offset = exit_result.exit_offset
        exit_bar = exit_window[exit_offset]
        same_bar += 1 if exit_result.same_bar else 0

        entry_time = _parse_time(entry_bar.get("time") or entry_bar.get("datetime"))
        exit_time = _parse_time(exit_bar.get("time") or exit_bar.get("datetime"))
        if entry_time is None or exit_time is None:
            continue

        cost_r = _cost_r(
            entry, sl,
            spread_bps=spread_bps,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            swap_bps_per_day=swap_bps,
            entry_time=entry_time,
            exit_time=exit_time,
            round_trip=round_trip,
        )
        result_r = exit_result.result_r - cost_r

        exec_rows = _candles_for_tf(candles, execution_tf)
        exec_epochs = _open_epochs.get(execution_tf, [])
        exec_start = bisect.bisect_right(exec_epochs, entry_epoch)
        exec_end = bisect.bisect_right(exec_epochs, candle_timestamp_epoch(exit_bar))
        future_window = exec_rows[exec_start:exec_end + 1] if exec_rows else exit_window[: exit_offset + 1]

        path = trade_path_diagnostics(
            future_window, direction=direction, entry=entry, sl=sl, tp1=tp1, tp2=tp2
        )
        trades.append({
            "date": signal.decisionTime,
            "direction": direction,
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp1": round(tp1, 8) if tp1 is not None else None,
            "tp": round(tp2, 8),
            "outcome": exit_result.outcome,
            "exitPolicy": signal.exitPolicy,
            "resultR": round(result_r, 4),
            "entryTime": entry_time.isoformat() if entry_time else None,
            "exitTime": exit_time.isoformat() if exit_time else None,
            "max_favorable_excursion_r": path["max_favorable_excursion_r"],
            "max_adverse_excursion_r": path["max_adverse_excursion_r"],
            "signal_tf": signal_tf,
            "entry_tf": entry_tf,
            "exit_tf": exit_tf,
            "execution_tf": execution_tf,
        })
        next_available = index + exit_offset + 2

    summary = _summarize(pair, eval_horizon, trades, same_bar)
    summary["_diagnostic_mode"] = "separated_tf_v3"
    summary["_signal_tf"] = signal_tf
    summary["_entry_tf"] = entry_tf
    summary["_exit_tf"] = exit_tf
    summary["_execution_tf"] = execution_tf
    return summary


def _tf_bars_multiplier(exit_tf: str, signal_tf: str) -> int:
    hours = {"M15": 0.25, "H1": 1, "H4": 4, "D1": 24}
    e = hours.get(exit_tf, 4)
    s = hours.get(signal_tf, 4)
    return max(1, int(round(s / e)))


def _fetch_engine_a_candles(pair: dict) -> dict[str, list[dict]] | None:
    import backtest_runner

    try:
        style = "intraday"
        requested = "intraday"
        effective = backtest_runner._effective_backtest_style(pair, requested)
        _bt_limits = backtest_runner._backtest_candle_limits(asset_type=pair.get("type", ""))
        if pair.get("source") == "binance":
            d1 = backtest_runner._crypto_bt_signal_candles(pair, engine="A", tf="D1", limit=_bt_limits["d1"], min_bars=60)
            h4 = backtest_runner._crypto_bt_signal_candles(pair, engine="A", tf="H4", limit=_bt_limits["h4"], min_bars=80)
            h1 = backtest_runner._crypto_bt_signal_candles(pair, engine="A", tf="H1", limit=_bt_limits["h1"], min_bars=80)
        elif pair.get("source") == "mt5":
            rt = backtest_runner._rt()
            d1 = backtest_runner._bt_cached_fetch(pair, "D1", _bt_limits["d1"], lambda lim: rt.fetch_candles(pair, "D1", lim), provider="mt5", min_bars=60)
            h4 = backtest_runner._bt_cached_fetch(pair, "H4", _bt_limits["h4"], lambda lim: rt.fetch_candles(pair, "H4", lim), provider="mt5", min_bars=80)
            h1 = backtest_runner._bt_cached_fetch(pair, "H1", _bt_limits["h1"], lambda lim: rt.fetch_candles(pair, "H1", lim), provider="mt5", min_bars=80)
        else:
            return None
        return {"D1": d1 or [], "H4": h4 or [], "H1": h1 or []}
    except Exception:
        return None
