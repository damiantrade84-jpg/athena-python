from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from athena_app.services.market_state import candle_timestamp_epoch, split_market_state
from market_structure import engine_b_candles_for_tf, resolve_engine_b_tfs
from timeframe_policy import (
    M5Role,
    PolicySource,
    SpeedClass,
    SpeedState,
    Timeframe,
    apply_speed_hysteresis,
    attach_timeframe_policy_payload,
    calculate_speed_state,
    canonical_symbol,
    derive_warmup_bars,
    reconcile_symbol_universe,
    resolve_timeframe_policy,
    speed_class_for_percentile,
)


def _registered_pairs() -> list[dict]:
    """Read source literals without importing side-effectful athena.py."""
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "athena.py").read_text(encoding="utf-8"))
    wanted = {
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    }
    pairs: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in wanted:
            pairs.extend(ast.literal_eval(node.value))
    return pairs


def test_every_source_enabled_registered_symbol_resolves_once() -> None:
    result = reconcile_symbol_universe(_registered_pairs())
    expected = [pair for pair in _registered_pairs() if pair.get("enabled", True)]

    assert len(result["rows"]) == len(expected)
    assert result["duplicate_canonical_symbols"] == []
    assert result["aliases_mapping_to_multiple_groups"] == {}
    assert result["unsafe_symbols"] == []
    assert all(row["policy_source"] != PolicySource.SAFE_FALLBACK.value for row in result["rows"])


def test_aliases_resolve_to_same_canonical_policy() -> None:
    left = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")
    right = resolve_timeframe_policy("EURUSD=X", "forex", "forex_majors", "intraday")
    crypto_old = resolve_timeframe_policy("MATICUSDT", "crypto", "crypto_alt_majors", "intraday")

    assert canonical_symbol("MT5:EURUSD") == "EURUSD"
    assert left.profile == right.profile
    assert left.execution_tf == right.execution_tf
    assert crypto_old.diagnostics.canonical_symbol == "POLUSDT"


def test_unknown_symbol_resolution_is_visible_and_fail_closed_when_asset_unknown() -> None:
    asset_default = resolve_timeframe_policy("NEWCOIN", "crypto", None, "intraday")
    fallback = resolve_timeframe_policy("UNKNOWN", "other", None, "intraday")

    assert asset_default.policy_source == PolicySource.ASSET_STYLE_DEFAULT
    assert any("symbol_not_in_alias_registry" in msg for msg in asset_default.diagnostics.messages)
    assert fallback.policy_source == PolicySource.SAFE_FALLBACK
    assert fallback.diagnostics.safe_fallback is True
    assert fallback.m5_role == M5Role.DISABLED


def test_required_baseline_matrix() -> None:
    cases = {
        "EUR/USD": ("forex", "forex_majors", Timeframe.H1, Timeframe.M15, M5Role.REFINEMENT),
        "GBP/USD": ("forex", "forex_majors", Timeframe.H1, Timeframe.M5, M5Role.EXECUTION),
        "EUR/GBP": ("forex", "forex_crosses", Timeframe.H4, Timeframe.M15, M5Role.DISABLED),
        "XAU/USD": ("commodity", "precious_trackers", Timeframe.H1, Timeframe.M5, M5Role.EXECUTION),
        "XPT/USD": ("commodity", "pgm_metals", Timeframe.H1, Timeframe.M15, M5Role.DISABLED),
        "WTI Oil": ("commodity", "energy_oil", Timeframe.H1, Timeframe.M5, M5Role.EXECUTION),
        "S&P 500": ("index", "us_indices_trackers", Timeframe.H1, Timeframe.M15, M5Role.REFINEMENT),
        "AAPL": ("stock", "us_stock_single", Timeframe.H1, Timeframe.M5, M5Role.EXECUTION),
        "BTC/USDT": ("crypto", "crypto_btc", Timeframe.H1, Timeframe.M5, M5Role.EXECUTION),
        "SOL/USDT": ("crypto", "crypto_alt_majors", Timeframe.H1, Timeframe.M15, M5Role.REFINEMENT),
    }
    for symbol, (asset, group, structure, execution, m5_role) in cases.items():
        policy = resolve_timeframe_policy(symbol, asset, group, "intraday")
        assert policy.regime_tf == Timeframe.D1
        assert policy.structure_tf == structure
        assert policy.execution_tf == execution
        assert policy.m5_role == m5_role

    oil = resolve_timeframe_policy("SpotBrent", "commodity", "energy_oil", "intraday")
    assert oil.diagnostics.m15_confirmation_required_for_m5 is True


