"""Targeted tests for the Engine D crypto live-ticker / quote-freshness audit fixes.

Covers:
- BUG-1: BinanceLivePriceWS now writes ``source: "binance_ws"``.
- BUG-2: BinanceLivePriceWS now writes optional bid/ask from `b`/`a`.
- BUG-3: bybit_get_symbol_info returns real bid/ask/spread (not last==bid==ask).
- BUG-4: EODHD WS producer normalizes `t` (ms) → seconds at the write site.
- GAP-5: useLivePrices hook surface (TypeScript — covered by static type
  contract; here we assert the API payload still carries the fields the hook
  expects so the contract holds).
- GAP-6: bybit_execute fails-closed when ticker.timestamp is missing AND the
  MAX_BROKER_TICK_AGE_SEC gate is enabled.
- GAP-7: MAX_SIGNAL_DRIFT_PCT helper + `_provider_drift_pct` math.
"""

import importlib.util
import json
from pathlib import Path

# Path to candle_feeds.py for source-level contract checks. The module itself
# imports an optional EODHD client that may not be installed in this env, so
# tests that only need to verify the patched source read the file directly.
_CANDLE_FEEDS_SRC = (Path(__file__).resolve().parents[1] / "candle_feeds.py").read_text(encoding="utf-8")


# ── BUG-1 + BUG-2: BinanceLivePriceWS schema ────────────────────────────────

def test_binance_live_price_ws_writes_source_field():
    """The patched handler must include ``source: "binance_ws"`` in every entry."""
    assert '"source": "binance_ws"' in _CANDLE_FEEDS_SRC, "BUG-1 regression: source field not written"


def test_binance_live_price_ws_writes_bid_ask_when_available():
    """The patched handler must extract `b`/`a` fields from the ticker payload."""
    assert 'ticker.get("b"' in _CANDLE_FEEDS_SRC, "BUG-2 regression: bid field not extracted"
    assert 'ticker.get("a"' in _CANDLE_FEEDS_SRC, "BUG-2 regression: ask field not extracted"


# ── BUG-3: bybit_get_symbol_info bid/ask/spread ────────────────────────────

def test_bybit_get_symbol_info_returns_real_bid_ask_spread(monkeypatch):
    """When bid/ask are present in the ticker, symbol_info must surface them."""
    import bybit_executor

    class _StubExchange:
        def load_markets(self):
            return None

        def market(self, _sym):
            return {
                "precision": {"price": 0.01, "amount": 0.001},
                "limits": {"amount": {"min": 0.001, "max": 1_000_000}},
                "baseId": "BTCUSDT",
            }

        def fetch_ticker(self, _sym):
            return {"last": 50_005.0, "bid": 50_000.0, "ask": 50_010.0, "timestamp": 0}

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    info = bybit_executor.bybit_get_symbol_info("BTC/USDT")
    assert info.get("error") is False
    assert info["bid"] == 50_000.0
    assert info["ask"] == 50_010.0
    assert abs(info["spread"] - 10.0) < 1e-9


def test_bybit_get_symbol_info_falls_back_to_last_when_bid_ask_missing(monkeypatch):
    """If bid/ask are zero/missing the fallback should still return non-zero values."""
    import bybit_executor

    class _StubExchange:
        def load_markets(self):
            return None

        def market(self, _sym):
            return {
                "precision": {"price": 0.01, "amount": 0.001},
                "limits": {"amount": {"min": 0.001, "max": 1_000_000}},
                "baseId": "BTCUSDT",
            }

        def fetch_ticker(self, _sym):
            return {"last": 50_005.0, "bid": 0, "ask": 0}

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    info = bybit_executor.bybit_get_symbol_info("BTC/USDT")
    # Both sides fall back to last; spread reports as 0 (the legacy behaviour).
    assert info["bid"] == 50_005.0
    assert info["ask"] == 50_005.0
    assert info["spread"] == 0.0


# ── BUG-4: EODHD WS producer ts normalize ───────────────────────────────────

def test_eodhd_ws_producer_normalizes_ms_to_seconds_at_write():
    """The candle_feeds.py source for the EODHD WS writer must convert `t` (ms) to seconds."""
    assert '_t_val / 1000.0' in _CANDLE_FEEDS_SRC, "BUG-4 regression: EODHD WS ts no longer normalized"
    assert '_t_val > 1e12' in _CANDLE_FEEDS_SRC, "BUG-4 regression: magnitude guard removed"


