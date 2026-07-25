from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .constants import TIMEFRAME_SECONDS
from .simulator import SignalIntent
from .snapshot import CausalIndicatorIndex, CausalMarketView


class _FrozenPromotionRegistry:
    """Return the promotion decision frozen at task start for every replay bar."""

    def __init__(self, decision: Any):
        self._decision = decision

    def resolve(self, *_args: Any, **_kwargs: Any) -> Any:
        return self._decision


def _as_utc(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _policy_roles(policy: dict[str, Any]) -> dict[str, str]:
    return {
        "regime": str(policy.get("regime_tf") or policy.get("regimeTf") or "D1").upper(),
        "bias": str(policy.get("bias_tf") or policy.get("biasTf") or "H4").upper(),
        "structure": str(policy.get("structure_tf") or policy.get("structureTf") or "H1").upper(),
        "setup": str(policy.get("setup_tf") or policy.get("setupTf") or "H1").upper(),
        "trigger": str(policy.get("trigger_tf") or policy.get("triggerTf") or "H1").upper(),
        "execution": str(policy.get("execution_tf") or policy.get("executionTf") or "H1").upper(),
        # Carried so replay confirms entries on the same rung as live: policy
        # marks M5 conditional refinement behind an M15 prerequisite.
        "m5_policy": str(policy.get("m5_policy") or policy.get("m5Policy") or ""),
        "execution_prerequisite": str(
            policy.get("execution_prerequisite_tf")
            or policy.get("executionPrerequisiteTf")
            or ""
        ).upper(),
    }


def _warm(view: CausalMarketView, snapshot: dict[str, list[dict[str, Any]]], required: set[str]) -> bool:
    _ = view
    return all(len(snapshot.get(timeframe) or []) >= (50 if timeframe != "D1" else 50) for timeframe in required)


def replay_engine_a(
    pair: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    *,
    style: str,
    policy: dict[str, Any],
) -> tuple[list[SignalIntent], dict[str, Any]]:
    from engine_a_v3.evaluator import BacktestCandleValidationIndex, evaluate_engine_a_v3
    from engine_a_v3.promotion import production_registry
    from engine_a_v3.routing import route_specialist
    from engine_a_v3.setups import SetupSeriesCache

    roles = _policy_roles(policy)
    view = CausalMarketView(frames)
    full_rows = view.rows
    indicator_index = CausalIndicatorIndex(full_rows)
    validation_index = BacktestCandleValidationIndex(full_rows)
    execution_tf = roles["execution"]
    # Production's authoritative Engine A policy evaluation is confirmed-only.
    # ``forming_tf`` belongs to the legacy shadow path and must not disable the
    # indexed confirmed replay or introduce an M1 reconstruction dependency.
    forming_requested: set[str] = set()
    setup_series_cache = SetupSeriesCache(full_rows)
    quant_feature_cache: dict[Any, Any] = {}
    setup_candidate_cache: dict[str, Any] = {}
    route = route_specialist(pair)
    resolved_style = str(policy.get("style") or style).lower()
    symbol = str(pair.get("symbol") or pair.get("display") or "")
    promotion = production_registry().resolve(
        route,
        resolved_style,
        symbol=symbol,
    )
    promotion_registry = _FrozenPromotionRegistry(promotion)
    decisions = 0
    qualified = 0
    unique_signal_ids: set[str] = set()
    rejection_counts: Counter[str] = Counter()
    intents: list[SignalIntent] = []
    required = set(roles.values()) | {"D1", "H4", "H1"}
    if forming_requested:
        snapshots = (
            (decision_time, *view.snapshot(decision_time, forming_timeframes=forming_requested))
            for decision_time in view.decision_times(execution_tf)
        )
    else:
        snapshots = (
            (decision_time, candles, set())
            for decision_time, candles in view.iter_confirmed_snapshots(execution_tf)
        )
    for decision_time, candles, forming in snapshots:
        if not _warm(view, candles, required):
            continue
        indicator_index.set_forming_lengths({(tf, len(candles.get(tf) or [])) for tf in forming})
        current_rows = candles.get(execution_tf) or []
        if not current_rows:
            continue
        current_price = float(current_rows[-1]["close"])
        signal = evaluate_engine_a_v3(
            pair,
            candles,
            horizon=style,
            registry=promotion_registry,
            context={"historicalReplay": True, "decisionTimestamp": decision_time.isoformat()},
            snapshot_cache=indicator_index,
            current_price=current_price,
            entry_tf_override=roles["setup"] if forming_requested else None,
            policy_timeframes=roles,
            entry_candle_is_forming=roles["setup"] in forming or roles["trigger"] in forming,
            candle_validation_index=validation_index,
            setup_series_cache=setup_series_cache,
            quant_feature_cache=quant_feature_cache,
            setup_candidate_cache=setup_candidate_cache,
            compact_replay=True,
        )
        decisions += 1
        for reason in signal.rejectionReasons:
            rejection_counts[str(reason)] += 1
        if not signal.qualified or str(signal.direction or "") not in {"LONG", "SHORT"}:
            continue
        payload = signal.to_dict()
        signal_id = str(payload.get("signalId") or "")
        if signal_id and signal_id in unique_signal_ids:
            continue
        if signal_id:
            unique_signal_ids.add(signal_id)
        try:
            reference = float(payload["price"])
            stop = float(payload["sl"])
            targets = tuple(
                float(value)
                for value in (payload.get("tp1"), payload.get("tp2"))
                if value is not None
            )
        except (KeyError, TypeError, ValueError):
            rejection_counts["levels_malformed"] += 1
            continue
        if not targets:
            rejection_counts["targets_missing"] += 1
            continue
        valid_until = _as_utc(payload["validUntil"]) if payload.get("validUntil") else None
        order_type = str(payload.get("orderType") or "market").lower()
        entry_zone = payload.get("entryZone")
        limit_price = None
        if order_type == "limit" and isinstance(entry_zone, (list, tuple)) and len(entry_zone) == 2:
            limit_price = (float(entry_zone[0]) + float(entry_zone[1])) / 2.0
        intents.append(
            SignalIntent(
                engine="A",
                symbol=str(pair.get("display") or pair.get("symbol") or ""),
                asset_type=str(pair.get("type") or ""),
                style=style,
                direction=str(payload["direction"]),
                decision_time=decision_time,
                valid_until=valid_until,
                reference_price=reference,
                stop_price=stop,
                targets=targets,
                exit_policy=str(payload.get("exitPolicy") or "SINGLE_TP1"),
                order_type=order_type,
                limit_price=limit_price,
                metadata=payload,
            )
        )
        qualified += 1
    return intents, {
        "engine": "A",
        "decisionCount": decisions,
        "qualifiedCount": qualified,
        "uniqueIntentCount": len(intents),
        "rejectionFunnel": dict(rejection_counts.most_common()),
        "executionTimeframe": execution_tf,
        "policy": policy,
    }


def replay_engine_b(
    pair: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    *,
    style: str,
    policy: dict[str, Any],
) -> tuple[list[SignalIntent], dict[str, Any]]:
    from engine_a_groups import resolve_score_group_by_type
    from engine_b_snapshot import (
        EngineBHistoricalFeatureCache,
        evaluate_engine_b_snapshot,
        resolve_engine_b_style_profile,
    )
    from config import scan_candle_limits
    from market_structure import NakedEngine

    score_group = resolve_score_group_by_type(pair)
    resolved_style, style_profile = resolve_engine_b_style_profile(
        style,
        score_group,
        str(pair.get("type") or ""),
        symbol=str(pair.get("display") or pair.get("symbol") or ""),
    )
    roles = _policy_roles(policy)
    # The shared production profile is the actual Engine B consumer contract;
    # the immutable policy remains stamped for provenance.
    execution_tf = str(style_profile.get("execution_tf") or roles["execution"]).upper()
    entry_tf = str(style_profile.get("entry_tf") or roles["trigger"]).upper()
    view = CausalMarketView(frames)
    forming_requested = {
        entry_tf,
        str(style_profile.get("setup_tf") or entry_tf).upper(),
    }
    required = {
        "D1",
        "H4",
        "H1",
        execution_tf,
        entry_tf,
        str(style_profile.get("atr_tf") or "H1").upper(),
    }
    decisions = 0
    passed_count = 0
    intents: list[SignalIntent] = []
    rejection_counts: Counter[str] = Counter()
    lifecycle_counts: Counter[str] = Counter()
    last_identity: tuple[Any, ...] | None = None
    engine = NakedEngine()
    feature_cache = EngineBHistoricalFeatureCache()
    context_limits = scan_candle_limits()
    for decision_time in view.decision_times(execution_tf):
        candles, _forming = view.snapshot(
            decision_time,
            forming_timeframes=forming_requested,
            limits=context_limits,
        )
        if not _warm(view, candles, required):
            continue
        execution_rows = candles.get(execution_tf) or []
        if not execution_rows:
            continue
        current_price = float(execution_rows[-1]["close"])
        result = evaluate_engine_b_snapshot(
            pair,
            candles,
            current_price=current_price,
            style=resolved_style,
            score_group=score_group,
            style_profile=style_profile,
            context_mode="historical",
            engine=engine,
            feature_cache=feature_cache,
        )
        decisions += 1
        selected = result.selected
        if selected is None:
            rejection_counts[str(result.rejection_reason or "no_candidate")] += 1
            continue
        structure = dict(selected.get("structure") or {})
        confidence = dict(selected.get("confidence") or {})
        lifecycle_counts[str(confidence.get("lifecycle_state") or "unknown")] += 1
        if not bool(selected.get("passed")):
            failed = confidence.get("failed_gates") or confidence.get("reasons") or ["confidence_gate_failed"]
            if isinstance(failed, str):
                failed = [failed]
            for reason in failed:
                rejection_counts[str(reason)] += 1
            continue
        direction = str(selected.get("direction") or "")
        try:
            stop = float(confidence.get("execution_sl", structure.get("execution_sl", structure.get("recommended_stop_loss"))))
            plan = str(confidence.get("execution_plan") or structure.get("execution_plan") or "single_target").lower()
            tp1 = confidence.get("execution_tp1", structure.get("execution_tp1", structure.get("recommended_take_profit")))
            tp2 = confidence.get("execution_tp2", structure.get("execution_tp2", tp1))
            if plan == "scale_out":
                targets = (float(tp1), float(tp2))
                exit_policy = "SCALE_OUT"
            else:
                target = confidence.get("single_target_execution_tp", tp1)
                targets = (float(target),)
                exit_policy = "SINGLE_TARGET"
        except (TypeError, ValueError):
            rejection_counts["execution_plan_malformed"] += 1
            continue
        identity = (
            direction,
            round(stop, 10),
            tuple(round(target, 10) for target in targets),
            structure.get("trigger_timeframe"),
            structure.get("bos_timestamp"),
            structure.get("choch_timestamp"),
        )
        if identity == last_identity:
            continue
        last_identity = identity
        metadata = dict(structure)
        metadata["confidence"] = confidence
        metadata["snapshotContractVersion"] = result.contract_version
        metadata["timeframePolicy"] = policy
        intents.append(
            SignalIntent(
                engine="B",
                symbol=str(pair.get("display") or pair.get("symbol") or ""),
                asset_type=str(pair.get("type") or ""),
                style=resolved_style,
                direction=direction,
                decision_time=decision_time,
                valid_until=decision_time + timedelta(seconds=TIMEFRAME_SECONDS[execution_tf]),
                reference_price=current_price,
                stop_price=stop,
                targets=targets,
                exit_policy=exit_policy,
                order_type=str(confidence.get("order_type") or "market").lower(),
                limit_price=(float(confidence["limit_price"]) if confidence.get("limit_price") is not None else None),
                metadata=metadata,
            )
        )
        passed_count += 1
    return intents, {
        "engine": "B",
        "decisionCount": decisions,
        "passedCount": passed_count,
        "uniqueIntentCount": len(intents),
        "rejectionFunnel": dict(rejection_counts.most_common()),
        "lifecycleFunnel": dict(lifecycle_counts.most_common()),
        "executionTimeframe": execution_tf,
        "policy": policy,
        "styleProfile": style_profile,
        "featureCache": feature_cache.diagnostics(),
    }
