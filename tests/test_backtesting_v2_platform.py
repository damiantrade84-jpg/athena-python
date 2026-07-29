from __future__ import annotations

import ast
import multiprocessing
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from flask import Flask

from athena_backtesting_v2.api import register_backtesting_v2_routes
from athena_backtesting_v2.models import CostModel, RequestValidationError, RunRequest, ValidationPlan
from athena_backtesting_v2.jobs import _AthenaSpawnProcessPoolExecutor, _resolve_run_outcome, _spawn_main_bootstrap, _SupervisorProcessLock
from athena_backtesting_v2.quality import normalize_and_validate
from athena_backtesting_v2.datasets import _forming_reconstruction_source
from athena_backtesting_v2.retirement import install_legacy_retirement_guard
from athena_backtesting_v2.simulator import EventDrivenSimulator, SignalIntent
from athena_backtesting_v2.snapshot import CausalMarketView
from athena_backtesting_v2.validation import anchored_boundaries
from engine_b_snapshot import EngineBHistoricalFeatureCache


def _run_payload(**overrides):
    payload = {
        "engines": ["A", "B"],
        "styles": {"A": "intraday", "B": "scalp"},
        "symbols": ["EURUSD"],
        "startDate": "2025-01-01",
        "endDate": "2025-12-31",
        "mode": "CERTIFIED",
        "costModel": {"spreadBps": 1.0, "source": "test", "version": "test-v1"},
    }
    payload.update(overrides)
    return payload


def test_request_requires_explicit_universe_and_locked_certified_validation():
    with pytest.raises(RequestValidationError) as missing:
        RunRequest.from_payload(_run_payload(symbols=[]))
    assert missing.value.code == "EXPLICIT_UNIVERSE_REQUIRED"

    with pytest.raises(RequestValidationError) as unlocked:
        RunRequest.from_payload(_run_payload(validation={"enabled": False}))
    assert unlocked.value.code == "CERTIFIED_VALIDATION_REQUIRED"

    request = RunRequest.from_payload(_run_payload())
    assert request.engines == ("A", "B")
    assert request.styles == {"A": "intraday", "B": "scalp"}


def test_point_in_time_view_does_not_admit_future_bar_changes():
    base = pd.DataFrame(
        [
            {"time": "2025-01-01T00:00:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"time": "2025-01-01T01:00:00Z", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 2},
            {"time": "2025-01-01T02:00:00Z", "open": 11, "high": 99, "low": 1, "close": 50, "volume": 3},
        ]
    )
    changed = base.copy()
    changed.loc[2, ["open", "high", "low", "close", "volume"]] = [500, 700, 400, 650, 999]
    cutoff = datetime(2025, 1, 1, 2, tzinfo=timezone.utc)
    before, _ = CausalMarketView({"H1": base}).snapshot(cutoff)
    after, _ = CausalMarketView({"H1": changed}).snapshot(cutoff)
    assert before == after
    assert len(before["H1"]) == 2


def test_incremental_confirmed_snapshots_match_indexed_prefixes():
    frames = {
        "H1": pd.DataFrame([
            {"time": f"2025-01-01T0{hour}:00:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1}
            for hour in range(4)
        ]),
        "M15": pd.DataFrame([
            {"time": f"2025-01-01T00:{minute:02d}:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1}
            for minute in (0, 15, 30, 45)
        ]),
    }
    view = CausalMarketView(frames)
    for decision_time, incremental in view.iter_confirmed_snapshots("M15"):
        indexed, forming = view.snapshot(decision_time)
        assert forming == set()
        assert incremental == indexed


def test_bounded_snapshot_keeps_live_sized_context_and_forming_bar():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def frame(step: timedelta, count: int) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "time": (start + step * index).isoformat(),
                "open": 10 + index,
                "high": 11 + index,
                "low": 9 + index,
                "close": 10.5 + index,
                "volume": 1,
            }
            for index in range(count)
        ])

    view = CausalMarketView({"M15": frame(timedelta(minutes=15), 8), "M5": frame(timedelta(minutes=5), 15)})
    decision_time = start + timedelta(hours=1, minutes=10)
    snapshot, forming = view.snapshot(
        decision_time,
        forming_timeframes={"M15"},
        limits={"M15": 3, "M5": 2},
    )

    assert forming == {"M15"}
    assert [row["time"] for row in snapshot["M15"][:2]] == [
        "2025-01-01T00:30:00+00:00",
        "2025-01-01T00:45:00+00:00",
    ]
    assert snapshot["M15"][-1]["time"] == "2025-01-01T01:00:00+00:00"
    assert snapshot["M15"][-1]["is_forming"] is True
    assert [row["time"] for row in snapshot["M5"]] == [
        "2025-01-01T01:00:00+00:00",
        "2025-01-01T01:05:00+00:00",
    ]


