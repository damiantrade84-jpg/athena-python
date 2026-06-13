from __future__ import annotations

from math import sqrt
from statistics import fmean, stdev

from engine_a_v3.contract import CONTRACT_VERSION
from engine_a_v3.diagnostics import reverse_direction_result_r, trade_path_diagnostics
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.promotion import PromotionRegistry
from engine_a_v3.setups import _efficiency_ratio


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
    collect_funnel: bool = False,
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
        tp1 = float(signal.tp1) if signal.tp1 is not None else None
        tp2 = float(signal.tp2 or signal.tp1)
        direction = str(signal.direction)
        if direction == "LONG" and not (sl < entry < tp2):
            continue
        if direction == "SHORT" and not (sl > entry > tp2):
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
            tp1_hit = (
                tp1 is not None
                and (high >= tp1 if direction == "LONG" else low <= tp1)
            )
            tp2_hit = high >= tp2 if direction == "LONG" else low <= tp2
            if sl_hit and (tp1_hit or tp2_hit):
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
            if tp2_hit:
                outcome = "TP2"
                result_r = abs(tp2 - entry) / risk
                exit_index = probe_index
                break
            if tp1_hit:
                outcome = "TP1"
                result_r = abs(tp1 - entry) / risk
                exit_index = probe_index
                break
        if outcome == "TIMEOUT":
            close = float(primary[exit_index]["close"])
            signed = close - entry if direction == "LONG" else entry - close
            result_r = signed / risk
        holding_bars = max(1, exit_index - index)
        cost_r = _cost_r(
            entry,
            sl,
            spread_bps=spread_bps,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            swap_bps_per_day=swap_bps_per_day,
            holding_bars=holding_bars,
            horizon=horizon,
        )
        result_r -= cost_r
        future_window = primary[index + 1 : exit_index + 1]
        path = trade_path_diagnostics(
            future_window,
            direction=direction,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
        )
        h4_prefix = prefix.get("H4") or []
        regime_label = "unknown"
        if len(h4_prefix) >= 20:
            h4_eff, _ = _efficiency_ratio(h4_prefix, 20)
            if h4_eff >= 0.3:
                regime_label = "trending"
            elif h4_eff <= 0.2:
                regime_label = "ranging"
            else:
                regime_label = "neutral"
        efficiency_at_entry, _ = _efficiency_ratio(primary[: index + 1], 20)
        trades.append(
            {
                "date": signal.decisionTime,
                "direction": direction,
                "setupId": signal.setupId,
                "entry": round(entry, 8),
                "sl": round(sl, 8),
                "tp1": round(tp1, 8) if tp1 is not None else None,
                "tp": round(tp2, 8),
                "outcome": outcome,
                "resultR": round(result_r, 4),
                "reverseResultR": reverse_direction_result_r(
                    future_window,
                    direction=direction,
                    entry=entry,
                    sl=sl,
                    tp=tp2,
                    cost_r=cost_r,
                ),
                "max_favorable_excursion_r": path["max_favorable_excursion_r"],
                "max_adverse_excursion_r": path["max_adverse_excursion_r"],
                "regime": regime_label,
                "efficiencyAtEntry": round(efficiency_at_entry, 4),
                "signalId": signal.signalId,
                "oos": True,
            }
        )
        next_available_index = exit_index + 1

    result = _summarize(pair, horizon, trades, same_bar)
    if collect_funnel:
        # Diagnostic: re-walk every bar (ignoring the post-trade cooldown) to attribute
        # why trades are scarce — data depth vs decision distribution vs which gate fires.
        decisions = {"TRADE": 0, "WATCH": 0, "NO_SIGNAL": 0}
        qualified = 0
        reasons: dict[str, int] = {}
        evaluated = 0
        for index in range(min_index, len(primary) - 1):
            cutoff = primary[index].get("time")
            prefix = {
                timeframe: [
                    candle
                    for candle in rows
                    if str(candle.get("time") or candle.get("datetime")) <= str(cutoff)
                ]
                for timeframe, rows in candles.items()
            }
            sig = evaluate_engine_a_v3(pair, prefix, horizon=horizon, registry=registry)
            decisions[sig.decision] = decisions.get(sig.decision, 0) + 1
            qualified += 1 if sig.qualified else 0
            for reason in sig.rejectionReasons:
                reasons[reason] = reasons.get(reason, 0) + 1
            evaluated += 1
        result["funnel"] = {
            "primaryTf": primary_tf,
            "dataDepth": {tf: len(rows) for tf, rows in candles.items()},
            "minIndexWarmup": min_index,
            "barsEvaluated": evaluated,
            "decisions": decisions,
            "qualified": qualified,
            "tradesTaken": len(trades),
            "topRejectionReasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:15]),
        }
    return result