# ── GAP-5: API contract still includes the fields useLivePrices reads ──────

def test_api_prices_contract_includes_age_sec_and_source_fields(monkeypatch):
    """Smoke: /api/prices decorates every dict entry with ageSec.
    GAP-5 surfaces these through the React hook; the API contract must hold.
    """
    import threading
    import time as _time
    from athena_app.api import routes_market_data

    # Inject a stub live_prices snapshot that includes a `source` field — the
    # API must not strip it.
    stub = {
        "BTC/USDT": {"price": 50_000.0, "ts": _time.time(), "source": "binance_ws"},
        "EUR/USD": {"price": 1.1234, "bid": 1.1232, "ask": 1.1236, "ts": _time.time(), "source": "mt5"},
    }
    monkeypatch.setattr(routes_market_data, "_live_prices", stub)
    monkeypatch.setattr(routes_market_data, "_live_prices_lock", threading.Lock())

    from flask import Flask
    app = Flask(__name__)
    app.add_url_rule("/api/prices", "api_prices", routes_market_data.api_prices)
    with app.test_client() as c:
        resp = c.get("/api/prices")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    prices = payload["prices"]
    for entry in prices.values():
        assert "ageSec" in entry, "GAP-5 contract regression: ageSec missing on /api/prices entries"
    # `source` must be preserved verbatim.
    assert prices["BTC/USDT"]["source"] == "binance_ws"
    assert prices["EUR/USD"]["source"] == "mt5"


# ── GAP-6: fail-closed on missing tick timestamp ────────────────────────────

def test_bybit_max_signal_drift_pct_default_disabled(monkeypatch):
    import bybit_executor

    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": None})
    assert bybit_executor._bybit_max_signal_drift_pct({"type": "crypto"}) is None


def test_bybit_max_signal_drift_pct_returns_configured_value(monkeypatch):
    import bybit_executor

    monkeypatch.setitem(
        bybit_executor.CONFIG,
        "MAX_SIGNAL_DRIFT_PCT",
        {"crypto": 0.005, "forex": None},
    )
    assert bybit_executor._bybit_max_signal_drift_pct({"type": "crypto"}) == 0.005
    assert bybit_executor._bybit_max_signal_drift_pct({"type": "forex"}) is None
    # Signal with no type defaults to crypto bucket (bybit is crypto-only).
    assert bybit_executor._bybit_max_signal_drift_pct({}) == 0.005


