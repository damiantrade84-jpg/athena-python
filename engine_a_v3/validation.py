from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from engine_a_v3.backtest import run_v3_backtest
from engine_a_v3.contract import ValidationArtifact
from engine_a_v3.promotion import (
    PromotionDecision,
    PromotionRegistry,
    validate_promotion_artifact,
)
from engine_a_v3.routing import route_specialist
from engine_a_v3.profile import EngineAV3Profile, baseline_profile, scorer_sha256


def _profile_ttl_days() -> int:
    from config import CONFIG
    value = int(CONFIG.get("ENGINE_A_V3_PROFILE_TTL_DAYS", 90))
    if not 1 <= value <= 365:
        raise ValueError("engine_a_v3_profile_ttl_invalid")
    return value


@dataclass(frozen=True)
class ValidationWindow:
    name: str
    train_end: int
    test_start: int
    test_end: int


class _CandidateRegistry:
    def resolve(self, route, horizon, *, symbol, now=None) -> PromotionDecision:
        return PromotionDecision(
            True,
            (),
            ValidationArtifact(
                artifactId="offline-validation-candidate",
                family=route.family,
                subclass=route.subclass,
                horizon=horizon,
                status="VALIDATION",
                metrics=(),
                provenanceSha256="0" * 64,
            ),
        )


