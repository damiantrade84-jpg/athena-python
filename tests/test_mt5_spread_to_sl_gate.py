"""Spread-to-stop-distance gate (SPREAD_TOO_WIDE_FOR_SL) unit tests."""

from __future__ import annotations

from types import SimpleNamespace

import mt5_executor


def test_ratio_cap_disabled_when_missing_or_nonpositive(monkeypatch):
    monkeypatch.delitem(
        mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", raising=False
    )
    assert mt5_executor._mt5_max_spread_to_sl_ratio() is None

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", 0)
    assert mt5_executor._mt5_max_spread_to_sl_ratio() is None

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", None)
    assert mt5_executor._mt5_max_spread_to_sl_ratio() is None

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", 0.15)
    assert mt5_executor._mt5_max_spread_to_sl_ratio() == 0.15


def test_wide_spread_tight_stop_blocks():
    # ATFX-like AUDCHF: 3-pip spread, 15-pip stop -> ratio 0.20 > 0.15 cap
    tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57110, sl=0.56960, ratio_cap=0.15
    )
    assert exceeded is True
    assert ratio is not None
    assert abs(ratio - 0.20) < 1e-6


def test_tight_spread_or_wide_stop_passes():
    # Same spread but a 60-pip stop -> ratio 0.05 passes
    tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57110, sl=0.56510, ratio_cap=0.15
    )
    assert exceeded is False
    assert ratio is not None
    assert abs(ratio - 0.05) < 1e-6

    # Pepperstone-like 0.2-pip spread, 15-pip stop -> ratio ~0.013 passes
    tick = SimpleNamespace(ask=0.57082, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57082, sl=0.56932, ratio_cap=0.15
    )
    assert exceeded is False
    assert ratio is not None
    assert ratio < 0.02


def test_short_direction_uses_absolute_distance():
    # SHORT: fill at bid, SL above entry — |price - sl| basis is direction-agnostic
    tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    exceeded, ratio = mt5_executor._mt5_spread_to_sl_exceeded(
        tick, price=0.57080, sl=0.57230, ratio_cap=0.15
    )
    assert exceeded is True
    assert ratio is not None
    assert abs(ratio - 0.20) < 1e-6


def test_malformed_inputs_fail_open_to_other_gates():
    good_tick = SimpleNamespace(ask=0.57110, bid=0.57080)
    # Missing/zero SL: SL validation and MAX_SL_PCT gates own that case
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        good_tick, price=0.57110, sl=0.0, ratio_cap=0.15
    ) == (False, None)
    # Broken tick: plain spread cap owns that case
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        SimpleNamespace(ask=0, bid=0), price=0.57110, sl=0.56960, ratio_cap=0.15
    ) == (False, None)
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        SimpleNamespace(ask=1.0, bid=1.1), price=1.05, sl=1.0, ratio_cap=0.15
    ) == (False, None)
    # SL equal to price (zero distance)
    assert mt5_executor._mt5_spread_to_sl_exceeded(
        good_tick, price=0.57110, sl=0.57110, ratio_cap=0.15
    ) == (False, None)


def test_rejection_reports_engine_levels_and_timeframe_provenance():
    tick = SimpleNamespace(ask=0.81971, bid=0.81951)
    signal = {
        "pair": "USD/CHF",
        "engine": "engine_b",
        "direction": "LONG",
        "level_source": "engine_b_execution",
        "sl_method": "structural",
        "atr": 0.0008937265,
        "structureTf": "H1",
        "triggerTf": "M15",
        "executionTf": "M15",
        "naked_data": {
            "atr_tf": "H1",
            "zone_tf": "H1",
            "entry_tf": "M15",
        },
    }

    rejection = mt5_executor._mt5_spread_to_sl_rejection(
        signal,
        tick,
        price=0.81971,
        sl=0.81823,
        ratio_cap=0.10,
    )

    assert rejection is not None
    assert rejection["success"] is False
    assert rejection["spreadToSlRatio"] == 0.1351
    assert rejection["spreadToSlRatioExceeded"] is True
    assert rejection["engine"] == "engine_b"
    assert rejection["entryPrice"] == 0.81971
    assert rejection["stopLoss"] == 0.81823
    assert abs(rejection["spread"] - 0.00020) < 1e-12
    assert abs(rejection["stopDistance"] - 0.00148) < 1e-12
    assert rejection["levelSource"] == "engine_b_execution"
    assert rejection["slMethod"] == "structural"
    assert rejection["atrTimeframe"] == "H1"
    assert rejection["structureTimeframe"] == "H1"
    assert rejection["triggerTimeframe"] == "M15"
    assert rejection["executionTimeframe"] == "M15"
    assert rejection["spreadGateStage"] == "broker_execute"


