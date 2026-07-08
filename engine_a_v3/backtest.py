from __future__ import annotations

import bisect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from statistics import fmean, stdev
from typing import Any

from engine_a_v3.contract import CONTRACT_VERSION
from engine_a_v3.diagnostics import reverse_direction_result_r, trade_path_diagnostics
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.promotion import PromotionRegistry
from engine_a_v3.setups import _efficiency_ratio

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}


def _tf_seconds(tf: str) -> int:
    return _TF_SECONDS.get(str(tf or "").upper(), 3600)


def confirmed_cutoff_open_epoch(primary_tf: str, primary_open_epoch: int, tf: str) -> int:
    """Largest open-epoch a ``tf`` bar may have while still being fully CLOSED by
    the close of the primary decision bar.

    A bar with ``open_epoch <= confirmed_cutoff_open_epoch(...)`` has finished
    forming as of the decision and is therefore safe to use. Anything later is a
    still-forming higher-timeframe bar (e.g. the same-day D1 during an H4/H1
    decision) whose eventual close would leak future data into scoring. This is
    the anti-lookahead rule that mirrors the live confirmed-only assembly
    (candle_service / split_market_state strip the forming bar of every TF).
    """
    return int(primary_open_epoch) + _tf_seconds(primary_tf) - _tf_seconds(tf)


def _cost_r(
    entry: float,
    sl: float,
    *,
    spread_bps: float,
    commission_bps: float,
    slippage_bps: float,
    swap_bps_per_day: float,
    entry_time: datetime,
    exit_time: datetime,
    round_trip: bool = True,
) -> float:
    risk = abs(entry - sl)
    if entry <= 0 or risk <= 0:
        return 0.0
    days = max(0.0, (exit_time - entry_time).total_seconds() / 86_400.0)
    # Round-trip friction: the full bid/ask spread is crossed once over the round
    # trip, while commission and slippage are paid on BOTH the entry and exit
    # legs. The previous model charged every component only once, understating
    # realistic cost (especially for tight-stop / high-turnover setups).
    sided = 2.0 if round_trip else 1.0
    total_bps = (
        spread_bps
        + sided * commission_bps
        + sided * slippage_bps
        + swap_bps_per_day * days
    )
    return (entry * total_bps / 10_000.0) / risk


@dataclass(frozen=True)
class ExitResult:
    outcome: str
    result_r: float
    exit_offset: int
    same_bar: bool = False


def _simulate_exit(
    bars: list[dict], *, direction: str, entry: float, sl: float,
    tp1: float, tp2: float, exit_policy: str,
) -> ExitResult:
    risk = abs(entry - sl)
    if risk <= 0 or not bars:
        return ExitResult("INVALID", 0.0, 0)
    split = exit_policy == "SPLIT_50_50"
    tp1_filled = False
    for offset, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        sl_hit = low <= sl if direction == "LONG" else high >= sl
        tp1_hit = high >= tp1 if direction == "LONG" else low <= tp1
        tp2_hit = high >= tp2 if direction == "LONG" else low <= tp2
        if sl_hit and ((not tp1_filled and tp1_hit) or (tp1_filled and tp2_hit)):
            return ExitResult("SL" if not tp1_filled else "TP1_THEN_SL", -1.0 if not tp1_filled else 0.0, offset, True)
        if sl_hit:
            return ExitResult("SL" if not tp1_filled else "TP1_THEN_SL", -1.0 if not tp1_filled else 0.0, offset)
        if not split and tp1_hit:
            return ExitResult("TP1", abs(tp1 - entry) / risk, offset)
        if split:
            if tp1_filled and tp2_hit:
                return ExitResult("TP1_TP2", 0.5 * abs(tp1 - entry) / risk + 0.5 * abs(tp2 - entry) / risk, offset)
            if not tp1_filled and tp2_hit:
                return ExitResult("TP1_TP2", 0.5 * abs(tp1 - entry) / risk + 0.5 * abs(tp2 - entry) / risk, offset)
            if tp1_hit:
                tp1_filled = True
    close = float(bars[-1]["close"])
    runner_r = ((close - entry) if direction == "LONG" else (entry - close)) / risk
    if split and tp1_filled:
        return ExitResult("TP1_TIMEOUT", 0.5 * abs(tp1 - entry) / risk + 0.5 * runner_r, len(bars) - 1)
    return ExitResult("TIMEOUT", runner_r, len(bars) - 1)