def provenance_sha256(candles: dict[str, list[dict]]) -> str:
    canonical = {
        timeframe: [
            {
                "time": row.get("time") or row.get("datetime"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "vol": row.get("vol", row.get("volume")),
            }
            for row in rows
        ]
        for timeframe, rows in sorted(candles.items())
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def implementation_sha256() -> str:
    return scorer_sha256()


def build_validation_windows(
    total_bars: int,
    *,
    folds: int = 4,
    holdout_pct: int = 20,
    purge_bars: int = 5,
) -> tuple[tuple[ValidationWindow, ...], ValidationWindow]:
    if folds != 4 or holdout_pct != 20:
        raise ValueError("Engine A V3 validation requires four folds and a 20% holdout")
    if total_bars < 120:
        raise ValueError("Insufficient bars for purged expanding validation")
    development_end = int(math.floor(total_bars * 0.80))
    initial_train = max(80, development_end // (folds + 1))
    remaining = development_end - initial_train
    fold_width = remaining // folds
    if fold_width <= purge_bars:
        raise ValueError("Insufficient bars after purge for four validation folds")
    windows = []
    for index in range(folds):
        test_end = development_end if index == folds - 1 else initial_train + fold_width * (index + 1)
        train_end = initial_train + fold_width * index
        test_start = train_end + purge_bars
        windows.append(
            ValidationWindow(
                name=f"fold_{index + 1}",
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    holdout = ValidationWindow(
        name="holdout",
        train_end=max(0, development_end - purge_bars),
        test_start=development_end,
        test_end=total_bars,
    )
    return tuple(windows), holdout


def block_bootstrap_lower95(
    results_r: list[float],
    *,
    block_size: int = 5,
    samples: int = 2_000,
    seed: int = 240613,
) -> float | None:
    if not results_r:
        return None
    rng = random.Random(seed)
    values = [float(value) for value in results_r]
    block = max(1, min(block_size, len(values)))
    starts = list(range(0, len(values) - block + 1))
    means = []
    for _ in range(samples):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.choice(starts)
            sample.extend(values[start : start + block])
        means.append(fmean(sample[: len(values)]))
    means.sort()
    return means[max(0, int(samples * 0.025) - 1)]


def _summary_metrics(
    fold_results: list[list[float]],
    holdout_results: list[float],
) -> dict[str, Any]:
    oos = [value for fold in fold_results for value in fold]
    all_results = oos + holdout_results
    wins = [value for value in all_results if value > 0]
    losses = [value for value in all_results if value <= 0]
    expectancy = fmean(all_results) if all_results else 0.0
    deviation = stdev(all_results) if len(all_results) > 1 else 0.0
    sqn = expectancy / deviation * math.sqrt(len(all_results)) if deviation > 0 else 0.0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in all_results:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gross_loss = abs(sum(losses))
    return {
        "oosTrades": len(oos),
        "foldExpectancyR": [fmean(fold) if fold else 0.0 for fold in fold_results],
        "holdoutTrades": len(holdout_results),
        "holdoutExpectancyR": fmean(holdout_results) if holdout_results else 0.0,
        "expectancyR": expectancy,
        "profitFactor": sum(wins) / gross_loss if gross_loss > 0 else None,
        "sqn": sqn,
        "maxDrawdownR": max_drawdown,
        "bootstrapLower95ExpectancyR": block_bootstrap_lower95(all_results),
    }


def _promotion_passes(metrics: dict[str, Any], *, pair_adapter: bool) -> bool:
    trade_floor = 40 if pair_adapter else 60
    folds = metrics.get("foldExpectancyR") or []
    return bool(
        metrics.get("oosTrades", 0) >= trade_floor
        and len(folds) == 4
        and sum(1 for value in folds if float(value) > 0) >= 3
        and float(metrics.get("holdoutExpectancyR") or 0) > 0
        and int(metrics.get("holdoutTrades") or 0) >= 15
        and float(metrics.get("expectancyR") or 0) >= 0.08
        and float(metrics.get("profitFactor") or 0) >= 1.20
        and float(metrics.get("sqn") or 0) >= 1.5
        and float(metrics.get("maxDrawdownR") or float("inf")) <= 12
        and float(metrics.get("bootstrapLower95ExpectancyR") or 0) > 0
    )


def _slice_candles(
    candles: dict[str, list[dict]],
    *,
    primary_tf: str,
    end_index: int,
) -> dict[str, list[dict]]:
    primary = candles[primary_tf]
    cutoff = str(primary[end_index - 1].get("time") or primary[end_index - 1].get("datetime"))
    return {
        timeframe: [
            row
            for row in rows
            if str(row.get("time") or row.get("datetime")) <= cutoff
        ]
        for timeframe, rows in candles.items()
    }


def _validation_results(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    horizon: str,
    spread_bps: float,
    commission_bps: float,
    slippage_bps: float,
    swap_bps_per_day: float,
    purge_bars: int = 5,
    max_hold_bars: int = 24,
) -> tuple[list[list[float]], list[float]]:
    primary_tf = "H1" if horizon == "intraday" else "H4"
    primary = list(candles.get(primary_tf) or [])
    windows, holdout = build_validation_windows(
        len(primary),
        purge_bars=purge_bars,
    )
    candidate_registry = _CandidateRegistry()

    def run_window(window: ValidationWindow) -> list[float]:
        sliced = _slice_candles(
            candles,
            primary_tf=primary_tf,
            end_index=window.test_end,
        )
        result = run_v3_backtest(
            pair,
            sliced,
            horizon=horizon,
            registry=candidate_registry,
            spread_bps=spread_bps,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            swap_bps_per_day=swap_bps_per_day,
            max_hold_bars=max_hold_bars,
            start_index=window.test_start,
        )
        return [float(trade["resultR"]) for trade in result.get("trades", [])]

    fold_results = [run_window(window) for window in windows]
    holdout_results = run_window(holdout) if holdout.test_start < holdout.test_end else []
    return fold_results, holdout_results


def validate_specialist(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    horizon: str,
    dataset_id: str,
    spread_bps: float,
    commission_bps: float,
    slippage_bps: float,
    swap_bps_per_day: float,
    purge_bars: int = 5,
    max_hold_bars: int = 24,
    pair_adapter: bool = False,
    provider: str = "",
) -> dict[str, Any]:
    purge_bars = max(int(purge_bars), int(max_hold_bars) + 1)
    fold_results, holdout_results = _validation_results(
        pair,
        candles,
        horizon=horizon,
        spread_bps=spread_bps,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        swap_bps_per_day=swap_bps_per_day,
        purge_bars=purge_bars,
        max_hold_bars=max_hold_bars,
    )
    metrics = _summary_metrics(fold_results, holdout_results)
    route = route_specialist(pair)
    promoted = _promotion_passes(metrics, pair_adapter=pair_adapter)
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=_profile_ttl_days())
    baseline = baseline_profile(route.score_group, horizon)
    profile = EngineAV3Profile.create(
        score_group=route.score_group, horizon=horizon,
        indicator_periods=dict(baseline.indicator_periods), weights=dict(baseline.weights),
        direction_deadband=baseline.direction_deadband, trade_threshold=baseline.trade_threshold,
        exit_policy=baseline.exit_policy, status="PROMOTED", profile_id=f"{route.score_group}-{horizon}-{dataset_id}",
        created_at=now.isoformat(), valid_until=valid_until.isoformat(),
    )
    symbol = str(pair.get("symbol") or pair.get("display") or "")
    artifact_id = hashlib.sha256(
        f"{dataset_id}|{pair.get('symbol')}|{route.family}|{route.subclass}|{horizon}".encode()
    ).hexdigest()[:24]
    return {
        "schemaVersion": 3,
        "artifactId": artifact_id,
        "family": route.family,
        "subclass": route.subclass,
        "horizon": horizon,
        "status": "PROMOTED" if promoted else "REJECTED",
        "createdAt": now.isoformat(),
        "validUntil": valid_until.isoformat(),
        "validationScope": {
            "type": "pair" if pair_adapter else "family",
            "expectedSymbols": [symbol],
            "validatedSymbols": [symbol],
        },
        "provenance": {
            "datasetId": dataset_id,
            "provider": provider,
            "sha256": provenance_sha256(candles),
            "implementationSha256": implementation_sha256(),
            "confirmedOnly": True,
            "untouchedHoldoutPct": 20,
            "walkForwardFolds": 4,
            "purgeBars": purge_bars,
            "maxHoldBars": max_hold_bars,
            "costs": {
                "spreadBps": float(spread_bps),
                "commissionBps": float(commission_bps),
                "slippageBps": float(slippage_bps),
                "swapBpsPerDay": float(swap_bps_per_day),
                "adverseSameBarOrdering": True,
            },
        },
        "metrics": metrics,
        "pairAdapter": bool(pair_adapter),
        "profile": profile.to_dict(),
    }


def validate_specialist_cohort(
    datasets: list[dict[str, Any]],
    *,
    horizon: str,
    dataset_id: str,
    expected_symbols: list[str],
    spread_bps: float,
    commission_bps: float,
    slippage_bps: float,
    swap_bps_per_day: float,
    purge_bars: int = 5,
    max_hold_bars: int = 24,
    provider: str = "",
) -> dict[str, Any]:
    if not datasets:
        raise ValueError("validation_cohort_empty")
    purge_bars = max(int(purge_bars), int(max_hold_bars) + 1)
    normalized_expected = {
        str(symbol).upper().replace("/", "").replace("-", "")
        for symbol in expected_symbols
        if str(symbol).strip()
    }
    validated_symbols = {
        str(item["pair"].get("symbol") or "").upper().replace("/", "").replace("-", "")
        for item in datasets
    }
    if not normalized_expected or normalized_expected != validated_symbols:
        raise ValueError("validation_cohort_incomplete")

    first_route = route_specialist(datasets[0]["pair"])
    primary_tf = "H1" if horizon == "intraday" else "H4"
    pooled_folds: list[list[float]] = [[], [], [], []]
    pooled_holdout: list[float] = []
    pair_metrics: dict[str, Any] = {}
    dataset_hashes: dict[str, str] = {}
    dataset_spans: dict[str, Any] = {}
    for item in datasets:
        pair = item["pair"]
        candles = item["candles"]
        route = route_specialist(pair)
        if (route.family, route.subclass) != (first_route.family, first_route.subclass):
            raise ValueError("validation_cohort_route_mismatch")
        fold_results, holdout_results = _validation_results(
            pair,
            candles,
            horizon=horizon,
            spread_bps=spread_bps,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            swap_bps_per_day=swap_bps_per_day,
            purge_bars=purge_bars,
            max_hold_bars=max_hold_bars,
        )
        symbol = str(pair.get("symbol") or "")
        for index, results in enumerate(fold_results):
            pooled_folds[index].extend(results)
        pooled_holdout.extend(holdout_results)
        pair_metrics[symbol] = _summary_metrics(fold_results, holdout_results)
        dataset_hashes[symbol] = provenance_sha256(candles)
        primary_rows = candles.get(primary_tf) or []
        if primary_rows:
            dataset_spans[symbol] = {
                "primaryTf": primary_tf,
                "bars": len(primary_rows),
                "start": str(primary_rows[0].get("time") or primary_rows[0].get("datetime")),
                "end": str(primary_rows[-1].get("time") or primary_rows[-1].get("datetime")),
            }

    metrics = _summary_metrics(pooled_folds, pooled_holdout)
    promoted = _promotion_passes(metrics, pair_adapter=False)
    aggregate_sha = hashlib.sha256(
        json.dumps(dataset_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=_profile_ttl_days())
    baseline = baseline_profile(first_route.score_group, horizon)
    profile = EngineAV3Profile.create(
        score_group=first_route.score_group, horizon=horizon,
        indicator_periods=dict(baseline.indicator_periods), weights=dict(baseline.weights),
        direction_deadband=baseline.direction_deadband, trade_threshold=baseline.trade_threshold,
        exit_policy=baseline.exit_policy, status="PROMOTED", profile_id=f"{first_route.score_group}-{horizon}-{dataset_id}",
        created_at=now.isoformat(), valid_until=valid_until.isoformat(),
    )
    artifact_id = hashlib.sha256(
        (
            f"{dataset_id}|{first_route.family}|{first_route.subclass}|{horizon}|"
            f"{aggregate_sha}|{implementation_sha256()}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schemaVersion": 3,
        "artifactId": artifact_id,
        "family": first_route.family,
        "subclass": first_route.subclass,
        "horizon": horizon,
        "status": "PROMOTED" if promoted else "REJECTED",
        "createdAt": now.isoformat(),
        "validUntil": valid_until.isoformat(),
        "validationScope": {
            "type": "family",
            "expectedSymbols": sorted(normalized_expected),
            "validatedSymbols": sorted(validated_symbols),
        },
        "provenance": {
            "datasetId": dataset_id,
            "provider": provider,
            "sha256": aggregate_sha,
            "implementationSha256": implementation_sha256(),
            "datasetHashes": dataset_hashes,
            # Regime-coverage disclosure: the calendar window each symbol's
            # metrics were computed over. A pass covering one regime is weaker
            # evidence than one spanning several; reviewers can now see which.
            "datasetSpans": dataset_spans,
            "confirmedOnly": True,
            "untouchedHoldoutPct": 20,
            "walkForwardFolds": 4,
            "purgeBars": purge_bars,
            "maxHoldBars": max_hold_bars,
            "costs": {
                "spreadBps": float(spread_bps),
                "commissionBps": float(commission_bps),
                "slippageBps": float(slippage_bps),
                "swapBpsPerDay": float(swap_bps_per_day),
                "adverseSameBarOrdering": True,
            },
        },
        "metrics": metrics,
        "pairMetrics": pair_metrics,
        "pairAdapter": False,
        "profile": profile.to_dict(),
    }


def write_validation_artifact(
    artifact: dict[str, Any],
    *,
    registry: PromotionRegistry,
    pair: dict,
) -> Path:
    route = route_specialist(pair)
    horizon = str(artifact.get("horizon") or "")
    symbol = str(pair.get("symbol") or "")
    pair_adapter = bool(artifact.get("pairAdapter"))
    decision = validate_promotion_artifact(
        artifact,
        route,
        horizon,
        pair_adapter=pair_adapter,
    )
    if not decision.qualified:
        raise ValueError(",".join(decision.reasons))
    path = registry._paths(route, horizon, symbol)[0 if pair_adapter else -1][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(artifact, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
