"""Targeted tests for Fixes #1, #3, #4, #5 live-price/freshness work.

Covers:
- Fix #1: evaluate_live_quote_age helper (config-gated, default disabled)
- Fix #3: broker tick-age helpers (mt5 + bybit), default disabled
- Fix #4: execution spread helpers (mt5 + bybit), default disabled
- Fix #5: live_price_diagnostics CLI filter logic (no network)

These are unit-level tests for the new helpers and CLI utilities. End-to-end
executor tests (mocking the full MT5/Bybit stack) live in test_execution_safety.py
and are intentionally not duplicated here.
"""

from types import SimpleNamespace


# ─── Fix #1 ─────────────────────────────────────────────────────────────────

def test_evaluate_live_quote_age_disabled_without_config():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    result = evaluate_live_quote_age({"type": "forex"}, 30.0, {})
    assert result["enabled"] is False
    assert result["stale"] is False
    assert result["thresholdSec"] is None
    assert result["assetType"] == "forex"


def test_evaluate_live_quote_age_disabled_when_asset_type_missing_in_config():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30, "crypto": None}}
    # crypto has explicit null → still disabled
    result = evaluate_live_quote_age({"type": "crypto"}, 999.0, config)
    assert result["enabled"] is False
    assert result["stale"] is False


def test_evaluate_live_quote_age_flags_stale_when_over_threshold():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}}
    result = evaluate_live_quote_age({"type": "forex"}, 45.0, config)
    assert result["enabled"] is True
    assert result["stale"] is True
    assert result["thresholdSec"] == 30.0
    assert result["ageSec"] == 45.0
    assert result["reason"] == "STALE"
    assert result["would_block"] is False  # block flag off by default


def test_evaluate_live_quote_age_fresh_when_under_threshold():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}}
    result = evaluate_live_quote_age({"type": "forex"}, 10.0, config)
    assert result["enabled"] is True
    assert result["stale"] is False
    assert result["reason"] == "FRESH"
    assert result["would_block"] is False


def test_evaluate_live_quote_age_unknown_age_does_not_block_without_flag():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}}
    result = evaluate_live_quote_age({"type": "forex"}, None, config)
    assert result["enabled"] is True
    assert result["stale"] is False
    assert result["reason"] == "AGE_UNKNOWN"
    assert result["would_block"] is False


def test_evaluate_live_quote_age_would_block_when_stale_and_block_enabled():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {
        "LIVE_PRICE_MAX_AGE_SEC": {"crypto": 15},
        "LIVE_PRICE_BLOCK_EXECUTION": True,
    }
    result = evaluate_live_quote_age({"type": "crypto"}, 45.0, config)
    assert result["stale"] is True
    assert result["would_block"] is True


def test_evaluate_live_quote_age_would_block_unknown_when_configured():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {
        "LIVE_PRICE_MAX_AGE_SEC": {"forex": 60},
        "LIVE_PRICE_BLOCK_EXECUTION": True,
        "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE": True,
    }
    result = evaluate_live_quote_age({"type": "forex"}, None, config)
    assert result["reason"] == "AGE_UNKNOWN"
    assert result["would_block"] is True


# ─── Fix #3 + Fix #4 (MT5 helpers) ──────────────────────────────────────────

