"""Engine B backtest adapter.

Zero scoring logic here: every decision comes from the shared live snapshot
contract `engine_b_snapshot.evaluate_engine_b_snapshot` (the same function the
live scanner's independent direction probe calls), evaluated at trigger-TF
bar closes over point-in-time confirmed prefixes. Speed state is replayed
historically (`athena_backtest.speed_replay`) so timeframe-policy promotion
matches live instead of being pinned to the baseline.

Trade construction mirrors the retired legacy runner's Engine B loop
risk contract (execution levels from the gating confidence dict, fallback-RR
cap, side check, rr_required gate, MAX_SL_PCT cap) without re-implementing
any scoring. Exits go through the unified FIXED simulator; the donor's
optional runner-trail/timed variants are disclosed as unreplayed in v1.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timezone

from athena_backtest.costs import cost_r, resolve_costs
from athena_backtest.data.store import ParquetCandleStore
from athena_backtest.exits import SAME_BAR_POLICY, simulate_fixed_exit
from athena_backtest.speed_replay import SpeedStateReplay

_PROVIDER_BY_SOURCE = {
    "mt5": "mt5",
    "binance": "binance_futures",
    "eodhd": "eodhd",
}

_ROLE_TFS = ("D1", "H4", "H1", "M30", "M15")
_TF_SECONDS = {"D1": 86_400, "H4": 14_400, "H1": 3_600, "M30": 1_800, "M15": 900}

_UNREPLAYED = (
    "live_quote_gates",
    "microstructure_context",
    "dxy_h4_closes",
    "zone_registry_persistence",
    "runner_trail_and_timed_exit_variants",
    "spread_and_session_speed_inputs",
)


def load_candles(pair: dict, *, store: ParquetCandleStore | None = None) -> dict[str, list[dict]]:
    store = store or ParquetCandleStore()
    source = str(pair.get("source") or "").strip().lower()
    provider = _PROVIDER_BY_SOURCE.get(source)
    if provider is None:
        return {}
    # Fail-closed: a missing timeframe stays [] and is never substituted.
    return {tf: store.read(pair, tf, provider=provider) for tf in _ROLE_TFS}


def _epoch(row: dict) -> int:
    value = row.get("time")
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def _bar_dt(row: dict) -> datetime:
    return datetime.fromtimestamp(_epoch(row), tz=timezone.utc)


def run_engine_b_backtest(
    pair: dict,
    *,
    style: str = "intraday",
    store: ParquetCandleStore | None = None,
    candles: dict[str, list[dict]] | None = None,
    collect_funnel: bool = True,
) -> dict:
    from engine_a_v3.backtest import _summarize
    from engine_b_snapshot import (
        EngineBHistoricalFeatureCache,
        evaluate_engine_b_snapshot,
        resolve_engine_b_regime_label,
        resolve_engine_b_style_profile,
    )
    from engine_a_groups import resolve_score_group_by_type
    from indicators import calc_indicators_with_normalized
    from market_structure import NakedEngine, resolve_engine_b_h4_snap
    from athena_app.services.engine_b_direction import independent_conflict_blocks_emit
    from config import CONFIG, scan_candle_limits

    candles = candles if candles is not None else load_candles(pair, store=store)
    role_rows = {tf: list(candles.get(tf) or []) for tf in _ROLE_TFS}
    role_close_epochs = {
        tf: [_epoch(row) + _TF_SECONDS[tf] for row in rows] for tf, rows in role_rows.items()
    }

    # Iteration series: fastest stored intraday TF (trigger cadence upper bound).
    iter_tf = next((tf for tf in ("M15", "M30", "H1") if role_rows.get(tf)), None)
    if iter_tf is None or len(role_rows["D1"]) < 30 or len(role_rows["H4"]) < 30 or len(role_rows["H1"]) < 50:
        return {
            "pair": pair.get("display"),
            "engine": "B",
            "style": style,
            "error": "insufficient_history",
            "coverage": {tf: len(rows) for tf, rows in role_rows.items()},
        }
    iter_rows = role_rows[iter_tf]
    iter_dur = _TF_SECONDS[iter_tf]

    # Full-series Wilder-ATR precompute (causal recurrence — value at a prefix
    # length equals the from-scratch prefix computation; proven by the Engine A
    # SetupSeriesCache parity tests). Passed as atr_override so the snapshot
    # skips its per-bar O(n) `_atr_from_candles` walk over the full prefix.
    from engine_a_v3.setups import SetupSeriesCache

    atr_series_cache = SetupSeriesCache(role_rows)

    # Live parity: the scanner never sees full history -- every scan fetch is
    # capped at scan_candle_limits() bars per TF (D1/H4/H1 1000, M30/M15 500).
    # Windowing each point-in-time prefix to the same depth both mirrors what
    # the live probe evaluates and keeps the per-bar cost constant instead of
    # O(full prefix length).
    tf_window = scan_candle_limits()

    spec = resolve_costs(pair, engine="B")
    score_group = resolve_score_group_by_type(pair)
    asset_type = str(pair.get("type") or "")
    symbol = str(pair.get("display") or pair.get("symbol") or "")
    replay = SpeedStateReplay(pair, role_rows["H1"], role_rows["M15"])
    h1_close_epochs = role_close_epochs["H1"]
    naked = NakedEngine()

    def _trigger_atr_lookup(tf: str, rows: list) -> float | None:
        # Same value as NakedEngine._compute_atr_from_candles for the positive
        # case (last positive causal Wilder-ATR over the prefix); returning
        # None defers to the default walk so fallback semantics are untouched.
        try:
            if len(rows) < 2 or not atr_series_cache.matches(tf, rows):
                return None
            value = atr_series_cache.atr_at(tf, len(rows), 14)
        except Exception:
            return None
        return value if value is not None and value > 0 else None

    feature_cache = EngineBHistoricalFeatureCache(trigger_atr_fn=_trigger_atr_lookup)

    bt_exit_cfg = (CONFIG.get("ENGINE_B_BT_EXIT", {}) or {}).get(
        asset_type, (CONFIG.get("ENGINE_B_BT_EXIT", {}) or {}).get("stock", {})
    )
    style_exit_cfg = bt_exit_cfg.get(style, bt_exit_cfg.get("intraday", {})) if isinstance(bt_exit_cfg, dict) else {}
    try:
        max_hold_h4_bars = int(style_exit_cfg.get("max_hold_bars", 24))
    except (TypeError, ValueError):
        max_hold_h4_bars = 24
    max_hold_iter_bars = max_hold_h4_bars * (_TF_SECONDS["H4"] // iter_dur)
    max_sl_pct = float((CONFIG.get("MAX_SL_PCT", {}) or {}).get(asset_type, 0.05))

    funnel: dict[str, int] = {
        "bars_seen": 0,
        "bars_evaluated": 0,
        "no_clear_direction": 0,
        "gate_or_conflict_rejected": 0,
        "selected_not_passed": 0,
        "missing_execution_levels": 0,
        "levels_side_invalid": 0,
        "rr_gate_rejected": 0,
        "sl_cap_rejected": 0,
        "entered": 0,
    }
    rejection_reasons: dict[str, int] = {}
    trades: list[dict] = []
    same_bar = 0

    # D1/H4 snapshots + regime label are pure functions of their confirmed
    # prefixes. Live computes them once per scan refresh and passes them into
    # the probe; recomputing them per trigger bar was the dominant cost. The
    # values are identical between D1/H4 closes, so hoist and rebuild only
    # when the owning prefix length changes (same functions, same inputs).
    _d1_snap_len = -1
    _d1_snap: dict = {}
    _h4_snap_len = -1
    _h4_snap: dict = {}
    _regime_label = "RANGING"
    # Reported in comparability.slTpGeometry, which is built after the loop — a
    # run whose replay window contains no evaluable bar must not NameError there.
    atr_tf = ""

    # Warmup: skip until every core TF has its minimum confirmed history.
    min_start_epoch = max(
        role_close_epochs["D1"][29],
        role_close_epochs["H4"][29],
        role_close_epochs["H1"][49],
    )
    start_index = bisect.bisect_left(
        [e for e in role_close_epochs[iter_tf]], min_start_epoch
    )

    # With live-parity TF windows the per-bar cost is constant (~29ms
    # measured for BTCUSDT M15), but a full multi-year M15 replay is still
    # tens of minutes. Bound the replay to the most recent window by default
    # (only the trigger-bar iteration count is capped; warmup/regime inputs
    # use the same live-depth windows either way) so a single symbol stays
    # under the Stage-5 <5min target; the applied window is disclosed in
    # `comparability` so results are never silently partial. 180 days
    # extrapolates to ~2min for BTCUSDT M15.
    max_replay_days = float((CONFIG.get("ENGINE_B_BT_MAX_REPLAY_DAYS", {}) or {}).get(asset_type, 180))
    max_replay_bars = int(max_replay_days * 86_400 // iter_dur) if max_replay_days > 0 else None
    replay_window_bounded = False
    total_index = max(0, start_index)
    if max_replay_bars is not None and (len(iter_rows) - total_index) > max_replay_bars:
        total_index = max(total_index, len(iter_rows) - max_replay_bars)
        replay_window_bounded = True

    index = total_index
    while index < len(iter_rows) - 1:
        decision_epoch = role_close_epochs[iter_tf][index]
        funnel["bars_seen"] += 1

        h1_closed = bisect.bisect_right(h1_close_epochs, decision_epoch) - 1
        speed_state = replay.state_at(h1_closed) if h1_closed >= 0 else None
        resolved_style, profile = resolve_engine_b_style_profile(
            style,
            score_group,
            asset_type,
            symbol=symbol,
            speed_state=speed_state,
        )
        trigger_tf = str(profile.get("entry_tf") or "H1").upper()
        trigger_dur = _TF_SECONDS.get(trigger_tf, 3_600)
        # Evaluate only on trigger-TF close boundaries (all supported TFs are
        # midnight-UTC aligned, so a modulo check identifies shared closes).
        if decision_epoch % trigger_dur != 0:
            index += 1
            continue
        funnel["bars_evaluated"] += 1

        prefix_counts = {
            tf: bisect.bisect_right(role_close_epochs[tf], decision_epoch)
            for tf, rows in role_rows.items()
            if rows
        }
        role_prefixes = {
            tf: role_rows[tf][max(0, count - int(tf_window.get(tf, count))) : count]
            for tf, count in prefix_counts.items()
        }
        current_price = float(iter_rows[index]["close"])
        d1_count = prefix_counts.get("D1", 0)
        h4_count = prefix_counts.get("H4", 0)
        if d1_count != _d1_snap_len:
            try:
                _d1_snap = (calc_indicators_with_normalized(role_prefixes.get("D1") or [], asset_type) or {}).get("snap") or {}
            except Exception:
                _d1_snap = {}
            _d1_snap_len = d1_count
        if h4_count != _h4_snap_len:
            h4_prefix = role_prefixes.get("H4") or []
            try:
                _h4_snap = resolve_engine_b_h4_snap(h4_prefix, asset_type)
            except Exception:
                _h4_snap = {}
            _regime_label = str(resolve_engine_b_regime_label(h4_prefix, asset_type)).upper()
            _h4_snap_len = h4_count
        atr_tf = str(profile.get("atr_tf") or "H1").upper()
        # True prefix count, not the windowed length: the full-series Wilder
        # ATR at index count-1 equals a windowed recompute (period-14
        # recurrence is fully converged after >=500 bars of window).
        atr_prefix_len = prefix_counts.get(atr_tf, 0)
        atr_override = (
            atr_series_cache.atr_at(atr_tf, atr_prefix_len, 14)
            if atr_prefix_len >= 15
            else 0.0
        )
        result = evaluate_engine_b_snapshot(
            pair,
            role_prefixes,
            current_price=current_price,
            style=style,
            score_group=score_group,
            style_profile=profile,
            atr_override=atr_override,
            regime_label=_regime_label,
            d1_snapshot=_d1_snap,
            h4_snapshot=_h4_snap,
            engine=naked,
            context_mode="historical",
            feature_cache=feature_cache,
            conflict_fn=independent_conflict_blocks_emit,
        )
        selected = result.selected
        if selected is None:
            reason = result.rejection_reason or "no_selection"
            if reason == "no_clear_direction":
                funnel["no_clear_direction"] += 1
            else:
                funnel["gate_or_conflict_rejected"] += 1
            if collect_funnel:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            index += 1
            continue
        if not selected.get("passed"):
            funnel["selected_not_passed"] += 1
            index += 1
            continue

        conf = dict(selected.get("confidence") or {})
        direction = str(selected["direction"])
        entry_bar = iter_rows[index + 1]
        entry = float(entry_bar.get("open", entry_bar["close"]))
        sl = conf.get("execution_sl")
        tp = conf.get("execution_tp")
        tp1 = conf.get("execution_tp1", tp)
        tp2 = conf.get("execution_tp2", tp)
        if sl is None or tp1 is None or tp2 is None:
            funnel["missing_execution_levels"] += 1
            index += 1
            continue
        sl, tp1, tp2 = float(sl), float(tp1), float(tp2)

        risk = abs(entry - sl)
        # Donor parity: runner target capped at the style's fallback RR contract.
        fallback_rr = float(profile.get("fallback_rr", 0.0) or 0.0)
        if risk > 0 and fallback_rr > 0 and abs(tp2 - entry) / risk > fallback_rr:
            tp2 = entry + risk * fallback_rr if direction == "LONG" else entry - risk * fallback_rr
            if (direction == "LONG" and tp1 > tp2) or (direction == "SHORT" and tp1 < tp2):
                tp1 = tp2
        side_ok = (
            (direction == "LONG" and sl < entry < tp1 and entry < tp2)
            or (direction == "SHORT" and sl > entry > tp1 and entry > tp2)
        )
        if risk <= 0 or not side_ok:
            funnel["levels_side_invalid"] += 1
            index += 1
            continue
        target_rr = abs(tp2 - entry) / risk
        try:
            rr_required = float(conf.get("rr_required"))
        except (TypeError, ValueError):
            rr_required = float(profile.get("min_rr", 1.0))
        if target_rr < rr_required:
            funnel["rr_gate_rejected"] += 1
            index += 1
            continue
        if entry > 0 and risk / entry > max_sl_pct:
            funnel["sl_cap_rejected"] += 1
            index += 1
            continue

        exit_strategy = str(conf.get("exit_strategy") or "")
        exit_policy = (
            "SPLIT_50_50"
            if exit_strategy == "scale_out_structural_tp1_fallback_tp2"
            else "SINGLE_TP1"
        )
        max_exit_index = min(len(iter_rows) - 1, index + 1 + max_hold_iter_bars)
        outcome = simulate_fixed_exit(
            iter_rows[index + 2 : max_exit_index + 1],
            direction=direction,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            exit_policy=exit_policy,
        )
        if outcome.outcome == "INVALID":
            funnel["levels_side_invalid"] += 1
            index += 1
            continue
        funnel["entered"] += 1
        same_bar += 1 if outcome.same_bar else 0
        exit_index = min(len(iter_rows) - 1, index + 2 + outcome.exit_offset)
        friction = cost_r(
            entry,
            sl,
            spec,
            entry_time=_bar_dt(entry_bar),
            exit_time=_bar_dt(iter_rows[exit_index]),
        )
        result_r = outcome.result_r - friction
        trades.append(
            {
                "entryTime": str(entry_bar.get("time")),
                "exitTime": str(iter_rows[exit_index].get("time")),
                "direction": direction,
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "rr": round(target_rr, 3),
                "score": float(selected.get("score") or 0.0),
                "style": resolved_style,
                "triggerTf": trigger_tf,
                "exitPolicy": exit_policy,
                # SL/TP geometry attribution: which stage produced this stop and
                # target, so stop-width and RR outcomes can be split by origin
                # (structural pivot vs mechanical ATR vs RR-tighten vs synthesis)
                # instead of only in aggregate.
                "slSource": conf.get("sl_source"),
                "tpSource": conf.get("tp_source"),
                "levelCohort": conf.get("level_cohort"),
                "stopDistanceAtr": conf.get("stop_distance_atr"),
                "slTightenedForRr": bool(conf.get("sl_tightened_for_rr")),
                "syntheticRrTpUsed": bool(conf.get("synthetic_rr_tp_used")),
                "outcome": outcome.outcome,
                "grossR": round(outcome.result_r, 4),
                "costR": round(friction, 4),
                "resultR": round(result_r, 4),
                "speedClass": str(getattr(speed_state, "speed_class", "") or ""),
            }
        )
        index = exit_index + 1

    summary = _summarize(pair, style, trades, same_bar)
    summary.update(
        {
            "engine": "B",
            "style": style,
            "iterationTimeframe": iter_tf,
            "sameBarPolicy": SAME_BAR_POLICY,
            "funnel": funnel if collect_funnel else None,
            "rejectionReasons": rejection_reasons if collect_funnel else None,
            "featureCache": feature_cache.diagnostics(),
            "costSpec": {
                "spreadBps": spec.spread_bps,
                "commissionBps": spec.commission_bps,
                "slippageBps": spec.slippage_bps,
                "swapBpsPerDay": spec.swap_bps_per_day,
                "sourceKeys": list(spec.source_keys),
            },
            "comparability": {
                "unreplayed": list(_UNREPLAYED),
                "exitSimGranularity": iter_tf,
                "speedStateReplayed": True,
                "replayWindowBounded": replay_window_bounded,
                "replayWindowDays": max_replay_days if replay_window_bounded else None,
                "replayStartIndex": total_index,
                "tfWindowBars": {tf: int(tf_window.get(tf, 0)) for tf in _ROLE_TFS},
                # SL/TP geometry contract this run was produced under. Without it
                # two runs with different stop/target geometry are byte-comparable
                # in the result files but not comparable in fact — including runs
                # from a sibling git worktree still on the pre-2026-07-25 code,
                # where these keys are absent entirely.
                "slTpGeometry": {
                    "pivotBufferEnabled": bool(
                        CONFIG.get("ENGINE_B_STRUCTURAL_SL_PIVOT_BUFFER_ENABLED", True)
                    ),
                    "pivotBufferAtr": (
                        (CONFIG.get("NAKED_ENGINE") or {}).get(
                            "structural_sl_pivot_buffer_atr"
                        )
                    ),
                    "syntheticTpRrBasis": str(
                        CONFIG.get("ENGINE_B_SYNTHETIC_TP_RR_BASIS", "min_rr")
                    ),
                    "preferSlTightenOverTpExtension": bool(
                        CONFIG.get("ENGINE_B_PREFER_SL_TIGHTEN_OVER_TP_EXTENSION", True)
                    ),
                    "minSlAtr": CONFIG.get("ENGINE_B_MIN_SL_ATR_DEFAULT"),
                    "maxSlAtr": CONFIG.get("ENGINE_B_MAX_SL_ATR_DEFAULT"),
                    "absoluteMinSlAtr": CONFIG.get("ENGINE_B_ABSOLUTE_MIN_SL_ATR"),
                    "minLegRespectsStructural": bool(
                        CONFIG.get(
                            "ENGINE_B_ATR_SL_CLAMP_MIN_RESPECTS_STRUCTURAL", True
                        )
                    ),
                    "levelsAtrTf": atr_tf,
                },
            },
        }
    )
    return summary