def _sqn(results: list[float]) -> float | None:
    """Van Tharp SQN over a list of R-multiples, or None when undefined."""
    if len(results) > 1 and stdev(results) > 0:
        return fmean(results) / stdev(results) * sqrt(len(results))
    return None


def _summarize(
    pair: dict,
    horizon: str,
    trades: list[dict],
    same_bar: int,
    *,
    oos_frac: float = 0.3,
) -> dict:
    results = [float(trade["resultR"]) for trade in trades]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    expectancy = fmean(results) if results else 0.0
    sqn = _sqn(results) or 0.0

    # Temporal in-sample / out-of-sample split. Trades are appended in bar order
    # so slicing by count is chronological. The last ``oos_frac`` fraction is the
    # holdout; a threshold that only worked on the fitting window shows up as a
    # positive is_sqn with a collapsed oos_sqn. REPORTING ONLY — it does not
    # change which trades are generated.
    oos_frac = min(0.9, max(0.0, float(oos_frac)))
    n = len(trades)
    oos_start = int(n * (1.0 - oos_frac)) if n else 0
    for idx, trade in enumerate(trades):
        trade["oos"] = idx >= oos_start
    is_results = results[:oos_start]
    oos_results = results[oos_start:]
    is_sqn = _sqn(is_results)
    oos_sqn = _sqn(oos_results)
    overfit_flag = bool(
        is_sqn is not None
        and oos_sqn is not None
        and len(is_results) >= 20
        and len(oos_results) >= 20
        and (is_sqn - oos_sqn) >= 1.0
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
            "is_trades": len(is_results),
            "oos_trades": len(oos_results),
            "is_sqn": round(is_sqn, 4) if is_sqn is not None else None,
            "oos_sqn": round(oos_sqn, 4) if oos_sqn is not None else None,
            "oosFraction": oos_frac,
            "overfit_flag": overfit_flag,
            "wf_note": (
                "temporal holdout: last {:.0%} of trades are out-of-sample; "
                "V3 evaluates confirmed prefixes only".format(oos_frac)
            ),
            "lowSampleSqnWarning": len(trades) < 60,
            "lowSampleSqnTradeFloor": 60,
        },
    }