def test_mt5_max_tick_age_disabled_by_default(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": None})
    assert mt5_executor._mt5_max_tick_age_sec() is None


def test_mt5_max_tick_age_returns_configured_value(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": 8.5})
    assert mt5_executor._mt5_max_tick_age_sec() == 8.5


def test_mt5_tick_age_seconds_uses_now_minus_tick_time(monkeypatch):
    import mt5_executor

    monkeypatch.setattr(mt5_executor.time, "time", lambda: 1_716_200_010.0)
    tick = SimpleNamespace(time=1_716_200_000.0)
    assert mt5_executor._mt5_tick_age_seconds(tick) == 10.0


def test_mt5_tick_age_seconds_returns_none_when_tick_time_missing():
    import mt5_executor

    assert mt5_executor._mt5_tick_age_seconds(SimpleNamespace(time=0)) is None
    assert mt5_executor._mt5_tick_age_seconds(SimpleNamespace()) is None


def test_mt5_max_spread_disabled_when_asset_type_null(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "MAX_EXECUTION_SPREAD_PCT",
        {"forex": None, "commodity": 0.001},
    )
    assert mt5_executor._mt5_max_spread_pct({"type": "forex"}) is None
    assert mt5_executor._mt5_max_spread_pct({"type": "commodity"}) == 0.001


def test_mt5_spread_pct_uses_ask_bid_mid():
    import mt5_executor

    tick = SimpleNamespace(ask=1.0805, bid=1.0800)
    spread = mt5_executor._mt5_spread_pct(tick)
    # (0.0005)/1.08025 ≈ 0.000463
    assert spread is not None
    assert abs(spread - 0.000463) < 1e-5


def test_mt5_spread_pct_returns_none_when_ask_lt_bid():
    import mt5_executor

    assert mt5_executor._mt5_spread_pct(SimpleNamespace(ask=0, bid=0)) is None
    assert mt5_executor._mt5_spread_pct(SimpleNamespace(ask=1.0, bid=1.1)) is None


def test_mt5_execute_blocks_missing_tick_timestamp_when_age_guard_enabled(monkeypatch):
    import mt5_executor
    from risk_engine import RiskApproval

    class FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1

        def symbol_select(self, symbol, enabled):
            return True

        def symbol_info_tick(self, symbol):
            return SimpleNamespace(ask=1.0805, bid=1.0800, time=0)

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: "EURUSD")
    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": 5.0})

    approval = RiskApproval(True, 0.01, 10.0, 0.01, 0.01, 0.0, "OK")
    result = mt5_executor.mt5_execute(
        {"pair": "EUR/USD", "direction": "LONG", "type": "forex", "price": 1.08, "sl": 1.07, "tp1": 1.10},
        approval,
    )

    assert result["success"] is False
    assert result["error"] == "BROKER_TICK_TIMESTAMP_MISSING"


def test_mt5_execute_blocks_when_spread_guard_enabled_and_bid_missing(monkeypatch):
    import mt5_executor
    from risk_engine import RiskApproval

    class FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1

        def symbol_select(self, symbol, enabled):
            return True

        def symbol_info_tick(self, symbol):
            return SimpleNamespace(ask=1.0805, bid=0, time=1_716_200_000)

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: "EURUSD")
    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"mt5": None})
    monkeypatch.setitem(mt5_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"forex": 0.001})

    approval = RiskApproval(True, 0.01, 10.0, 0.01, 0.01, 0.0, "OK")
    result = mt5_executor.mt5_execute(
        {"pair": "EUR/USD", "direction": "LONG", "type": "forex", "price": 1.08, "sl": 1.07, "tp1": 1.10},
        approval,
    )

    assert result["success"] is False
    assert result["error"] == "EXECUTABLE_SPREAD_UNAVAILABLE"


# ─── Fix #3 + Fix #4 (Bybit helpers) ────────────────────────────────────────

def test_bybit_max_tick_age_disabled_by_default(monkeypatch):
    import bybit_executor

    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": None})
    assert bybit_executor._bybit_max_tick_age_sec() is None


def test_bybit_max_tick_age_returns_configured_value(monkeypatch):
    import bybit_executor

    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": 5.0})
    assert bybit_executor._bybit_max_tick_age_sec() == 5.0


def test_bybit_ticker_age_converts_ms_to_seconds(monkeypatch):
    import bybit_executor

    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_010.0)
    # ccxt reports timestamp in milliseconds
    ticker = {"timestamp": 1_716_200_000.0 * 1000.0}
    age = bybit_executor._bybit_ticker_age_seconds(ticker)
    assert age is not None
    assert abs(age - 10.0) < 1e-6


def test_bybit_ticker_age_returns_none_when_timestamp_missing():
    import bybit_executor

    assert bybit_executor._bybit_ticker_age_seconds({}) is None
    assert bybit_executor._bybit_ticker_age_seconds({"timestamp": 0}) is None


def test_bybit_ticker_timestamp_ms_from_info_ts(monkeypatch):
    import bybit_executor

    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_010.0)
    ts_ms = 1_716_200_000_000
    ticker = {"ask": 50_010.0, "bid": 50_000.0, "info": {"ts": ts_ms}}
    assert bybit_executor._bybit_ticker_timestamp_ms(ticker) == ts_ms
    age = bybit_executor._bybit_ticker_age_seconds(ticker)
    assert age is not None
    assert abs(age - 10.0) < 1e-6


def test_bybit_ticker_timestamp_ms_from_datetime(monkeypatch):
    import bybit_executor

    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_010.0)
    ticker = {
        "ask": 50_010.0,
        "bid": 50_000.0,
        "datetime": "2024-05-20T10:13:20.000Z",
    }
    assert bybit_executor._bybit_ticker_timestamp_ms(ticker) == 1_716_200_000_000
    age = bybit_executor._bybit_ticker_age_seconds(ticker)
    assert age is not None
    assert abs(age - 10.0) < 1e-6


def test_bybit_ticker_timestamp_ms_returns_none_when_unattestable():
    import bybit_executor

    assert bybit_executor._bybit_ticker_timestamp_ms({}) is None
    assert bybit_executor._bybit_ticker_timestamp_ms({"info": {}}) is None
    assert bybit_executor._bybit_ticker_timestamp_ms({"datetime": "not-a-date"}) is None


