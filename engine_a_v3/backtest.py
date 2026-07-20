from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from statistics import fmean, stdev
from typing import Any

from engine_a_v3.contract import CONTRACT_VERSION
from engine_a_v3.diagnostics import reverse_direction_result_r, trade_path_diagnostics
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.promotion import PromotionRegistry
from engine_a_v3.routing import route_specialist
from engine_a_v3.setups import _efficiency_ratio
from engine_a_v3.timeframes import resolve_v3_entry_timeframe

_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


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


def engine_a_v3_backtest_costs(asset_type: str | None, config: dict) -> dict:
    """Effective ENGINE_A_V3_BACKTEST cost block for one asset class.

    Base keys stay flat for backwards compatibility; BY_ASSET entries override
    per asset class so crypto backtests carry exchange-grade commissions (the
    promotion manifest's costs) instead of the flat forex-grade defaults.
    """
    base = dict(config.get("ENGINE_A_V3_BACKTEST") or {})
    by_asset = base.pop("BY_ASSET", None) or {}
    override = by_asset.get(str(asset_type or "").strip().lower())
    if isinstance(override, dict):
        base.update(override)
    return base


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
    # Split policy keeps the ORIGINAL stop on the runner half after TP1 fills:
    # half banked at TP1, half stopped at -1R. The previous hardcoded 0.0 was
    # only correct when TP1 sat exactly 1R from entry.
    tp1_then_sl_r = 0.5 * abs(tp1 - entry) / risk - 0.5
    for offset, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        sl_hit = low <= sl if direction == "LONG" else high >= sl
        tp1_hit = high >= tp1 if direction == "LONG" else low <= tp1
        tp2_hit = high >= tp2 if direction == "LONG" else low <= tp2
        if sl_hit and ((not tp1_filled and tp1_hit) or (tp1_filled and tp2_hit)):
            return ExitResult("SL" if not tp1_filled else "TP1_THEN_SL", -1.0 if not tp1_filled else tp1_then_sl_r, offset, True)
        if sl_hit:
            return ExitResult("SL" if not tp1_filled else "TP1_THEN_SL", -1.0 if not tp1_filled else tp1_then_sl_r, offset)
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
    direction_counts = Counter()
    for trade in trades:
        direction = str(trade.get("direction") or "").upper()
        if direction in {"LONG", "SHORT"}:
            direction_counts[direction] += 1
        else:
            direction_counts["UNKNOWN"] += 1
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
        "directionBreakdown": {
            "LONG": direction_counts["LONG"],
            "SHORT": direction_counts["SHORT"],
            "UNKNOWN": direction_counts["UNKNOWN"],
        },
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


def _v3_backtest_comparability() -> dict[str, Any]:
    """Describe live inputs this historical runner does not replay."""
    return {
        "liveComparable": False,
        "promotionEligible": False,
        "unreplayedInputs": [
            "live_context",
            "live_scan_gates",
            "timeframe_speed_adaptation",
        ],
        "unreplayedScanGates": [
            "exchangeClosed",
            "eventRisk",
            "macroEventRisk",
            "directionConflicted",
        ],
    }


class _MemoizedPromotionRegistry:
    """Run-scoped promotion memo for the backtest loop.

    Registry artifacts on disk cannot change mid-run, and the decision is a
    frozen dataclass, so resolving once per (route, horizon, symbol) is
    behavior-identical to the per-bar re-read the evaluator does live (where
    artifacts CAN change between scans). Never used outside a backtest run.
    """

    def __init__(self, inner):
        self._inner = inner
        self._memo: dict[tuple, Any] = {}

    def resolve(self, route, horizon, *, symbol, now=None):
        key = (route.family, route.subclass, route.score_group, str(horizon), str(symbol))
        hit = self._memo.get(key)
        if hit is None:
            hit = self._inner.resolve(route, horizon, symbol=symbol, now=now)
            self._memo[key] = hit
        return hit