def test_causal_indicator_cache_keeps_only_latest_snapshot_per_timeframe():
    from athena_backtesting_v2.snapshot import CausalIndicatorIndex

    cache = CausalIndicatorIndex({"H1": []})
    cache[("H1", 100)] = {"close": 1}
    cache[("H1", 101)] = {"close": 2}
    assert ("H1", 100) not in cache
    assert cache[("H1", 101)] == {"close": 2}


def test_causal_indicator_index_matches_production_prefix_snapshots():
    import math

    from athena_backtesting_v2.snapshot import CausalIndicatorIndex
    from engine_a_v3.indicator_adapter import indicator_snapshot

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(420):
        center = 100.0 + math.sin(index / 7.0)
        rows.append({
            "time": (start + timedelta(hours=index)).isoformat(),
            "open": center,
            "high": center + 1.2,
            "low": center - 1.2,
            "close": center + 0.2 * math.cos(index / 3.0),
            "volume": 1_000.0 + index,
            "vol": 1_000.0 + index,
        })
    periods = {
        "ema_trend": 21,
        "ema_momentum": 50,
        "ema_long": 200,
        "rsi": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr": 14,
        "adx": 14,
    }
    cache = CausalIndicatorIndex({"H1": rows})
    for length in (80, 120, 220, 319, 420):
        assert cache.snapshot_at("H1", length, periods, "forex") == indicator_snapshot(
            rows[:length], periods, "forex"
        )


def test_engine_a_series_cache_preserves_setup_and_trend_age_results():
    from engine_a_v3.quant_scorer import _trend_alignment_age
    from engine_a_v3.setups import (
        SetupPeriods,
        SetupSeriesCache,
        _pullback_candidate,
        _trend_direction,
    )

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(420):
        center = 100.0 + index * 0.04 + ((index % 9) - 4) * 0.03
        rows.append({
            "time": (start + timedelta(hours=4 * index)).isoformat(),
            "open": center - 0.05,
            "high": center + 0.4,
            "low": center - 0.4,
            "close": center + 0.05,
            "vol": 1_000.0 + index,
        })
    cache = SetupSeriesCache({"H4": rows})
    periods = SetupPeriods()

    for length in (80, 160, 320, 420):
        prefix = rows[:length]
        assert _trend_direction(prefix, periods=periods) == _trend_direction(
            prefix,
            periods=periods,
            series_cache=cache,
            context_tf="H4",
        )
        assert _pullback_candidate(
            "test", prefix, prefix, periods=periods
        ) == _pullback_candidate(
            "test",
            prefix,
            prefix,
            periods=periods,
            series_cache=cache,
            primary_tf="H4",
            context_tf="H4",
        )
        assert _trend_alignment_age(prefix, ema_period=periods.ema_trend) == (
            _trend_alignment_age(
                prefix,
                ema_period=periods.ema_trend,
                series_cache=cache,
                timeframe="H4",
            )
        )