def _intermarket_confirmation_for_bar(
    pair: dict,
    signal,
    *,
    raw_context: dict | None,
) -> dict | None:
    if not raw_context or signal.direction not in ("LONG", "SHORT"):
        return None
    try:
        from config import CONFIG
        from intermarket import apply_confirmation_to_score

        _im = apply_confirmation_to_score(
            float(signal.confluenceScore or 0.0),
            str(signal.direction),
            pair,
            raw_context,
            max_score=float(signal.maxScore or 3.0),
            config=CONFIG,
        )
        confirmation = _im.get("confirmation")
        return confirmation if isinstance(confirmation, dict) else None
    except Exception:
        return None


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
    intermarket_context_provider: Callable[[Any], dict | None] | None = None,
) -> dict:
    primary_tf = "H1" if horizon == "intraday" else "H4"
    primary = list(candles.get(primary_tf) or [])
    min_index = max(80, int(start_index or 80))
    trades: list[dict] = []
    same_bar = 0
    next_available_index = min_index
    from athena_app.services.market_state import candle_timestamp_epoch

    # Precompute per-TF open-time epochs once. The per-bar prefix keeps only bars
    # that have FULLY CLOSED by the primary decision bar's close, so a higher-TF
    # bar that is still forming at the decision (e.g. the same-day D1 during an
    # H4/H1 decision, or the same-4h H4 during an H1 decision) can never leak its
    # eventual close into scoring. This matches the live confirmed-only assembly
    # (candle_service / split_market_state strip the forming bar of every TF).
    # bisect on the (sorted) epoch array keeps this O(log n); an unsorted TF
    # falls back to a linear scan.
    _open_epochs: dict[str, list[int]] = {}
    _epoch_sorted: dict[str, bool] = {}
    for _tf, _rows in candles.items():
        _eps = [candle_timestamp_epoch(c) for c in _rows]
        _open_epochs[_tf] = _eps
        _epoch_sorted[_tf] = all(_eps[i] <= _eps[i + 1] for i in range(len(_eps) - 1))
    _primary_open_epochs = _open_epochs.get(primary_tf, [])

    def _build_prefix_at(primary_open_epoch: int) -> dict[str, list[dict]]:
        prefix: dict[str, list[dict]] = {}
        for tf, rows in candles.items():
            cutoff_open = confirmed_cutoff_open_epoch(primary_tf, primary_open_epoch, tf)
            eps = _open_epochs[tf]
            if _epoch_sorted[tf]:
                split = bisect.bisect_right(eps, cutoff_open)
                prefix[tf] = rows[:split]
            else:
                prefix[tf] = [
                    rows[k] for k in range(len(rows)) if 0 < eps[k] <= cutoff_open
                ]
        return prefix

    # Per-run indicator snapshot cache keyed by (tf, len(prefix)). Secondary TFs
    # (D1, H4 when intraday) keep the same prefix across many primary bars, so
    # this avoids recomputing their full indicator bundle each bar. Safe because
    # the underlying candle lists are immutable for the whole run and the prefix
    # for a given (tf, k) is always candles[tf][:k].
    snapshot_cache: dict = {}

    _bt_cfg: dict = {}
    try:
        from config import CONFIG

        _bt_cfg = dict(CONFIG.get("ENGINE_A_V3_BACKTEST") or {})
    except Exception:
        _bt_cfg = {}
    _round_trip_costs = bool(_bt_cfg.get("ROUND_TRIP_COSTS", True))
    try:
        _oos_frac = float(_bt_cfg.get("OOS_FRACTION", 0.3))
    except (TypeError, ValueError):
        _oos_frac = 0.3

    for index in range(min_index, len(primary) - 1):
        if index < next_available_index:
            continue
        decision_cutoff = primary[index].get("time")
        prefix = _build_prefix_at(_primary_open_epochs[index])
        signal = evaluate_engine_a_v3(
            pair,
            prefix,
            horizon=horizon,
            registry=registry,
            snapshot_cache=snapshot_cache,
        )
        _im_confirmation = None
        if intermarket_context_provider is not None:
            try:
                raw_ctx = intermarket_context_provider(decision_cutoff)
                _im_confirmation = _intermarket_confirmation_for_bar(
                    pair,
                    signal,
                    raw_context=raw_ctx,
                )
            except Exception:
                _im_confirmation = None
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
        max_exit_index = min(len(primary) - 1, index + max_hold_bars)
        exit_result = _simulate_exit(
            primary[index + 1 : max_exit_index + 1], direction=direction,
            entry=entry, sl=sl, tp1=float(tp1), tp2=tp2,
            exit_policy=signal.exitPolicy,
        )
        outcome = exit_result.outcome
        result_r = exit_result.result_r
        exit_index = index + 1 + exit_result.exit_offset
        same_bar += 1 if exit_result.same_bar else 0
        from engine_a_v3.evaluator import _parse_time
        entry_time = _parse_time(entry_bar.get("time") or entry_bar.get("datetime"))
        exit_time = _parse_time(primary[exit_index].get("time") or primary[exit_index].get("datetime"))
        if entry_time is None or exit_time is None:
            continue
        cost_r = _cost_r(
            entry,
            sl,
            spread_bps=spread_bps,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            swap_bps_per_day=swap_bps_per_day,
            entry_time=entry_time,
            exit_time=exit_time,
            round_trip=_round_trip_costs,
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
                "exitPolicy": signal.exitPolicy,
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
        if _im_confirmation is not None:
            trades[-1]["intermarketConfirmation"] = _im_confirmation
        next_available_index = exit_index + 1

    result = _summarize(pair, horizon, trades, same_bar, oos_frac=_oos_frac)
    if collect_funnel:
        # Diagnostic: re-walk every bar (ignoring the post-trade cooldown) to attribute
        # why trades are scarce — data depth vs decision distribution vs which gate fires.
        decisions = {"TRADE": 0, "WATCH": 0, "NO_SIGNAL": 0}
        qualified = 0
        reasons: dict[str, int] = {}
        evaluated = 0
        for index in range(min_index, len(primary) - 1):
            prefix = _build_prefix_at(_primary_open_epochs[index])
            sig = evaluate_engine_a_v3(pair, prefix, horizon=horizon, registry=registry, snapshot_cache=snapshot_cache)
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