def test_speed_class_boundaries_and_hysteresis() -> None:
    assert speed_class_for_percentile(0) == SpeedClass.SLOW
    assert speed_class_for_percentile(39.999) == SpeedClass.SLOW
    assert speed_class_for_percentile(40) == SpeedClass.NORMAL
    assert speed_class_for_percentile(70) == SpeedClass.FAST
    assert speed_class_for_percentile(90) == SpeedClass.EXTREME
    assert speed_class_for_percentile(100) == SpeedClass.EXTREME

    first, streak = apply_speed_hysteresis(SpeedClass.NORMAL, SpeedClass.FAST, 0)
    second, streak = apply_speed_hysteresis(first, SpeedClass.FAST, streak)
    assert first == SpeedClass.NORMAL
    assert second == SpeedClass.FAST
    assert streak == 0


def test_extreme_thin_market_cannot_gain_m5_authority() -> None:
    state = SpeedState(
        live_speed_class=SpeedClass.EXTREME,
        speed_percentile=95.0,
        thin_liquidity=True,
        m5_quality_acceptable=False,
    )
    policy = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "intraday", state)

    assert policy.execution_tf == Timeframe.M15
    assert policy.m5_role == M5Role.DISABLED
    assert any("EXTREME+thin" in message for message in policy.diagnostics.messages)


def test_dynamic_policy_moves_only_one_adjacent_execution_timeframe() -> None:
    slow = SpeedState(
        live_speed_class=SpeedClass.SLOW,
        speed_percentile=20.0,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )
    fast = SpeedState(
        live_speed_class=SpeedClass.FAST,
        speed_percentile=80.0,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )

    slow_policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", slow)
    fast_policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", fast)

    assert slow_policy.execution_tf == Timeframe.M30
    assert fast_policy.execution_tf == Timeframe.M5


def test_speed_state_uses_confirmed_volatility_velocity_volume_and_quote_quality() -> None:
    def bars(count: int, step: float) -> list[dict]:
        return [
            {
                "open": 100.0 + index * step,
                "high": 100.8 + index * step,
                "low": 99.4 + index * step,
                "close": 100.2 + index * step,
                "vol": 1000 + index * 10,
            }
            for index in range(count)
        ]

    state = calculate_speed_state(
        bars(80, 0.2),
        bars(160, 0.08),
        spread=0.02,
        quote_age_sec=1.0,
        current_session="london_ny",
        gap_status="normal",
        scheduled_event=False,
        last_closed_h1_open_time=123,
    )

    assert state.speed_percentile is not None
    assert state.price_velocity_percentile is not None
    assert state.relative_volume is not None
    assert state.live_speed_class is not None
    assert state.m5_quality_acceptable is True


def test_market_state_separates_mt5_forming_and_bybit_confirm_flags() -> None:
    now = datetime(2026, 7, 13, 10, 7, tzinfo=timezone.utc).timestamp()
    mt5 = split_market_state(
        [
            {"time": "2026-07-13T09:55:00", "close": 1.0},
            {"time": "2026-07-13T10:05:00", "close": 1.1},
        ],
        "M5",
        "EUR/USD",
        time_now=now,
        provider="mt5",
        provider_symbol="EURUSD",
    )
    bybit = split_market_state(
        [
            {"time": "2026-07-13T10:00:00Z", "close": 100.0, "confirm": True},
            {"time": "2026-07-13T09:55:00Z", "close": 101.0, "confirm": False},
        ],
        "M5",
        "BTC/USDT",
        time_now=now,
        provider="bybit",
        provider_symbol="BTCUSDT",
    )

    assert len(mt5["confirmed"]) == 1
    assert mt5["forming"]["close"] == 1.1
    assert mt5["provider_symbol"] == "EURUSD"
    assert len(bybit["confirmed"]) == 1
    assert bybit["forming"]["confirm"] is False


