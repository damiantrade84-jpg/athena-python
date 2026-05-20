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


def test_evaluate_live_quote_age_fresh_when_under_threshold():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}}
    result = evaluate_live_quote_age({"type": "forex"}, 10.0, config)
    assert result["enabled"] is True
    assert result["stale"] is False
    assert result["reason"] == "FRESH"


def test_evaluate_live_quote_age_unknown_age_does_not_block():
    from athena_app.services.data_freshness import evaluate_live_quote_age

    config = {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 30}}
    result = evaluate_live_quote_age({"type": "forex"}, None, config)
    assert result["enabled"] is True
    assert result["stale"] is False
    assert result["reason"] == "AGE_UNKNOWN"


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