def test_engine_a_cached_h4_volume_and_vwap_match_production_math():
    from engine_a_v3.setups import SetupSeriesCache
    from factor_scoring import _h4_volume_vs_ma, _h4_vwap_distance_metrics
    from indicators import calc_sma, calc_vwap

    rows = [
        {
            "time": (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * index)).isoformat(),
            "open": 100.0 + index * 0.1,
            "high": 101.0 + index * 0.1,
            "low": 99.0 + index * 0.1,
            "close": 100.5 + index * 0.1,
            "vol": 1_000.0 + index * 3,
        }
        for index in range(140)
    ]
    cache = SetupSeriesCache({"H4": rows})
    for length in (96, 111, 140):
        prefix = rows[:length]
        volumes = [float(row["vol"]) for row in prefix]
        assert _h4_volume_vs_ma(prefix, 20) == (
            volumes[-1],
            calc_sma(volumes, 20)[-1],
        )
        expected_vwap = calc_vwap(prefix[-96:], anchor_index=0)["vwap"][-1]
        snap = {"close": prefix[-1]["close"], "atr": 2.0}
        expected = _h4_vwap_distance_metrics(snap, prefix, lookback=96)
        cached = _h4_vwap_distance_metrics(
            snap,
            prefix,
            lookback=96,
            series_cache=cache,
            timeframe="H4",
        )
        assert cached == expected
        assert cached["vwap_value"] == round(float(expected_vwap), 6)


def test_engine_a_compact_replay_score_preserves_strategy_fields():
    from athena_backtesting_v2.snapshot import CausalIndicatorIndex
    from engine_a_v3.profile import baseline_profile
    from engine_a_v3.quant_scorer import score_pair
    from engine_a_v3.routing import route_specialist
    from engine_a_v3.setups import SetupSeriesCache

    steps = {
        "D1": timedelta(days=1),
        "H4": timedelta(hours=4),
        "H1": timedelta(hours=1),
        "M30": timedelta(minutes=30),
        "M15": timedelta(minutes=15),
    }
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = {}
    for timeframe, step in steps.items():
        candles[timeframe] = [
            {
                "time": (start + step * index).isoformat(),
                "open": 100.0 + index * 0.02,
                "high": 100.8 + index * 0.02,
                "low": 99.2 + index * 0.02,
                "close": 100.1 + index * 0.02 + ((index % 7) - 3) * 0.01,
                "vol": 1_000.0 + index,
                "volume": 1_000.0 + index,
            }
            for index in range(320)
        ]
    pair = {
        "display": "LINK/USDT",
        "symbol": "LINKUSDT",
        "type": "crypto",
        "score_group": "crypto_alt_majors",
    }
    route = route_specialist(pair)
    profile = baseline_profile(route.score_group, "intraday")
    policy = {
        "regime": "D1",
        "bias": "H4",
        "structure": "H1",
        "setup": "M30",
        "trigger": "M15",
        "execution": "M15",
    }
    series_cache = SetupSeriesCache(candles)
    snapshots = CausalIndicatorIndex(candles)
    common = {
        "context": {"historicalReplay": True},
        "profile": profile,
        "snapshot_cache": snapshots,
        "policy_timeframes": policy,
        "series_cache": series_cache,
    }
    full = score_pair(route, "intraday", candles, **common)
    compact = score_pair(
        route,
        "intraday",
        candles,
        **common,
        feature_cache={},
        compact_result=True,
    )
    assert (
        compact.direction,
        compact.confluence_score,
        compact.score_norm,
        compact.conviction,
        compact.decision,
        compact.threshold,
        compact.level_style,
        compact.components,
    ) == (
        full.direction,
        full.confluence_score,
        full.score_norm,
        full.conviction,
        full.decision,
        full.threshold,
        full.level_style,
        full.components,
    )
    for key in (
        "atrPct",
        "minDirectionalFailed",
        "legacyFilters",
        "equityVolumeBlocked",
        "cryptoDerivBlocked",
    ):
        assert compact.factor_diagnostics[key] == full.factor_diagnostics[key]