def test_bybit_execute_fail_closed_when_ticker_timestamp_missing(monkeypatch):
    """GAP-6: when MAX_BROKER_TICK_AGE_SEC.bybit is enabled and the ticker has
    no timestamp, bybit_execute must reject with BROKER_TICK_TIMESTAMP_MISSING.
    """
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def load_markets(self):
            return None

        def fetch_ticker(self, _sym):
            # No timestamp key.
            return {"ask": 50_010.0, "bid": 50_000.0, "last": 50_005.0}

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor, "_fetch_bybit_ticker", lambda *_a, **_k: None)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": 5.0})

    approval = RiskApproval(
        approved=True,
        volume=0.001,
        risk_amount=10.0,
        risk_pct=0.5,
        portfolio_heat=0.0,
        drawdown_pct=0.0,
        reason="test",
    )
    signal = {
        "pair": "BTC/USDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 50_000.0,
        "sl": 49_500.0,
        "tp1": 51_000.0,
        "engine": "scalp",
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result["success"] is False
    assert result["error"] == "BROKER_TICK_TIMESTAMP_MISSING"
    assert result.get("tickAgeLimitSec") == 5.0


def test_bybit_execute_drift_block_rejects_when_drift_above_limit(monkeypatch):
    """GAP-7: when MAX_SIGNAL_DRIFT_PCT.crypto is enabled and drift exceeds it,
    bybit_execute must reject with SIGNAL_DRIFT_TOO_LARGE and attach providerDrift.
    """
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def load_markets(self):
            return None

        def fetch_ticker(self, _sym):
            # 2% above the signal price on last — drift gate uses last, not ask.
            return {
                "ask": 51_010.0,
                "bid": 50_990.0,
                "last": 51_000.0,
                "timestamp": 1_716_200_000.0 * 1000.0,
            }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_001.0)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": 0.005})
    # Disable the broker-tick gate so we isolate the drift path.
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": None})

    approval = RiskApproval(
        approved=True,
        volume=0.001,
        risk_amount=10.0,
        risk_pct=0.5,
        portfolio_heat=0.0,
        drawdown_pct=0.0,
        reason="test",
    )
    signal = {
        "pair": "BTC/USDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 50_000.0,  # Engine D signal candle-close price
        "sl": 49_500.0,
        "tp1": 51_000.0,
        "engine": "scalp",
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result["success"] is False
    assert "SIGNAL_DRIFT_TOO_LARGE" in result["error"]
    # ask=51000 vs signal 50000 → 2% drift
    assert abs(result["providerDrift"] - 0.02) < 1e-6
    assert result["signalDriftLimitPct"] == 0.005
    assert result["signalPrice"] == 50_000.0
    assert result["brokerPrice"] == 51_010.0
    assert result.get("driftVsLast") == result["providerDrift"]
    assert result.get("driftVsExecutable") is not None


def test_bybit_execute_drift_passes_when_last_near_signal_despite_wide_ask(monkeypatch):
    """AAVE-like case: wide ask premium must not trip drift when last is near signal."""
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def load_markets(self):
            return None

        def fetch_ticker(self, _sym):
            return {
                "ask": 90.22,
                "bid": 89.90,
                "last": 89.50,
                "timestamp": 1_716_200_000.0 * 1000.0,
            }

        def create_market_order(self, *_a, **_kw):
            raise RuntimeError("STUB_ORDER_PATH_REACHED")

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_001.0)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": 0.005})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": None})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"crypto": None})

    approval = RiskApproval(
        approved=True,
        volume=0.1,
        risk_amount=10.0,
        risk_pct=0.5,
        portfolio_heat=0.0,
        drawdown_pct=0.0,
        reason="test",
    )
    signal = {
        "pair": "AAVE/USDT",
        "symbol": "AAVEUSDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 89.08,
        "sl": 88.0,
        "tp1": 91.0,
        "engine": "intraday",
        "candleFetchMeta": {"H1": {"signalFeed": "bybit"}},
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result["success"] is False
    assert "SIGNAL_DRIFT_TOO_LARGE" not in (result.get("error") or "")
    assert "STUB_ORDER_PATH_REACHED" in (result.get("error") or "")


def test_bybit_execute_drift_rejects_when_last_stale_not_just_ask(monkeypatch):
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def load_markets(self):
            return None

        def fetch_ticker(self, _sym):
            return {
                "ask": 90.50,
                "bid": 90.40,
                "last": 91.00,
                "timestamp": 1_716_200_000.0 * 1000.0,
            }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": 0.005})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": None})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"crypto": None})

    approval = RiskApproval(
        approved=True,
        volume=0.1,
        risk_amount=10.0,
        risk_pct=0.5,
        portfolio_heat=0.0,
        drawdown_pct=0.0,
        reason="test",
    )
    signal = {
        "pair": "AAVE/USDT",
        "symbol": "AAVEUSDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 89.08,
        "sl": 88.0,
        "tp1": 91.0,
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result["success"] is False
    assert "SIGNAL_DRIFT_TOO_LARGE" in result["error"]
    assert result.get("driftVsLast") is not None
    assert result.get("driftVsExecutable") is not None
    assert result.get("brokerLast") == 91.00


def test_should_refresh_engine_b_before_execute_for_fresh_crypto() -> None:
    from execution import _should_refresh_engine_b_before_execute

    sig = {"type": "crypto", "price": 89.08}
    assert _should_refresh_engine_b_before_execute(
        sig, sig_age=5, max_age=300, missing_price=False
    )
    assert not _should_refresh_engine_b_before_execute(
        {"type": "forex", "price": 1.1}, sig_age=5, max_age=300, missing_price=False
    )


def test_bybit_execute_drift_disabled_does_not_block(monkeypatch):
    """When MAX_SIGNAL_DRIFT_PCT is null/disabled, bybit_execute must not reject
    on drift — the existing 1% rebase still runs but no hard block fires here.
    The test sends a 0.4% drift signal which is below the 1% rebase threshold,
    so we don't trigger that path either; bybit_execute proceeds to the order
    placement step. We stub create_market_order to short-circuit, then check
    that we got PAST the drift gate (i.e. didn't return SIGNAL_DRIFT_TOO_LARGE).
    """
    import bybit_executor
    from risk_engine import RiskApproval

    class _StubExchange:
        def load_markets(self):
            return None

        def fetch_ticker(self, _sym):
            return {
                "ask": 50_200.0,  # 0.4% drift vs signal 50000
                "bid": 50_190.0,
                "last": 50_195.0,
                "timestamp": 1_716_200_000.0 * 1000.0,
            }

        def create_market_order(self, *_a, **_kw):
            # Short-circuit by raising — we only care that we reached this call.
            raise RuntimeError("STUB_ORDER_PATH_REACHED")

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _StubExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *a, **k: None)
    monkeypatch.setattr(bybit_executor.time, "time", lambda: 1_716_200_001.0)
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_SIGNAL_DRIFT_PCT", {"crypto": None})
    monkeypatch.setitem(bybit_executor.CONFIG, "MAX_BROKER_TICK_AGE_SEC", {"bybit": None})

    approval = RiskApproval(
        approved=True,
        volume=0.001,
        risk_amount=10.0,
        risk_pct=0.5,
        portfolio_heat=0.0,
        drawdown_pct=0.0,
        reason="test",
    )
    signal = {
        "pair": "BTC/USDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 50_000.0,
        "sl": 49_500.0,
        "tp1": 51_000.0,
        "engine": "scalp",
    }
    result = bybit_executor.bybit_execute(signal, approval)
    # We expect to fail at the broker-order step, NOT at the drift gate.
    assert result["success"] is False
    assert "SIGNAL_DRIFT_TOO_LARGE" not in (result.get("error") or "")
    assert "STUB_ORDER_PATH_REACHED" in (result.get("error") or "")


