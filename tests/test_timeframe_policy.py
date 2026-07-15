from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from athena_app.services.market_state import candle_timestamp_epoch, split_market_state
from market_structure import engine_b_candles_for_tf, resolve_engine_b_tfs
from timeframe_policy import (
    LiquidityClass,
    M5Role,
    PolicyConfigurationError,
    PolicyMode,
    PolicySource,
    SessionCalendarSource,
    SpeedClass,
    SpeedState,
    TIMEFRAME_LADDER,
    Timeframe,
    apply_authoritative_policy_result,
    apply_speed_hysteresis,
    attach_timeframe_policy_payload,
    calculate_speed_state,
    classify_liquidity,
    canonical_symbol,
    derive_warmup_bars,
    parse_m5_matrix_language,
    reconcile_symbol_universe,
    resolve_session_calendar,
    resolve_timeframe_policy,
    speed_class_for_percentile,
    speed_transition_candidate,
    timeframe_policy_execution_block_reason,
    validate_timeframe_role_order,
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
    assert left.policy_key == right.policy_key
    assert left.payload()["timeframePolicyHash"] == right.payload()["timeframePolicyHash"]
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
        "EUR/GBP": ("forex", "forex_crosses", Timeframe.H4, Timeframe.M15, M5Role.ADVISORY),
        "XAU/USD": ("commodity", "precious_trackers", Timeframe.H1, Timeframe.M5, M5Role.EXECUTION),
        "XPT/USD": ("commodity", "pgm_metals", Timeframe.H1, Timeframe.M15, M5Role.ADVISORY),
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


def test_corrected_symbol_specific_profiles() -> None:
    cases = {
        "USD/JPY": ("forex", "forex_majors", "LIQUID_FAST_SESSION_CONDITIONAL", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "USD/CAD": ("forex", "forex_majors", "STANDARD_SESSION_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "AUD/NZD": ("forex", "forex_crosses", "BROAD_STRUCTURE_CROSS", Timeframe.H4, Timeframe.M30, Timeframe.M15, M5Role.ADVISORY),
        "GBP/JPY": ("forex", "forex_crosses", "LIQUID_FAST_SESSION_CONDITIONAL", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "EUR/JPY": ("forex", "forex_crosses", "STANDARD_YEN_CROSS", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "AUD/JPY": ("forex", "forex_crosses", "STANDARD_YEN_CROSS", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "EUR/CHF": ("forex", "forex_crosses", "BROAD_STRUCTURE_CROSS", Timeframe.H4, Timeframe.M30, Timeframe.M15, M5Role.ADVISORY),
        "XAG/USD": ("commodity", "precious_trackers", "VOLATILE_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "XPT/USD": ("commodity", "pgm_metals", "THIN_EVENT_SENSITIVE", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.ADVISORY),
        "XPD/USD": ("commodity", "pgm_metals", "THIN_EVENT_SENSITIVE", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.ADVISORY),
        "Natural Gas": ("commodity", "nat_gas", "NATGAS_NO_M5", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.DISABLED),
        "NAS100": ("index", "us_indices_trackers", "LIQUID_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "US30": ("index", "us_indices_trackers", "LIQUID_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "GER40": ("index", "eu_indices", "LIQUID_FAST_SESSION_CONDITIONAL", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "JPN225": ("index", "asia_indices", "STANDARD_INDEX_SESSION_CONDITIONAL", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "US500": ("index", "us_indices_trackers", "STANDARD_LIQUID_INDEX", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "UK100": ("index", "eu_indices", "STANDARD_LIQUID_INDEX", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "AAPL": ("stock", "us_stock_single", "CASH_EQUITY_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M5, M5Role.EXECUTION),
        "SPY": ("etf", "us_etfs", "CASH_EQUITY_LIQUID", Timeframe.H1, Timeframe.M15, Timeframe.M5, M5Role.EXECUTION),
        "BTC/USDT": ("crypto", "crypto_btc", "CRYPTO_LIQUID_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M5, M5Role.EXECUTION),
        "ETH/USDT": ("crypto", "crypto_eth", "CRYPTO_LIQUID_FAST", Timeframe.H1, Timeframe.M15, Timeframe.M5, M5Role.EXECUTION),
        "BNB/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_STANDARD", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "SOL/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_HIGH_BETA", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "XRP/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_HIGH_BETA_EVENT", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "DOGE/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_HIGH_BETA_SPECULATIVE", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "ADA/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_HIGH_BETA", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
        "LINK/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_HIGH_BETA", Timeframe.H1, Timeframe.M15, Timeframe.M15, M5Role.REFINEMENT),
    }
    for symbol, (asset, group, profile, structure, trigger, execution, m5_role) in cases.items():
        policy = resolve_timeframe_policy(symbol, asset, group, "intraday")
        assert policy.profile == profile
        assert policy.structure_tf == structure
        assert policy.trigger_tf == trigger
        assert policy.execution_tf == execution
        assert policy.m5_role == m5_role


def test_policy_keys_are_scoped_by_engine_and_style() -> None:
    engine_a = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a")
    engine_b = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_b")
    engine_d = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "scalp", engine_id="engine_d")

    assert len({engine_a.policy_key, engine_b.policy_key, engine_d.policy_key}) == 3


def test_speed_class_boundaries_and_hysteresis() -> None:
    assert speed_class_for_percentile(0) == SpeedClass.SLOW
    assert speed_class_for_percentile(35) == SpeedClass.SLOW
    assert speed_class_for_percentile(36) == SpeedClass.NORMAL
    assert speed_class_for_percentile(75) == SpeedClass.FAST
    assert speed_class_for_percentile(91.999) == SpeedClass.FAST
    assert speed_class_for_percentile(92) == SpeedClass.EXTREME
    assert speed_class_for_percentile(100) == SpeedClass.EXTREME

    first, streak = apply_speed_hysteresis(SpeedClass.NORMAL, SpeedClass.FAST, 0)
    second, streak = apply_speed_hysteresis(first, SpeedClass.FAST, streak)
    assert first == SpeedClass.NORMAL
    assert second == SpeedClass.FAST
    assert streak == 0

    cfg = SpeedState().thresholds
    assert speed_transition_candidate(SpeedClass.SLOW, 44.9, cfg) == SpeedClass.SLOW
    assert speed_transition_candidate(SpeedClass.SLOW, 45, cfg) == SpeedClass.NORMAL
    assert speed_transition_candidate(SpeedClass.NORMAL, 35, cfg) == SpeedClass.SLOW
    assert speed_transition_candidate(SpeedClass.NORMAL, 74.9, cfg) == SpeedClass.NORMAL
    assert speed_transition_candidate(SpeedClass.NORMAL, 75, cfg) == SpeedClass.FAST
    assert speed_transition_candidate(SpeedClass.FAST, 65, cfg) == SpeedClass.NORMAL
    assert speed_transition_candidate(SpeedClass.FAST, 92, cfg) == SpeedClass.EXTREME
    assert speed_transition_candidate(SpeedClass.EXTREME, 88, cfg) == SpeedClass.FAST
    pending, age = apply_speed_hysteresis(SpeedClass.SLOW, SpeedClass.EXTREME, 0)
    transitioned, _ = apply_speed_hysteresis(pending, SpeedClass.EXTREME, age)
    assert pending == SpeedClass.SLOW
    assert transitioned == SpeedClass.NORMAL


def test_extreme_thin_market_cannot_gain_m5_authority() -> None:
    state = SpeedState(
        live_speed_class=SpeedClass.EXTREME,
        speed_percentile=95.0,
        history_ready=True,
        liquidity_class=LiquidityClass.THIN,
        thin_liquidity=True,
        m5_quality_acceptable=False,
    )
    policy = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "intraday", state)

    assert policy.execution_tf == Timeframe.M15
    assert policy.m5_role == M5Role.DISABLED
    assert any("liquidity removed" in message for message in policy.diagnostics.messages)


def test_dynamic_policy_moves_only_one_adjacent_execution_timeframe() -> None:
    slow = SpeedState(
        live_speed_class=SpeedClass.SLOW,
        speed_percentile=20.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )
    fast = SpeedState(
        live_speed_class=SpeedClass.FAST,
        speed_percentile=80.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )

    slow_policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", slow)
    fast_policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", fast)
    promoted_policy = resolve_timeframe_policy("USD/JPY", "forex", "forex_majors", "intraday", fast)

    assert slow_policy.execution_tf == Timeframe.M30
    # STANDARD_LIQUID keeps M5 as refinement-only even when speed is fast.
    assert fast_policy.execution_tf == Timeframe.M15
    # Session-conditional profiles may promote exactly one rung when the caller
    # supplies qualifying liquidity and quote quality.
    assert promoted_policy.execution_tf == Timeframe.M5
    assert promoted_policy.m5_role == M5Role.EXECUTION


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
        bars(240, 0.2),
        bars(240, 0.08),
        spread=0.02,
        quote_age_sec=1.0,
        current_session="london_ny",
        gap_status="normal",
        scheduled_event=False,
        provider_market_state="open",
        last_closed_h1_open_time=123,
    )

    assert state.speed_percentile is not None
    assert state.price_velocity_percentile is not None
    assert state.relative_volume is not None
    assert state.live_speed_class is not None
    assert state.live_speed_class != SpeedClass.UNAVAILABLE
    assert state.liquidity_class in {LiquidityClass.DEEP, LiquidityClass.NORMAL}
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


def test_engine_b_enforced_intraday_policy_uses_new_timeframes() -> None:
    equity = resolve_timeframe_policy("MSFT", "stock", "us_stock_single", "intraday")
    engine_b_intraday = resolve_engine_b_tfs(
        "forex", "intraday", symbol="EUR/USD", score_group="forex_majors"
    )
    engine_b_swing = resolve_engine_b_tfs(
        "forex", "swing", symbol="EUR/USD", score_group="forex_majors"
    )
    engine_b_fast = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_b"
    )
    engine_b_cross = resolve_timeframe_policy(
        "EUR/GBP", "forex", "forex_crosses", "intraday", engine_id="engine_b"
    )
    engine_d = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "engine_d")

    assert equity.structure_tf == Timeframe.H1
    assert engine_b_intraday["bias"] == "H4"
    assert engine_b_intraday["struct"] == "H1"
    # Zone walls track structure TF (intraday H1 / swing H4). Bias stays one
    # rung higher for MTF context only.
    assert engine_b_intraday["zone"] == "H1"
    assert engine_b_intraday["zone"] == engine_b_intraday["struct"]
    assert engine_b_intraday["setup"] == "M30"
    assert engine_b_intraday["trigger"] == "M15"
    assert engine_b_intraday["execution"] == "M15"
    assert engine_b_intraday["atr"] == "H1"
    assert engine_b_swing["struct"] == "H4"
    assert engine_b_swing["zone"] == "H4"
    assert engine_b_swing["zone"] == engine_b_swing["struct"]
    assert engine_b_swing["bias"] == "D1"
    assert engine_b_swing["trigger"] == "H1"
    assert engine_b_fast.execution_tf == Timeframe.M5
    assert engine_b_fast.m5_role == M5Role.EXECUTION
    assert engine_b_fast.execution_prerequisite_tf == Timeframe.M15
    assert engine_b_cross.structure_tf == Timeframe.H4
    assert engine_b_cross.trigger_tf == Timeframe.M30
    assert engine_b_cross.m5_role == M5Role.ADVISORY
    assert engine_d.bias_tf == Timeframe.H1
    assert engine_d.structure_tf == Timeframe.M15
    assert engine_d.trigger_tf == Timeframe.M5
    assert engine_d.execution_tf == Timeframe.M5

    eng_a_intra = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    eng_a_swing = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "swing", engine_id="engine_a"
    )
    assert eng_a_intra.structure_tf == Timeframe.H1
    assert eng_a_swing.structure_tf == Timeframe.H4


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
        "currentSpeedClass",
        "candidateSpeedClass",
        "candidateAgeH1Bars",
        "transitionPending",
        "lastSpeedTransitionUtc",
        "speedPercentile",
        "policySource",
        "signalId",
        "engineId",
        "style",
        "policyKey",
        "entryDriftAtr",
        "entryDriftAtrTimeframe",
        "riskAtr",
        "riskAtrTimeframe",
        "executionMoveAtr",
        "executionMoveAtrTimeframe",
        "sessionCalendarId",
        "sessionCalendarSource",
        "providerSessionTimezone",
        "sessionState",
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


def test_policy_identity_is_canonical_engine_and_style_scoped() -> None:
    intraday = resolve_timeframe_policy(
        "EURUSD=X", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    swing = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "swing", engine_id="engine_a"
    )
    engine_b = resolve_timeframe_policy(
        "MT5:EURUSD", "forex", "forex_majors", "intraday", engine_id="engine_b"
    )

    assert intraday.policy_key == "EURUSD:engine_a:intraday"
    assert len({intraday.policy_key, swing.policy_key, engine_b.policy_key}) == 3


def test_m5_matrix_language_maps_only_approved_authority() -> None:
    assert [role.value for role in M5Role] == [
        "execution",
        "refinement",
        "advisory",
        "disabled",
    ]
    assert parse_m5_matrix_language("M5") == (M5Role.EXECUTION, None)
    assert parse_m5_matrix_language("M5 after M15 confirmation") == (
        M5Role.EXECUTION,
        Timeframe.M15,
    )
    assert parse_m5_matrix_language("M5 refinement optional")[0] == M5Role.REFINEMENT
    assert parse_m5_matrix_language("M5 refinement only")[0] == M5Role.REFINEMENT
    assert parse_m5_matrix_language("M5 advisory")[0] == M5Role.ADVISORY
    assert parse_m5_matrix_language("no M5 authority")[0] == M5Role.DISABLED


def test_role_order_and_ladder_are_canonical_and_reversals_are_rejected() -> None:
    assert TIMEFRAME_LADDER == (
        Timeframe.D1,
        Timeframe.H4,
        Timeframe.H1,
        Timeframe.M30,
        Timeframe.M15,
        Timeframe.M5,
    )
    try:
        validate_timeframe_role_order(
            Timeframe.D1,
            Timeframe.H4,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.M5,
            Timeframe.M5,
        )
    except ValueError as exc:
        assert "reverses" in str(exc)
    else:
        raise AssertionError("reversed policy was accepted")


def test_cold_start_keeps_baseline_and_reports_unavailable() -> None:
    bars = [{"high": 101, "low": 99, "close": 100, "vol": 1000}] * 40
    state = calculate_speed_state(
        bars,
        bars,
        spread=0.1,
        quote_age_sec=1,
        relative_volume=1.0,
        provider_market_state="open",
        current_session="open",
        gap_status="normal",
        scheduled_event=False,
        last_closed_h1_open_time=1,
    )
    policy = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", state
    )

    assert state.live_speed_class == SpeedClass.UNAVAILABLE
    assert state.history_ready is False
    assert state.adaptation_reason == "INSUFFICIENT_HISTORY"
    assert policy.execution_tf == Timeframe.M15
    assert policy.diagnostics.adaptation_applied is False


def test_hard_liquidity_failure_does_not_reclassify_persistent_speed() -> None:
    def bars(count: int) -> list[dict]:
        return [
            {
                "high": 101 + index * 0.01,
                "low": 99 + index * 0.01,
                "close": 100 + index * 0.01,
                "vol": 1000 + index,
            }
            for index in range(count)
        ]

    previous = SpeedState(
        live_speed_class=SpeedClass.FAST,
        history_ready=True,
        last_closed_h1_open_time=1,
    )
    state = calculate_speed_state(
        bars(240),
        bars(240),
        spread=None,
        quote_age_sec=None,
        current_session="open",
        gap_status="normal",
        scheduled_event=False,
        previous=previous,
        last_closed_h1_open_time=2,
    )

    assert state.live_speed_class == SpeedClass.FAST
    assert state.liquidity_class == LiquidityClass.UNAVAILABLE
    assert state.m5_quality_acceptable is False


def test_liquidity_is_separate_from_speed_and_fail_closed_for_m5() -> None:
    liquidity = classify_liquidity(
        quote_age_sec=1,
        spread_trigger_atr=0.25,
        relative_volume=2.0,
        provider_market_state="open",
    )
    assert liquidity == LiquidityClass.THIN
    state = SpeedState(
        live_speed_class=SpeedClass.FAST,
        history_ready=True,
        liquidity_class=liquidity,
        m5_quality_acceptable=False,
    )
    policy = resolve_timeframe_policy(
        "BTC/USDT", "crypto", "crypto_btc", "intraday", state
    )
    assert policy.execution_tf != Timeframe.M5
    assert policy.m5_role == M5Role.DISABLED


def test_alias_group_conflict_sets_config_error_and_disables_policy_autotrade() -> None:
    signal = {"score": 2.0, "direction": "LONG"}
    policy = attach_timeframe_policy_payload(
        signal,
        {
            "display": "EURUSD=X",
            "type": "forex",
            "score_group": "forex_exotics",
        },
        "intraday",
        config={"TF_POLICY_MODE": "enforced", "TF_POLICY_AUTOTRADE_ENABLED": True},
    )

    assert policy.policy_source == PolicySource.CONFIG_CONFLICT
    assert signal["entryReadiness"] == "CONFIG_ERROR"
    assert signal["timeframePolicyAutotradeEnabled"] is False
    assert timeframe_policy_execution_block_reason(
        signal, {"TF_POLICY_MODE": "shadow", "TF_POLICY_AUTOTRADE_ENABLED": False}
    ) == "TF_POLICY_CONFIG_CONFLICT"


def test_reconciliation_rejects_aliases_assigned_to_conflicting_groups() -> None:
    pairs = [
        {
            "display": "EUR/USD",
            "symbol": "EURUSD=X",
            "type": "forex",
            "score_group": "forex_majors",
        },
        {
            "display": "EURUSD=X",
            "symbol": "EURUSD",
            "type": "forex",
            "score_group": "forex_exotics",
        },
    ]
    try:
        reconcile_symbol_universe(pairs)
    except PolicyConfigurationError as exc:
        assert "EURUSD" in str(exc)
    else:
        raise AssertionError("conflicting alias groups were accepted")


def test_session_calendar_resolution_is_provider_first_and_timezone_aware() -> None:
    resolved = resolve_session_calendar(
        provider_metadata={
            "sessionCalendarId": "broker-x-aapl",
            "timezone": "America/New_York",
            "state": "open",
        },
        underlying_exchange_calendar={
            "sessionCalendarId": "XNYS",
            "timezone": "America/New_York",
        },
    )
    assert resolved.source == SessionCalendarSource.PROVIDER_METADATA
    assert resolved.calendar_id == "broker-x-aapl"
    unavailable = resolve_session_calendar(
        provider_calendar={"sessionCalendarId": "bad", "timezone": "Not/AZone"}
    )
    assert unavailable.source == SessionCalendarSource.SESSION_UNAVAILABLE


def test_enforced_policy_requires_separate_autotrade_promotion() -> None:
    signal = {"policySource": PolicySource.SYMBOL_OVERRIDE.value}
    assert timeframe_policy_execution_block_reason(
        signal, {"TF_POLICY_MODE": PolicyMode.SHADOW.value, "TF_POLICY_AUTOTRADE_ENABLED": False}
    ) is None
    assert timeframe_policy_execution_block_reason(
        signal, {"TF_POLICY_MODE": PolicyMode.ENFORCED.value, "TF_POLICY_AUTOTRADE_ENABLED": False}
    ) == "TF_POLICY_DEMO_AUTOTRADE_DISABLED"


def test_enforced_demo_promotes_policy_score_direction_and_locks_real_account() -> None:
    signal = {"confluenceScore": 5.9, "score": 5.9, "direction": "SHORT", "maxScore": 10}
    cfg = {
        "TF_POLICY_MODE": "enforced_demo",
        "TF_POLICY_DEMO_AUTOTRADE_ENABLED": True,
        "TF_POLICY_REAL_AUTOTRADE_ENABLED": False,
    }

    apply_authoritative_policy_result(
        signal,
        policy_score=7.2,
        policy_direction="LONG",
        config=cfg,
    )

    assert signal["legacyScore"] == 5.9
    assert signal["legacyDirection"] == "SHORT"
    assert signal["policyScore"] == 7.2
    assert signal["policyDirection"] == "LONG"
    assert signal["confluenceScore"] == 7.2
    assert signal["direction"] == "LONG"
    assert signal["authoritativeScoreSource"] == "POLICY"
    assert timeframe_policy_execution_block_reason(
        signal, cfg, {"accountId": "demo-42", "exchange": "Bybit", "demo": True}
    ) is None
    assert timeframe_policy_execution_block_reason(
        signal, cfg, {"accountId": "real-42", "exchange": "Bybit", "demo": False}
    ) == "TF_POLICY_REAL_ACCOUNT_LOCKED"
    assert signal["timeframePolicyExecution"]["accountEnvironment"] == "real"