def test_naive_provider_timestamp_is_normalized_as_utc() -> None:
    expected = int(datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc).timestamp())
    assert candle_timestamp_epoch({"time": "2026-07-13T10:00:00"}) == expected


def test_equity_h4_is_not_mandatory_structure_and_engine_roles_are_preserved() -> None:
    equity = resolve_timeframe_policy("MSFT", "stock", "us_stock_single", "intraday")
    engine_b_intraday = resolve_engine_b_tfs(
        "forex", "intraday", symbol="EUR/USD", score_group="forex_majors"
    )
    engine_b_swing = resolve_engine_b_tfs(
        "forex", "swing", symbol="EUR/USD", score_group="forex_majors"
    )
    engine_d = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "engine_d")

    assert equity.structure_tf == Timeframe.H1
    assert engine_b_intraday["struct"] == "H1"
    assert engine_b_intraday["setup"] == "M30"
    assert engine_b_intraday["trigger"] == "M15"
    assert engine_b_intraday["atr"] == "H1"
    assert engine_b_swing["struct"] == "H4"
    assert engine_b_swing["trigger"] == "H1"
    assert engine_d.bias_tf == Timeframe.H1
    assert engine_d.structure_tf == Timeframe.M15
    assert engine_d.trigger_tf == Timeframe.M5
    assert engine_d.execution_tf == Timeframe.M5


def test_engine_a_payload_attachment_does_not_change_score_or_direction() -> None:
    signal = {
        "score": 2.15,
        "confluenceScore": 2.15,
        "direction": "LONG",
        "price": 1.1,
        "atr": 0.01,
    }
    before = {key: signal[key] for key in ("score", "confluenceScore", "direction")}
    states = {
        tf: {"confirmed": [{"time": f"2026-07-13T0{index}:00:00Z"}]}
        for index, tf in enumerate(("D1", "H4", "H1", "M30", "M15"), start=1)
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        market_states=states,
    )

    assert {key: signal[key] for key in before} == before
    assert signal["entryReadiness"] == "PENDING"
    assert signal["triggerCandleClosed"] is True


def test_confirmed_engine_b_trigger_sets_ready_without_changing_structure_result() -> None:
    signal = {"trigger_ok": True, "structural_verdict": "CLEAR"}
    states = {
        tf: {"confirmed": [{"time": "2026-07-13T10:00:00Z"}], "stale": False}
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        engine="engine_b",
        market_states=states,
    )

    assert signal["structural_verdict"] == "CLEAR"
    assert signal["entryReadiness"] == "READY"
    assert signal["triggerConfirmed"] is True


def test_missing_lower_tf_preserves_payload_and_execution_has_no_higher_tf_fallback() -> None:
    signal = {"score": 2.0, "direction": "SHORT"}
    attach_timeframe_policy_payload(
        signal,
        {"display": "GBP/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        market_states={"D1": {"confirmed": [{}]}, "H4": {"confirmed": [{}]}, "H1": {"confirmed": [{}]}},
    )

    assert signal["direction"] == "SHORT"
    assert signal["entryReadiness"] == "UNAVAILABLE"
    assert "M15" in signal["entryReadinessReason"]
    assert engine_b_candles_for_tf("M15", [1], [2], [3], extra_by_tf={}) == []


def test_payload_contract_and_warmup_derivation() -> None:
    signal: dict = {"atr": 12.5}
    policy = attach_timeframe_policy_payload(
        signal,
        {"display": "AAPL", "type": "stock", "score_group": "us_stock_single"},
        "intraday",
        market_states={},
    )
    required = {
        "timeframePolicyVersion",
        "timeframeProfile",
        "regimeTf",
        "biasTf",
        "structureTf",
        "setupTf",
        "triggerTf",
        "executionTf",
        "m5Role",
        "baselineSpeedClass",
        "liveSpeedClass",
        "speedPercentile",
        "policySource",
        "confirmedBarTimes",
        "formingBarTime",
        "entryReadiness",
        "entryReadinessReason",
        "atrValue",
        "atrTimeframe",
        "structureAgeBars",
        "quoteAgeSec",
    }
    assert required <= signal.keys()
    assert signal["timeframePolicyHash"]
    assert policy.structure_tf == Timeframe.H1
    assert derive_warmup_bars(ema_periods=(21, 50, 200), safety_margin=20) == 220