def test_scan_gate_marks_fresh_mt5_signal_non_executable_before_quick_execute():
    signal = {
        "pair": "USD/CHF",
        "display": "USD/CHF",
        "symbol": "USDCHF.s",
        "type": "forex",
        "engine": "engine_b",
        "direction": "LONG",
        "price": 0.81961,
        "sl": 0.81823,
        "atr": 0.0008937265,
        "atr_tf": "H1",
        "structureTf": "H1",
        "triggerTf": "M15",
        "executionTf": "M15",
        "executable": True,
    }
    live_prices = {
        "USD/CHF": {
            "price": 0.81961,
            "bid": 0.81951,
            "ask": 0.81971,
            "source": "mt5",
            "broker_ts": 995.0,
        }
    }

    annotated = mt5_executor.apply_mt5_spread_to_sl_scan_gate(
        [signal],
        live_prices,
        ratio_cap=0.10,
        config={"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}},
        time_now=1000.0,
    )

    assert annotated == 1
    assert signal["executable"] is False
    assert signal["executionBlockReason"] == "SPREAD_TOO_WIDE_FOR_SL"
    assert signal["spreadToSlBlockReason"] == "SPREAD_TOO_WIDE_FOR_SL"
    assert signal["spreadToSlRatio"] == 0.1351
    assert signal["spreadToSlRatioExceeded"] is True
    assert signal["executionSpreadGate"]["entryPrice"] == 0.81971
    assert signal["executionSpreadGate"]["atrTimeframe"] == "H1"
    assert signal["executionSpreadGate"]["spreadGateStage"] == "scan_classification"


def test_scan_gate_does_not_block_when_spread_ratio_passes():
    signal = {
        "pair": "USD/CHF",
        "display": "USD/CHF",
        "type": "forex",
        "engine": "ENGINE_A_V3",
        "direction": "SHORT",
        "sl": 0.82551,
        "atrDiagnostics": {"atr_tf": "M30"},
        "executable": True,
    }
    live_prices = {
        "USD/CHF": {
            "price": 0.81961,
            "bid": 0.81951,
            "ask": 0.81971,
            "source": "mt5",
            "broker_ts": 995.0,
        }
    }

    annotated = mt5_executor.apply_mt5_spread_to_sl_scan_gate(
        [signal],
        live_prices,
        ratio_cap=0.10,
        config={"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}},
        time_now=1000.0,
    )

    assert annotated == 1
    assert signal["executable"] is True
    assert signal["spreadToSlRatioExceeded"] is False
    assert signal["executionSpreadGate"]["entryPrice"] == 0.81951
    assert signal["executionSpreadGate"]["atrTimeframe"] == "M30"
    assert "executionBlockReason" not in signal


# ── Per-asset ratio cap (2026-07-28 dict form) ───────────────────────────────


def test_ratio_cap_dict_resolves_per_asset(monkeypatch):
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "MAX_EXECUTION_SPREAD_TO_SL_RATIO",
        {"default": 0.10, "forex": 0.20},
    )
    assert mt5_executor._mt5_max_spread_to_sl_ratio("forex") == 0.20
    assert mt5_executor._mt5_max_spread_to_sl_ratio("crypto") == 0.10
    assert mt5_executor._mt5_max_spread_to_sl_ratio(None) == 0.10
    assert mt5_executor._mt5_max_spread_to_sl_ratio("FOREX") == 0.20


def test_ratio_cap_scalar_still_global(monkeypatch):
    monkeypatch.setitem(
        mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_TO_SL_RATIO", 0.15
    )
    assert mt5_executor._mt5_max_spread_to_sl_ratio("forex") == 0.15
    assert mt5_executor._mt5_max_spread_to_sl_ratio("crypto") == 0.15


def test_scan_gate_applies_per_asset_cap(monkeypatch):
    from athena_app.services import engine_b_market_state

    monkeypatch.setattr(
        engine_b_market_state,
        "fresh_live_gate_quote",
        lambda pair, live_prices, config, time_now=None: (None, {"matchedKey": "k"}),
    )
    # 0.1% absolute spread vs 0.75% stop distance -> ratio ~0.133:
    # over the 0.10 default, under the 0.20 forex ceiling.
    live_prices = {"k": {"source": "mt5", "ask": 1.1010, "bid": 1.1000}}
    base = {"display": "EUR/USD", "pair": "EUR/USD", "direction": "LONG", "sl": 1.0935}
    forex_signal = {**base, "type": "forex", "engine": "ENGINE_A_V3"}
    crypto_signal = {**base, "type": "crypto", "engine": "ENGINE_A_V3"}
    annotated = mt5_executor.apply_mt5_spread_to_sl_scan_gate(
        [forex_signal, crypto_signal],
        live_prices,
        config={"MAX_EXECUTION_SPREAD_TO_SL_RATIO": {"default": 0.10, "forex": 0.20}},
    )
    assert annotated == 2
    assert forex_signal.get("executable") is not False
    assert forex_signal["spreadToSlRatioExceeded"] is False
    assert crypto_signal["executable"] is False
    assert crypto_signal["spreadToSlBlockReason"] == "SPREAD_TOO_WIDE_FOR_SL"
