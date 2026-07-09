from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import engine_a_v3.evaluator as evaluator_module
from engine_a_v3.backtest import run_v3_backtest
from engine_a_v3.contract import CONTRACT_VERSION, EngineASetupSignal, PriceZone, Target
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.levels import StructuralLevels
from engine_a_v3.promotion import (
    PromotionRegistry,
    demo_unvalidated_registry,
    production_registry,
    validate_promotion_artifact,
)
from engine_a_v3.quant_scorer import Component, QuantScore
from engine_a_v3.routing import KNOWN_SCORE_GROUPS, route_specialist
from engine_a_v3.profile import EngineAV3Profile, baseline_profile, scorer_sha256
from engine_a_v3.setups import detect_setup


REPRESENTATIVE_PAIRS = {
    "forex_majors": {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
    "forex_crosses": {"display": "EUR/GBP", "symbol": "EURGBP", "type": "forex"},
    "forex_exotics": {"display": "USD/ZAR", "symbol": "USDZAR", "type": "forex"},
    "forex_other": {"display": "USD/ILS", "symbol": "USDILS", "type": "forex"},
    "crypto_btc": {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
    "crypto_eth": {"display": "ETH/USDT", "symbol": "ETHUSDT", "type": "crypto"},
    "crypto_doge": {"display": "DOGE/USDT", "symbol": "DOGEUSDT", "type": "crypto"},
    "crypto_alt_majors": {"display": "SOL/USDT", "symbol": "SOLUSDT", "type": "crypto"},
    "crypto_other": {"display": "AAVE/USDT", "symbol": "AAVEUSDT", "type": "crypto"},
    "precious_trackers": {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"},
    "energy_oil": {"display": "WTI Oil", "symbol": "USOIL", "type": "commodity"},
    "nat_gas": {"display": "Nat Gas", "symbol": "NATGAS", "type": "commodity"},
    "copper": {"display": "Copper", "symbol": "COPPER", "type": "commodity"},
    "pgm_metals": {"display": "XPT/USD", "symbol": "XPTUSD", "type": "commodity"},
    "base_metals": {"display": "Aluminium", "symbol": "ALUMINIUM", "type": "commodity"},
    "softs": {"display": "Coffee", "symbol": "COFFEE", "type": "commodity"},
    "commodity_other": {"display": "Uranium", "symbol": "URANIUM", "type": "commodity"},
    "us_indices_trackers": {"display": "S&P 500", "symbol": "US500", "type": "index"},
    "eu_indices": {"display": "DAX", "symbol": "GER40", "type": "index"},
    "asian_indices": {"display": "Nikkei 225", "symbol": "JP225", "type": "index"},
    "index_other": {"display": "SA40", "symbol": "SA40", "type": "index"},
    "us_stock_single": {"display": "AAPL", "symbol": "AAPL.US", "type": "stock"},
    "bond_tlt": {"display": "TLT", "symbol": "TLT.US", "type": "etf_bond"},
    "smallcap_em_etf": {"display": "EEM", "symbol": "EEM.US", "type": "etf"},
    "stock_other": {"display": "JSE:NPN", "symbol": "NPN.JO", "type": "stock"},
    "unknown": {"display": "UNKNOWN", "symbol": "UNKNOWN", "type": "other"},
}


def _candles(
    *,
    count: int,
    start: datetime,
    step: timedelta,
    base: float,
    slope: float,
) -> list[dict]:
    rows = []
    for idx in range(count):
        close = base + slope * idx
        open_ = close - (0.3 if slope >= 0 else -0.3)
        rows.append(
            {
                "time": (start + step * idx).isoformat(),
                "open": open_,
                "high": max(open_, close) + 0.5,
                "low": min(open_, close) - 0.5,
                "close": close,
                "vol": 1_000 + idx,
            }
        )
    return rows


def _trend_pullback_candles() -> dict[str, list[dict]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    d1 = _candles(count=260, start=start, step=timedelta(days=1), base=80, slope=0.2)
    h4 = _candles(count=300, start=start, step=timedelta(hours=4), base=90, slope=0.12)
    h1 = _candles(count=320, start=start, step=timedelta(hours=1), base=100, slope=0.05)
    prior_close = h1[-2]["close"]
    h1[-2].update(
        {
            "open": prior_close + 0.8,
            "high": prior_close + 1.0,
            "low": prior_close - 1.2,
            "close": prior_close - 0.6,
        }
    )
    h1[-1].update(
        {
            "open": prior_close - 0.5,
            "high": prior_close + 1.2,
            "low": prior_close - 0.8,
            "close": prior_close + 0.9,
        }
    )
    return {"D1": d1, "H4": h4, "H1": h1}


def _breakout_retest_candles() -> dict[str, list[dict]]:
    candles = _trend_pullback_candles()
    h1 = [dict(candle) for candle in candles["H1"]]
    breakout_level = max(float(candle["high"]) for candle in h1[-22:-2])
    h1[-2].update(
        {
            "open": breakout_level - 0.2,
            "high": breakout_level + 0.8,
            "low": breakout_level - 0.3,
            "close": breakout_level + 0.6,
        }
    )
    h1[-1].update(
        {
            "open": breakout_level + 0.1,
            "high": breakout_level + 0.7,
            "low": breakout_level - 0.2,
            "close": breakout_level + 0.5,
        }
    )
    return {**candles, "H1": h1}


def _valid_artifact(route, horizon: str) -> dict:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    baseline = baseline_profile(route.score_group, horizon)
    profile = EngineAV3Profile.create(
        score_group=route.score_group, horizon=horizon,
        indicator_periods=dict(baseline.indicator_periods), weights=dict(baseline.weights),
        direction_deadband=baseline.direction_deadband, trade_threshold=baseline.trade_threshold,
        exit_policy=baseline.exit_policy, status="PROMOTED", profile_id=f"fixture-{route.score_group}-{horizon}",
        created_at=now.isoformat(), valid_until=(now + timedelta(days=3650)).isoformat(),
    )
    return {
        "schemaVersion": 3,
        "artifactId": f"{route.family}-{route.subclass}-{horizon}-v1",
        "family": route.family,
        "subclass": route.subclass,
        "horizon": horizon,
        "status": "PROMOTED",
        "createdAt": now.isoformat(),
        "validUntil": (now + timedelta(days=3650)).isoformat(),
        "validationScope": {
            "type": "family",
            "expectedSymbols": ["FIXTURE"],
            "validatedSymbols": ["FIXTURE"],
        },
        "provenance": {
            "datasetId": "fixture-ohlcv-v1",
            "provider": "mt5" if route.family != "crypto" else "bybit_rest",
            "sha256": "a" * 64,
            "implementationSha256": scorer_sha256(),
            "confirmedOnly": True,
            "untouchedHoldoutPct": 20,
            "walkForwardFolds": 4,
            "purgeBars": 25,
            "maxHoldBars": 24,
            "costs": {
                "spreadBps": 1.0,
                "commissionBps": 0.5,
                "slippageBps": 1.0,
                "swapBpsPerDay": 0.1,
                "adverseSameBarOrdering": True,
            },
        },
        "metrics": {
            "oosTrades": 80,
            "foldExpectancyR": [0.12, 0.09, -0.01, 0.11],
            "holdoutExpectancyR": 0.10,
            "holdoutTrades": 15,
            "expectancyR": 0.09,
            "profitFactor": 1.25,
            "sqn": 1.7,
            "maxDrawdownR": 8.0,
            "bootstrapLower95ExpectancyR": 0.01,
        },
        "profile": profile.to_dict(),
    }


def _registry(tmp_path, pair: dict, horizon: str) -> PromotionRegistry:
    route = route_specialist(pair)
    artifact = _valid_artifact(route, horizon)
    path = tmp_path / route.family / route.subclass / horizon / "promotion.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return PromotionRegistry(tmp_path)


def _patch_trade_quant(monkeypatch):
    def fake_score_pair(*_args, **_kwargs):
        components = {
            "trend": Component(signal=0.9, quality=0.9, available=True),
            "momentum": Component(signal=0.8, quality=0.8, available=True),
            "location": Component(signal=0.7, quality=0.7, available=True),
            "volume": Component(signal=0.2, quality=0.2, available=True),
        }
        return QuantScore(
            direction="LONG",
            confluence_score=2.4,
            max_score=3.0,
            score_norm=0.8,
            conviction=0.8,
            decision="TRADE",
            threshold=2.1,
            level_style="trend",
            factor_scores={},
            factor_diagnostics={},
            components=components,
        )

    def fake_levels(*_args, **_kwargs):
        return StructuralLevels(
            entry_zone=PriceZone(low=100.0, high=101.0),
            invalidation=99.0,
            targets=(
                Target(label="TP1", price=102.0, rr=1.0),
                Target(label="TP2", price=103.0, rr=2.0),
            ),
            price=101.0,
        )

    monkeypatch.setattr(evaluator_module, "score_pair", fake_score_pair)
    monkeypatch.setattr(evaluator_module, "detect_setup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(evaluator_module, "_build_levels", fake_levels)
    monkeypatch.setattr(evaluator_module, "session_score_passes", lambda *_args, **_kwargs: True)


def test_router_covers_all_26_score_groups_and_both_horizons():
    assert set(REPRESENTATIVE_PAIRS) == set(KNOWN_SCORE_GROUPS)
    assert len(KNOWN_SCORE_GROUPS) == 26

    for expected_group, pair in REPRESENTATIVE_PAIRS.items():
        route = route_specialist(pair)
        assert route.score_group == expected_group
        for horizon in ("intraday", "swing"):
            assert route.setup_ids(horizon)


def test_contract_evaluation_covers_all_groups_and_both_horizons(tmp_path):
    candles = _trend_pullback_candles()
    for expected_group, pair in REPRESENTATIVE_PAIRS.items():
        for horizon in ("intraday", "swing"):
            registry = _registry(tmp_path, pair, horizon)
            signal = evaluate_engine_a_v3(
                pair,
                candles,
                horizon=horizon,
                registry=registry,
            )

            assert signal.contractVersion == CONTRACT_VERSION
            assert signal.engine == "ENGINE_A_V3"
            assert signal.scoreGroup == expected_group
            assert signal.horizon == horizon
            assert signal.decision in {"TRADE", "WATCH", "NO_SIGNAL"}
            assert signal.confluenceScore is not None
            assert signal.scoringProfile is not None


def test_demo_research_missing_promotion_artifact_keeps_trade_signal(monkeypatch, tmp_path):
    from config import CONFIG

    _patch_trade_quant(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EXECUTOR_MODE", "demo")

    signal = evaluate_engine_a_v3(
        REPRESENTATIVE_PAIRS["forex_majors"],
        _trend_pullback_candles(),
        horizon="intraday",
        registry=PromotionRegistry(tmp_path),
    )

    assert signal.decision == "TRADE"
    assert signal.qualified is True
    assert signal.engineATradeEnabled is True
    assert signal.executionScope == "DEMO_ONLY"
    assert signal.validationStatus == "UNVALIDATED"
    assert signal.validationArtifact is None
    assert "promotion_artifact_missing" not in signal.rejectionReasons
    assert signal.confluenceScore is not None
    assert signal.scoreNorm is not None
    assert signal.factorDiagnostics["promotion"]["qualified"] is False
    assert signal.factorDiagnostics["promotion"]["demoResearchOverride"] is True
    assert "promotion_artifact_missing" in signal.factorDiagnostics["promotion"]["reasons"]


def test_live_missing_promotion_artifact_still_caps_trade_signal(monkeypatch, tmp_path):
    from config import CONFIG

    _patch_trade_quant(monkeypatch)
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EXECUTOR_MODE", "live")

    signal = evaluate_engine_a_v3(
        REPRESENTATIVE_PAIRS["forex_majors"],
        _trend_pullback_candles(),
        horizon="intraday",
        registry=PromotionRegistry(tmp_path),
    )

    assert signal.decision == "WATCH"
    assert signal.qualified is False
    assert signal.engineATradeEnabled is False
    assert signal.executionScope == "NONE"
    assert signal.validationStatus == "UNAVAILABLE"
    assert "promotion_artifact_missing" in signal.rejectionReasons
    assert signal.confluenceScore is not None
    assert signal.scoreNorm is not None
    assert signal.factorDiagnostics["promotion"]["demoResearchOverride"] is False


def test_demo_unvalidated_registry_activates_all_routed_specialists_without_fake_metrics(
    tmp_path,
):
    candles = _trend_pullback_candles()

    for pair in REPRESENTATIVE_PAIRS.values():
        for horizon in ("intraday", "swing"):
            signal = evaluate_engine_a_v3(
                pair,
                candles,
                horizon=horizon,
                registry=demo_unvalidated_registry(tmp_path),
            )

            assert "promotion_artifact_missing" not in signal.rejectionReasons
            assert signal.validationArtifact is not None
            assert signal.validationArtifact.status == "DEMO_UNVALIDATED"
            assert dict(signal.validationArtifact.metrics) == {}
            assert signal.executionScope == "DEMO_ONLY"
            assert signal.validationStatus == "UNVALIDATED"
            if signal.decision == "TRADE":
                assert signal.qualified is True
                assert signal.engineATradeEnabled is True


def test_production_demo_activation_requires_both_flag_and_demo_mode(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EXECUTOR_MODE", "demo")
    assert production_registry().allow_demo_unvalidated is True

    monkeypatch.setitem(CONFIG, "EXECUTOR_MODE", "live")
    assert production_registry().allow_demo_unvalidated is False

    monkeypatch.setitem(CONFIG, "EXECUTOR_MODE", "demo")
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", False)
    assert production_registry().allow_demo_unvalidated is False


def test_insufficient_context_history_still_visible_via_quant_path(tmp_path):
    pair = REPRESENTATIVE_PAIRS["forex_majors"]
    candles = _trend_pullback_candles()
    candles["H4"] = candles["H4"][-20:]
    registry = _registry(tmp_path, pair, "intraday")

    signal = evaluate_engine_a_v3(
        pair,
        candles,
        horizon="intraday",
        registry=registry,
    )

    assert signal.decision == "NO_SIGNAL"
    assert signal.qualified is False
    assert "h4_history_insufficient" in signal.rejectionReasons


def test_malformed_promotion_metrics_and_costs_fail_closed():
    pair = REPRESENTATIVE_PAIRS["forex_majors"]
    route = route_specialist(pair)
    artifact = _valid_artifact(route, "intraday")
    artifact["metrics"]["foldExpectancyR"] = ["invalid", 0.1, 0.1, 0.1]
    artifact["provenance"]["costs"]["slippageBps"] = -1

    decision = validate_promotion_artifact(
        artifact,
        route,
        "intraday",
        now=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )

    assert decision.qualified is False
    assert decision.artifact is None
    assert "promotion_positive_fold_gate_failed" in decision.reasons
    assert "promotion_cost_model_invalid" in decision.reasons


def test_promoted_specialist_is_deterministic_and_immutable(tmp_path):
    pair = REPRESENTATIVE_PAIRS["forex_majors"]
    candles = _trend_pullback_candles()
    registry = _registry(tmp_path, pair, "intraday")

    first = evaluate_engine_a_v3(pair, candles, horizon="intraday", registry=registry)
    second = evaluate_engine_a_v3(pair, candles, horizon="intraday", registry=registry)

    assert isinstance(first, EngineASetupSignal)
    assert first.contractVersion == CONTRACT_VERSION
    assert first == second
    assert first.signalId == second.signalId
    assert first.decision in {"TRADE", "WATCH"}
    assert first.setupId
    assert first.entryZone is not None
    assert first.invalidation is not None
    assert first.targets
    assert isinstance(dict(first.validationArtifact.metrics)["foldExpectancyR"], tuple)
    with pytest.raises((AttributeError, TypeError)):
        first.decision = "NO_SIGNAL"


def test_future_candle_does_not_change_prefix_decision(tmp_path):
    pair = REPRESENTATIVE_PAIRS["forex_majors"]
    candles = _trend_pullback_candles()
    registry = _registry(tmp_path, pair, "intraday")
    prefix = evaluate_engine_a_v3(pair, candles, horizon="intraday", registry=registry)

    future = {tf: list(rows) for tf, rows in candles.items()}
    future["H1"].append(
        {
            "time": "2030-01-01T00:00:00+00:00",
            "open": 1,
            "high": 10_000,
            "low": 0.1,
            "close": 9_000,
            "vol": 9_000,
        }
    )
    unchanged_prefix = evaluate_engine_a_v3(
        pair,
        {**future, "H1": future["H1"][:-1]},
        horizon="intraday",
        registry=registry,
    )

    assert unchanged_prefix == prefix


def test_backtest_uses_v3_contract_and_adverse_same_bar_ordering(tmp_path):
    pair = REPRESENTATIVE_PAIRS["forex_majors"]
    candles = _trend_pullback_candles()
    registry = _registry(tmp_path, pair, "intraday")

    result = run_v3_backtest(
        pair,
        candles,
        horizon="intraday",
        registry=registry,
        spread_bps=1.0,
        commission_bps=0.5,
        slippage_bps=1.0,
        swap_bps_per_day=0.1,
    )

    assert result["engine"] == "ENGINE_A_V3"
    assert result["contractVersion"] == CONTRACT_VERSION
    assert result["sameBarPolicy"] == "ADVERSE_SL_FIRST"
    assert result["lookaheadUsed"] is False
    assert result["equityCurve"]
    assert isinstance(result["trades"], list)


def test_specialists_expose_market_specific_predicates():
    candles = _trend_pullback_candles()
    breakout_candles = _breakout_retest_candles()

    forex = detect_setup(
        route_specialist(REPRESENTATIVE_PAIRS["forex_majors"]),
        "intraday",
        breakout_candles,
    )
    precious = detect_setup(
        route_specialist(REPRESENTATIVE_PAIRS["precious_trackers"]),
        "intraday",
        breakout_candles,
    )
    equity_intraday = detect_setup(
        route_specialist(REPRESENTATIVE_PAIRS["stock_other"]),
        "intraday",
        candles,
    )
    equity_swing = detect_setup(
        route_specialist(REPRESENTATIVE_PAIRS["stock_other"]),
        "swing",
        candles,
    )
    commodity = detect_setup(
        route_specialist(REPRESENTATIVE_PAIRS["nat_gas"]),
        "intraday",
        breakout_candles,
    )

    assert "active_session_utc" in {item.name for item in forex.predicates}
    assert "active_session_utc" in {item.name for item in precious.predicates}
    assert "opening_range_history" in {item.name for item in equity_intraday.predicates}
    assert "relative_strength_efficiency_20" in {
        item.name for item in equity_swing.predicates
    }
    breakout = next(
        item for item in commodity.predicates if item.name == "breakout_close"
    )
    assert "12-bar" in breakout.expected


def test_timezone_naive_parsing(monkeypatch):
    from config import CONFIG
    from engine_a_v3.evaluator import _parse_time
    from engine_a_v3.session_forex import parse_utc

    # Test with SERVER_TZ_OFFSET_HOURS = 2 (SAST)
    monkeypatch.setitem(CONFIG, "SERVER_TZ_OFFSET_HOURS", 2)
    dt1 = _parse_time("2026-06-16T10:00:00")
    assert dt1 is not None
    assert dt1.tzinfo == timezone.utc
    assert dt1.hour == 8
    assert dt1.minute == 0

    dt2 = parse_utc("2026-06-16T10:00:00")
    assert dt2 is not None
    assert dt2.tzinfo == timezone.utc
    assert dt2.hour == 8

    # Test with timezone-aware timestamp (no conversion should shift the time itself)
    dt3 = _parse_time("2026-06-16T10:00:00+00:00")
    assert dt3 is not None
    assert dt3.tzinfo == timezone.utc
    assert dt3.hour == 10