# ── Diagnostic CLI smoke ─────────────────────────────────────────────────────

def _load_diag_module():
    spec = importlib.util.spec_from_file_location(
        "_engine_d_crypto_quote_diag",
        Path(__file__).resolve().parents[1] / "tools" / "engine_d_crypto_quote_diagnostics.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_diagnostic_provider_drift_math():
    mod = _load_diag_module()
    assert mod._provider_drift_pct(100.0, 102.0) == 0.02
    assert mod._provider_drift_pct(100.0, 98.0) == 0.02
    assert mod._provider_drift_pct(0, 100.0) is None
    assert mod._provider_drift_pct(100.0, None) is None


def test_diagnostic_spread_math():
    mod = _load_diag_module()
    assert mod._spread_pct(100.0, 102.0) is not None
    spread = mod._spread_pct(100.0, 102.0)
    # mid=101, spread=2, 2/101 ≈ 0.0198
    assert abs(spread - (2.0 / 101.0)) < 1e-9
    assert mod._spread_pct(0, 100.0) is None
    assert mod._spread_pct(102.0, 100.0) is None  # ask < bid


def test_diagnostic_age_seconds():
    mod = _load_diag_module()
    now_s = 1_716_200_100.0
    assert mod._age_seconds(1_716_200_000.0, now_s) == 100.0
    assert mod._age_seconds(None, now_s) is None
    assert mod._age_seconds(0, now_s) is None


def test_diagnostic_diagnose_symbol_reports_missing_when_no_providers(monkeypatch):
    mod = _load_diag_module()
    monkeypatch.setattr(mod, "_fetch_binance_ticker", lambda *a, **k: {"_error": "net_down"})
    monkeypatch.setattr(mod, "_fetch_bybit_ticker", lambda *a, **k: {"_error": "net_down"})
    row = mod._diagnose_symbol("BTC/USDT", timeout_s=1.0, now_s=0.0, max_drift_pct=0.005)
    assert row["executable"] is False
    assert "binance_missing" in row["reason"]
    assert "bybit_missing" in row["reason"]


def test_diagnostic_diagnose_symbol_flags_drift_over_limit(monkeypatch):
    mod = _load_diag_module()
    monkeypatch.setattr(
        mod,
        "_fetch_binance_ticker",
        lambda *a, **k: {"last": 100.0, "bid": 100.0, "ask": 100.0, "ts": 0.0},
    )
    monkeypatch.setattr(
        mod,
        "_fetch_bybit_ticker",
        lambda *a, **k: {"last": 102.0, "bid": 102.0, "ask": 102.0, "ts": 0.0},
    )
    row = mod._diagnose_symbol("BTC/USDT", timeout_s=1.0, now_s=0.0, max_drift_pct=0.005)
    assert row["executable"] is False
    assert "drift_above" in row["reason"]
    assert row["provider_drift_pct"] is not None
    assert row["provider_drift_pct"] > 0.005