def _classify_backtest_signal(signal: Any, pair: dict) -> tuple[str, str]:
    """Use the scan tier only when the V3 contract can be serialized."""
    try:
        from athena_app.services.engine_a_v3_classify import classify_engine_a_v3_signal

        payload = signal.to_dict()
        if not isinstance(payload, dict):
            return "watchlist", "V3 signal contract serialization invalid"
        return classify_engine_a_v3_signal(payload, pair)
    except Exception:
        return "watchlist", "V3 scan eligibility unavailable"


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
    policy_timeframes: Mapping[str, Any] | None = None,
) -> dict:
    route = route_specialist(pair)
    policy = policy_timeframes if isinstance(policy_timeframes, Mapping) else None
    policy_setup_tf = str(
        (policy or {}).get("setup") or (policy or {}).get("setupTf") or ""
    ).upper()
    if policy is not None and not policy_setup_tf:
        return {
            "error": "ENGINE_A_V3_POLICY_SETUP_TIMEFRAME_MISSING",
            "engine": "ENGINE_A_V3",
            "contractVersion": CONTRACT_VERSION,
            "entryTimeframe": None,
            "comparability": _v3_backtest_comparability(),
        }
    primary_tf = policy_setup_tf or resolve_v3_entry_timeframe(
        route.score_group,
        str(pair.get("type") or pair.get("asset_type") or "other"),
        horizon,
    )
    comparability = _v3_backtest_comparability()
    if primary_tf is None:
        return {
            "error": "ENGINE_A_V3_INVALID_ENTRY_TIMEFRAME",
            "engine": "ENGINE_A_V3",
            "contractVersion": CONTRACT_VERSION,
            "entryTimeframe": None,
            "comparability": comparability,
        }
    primary = list(candles.get(primary_tf) or [])
    if policy is not None:
        policy_trigger_tf = str(
            policy.get("trigger") or policy.get("triggerTf") or ""
        ).upper()
        missing_policy_tfs = [
            tf
            for tf in dict.fromkeys((policy_setup_tf, policy_trigger_tf))
            if not tf or not candles.get(tf)
        ]
        if missing_policy_tfs:
            return {
                "error": "ENGINE_A_V3_POLICY_CANDLES_MISSING",
                "engine": "ENGINE_A_V3",
                "contractVersion": CONTRACT_VERSION,
                "entryTimeframe": primary_tf,
                "missingPolicyTimeframes": missing_policy_tfs,
                "comparability": comparability,
            }
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

    # Incremental prefix state (sorted TFs only). The loop below calls
    # _build_prefix_at with a strictly increasing primary_open_epoch each
    # iteration, so each sorted TF's split point is monotonically
    # non-decreasing across the whole run. Re-slicing `rows[:split]` from
    # scratch every bar costs O(split) per call -- summed over n bars with
    # split growing roughly linearly, that is O(n^2) total (confirmed via
    # profiling: real EURUSD H1 cost grew from 8.2ms/bar at 1500 bars to
    # 16.0ms/bar at 5000 bars). Instead, EXTEND the same growing list with
    # only the newly-included rows each bar -- O(1) amortized per row, O(n)
    # total across the run. Safe because evaluate_engine_a_v3 and everything
    # downstream consume the prefix synchronously within one call and never
    # retain the list reference afterward.
    _prefix_state: dict[str, list[dict]] = {tf: [] for tf in candles}
    _prefix_split: dict[str, int] = {tf: 0 for tf in candles}

    def _build_prefix_at(primary_open_epoch: int) -> dict[str, list[dict]]:
        prefix: dict[str, list[dict]] = {}
        for tf, rows in candles.items():
            cutoff_open = confirmed_cutoff_open_epoch(primary_tf, primary_open_epoch, tf)
            eps = _open_epochs[tf]
            if not _epoch_sorted[tf]:
                # Rare/defensive fallback for out-of-order feeds: no incremental
                # invariant holds, so filter fresh each call (matches legacy exactly).
                prefix[tf] = [
                    rows[k] for k in range(len(rows)) if 0 < eps[k] <= cutoff_open
                ]
                continue
            split = bisect.bisect_right(eps, cutoff_open)
            old_split = _prefix_split[tf]
            if split > old_split:
                _prefix_state[tf].extend(rows[old_split:split])
                _prefix_split[tf] = split
            elif split < old_split:
                # Defensive only: primary_open_epoch is expected to be strictly
                # increasing, so split should never shrink. Rebuild if it does.
                _prefix_state[tf] = rows[:split]
                _prefix_split[tf] = split
            prefix[tf] = _prefix_state[tf]
        return prefix

    # Per-run indicator snapshot cache: full-series indicator arrays computed
    # once per timeframe and served value-at-index, which is bit-identical to
    # recomputing over each prefix (same causal recurrence, same start) but
    # O(n) total instead of O(n^2). Always on -- there is no correctness
    # tradeoff, only a performance one, so gating this behind a flag served no
    # purpose once the identity was proven (see
    # tests/test_engine_a_v3_backtest_parity.py::
    # test_vectorized_snapshot_cache_matches_prefix_indicator_path_exactly).
    snapshot_cache: dict = {}
    candle_validation_index = None
    setup_series_cache = None
    try:
        from engine_a_v3.indicator_adapter import BacktestIndicatorSnapshotCache
        from engine_a_v3.evaluator import BacktestCandleValidationIndex
        from engine_a_v3.setups import SetupSeriesCache

        snapshot_cache = BacktestIndicatorSnapshotCache(candles)
        candle_validation_index = BacktestCandleValidationIndex(candles)
        setup_series_cache = SetupSeriesCache(candles)
    except (ImportError, KeyError, TypeError, ValueError, OverflowError):
        # Preserve the established per-prefix cache if the optimized cache cannot
        # be initialized. Backtest scoring remains fail-compatible, not fail-open.
        snapshot_cache = {}
        candle_validation_index = None
        setup_series_cache = None

    # Promotion artifacts cannot change mid-run: resolve once per route/symbol
    # instead of re-reading + re-validating the registry on every bar.
    from engine_a_v3.promotion import production_registry

    _run_registry: Any = _MemoizedPromotionRegistry(registry or production_registry())

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

    # Funnel accounting (diagnostic decision/rejection-reason counts across
    # every bar, ignoring the post-trade cooldown) is collected inline when
    # requested instead of via a second full re-walk: evaluate_engine_a_v3 is
    # the single most expensive call in this loop, so merging avoids paying
    # for it twice. When collect_funnel is False (the default, hot path) the
    # cooldown-skip below is unchanged and cheaper still.
    funnel_decisions = {"TRADE": 0, "WATCH": 0, "NO_SIGNAL": 0}
    funnel_qualified = 0
    funnel_raw_qualified = 0
    funnel_scan_eligible = 0
    funnel_reasons: dict[str, int] = {}
    funnel_scan_reasons: dict[str, int] = {}
    funnel_evaluated = 0

    for index in range(min_index, len(primary) - 1):
        in_cooldown = index < next_available_index
        if in_cooldown and not collect_funnel:
            continue
        decision_cutoff = primary[index].get("time")
        prefix = _build_prefix_at(_primary_open_epochs[index])
        signal = evaluate_engine_a_v3(
            pair,
            prefix,
            horizon=horizon,
            registry=_run_registry,
            snapshot_cache=snapshot_cache,
            policy_timeframes=policy,
            candle_validation_index=candle_validation_index,
            setup_series_cache=setup_series_cache,
        )
        _classified: tuple[str, str] | None = None
        if collect_funnel:
            funnel_decisions[signal.decision] = funnel_decisions.get(signal.decision, 0) + 1
            funnel_qualified += 1 if signal.qualified else 0
            _is_raw_qualified = signal.decision == "TRADE" and signal.qualified
            funnel_raw_qualified += 1 if _is_raw_qualified else 0
            _tier, _tier_reason = _classify_backtest_signal(signal, pair)
            _classified = (_tier, _tier_reason)
            funnel_scan_eligible += 1 if _tier == "trade" else 0
            if _is_raw_qualified and _tier != "trade":
                funnel_scan_reasons[_tier_reason] = funnel_scan_reasons.get(_tier_reason, 0) + 1
            for _reason in signal.rejectionReasons:
                funnel_reasons[_reason] = funnel_reasons.get(_reason, 0) + 1
            funnel_evaluated += 1
        if in_cooldown:
            continue
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
        raw_qualified = signal.decision == "TRADE" and signal.qualified
        if not raw_qualified:
            continue
        # Reuse the funnel classification (same signal object, same result)
        # instead of re-serializing the full contract a second time per bar.
        signal_tier, _signal_tier_reason = _classified or _classify_backtest_signal(signal, pair)
        if signal_tier != "trade":
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
        primary_prefix = prefix.get(primary_tf) or []
        regime_label = "unknown"
        if len(primary_prefix) >= 20:
            primary_eff, _ = _efficiency_ratio(primary_prefix, 20)
            if primary_eff >= 0.3:
                regime_label = "trending"
            elif primary_eff <= 0.2:
                regime_label = "ranging"
            else:
                regime_label = "neutral"
        # _efficiency_ratio only reads the last 20 closes; slicing the bounded
        # window here (instead of the historical `primary[: index + 1]` full
        # copy) avoids an O(index) list copy on every trade, which made this
        # call site's total cost scale with trade_count * bars instead of
        # trade_count * window -- the dominant super-linear cost at deep
        # history (confirmed via profiling: 1500 bars @ 8.2ms/bar vs 5000
        # bars @ 16.0ms/bar). Output is identical: same last-20-closes-up-to-
        # index either way.
        efficiency_at_entry, _ = _efficiency_ratio(primary[max(0, index + 1 - 20) : index + 1], 20)
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
                "signalTier": signal_tier,
                "signalTierReason": _signal_tier_reason,
                "oos": True,
            }
        )
        if _im_confirmation is not None:
            trades[-1]["intermarketConfirmation"] = _im_confirmation
        next_available_index = exit_index + 1

    result = _summarize(pair, horizon, trades, same_bar, oos_frac=_oos_frac)
    result["entryTimeframe"] = primary_tf
    result["policyTimeframesApplied"] = dict(policy) if policy else None
    result["comparability"] = comparability
    if collect_funnel:
        # Counters accumulated inline in the main loop above (every bar,
        # ignoring the post-trade cooldown) rather than via a second re-walk.
        result["funnel"] = {
            "primaryTf": primary_tf,
            "dataDepth": {tf: len(rows) for tf, rows in candles.items()},
            "minIndexWarmup": min_index,
            "barsEvaluated": funnel_evaluated,
            "decisions": funnel_decisions,
            "qualified": funnel_qualified,
            "rawQualified": funnel_raw_qualified,
            "scanEligible": funnel_scan_eligible,
            "tradesTaken": len(trades),
            "topRejectionReasons": dict(
                sorted(funnel_reasons.items(), key=lambda kv: -kv[1])[:15]
            ),
            "topScanEligibilityReasons": dict(
                sorted(funnel_scan_reasons.items(), key=lambda kv: -kv[1])[:15]
            ),
            "unreplayedScanGates": list(comparability["unreplayedScanGates"]),
        }
    return result
