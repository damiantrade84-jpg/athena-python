from __future__ import annotations

from math import sqrt
from statistics import fmean, stdev

from engine_a_v3.contract import CONTRACT_VERSION
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.promotion import PromotionRegistry


def _cost_r(
    entry: float,
    sl: float,
    *,
    spread_bps: float,
    commission_bps: float,
    slippage_bps: float,
    swap_bps_per_day: float,
    holding_bars: int,
    horizon: str,
) -> float:
    risk = abs(entry - sl)
    if entry <= 0 or risk <= 0:
        return 0.0
    days = holding_bars / (24.0 if horizon == "intraday" else 1.0)
    total_bps = spread_bps + commission_bps + slippage_bps + swap_bps_per_day * days
    return (entry * total_bps / 10_000.0) / risk


def _summarize(pair: dict, horizon: str, trades: list[dict], same_bar: int) -> dict:
    results = [float(trade["resultR"]) for trade in trades]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    expectancy = fmean(results) if results else 0.0
    sqn = (
        expectancy / stdev(results) * sqrt(len(results))
        if len(results) > 1 and stdev(results) > 0
        else 0.0
    )
    equity = 0.0
    peak = 0.0
    max_dd_r = 0.0
    equity_curve = [0.0]
    for value in results:
        equity += value
        peak = max(peak, equity)
        max_dd_r = max(max_dd_r, peak - equity)
        equity_curve.append(round(equity, 4))
    return {
        "pair": pair.get("display") or pair.get("symbol"),
        "symbol": pair.get("symbol"),
        "type": pair.get("type"),
        "engine": "ENGINE_A_V3",
        "contractVersion": CONTRACT_VERSION,
        "btStyle": horizon,
        "btStyleRequested": horizon,
        "totalTrades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "profitFactor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "totalR": round(sum(results), 4),
        "expectancy": round(expectancy, 4),
        "sqn": round(sqn, 4),
        "maxDrawdownR": round(max_dd_r, 4),
        "maxDrawdownPct": 0.0,
        "same_bar_both_hit": same_bar,
        "sameBarPolicy": "ADVERSE_SL_FIRST",
        "lookaheadUsed": False,
        "equityCurve": equity_curve,
        "trades": trades,
        "funnel": {},
        "wfSplit": {
            "is_trades": 0,
            "oos_trades": len(trades),
            "is_sqn": None,
            "oos_sqn": round(sqn, 4),
            "overfit_flag": False,
            "wf_note": "V3 evaluates confirmed prefixes only",
            "lowSampleSqnWarning": len(trades) < 60,
            "lowSampleSqnTradeFloor": 60,
        },
    }


def run_v3_backtest(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    horizon: str,
    registry: PromotionRegistry | None = None,
    spread_bps: float,
    commission_bps: float,
    slippage_bps: float,
    swap_bps_per_day: float,
    max_hold_bars: int = 24,
    start_index: int | None = None,
) -> dict:
    primary_tf = "H1" if horizon == "intraday" else "H4"
    primary = list(candles.get(primary_tf) or [])
    min_index = max(80, int(start_index or 80))
    trades: list[dict] = []
    same_bar = 0
    next_available_index = min_index
    for index in range(min_index, len(primary) - 1):
        if index < next_available_index:
            continue
        cutoff = primary[index].get("time")
        prefix = {
            timeframe: [
                candle
                for candle in rows
                if str(candle.get("time") or candle.get("datetime")) <= str(cutoff)
            ]
            for timeframe, rows in candles.items()
        }
        signal = evaluate_engine_a_v3(
            pair,
            prefix,
            horizon=horizon,
            registry=registry,
        )
        if signal.decision != "TRADE" or not signal.qualified:
            continue
        entry_bar = primary[index + 1]
        if signal.entryZone is None:
            continue
        zone_low = float(signal.entryZone.low)
        zone_high = float(signal.entryZone.high)
        bar_low = float(entry_bar["low"])
        bar_high = float(entry_bar["high"])
        if bar_high < zone_low or bar_low > zone_high:
            continue
        bar_open = float(entry_bar["open"])
        entry = (
            min(max(bar_open, zone_low), zone_high)
            if signal.direction == "LONG"
            else max(min(bar_open, zone_high), zone_low)
        )
        sl = float(signal.sl)
        tp = float(signal.tp2 or signal.tp1)
        direction = str(signal.direction)
        if direction == "LONG" and not (sl < entry < tp):
            continue
        if direction == "SHORT" and not (sl > entry > tp):
            continue
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        outcome = "TIMEOUT"
        result_r = 0.0
        exit_index = min(len(primary) - 1, index + max_hold_bars)
        for probe_index in range(index + 1, exit_index + 1):
            bar = primary[probe_index]
            high = float(bar["high"])
            low = float(bar["low"])
            sl_hit = low <= sl if direction == "LONG" else high >= sl
            tp_hit = high >= tp if direction == "LONG" else low <= tp
            if sl_hit and tp_hit:
                same_bar += 1
                outcome = "SL"
                result_r = -1.0
                exit_index = probe_index
                break
            if sl_hit:
                outcome = "SL"
                result_r = -1.0
                exit_index = probe_index
                break
            if tp_hit:
                outcome = "TP2"
                result_r = abs(tp - entry) / risk
                exit_index = probe_index
                break
        if outcome == "TIMEOUT":
            close = float(primary[exit_index]["close"])
            signed = close - entry if direction == "LONG" else entry - close
            result_r = signed / risk
        holding_bars = max(1, exit_index - index)
        result_r -= _cost_r(
            entry,
            sl,
            spread_bps=spread_bps,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            swap_bps_per_day=swap_bps_per_day,
            holding_bars=holding_bars,
            horizon=horizon,
        )
        trades.append(
            {
                "date": signal.decisionTime,
                "direction": direction,
                "setupId": signal.setupId,
                "entry": round(entry, 8),
                "sl": round(sl, 8),
                "tp": round(tp, 8),
                "outcome": outcome,
                "resultR": round(result_r, 4),
                "signalId": signal.signalId,
                "oos": True,
            }
        )
        next_available_index = exit_index + 1
    return _summarize(pair, horizon, trades, same_bar)