def test_bybit_execute_passes_age_gate_when_info_ts_present(monkeypatch):
    """ccxt tickers without top-level timestamp but with info.ts must pass the age gate."""
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def fetch_ticker(self, _sym):
            return {
                "ask": 50_010.0,
                "bid": 50_000.0,
                "last": 50_005.0,
                "info": {"ts": 1_716_200_000_000},
            }

        def create_market_order(self, _symbol, _side, amount, params=None):
            return {
                "id": "order-info-ts",
                "status": "closed",
                "average": 50_010.0,
                "filled": amount,
                "fee": {"cost": 0.0},
            }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor, "_set_trading_stop", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor.telegram_notify, "notify_trade_opened", lambda **_k: None)
    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_001.0)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": 5.0})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"crypto": None})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": None})

    approval = RiskApproval(True, 0.001, 10.0, 0.5, 0.0, 0.0, "test")
    signal = {
        "pair": "BTC/USDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 50_000.0,
        "sl": 49_500.0,
        "tp1": 51_000.0,
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result["success"] is True
    assert result.get("error") != "BROKER_TICK_TIMESTAMP_MISSING"


def test_bybit_execute_rest_fallback_passes_age_gate(monkeypatch):
    """When ccxt omits timestamp entirely, REST fallback supplies attestable age."""
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def fetch_ticker(self, _sym):
            return {"ask": 50_010.0, "bid": 50_000.0, "last": 50_005.0}

        def create_market_order(self, _symbol, _side, amount, params=None):
            return {
                "id": "order-rest-ts",
                "status": "closed",
                "average": 50_010.0,
                "filled": amount,
                "fee": {"cost": 0.0},
            }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor, "_set_trading_stop", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor.telegram_notify, "notify_trade_opened", lambda **_k: None)
    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_001.0)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": 5.0})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"crypto": None})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": None})
    monkeypatch.setattr(
        bybit_executor,
        "_fetch_bybit_ticker",
        lambda _symbol, category="linear": {
            "symbol": "BTCUSDT",
            "price": 50_005.0,
            "timestamp": 1_716_200_000_000,
        },
    )

    approval = RiskApproval(True, 0.001, 10.0, 0.5, 0.0, 0.0, "test")
    signal = {
        "pair": "BTC/USDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 50_000.0,
        "sl": 49_500.0,
        "tp1": 51_000.0,
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result["success"] is True
    assert result.get("error") != "BROKER_TICK_TIMESTAMP_MISSING"


def test_bybit_max_spread_falls_back_to_crypto_key_when_signal_missing_type(monkeypatch):
    import bybit_executor

    monkeypatch.setitem(
        bybit_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"crypto": 0.0015}
    )
    # signal with no `type` key still routes to crypto bucket
    assert bybit_executor._bybit_max_spread_pct({}) == 0.0015


def test_bybit_spread_pct_uses_ask_bid_mid():
    import bybit_executor

    ticker = {"ask": 67005.0, "bid": 67000.0}
    spread = bybit_executor._bybit_spread_pct(ticker)
    # 5 / 67002.5 ≈ 7.46e-5
    assert spread is not None
    assert abs(spread - 7.4624e-5) < 1e-7


def test_bybit_spread_pct_returns_none_when_either_side_zero():
    import bybit_executor

    assert bybit_executor._bybit_spread_pct({"ask": 0, "bid": 100}) is None
    assert bybit_executor._bybit_spread_pct({"ask": 100, "bid": 0}) is None


def test_bybit_execution_side_price_requires_ask_for_long():
    import bybit_executor

    assert bybit_executor._bybit_execution_side_price({"ask": 101, "bid": 100, "last": 100.5}, "LONG") == 101
    assert bybit_executor._bybit_execution_side_price({"bid": 100, "last": 100.5}, "LONG") is None


def test_bybit_execution_side_price_requires_bid_for_short():
    import bybit_executor

    assert bybit_executor._bybit_execution_side_price({"ask": 101, "bid": 100, "last": 100.5}, "SHORT") == 100
    assert bybit_executor._bybit_execution_side_price({"ask": 101, "last": 100.5}, "SHORT") is None


# ─── Fix #5 (CLI filter logic, no network) ─────────────────────────────────

def test_live_price_diagnostics_source_filter():
    """_matches_source narrows to the requested source set; None passes all."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_lpd",
        Path(__file__).resolve().parents[1] / "tools" / "live_price_diagnostics.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._matches_source({"source": "mt5"}, None) is True
    assert mod._matches_source({"source": "mt5"}, {"mt5"}) is True
    assert mod._matches_source({"source": "binance_ws"}, {"mt5"}) is False
    assert mod._matches_source({}, {"mt5"}) is False


def test_live_price_diagnostics_stale_filter():
    """_passes_stale only returns True when ageSec >= threshold."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_lpd",
        Path(__file__).resolve().parents[1] / "tools" / "live_price_diagnostics.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._passes_stale({"ageSec": 30}, None) is True
    assert mod._passes_stale({"ageSec": 30}, 20) is True
    assert mod._passes_stale({"ageSec": 5}, 20) is False
    assert mod._passes_stale({"ageSec": None}, 20) is False
    assert mod._passes_stale({}, 20) is False