@pytest.mark.parametrize(
    ("m5_policy", "execution_prerequisite"),
    (("conditional", "M15"), ("disabled", "")),
)
def test_engine_a_confirmed_policy_does_not_request_active_entry_override(
    monkeypatch,
    m5_policy,
    execution_prerequisite,
):
    import engine_a_v3.evaluator as evaluator_module
    import engine_a_v3.promotion as promotion_module
    from athena_backtesting_v2.replay import replay_engine_a

    calls = []

    def fake_evaluate(_pair, _candles, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(rejectionReasons=(), qualified=False, direction=None)

    class FakeRegistry:
        def resolve(self, *_args, **_kwargs):
            return SimpleNamespace()

    monkeypatch.setattr(evaluator_module, "evaluate_engine_a_v3", fake_evaluate)
    monkeypatch.setattr(promotion_module, "production_registry", lambda: FakeRegistry())
    times = pd.date_range("2025-01-01", periods=80, freq="1D", tz="UTC")
    frame = pd.DataFrame({
        "time": times,
        "open": [100.0] * len(times),
        "high": [101.0] * len(times),
        "low": [99.0] * len(times),
        "close": [100.0] * len(times),
        "volume": [1_000.0] * len(times),
    })
    frames = {
        timeframe: frame
        for timeframe in ("D1", "H4", "H1", "M30", "M15", "M5")
    }
    policy = {
        "style": "intraday",
        "regime_tf": "D1",
        "bias_tf": "H4",
        "structure_tf": "H1",
        "setup_tf": "M30",
        "trigger_tf": "M15",
        "execution_tf": "M5",
        "forming_tf": "M5",
        "m5_policy": m5_policy,
        "execution_prerequisite_tf": execution_prerequisite,
    }
    replay_engine_a(
        {
            "display": "LINK/USDT",
            "symbol": "LINKUSDT",
            "type": "crypto",
            "score_group": "crypto_alt_majors",
        },
        frames,
        style="auto",
        policy=policy,
    )
    assert calls
    assert all(call["entry_tf_override"] is None for call in calls)
    assert all(call["entry_candle_is_forming"] is False for call in calls)


def test_simulator_fills_next_open_and_resolves_same_bar_stop_first():
    bars = pd.DataFrame(
        [
            {"time": "2025-01-01T00:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"time": "2025-01-01T01:00:00Z", "open": 100, "high": 111, "low": 89, "close": 105},
        ]
    )
    intent = SignalIntent(
        engine="A",
        symbol="TEST",
        asset_type="crypto",
        style="intraday",
        direction="LONG",
        decision_time=datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
        valid_until=None,
        reference_price=100,
        stop_price=90,
        targets=(110,),
        exit_policy="SINGLE_TP1",
    )
    costs = CostModel(spread_bps=0, commission_bps_per_side=0, slippage_bps=0)
    trades, diagnostics = EventDrivenSimulator(costs, max_hold_bars=10).simulate("run", [intent], bars)
    assert len(trades) == 1
    assert trades[0].entry_time.startswith("2025-01-01T01:00:00")
    assert trades[0].exit_reason == "STOP"
    assert trades[0].gross_r == -1.0
    assert trades[0].ambiguous_same_bar is True
    assert diagnostics["sameBarAmbiguityRate"] == 1.0


def test_engine_b_feature_cache_refreshes_trigger_without_rebuilding_structure():
    class FakeEngine:
        @staticmethod
        def _compute_atr_from_candles(rows, fallback):
            return float(len(rows)) if rows else float(fallback)

    def rows(tf: str, count: int):
        return [{"time": f"{tf}-{index}", "high": 2, "low": 1, "close": 1.5} for index in range(count)]

    cache = EngineBHistoricalFeatureCache()
    role_candles = {"D1": rows("D1", 50), "H4": rows("H4", 50), "H1": rows("H1", 50), "M15": rows("M15", 50)}
    builds = 0

    def build():
        nonlocal builds
        builds += 1
        return {"_tfs": {"trigger": "M15"}, "_trigger_candles": role_candles["M15"], "trigger_atr": 1.0}

    first = cache.resolve(
        naked=FakeEngine(), role_candles=role_candles, atr=1, regime="RANGING", style="intraday",
        asset_type="forex", fallback_rr=1.8, trigger_tf="M15", build=build,
    )
    role_candles["M15"] = rows("M15", 51)
    second = cache.resolve(
        naked=FakeEngine(), role_candles=role_candles, atr=1, regime="RANGING", style="intraday",
        asset_type="forex", fallback_rr=1.8, trigger_tf="M15", build=build,
    )
    assert builds == 1
    assert len(first["_trigger_candles"]) == 50
    assert len(second["_trigger_candles"]) == 51
    assert cache.diagnostics() == {"structureBuilds": 1, "triggerRefreshes": 1, "cacheHits": 1}


def test_engine_b_feature_cache_reuses_higher_timeframe_snapshot_inputs(monkeypatch):
    import engine_b_snapshot

    calls = Counter()

    monkeypatch.setattr(
        engine_b_snapshot,
        "calc_indicators_with_normalized",
        lambda _rows, _asset: calls.update(["d1"]) or {"snap": {"source": "d1"}},
    )
    monkeypatch.setattr(
        engine_b_snapshot,
        "resolve_engine_b_h4_snap",
        lambda _rows, _asset: calls.update(["h4"]) or {"source": "h4"},
    )
    monkeypatch.setattr(
        engine_b_snapshot,
        "resolve_engine_b_regime_label",
        lambda _rows, _asset: calls.update(["regime"]) or "TRENDING",
    )
    monkeypatch.setattr(
        engine_b_snapshot,
        "_atr_from_candles",
        lambda _rows: calls.update(["atr"]) or 1.25,
    )

    def rows(timeframe: str, count: int) -> list[dict]:
        return [
            {
                "time": f"{timeframe}-{index}",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10.0,
            }
            for index in range(count)
        ]

    role_candles = {"D1": rows("D1", 50), "H4": rows("H4", 50), "H1": rows("H1", 50)}
    cache = EngineBHistoricalFeatureCache()
    first = cache.resolve_snapshot_inputs(role_candles=role_candles, asset_type="forex", atr_tf="H1")
    second = cache.resolve_snapshot_inputs(
        role_candles={timeframe: list(values) for timeframe, values in role_candles.items()},
        asset_type="forex",
        atr_tf="H1",
    )

    assert first == second
    assert first == {
        "atr": 1.25,
        "regime": "TRENDING",
        "d1_snapshot": {"source": "d1"},
        "h4_snapshot": {"source": "h4"},
    }
    assert calls == Counter({"d1": 1, "h4": 1, "regime": 1, "atr": 1})


def test_walk_forward_boundaries_lock_final_twenty_percent():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 4, 11, tzinfo=timezone.utc)
    result = anchored_boundaries(start, end, ValidationPlan(), execution_seconds=3_600)
    assert datetime.fromisoformat(result["holdoutStart"]) == datetime(2025, 3, 22, tzinfo=timezone.utc)
    assert len(result["folds"]) == 5
    assert result["purgeSeconds"] == 240 * 3_600
    assert result["embargoSeconds"] == 3_600


def test_engine_a_does_not_require_legacy_forming_candle_reconstruction():
    policy = {"trigger_tf": "M5", "forming_tf": "M5"}
    config = {"ENGINE_B_USE_FORMING_FOR_TRIGGER": True}
    assert _forming_reconstruction_source("A", policy, config) is None
    assert _forming_reconstruction_source("B", policy, config) == "M1"


def test_quality_counts_weekday_gap_but_not_forex_weekend():
    candles = [
        {"time": "2025-01-03T21:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2025-01-05T22:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2025-01-05T23:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2025-01-06T00:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2025-01-06T02:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
    ]
    _frame, report = normalize_and_validate(
        candles,
        timeframe="H1",
        asset_type="forex",
        requested_start=datetime(2025, 1, 3, 21, tzinfo=timezone.utc),
        requested_end=datetime(2025, 1, 6, 2, tzinfo=timezone.utc),
    )
    assert report.gap_count == 1


def test_quality_admits_forex_full_market_holiday_closures():
    daily = [
        {"time": "2024-12-24T00:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2024-12-26T00:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
    ]
    _frame, daily_report = normalize_and_validate(
        daily,
        timeframe="D1",
        asset_type="forex",
        requested_start=datetime(2024, 12, 24, tzinfo=timezone.utc),
        requested_end=datetime(2024, 12, 26, tzinfo=timezone.utc),
    )
    assert daily_report.gap_count == 0

    hourly = [
        {"time": "2024-12-24T21:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2024-12-25T22:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
    ]
    _frame, hourly_report = normalize_and_validate(
        hourly,
        timeframe="H1",
        asset_type="forex",
        requested_start=datetime(2024, 12, 24, 21, tzinfo=timezone.utc),
        requested_end=datetime(2024, 12, 25, 22, tzinfo=timezone.utc),
    )
    assert hourly_report.gap_count == 0


def test_quality_uses_frozen_mt5_utc_session_calendar():
    candles = [
        {"time": "2026-05-22T23:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
        {"time": "2026-05-25T00:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5},
    ]
    _frame, report = normalize_and_validate(
        candles,
        timeframe="H1",
        asset_type="forex",
        provider="mt5",
        requested_start=datetime(2026, 5, 22, 23, tzinfo=timezone.utc),
        requested_end=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )
    assert report.gap_count == 0


def test_quality_records_mt5_no_quote_interval_without_failing_certification():
    start = datetime(2025, 8, 25, tzinfo=timezone.utc)
    candles = [
        {"time": (start + timedelta(hours=index)).isoformat(), "open": 1, "high": 2, "low": 1, "close": 1.5}
        for index in range(61)
        if index != 24
    ]
    _frame, report = normalize_and_validate(
        candles,
        timeframe="H1",
        asset_type="forex",
        provider="mt5",
        requested_start=start,
        requested_end=start + timedelta(hours=60),
    )
    assert report.gap_count == 0
    assert report.provider_no_quote_count == 1
    assert report.observations == ("provider_no_quote_bars:1",)
    assert "missing_bars:1" not in report.limitations
    assert report.state == "PASS"


def test_quality_records_mt5_index_closed_session_without_failing_certification():
    start = datetime(2024, 11, 18, tzinfo=timezone.utc)
    end = start + timedelta(days=74)
    candles = []
    for offset in range(75):
        timestamp = start + timedelta(days=offset)
        if timestamp.weekday() >= 5 or timestamp.date().isoformat() == "2024-12-25":
            continue
        candles.append({"time": timestamp.isoformat(), "open": 1, "high": 2, "low": 1, "close": 1.5})

    _frame, report = normalize_and_validate(
        candles,
        timeframe="D1",
        asset_type="index",
        provider="mt5",
        requested_start=start,
        requested_end=end,
    )
    assert report.gap_count == 0
    assert report.provider_no_quote_count == 1
    assert report.observations == ("provider_no_quote_bars:1",)
    assert "missing_bars:1" not in report.limitations
    assert report.state == "PASS"


def test_quality_keeps_non_mt5_forex_gap_fail_closed():
    start = datetime(2025, 8, 25, tzinfo=timezone.utc)
    candles = [
        {"time": (start + timedelta(hours=index)).isoformat(), "open": 1, "high": 2, "low": 1, "close": 1.5}
        for index in range(61)
        if index != 24
    ]
    _frame, report = normalize_and_validate(
        candles,
        timeframe="H1",
        asset_type="forex",
        provider="eodhd",
        requested_start=start,
        requested_end=start + timedelta(hours=60),
    )
    assert report.gap_count == 1
    assert report.provider_no_quote_count == 0
    assert "missing_bars:1" in report.limitations
    assert report.state == "FAIL"


def test_quality_does_not_require_future_hours_on_current_end_date():
    as_of = datetime(2026, 7, 18, 17, 34, tzinfo=timezone.utc)
    candles = [
        {
            "time": datetime(2026, 7, 16, hour, tzinfo=timezone.utc).isoformat(),
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 1.5,
        }
        for hour in range(24)
    ] + [
        {
            "time": datetime(2026, 7, 17, hour, tzinfo=timezone.utc).isoformat(),
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 1.5,
        }
        for hour in range(24)
    ] + [
        {
            "time": datetime(2026, 7, 18, hour, tzinfo=timezone.utc).isoformat(),
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 1.5,
        }
        for hour in range(18)
    ]
    _frame, report = normalize_and_validate(
        candles,
        timeframe="H1",
        asset_type="crypto",
        requested_start=datetime(2026, 7, 16, tzinfo=timezone.utc),
        requested_end=datetime(2026, 7, 18, 23, 59, tzinfo=timezone.utc),
        as_of=as_of,
    )
    assert report.stale_end is False
    assert "requested_end_not_covered" not in report.limitations


def test_run_with_only_excluded_tasks_is_failed_not_completed():
    status, error = _resolve_run_outcome([], [], [{"excluded": True}])
    assert status == "FAILED"
    assert error and error["code"] == "ALL_TASKS_EXCLUDED"

    status, error = _resolve_run_outcome([{"success": True}], [], [{"excluded": True}])
    assert status == "COMPLETED"
    assert error is None


def test_quality_fails_closed_when_requested_start_is_not_covered():
    candles = [
        {"time": f"2025-03-{day:02d}T00:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5}
        for day in range(1, 29)
    ]
    _frame, report = normalize_and_validate(
        candles,
        timeframe="D1",
        asset_type="crypto",
        requested_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        requested_end=datetime(2025, 3, 28, tzinfo=timezone.utc),
    )
    assert report.stale_start is True
    assert "requested_start_not_covered" in report.limitations


def test_legacy_routes_return_410_and_v2_feature_flag_fails_closed(monkeypatch):
    app = Flask(__name__)

    class DisabledService:
        enabled = False

        @staticmethod
        def capabilities():
            return {}

    register_backtesting_v2_routes(app, DisabledService())
    install_legacy_retirement_guard(app)
    client = app.test_client()
    legacy = client.post("/api/backtest-consensus")
    assert legacy.status_code == 410
    assert legacy.get_json()["code"] == "LEGACY_BACKTEST_RETIRED"
    disabled = client.get("/api/v2/backtest-capabilities")
    assert disabled.status_code == 404
    assert disabled.get_json()["code"] == "BACKTESTING_V2_DISABLED"


def test_v2_package_has_no_retired_backtest_imports():
    root = Path(__file__).resolve().parents[1] / "athena_backtesting_v2"
    forbidden = {"backtest_runner", "engine_a_v3.backtest", "backtest_cache", "backtest_store"}
    imported: set[str] = set()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not {name for name in imported if any(name == item or name.startswith(item + ".") for item in forbidden)}
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "INSERT INTO audit" not in source
    assert "UPDATE config" not in source


def test_windows_worker_bootstrap_does_not_reexecute_athena_main():
    main_module = sys.modules["__main__"]
    original_file = getattr(main_module, "__file__", None)
    if original_file is None:
        pytest.skip("test runner has no __main__.__file__")
    with _spawn_main_bootstrap():
        assert Path(main_module.__file__).name == "spawn_bootstrap.py"
    assert main_module.__file__ == original_file

    with _AthenaSpawnProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
    ) as pool:
        assert pool.submit(abs, -7).result(timeout=10) == 7


def test_only_one_local_supervisor_can_own_runtime_lock():
    lock_path = Path(__file__).with_name("_backtesting_v2_supervisor_test.lock")
    first = _SupervisorProcessLock(lock_path)
    second = _SupervisorProcessLock(lock_path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.release()
        assert second.acquire() is True
    finally:
        first.release()
        second.release()
        lock_path.unlink(missing_ok=True)
