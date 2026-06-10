from __future__ import annotations

import sys
import types
from types import SimpleNamespace
import threading
import time

import pytest

if "eodhd" not in sys.modules:
    eodhd_stub = types.ModuleType("eodhd")
    eodhd_stub.APIClient = object
    sys.modules["eodhd"] = eodhd_stub
from flask import Flask

from athena_app.api.routes_market_data import register_market_data_routes


class _Log:
    def error(self, *_args, **_kwargs):
        pass


def _runtime(**overrides):
    pair = {
        "symbol": "BTCUSDT",
        "display": "BTC/USDT",
        "type": "crypto",
        "source": "binance",
        "enabled": True,
    }
    jse_pair = {
        "symbol": "NPN.JSE",
        "display": "Naspers",
        "type": "stock",
        "source": "eodhd",
        "enabled": True,
    }
    runtime = SimpleNamespace(
        CONFIG={},
        ALL_PAIRS=[pair, jse_pair],
        ACTIVE_PAIRS=[pair],
        ETF_PAIRS=[],
        JSE_PAIRS=[jse_pair],
        disabled_pairs=set(),
        live_prices={"BTC/USDT": {"price": 100.0}},
        live_prices_lock=threading.Lock(),
        fetch_yield_curve=lambda: {"success": True, "spread": 1.25},
        http_requests=None,
        fetch_candles=lambda _pair, _tf, _limit: [
            {
                "time": "2026-05-05T10:00:00",
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 99,
            }
        ],
        fetch_eodhd=lambda *_args, **_kwargs: [],
        extract_candles=lambda candles: candles,
        merge_forex_forming_ws=lambda candles, *_args, **_kwargs: (candles, None),
        resample_from_h1=lambda *_args, **_kwargs: [],
        forex_h4_resample_offset_hours=lambda: 2.0,
        eodhd_ticker_for_pair=lambda p: p.get("symbol"),
        json_safe=lambda value: value,
        log=_Log(),
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def _client(runtime):
    app = Flask(__name__)
    register_market_data_routes(app, runtime)
    return app.test_client()


def test_market_data_routes_register_expected_methods():
    app = Flask(__name__)
    register_market_data_routes(app, _runtime())

    methods_by_path = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert "GET" in methods_by_path["/api/market-hours"]
    assert "GET" in methods_by_path["/api/prices"]
    assert "GET" in methods_by_path["/api/candles"]
    assert "GET" in methods_by_path["/api/engine-b-overlays"]
    assert "GET" in methods_by_path["/api/chart-tick"]
    assert "POST" in methods_by_path["/api/news-sentiment"]


def test_prices_and_pairs_are_served_from_runtime_state():
    client = _client(_runtime())

    prices = client.get("/api/prices")
    pairs = client.get("/api/pairs")

    assert prices.status_code == 200
    assert prices.get_json()["prices"]["BTC/USDT"]["price"] == 100.0
    assert pairs.status_code == 200
    data = pairs.get_json()
    assert data["total"] == 1
    assert data["active"] == 1
    assert data["groups"]["Crypto"][0]["sym"] == "BTCUSDT"


def test_prices_exposes_live_price_source_priority_diagnostics(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    client = _client(
        _runtime(
            live_prices={
                "BTC/USDT": {
                    "price": 100.0,
                    "ts": 999.0,
                    "source": "binance_ws",
                    "last_ws_ts": 999.0,
                    "last_rest_ts": 998.0,
                    "preferred_source": "binance_ws",
                    "overwrite_reason": "fresh_ws_preferred_over_rest",
                }
            }
        )
    )

    resp = client.get("/api/prices")

    assert resp.status_code == 200
    entry = resp.get_json()["prices"]["BTC/USDT"]
    assert entry["source"] == "binance_ws"
    assert entry["preferred_source"] == "binance_ws"
    assert entry["overwrite_reason"] == "fresh_ws_preferred_over_rest"
    assert entry["ageSec"] == pytest.approx(1.0)
    assert entry["ws_age_sec"] == pytest.approx(1.0)
    assert entry["rest_age_sec"] == pytest.approx(2.0)


def test_yield_curve_and_candles_use_runtime_fetchers():
    client = _client(_runtime(CONFIG={"CRYPTO_EXECUTION_PROVIDER": "binance"}))

    yc = client.get("/api/yield-curve")
    candles = client.get("/api/candles?symbol=BTCUSDT&tf=H4")

    assert yc.status_code == 200
    assert yc.get_json()["spread"] == 1.25
    assert candles.status_code == 200
    payload = candles.get_json()
    assert payload["symbol"] == "BTCUSDT"
    assert payload["candles"][0]["t"] == "2026-05-05T10:00:00Z"
    assert payload["candles"][0]["v"] == 99.0
    pp = payload["price_precision"]
    assert pp["score_group"] == "crypto_btc"
    assert pp["precision"] == 1
    assert pp["min_move"] == pytest.approx(0.1)


def test_engine_b_overlays_preserve_legacy_structure_contract():
    def _compute_naked_analysis(signal, **_kwargs):
        assert signal["symbol"] == "BTCUSDT"
        assert signal["direction"] == "SHORT"
        return (
            {
                "nearest_support_zone": {"lower": 95.0, "upper": 97.0},
                "nearest_resistance_zone": {"lower": 104.0, "upper": 106.0},
                "bos_data": {"last_broken_high": 103.5, "last_broken_low": 96.5},
                "choch_data": {"choch_level": 101.25},
                "order_blocks": [
                    {"type": "bearish", "top": 105.0, "bottom": 104.2, "strength": 72, "mitigated": False},
                    {"type": "bullish", "top": 97.8, "bottom": 97.1, "strength": 61, "mitigated": False},
                    {"type": "bullish", "top": 96.8, "bottom": 96.2, "strength": 50, "mitigated": False},
                ],
                "active_fvgs": [
                    {"type": "bearish", "top": 103.0, "bottom": 102.4, "mitigated": False},
                    {"type": "bullish", "top": 98.9, "bottom": 98.3, "mitigated": False},
                    {"type": "bullish", "top": 98.0, "bottom": 97.7, "mitigated": True},
                ],
                "breaker_block": {"type": "bearish_breaker", "level": 101.25},
                "current_swing_sequence": "LH_LL",
                "macro_swing_sequence": "LH_LL",
                "bos_confirmed": True,
                "choch_confirmed": False,
                "struct_atr": 2.5,
            },
            {"display": "BTC/USDT"},
            None,
        )

    client = _client(
        _runtime(
            CONFIG={"CRYPTO_EXECUTION_PROVIDER": "binance"},
            compute_naked_analysis=_compute_naked_analysis,
        )
    )

    resp = client.get("/api/engine-b-overlays?symbol=BTCUSDT&tf=M1&direction=SHORT&style=scalp")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["overlay_source"] == "engine_b"
    assert payload["overlay_version"]
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "M1"
    assert payload["confirmed_only"] is not None
    assert payload["nearest_support_zone"]["lower"] == 95.0
    assert payload["bos_data"]["last_broken_high"] == 103.5
    assert payload["choch_data"]["choch_level"] == 101.25
    assert len(payload["order_blocks"]) == 2
    assert len(payload["active_fvgs"]) == 2
    assert all(not fvg.get("mitigated") for fvg in payload["active_fvgs"])
    assert payload["breaker_block"]["level"] == 101.25
    assert payload["struct_atr"] == pytest.approx(2.5)
    pp = payload["price_precision"]
    assert pp["score_group"] == "crypto_btc"
    assert pp["precision"] == 1
    assert pp["min_move"] == pytest.approx(0.1)


def test_crypto_chart_provider_resolves_to_bybit_when_execution_provider_bybit():
    bybit_calls = []

    def _bybit_klines(symbol, tf, limit):
        bybit_calls.append((symbol, tf, limit))
        return [
            {
                "open_time": 1_779_300_000_000,
                "time": "2026-05-20T20:00:00+00:00",
                "open": 0.284,
                "high": 0.291,
                "low": 0.281,
                "close": 0.289,
                "volume": 1000.0,
                "vol": 1000.0,
                "turnover": 289.0,
                "confirmed": True,
                "provider": "bybit",
                "category": "linear",
            },
            {
                "open_time": 1_779_314_400_000,
                "time": "2026-05-21T00:00:00+00:00",
                "open": 0.289,
                "high": 0.295,
                "low": 0.286,
                "close": 0.293,
                "volume": 1200.0,
                "vol": 1200.0,
                "turnover": 351.6,
                "confirmed": True,
                "provider": "bybit",
                "category": "linear",
            },
        ]

    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
            },
            ALL_PAIRS=[
                {
                    "symbol": "TRXUSDT",
                    "display": "TRX/USDT",
                    "type": "crypto",
                    "source": "binance",
                    "enabled": True,
                }
            ],
            ACTIVE_PAIRS=[],
            fetch_candles=lambda *_args, **_kwargs: pytest.fail("crypto chart should not use generic Binance candles"),
            fetch_bybit_klines=_bybit_klines,
            fetch_bybit_ticker=lambda symbol, category="linear": {
                "symbol": symbol,
                "price": 0.2935,
                "bid": 0.2934,
                "ask": 0.2936,
                "timestamp": 1_779_328_800_000,
                "provider": "bybit",
                "source": "bybit_rest",
                "category": category,
            },
        )
    )

    resp = client.get("/api/candles?symbol=TRX/USDT&tf=H4&limit=1000")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert bybit_calls == [("TRXUSDT", "H4", 1000)]
    assert payload["asset_group"] == "crypto"
    assert payload["execution_provider"] == "bybit"
    assert payload["chart_provider"] == "bybit"
    assert payload["candle_provider"] == "bybit"
    assert payload["volume_provider"] == "bybit"
    assert payload["turnover_provider"] == "bybit"
    assert payload["atr_provider"] == "bybit"
    assert payload["indicator_provider"] == "bybit"
    assert payload["vwap_provider"] == "bybit"
    assert payload["scoring_provider"] == "bybit"
    assert payload["provider_mismatch"] is False
    assert payload["chart_status"] == "execution_grade"
    assert payload["live_tick_provider"] == "bybit_rest"
    assert payload["live_tick_source"] == "bybit_rest"
    assert payload["fallback_used"] is True
    assert payload["fallback_reason"] == "ws_missing"
    assert payload["bybit_symbol"] == "TRXUSDT"
    assert payload["bybit_category"] == "linear"
    assert payload["liveTick"]["provider"] == "bybit_rest"
    assert payload["liveTick"]["source"] == "bybit_rest"


def test_crypto_chart_uses_bybit_ws_when_cache_fresh():
    import candle_feeds

    now = time.time()
    with candle_feeds._live_prices_lock:
        candle_feeds._live_prices["TRX/USDT"] = candle_feeds._merge_bybit_ws_price(
            None,
            0.294,
            0.2939,
            0.2941,
            now,
            bybit_symbol="TRXUSDT",
            category="linear",
        )

    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
            },
            ALL_PAIRS=[
                {
                    "symbol": "TRXUSDT",
                    "display": "TRX/USDT",
                    "type": "crypto",
                    "source": "binance",
                    "enabled": True,
                }
            ],
            fetch_bybit_klines=lambda *_args, **_kwargs: [
                {
                    "open_time": 1_779_300_000_000,
                    "time": "2026-05-20T20:00:00+00:00",
                    "open": 0.284,
                    "high": 0.291,
                    "low": 0.281,
                    "close": 0.289,
                    "volume": 1000.0,
                    "vol": 1000.0,
                    "turnover": 289.0,
                    "confirmed": True,
                    "provider": "bybit",
                    "category": "linear",
                }
            ],
            fetch_bybit_ticker=lambda *_args, **_kwargs: pytest.fail("REST should not run when WS cache is fresh"),
        )
    )

    resp = client.get("/api/candles?symbol=TRX/USDT&tf=H4&limit=100")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["live_tick_provider"] == "bybit_ws"
    assert payload["fallback_used"] is False
    assert payload["fallback_reason"] is None
    assert payload["liveTick"]["source"] == "bybit_ws"


def test_crypto_chart_tick_endpoint_uses_bybit_live_tick_provider():
    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
            },
            fetch_bybit_ticker=lambda symbol, category="linear": {
                "symbol": symbol,
                "price": 100.5,
                "bid": 100.4,
                "ask": 100.6,
                "timestamp": 1_779_328_800_000,
                "provider": "bybit",
                "source": "bybit_rest",
                "category": category,
            },
        )
    )

    resp = client.get("/api/chart-tick?symbol=BTC/USDT")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["asset_group"] == "crypto"
    assert payload["execution_provider"] == "bybit"
    assert payload["live_tick_provider"] == "bybit_rest"
    assert payload["provider_mismatch"] is False
    assert payload["provider"] == "bybit_rest"
    assert payload["source"] == "bybit_rest"
    assert payload["fallback_used"] is True
    assert payload["fallback_reason"] == "ws_missing"
    assert payload["price"] == 100.5
    assert payload["liveTick"]["provider"] == "bybit_rest"
    assert payload["liveTick"]["source"] == "bybit_rest"


def test_crypto_chart_payload_includes_volume_ma_vwap_and_same_provider_indicators():
    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
            },
            fetch_bybit_klines=lambda *_args, **_kwargs: [
                {
                    "open_time": 1_779_300_000_000 + idx * 14_400_000,
                    "time": f"2026-05-{20 + idx // 6:02d}T{(idx % 6) * 4:02d}:00:00+00:00",
                    "open": 100 + idx,
                    "high": 102 + idx,
                    "low": 99 + idx,
                    "close": 101 + idx,
                    "volume": 1000 + idx,
                    "vol": 1000 + idx,
                    "turnover": (1000 + idx) * (101 + idx),
                    "provider": "bybit",
                    "category": "linear",
                    "confirmed": True,
                }
                for idx in range(40)
            ],
            fetch_bybit_ticker=lambda symbol, category="linear": {
                "symbol": symbol,
                "price": 141.0,
                "timestamp": 1_779_876_000_000,
                "provider": "bybit",
                "source": "bybit_rest",
                "category": category,
            },
        )
    )

    resp = client.get("/api/candles?symbol=BTCUSDT&tf=H4&limit=40")

    assert resp.status_code == 200
    payload = resp.get_json()
    last = payload["candles"][-1]
    assert last["provider"] == "bybit"
    assert last["volume"] == last["v"]
    assert last["turnover"] > 0
    assert last["confirmed"] is True
    assert last["volume_ma"] is not None
    assert last["vwap"] is not None
    # BTCUSDT (crypto_btc) draws per-group EMA periods (trend 18, momentum 40);
    # the legacy ema21/ema50 aliases are only emitted when the period is 21/50.
    assert last["ema_trend"] is not None
    assert "ema21" not in last
    assert last["ema_momentum"] is not None  # 40 candles == momentum period 40
    assert "ema50" not in last
    assert last["rsi14"] is not None
    assert last["adx14"] is not None
    assert last["atr14"] is not None
    assert payload["indicator_source_candle_provider"] == payload["candle_provider"] == "bybit"
    assert payload["vwap_provider"] == "bybit"
    assert payload["scoring_provider"] == "bybit"
    assert (
        payload["vwap_formula"]
        == "session_anchored_utc_day:cumulative_turnover_divided_by_cumulative_volume"
    )


def test_crypto_chart_scoring_provider_mismatch_when_engine_a_feed_binance():
    trx_pair = {
        "symbol": "TRXUSDT",
        "display": "TRX/USDT",
        "type": "crypto",
        "source": "binance",
        "enabled": True,
    }
    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "binance",
                "ENGINE_AB_CRYPTO_SIGNAL_FEED": "binance",
            },
            ALL_PAIRS=[trx_pair],
            ACTIVE_PAIRS=[trx_pair],
            fetch_bybit_klines=lambda *_args, **_kwargs: [
                {
                    "open_time": 1_779_300_000_000,
                    "time": "2026-05-20T20:00:00+00:00",
                    "open": 0.284,
                    "high": 0.291,
                    "low": 0.281,
                    "close": 0.289,
                    "volume": 1000.0,
                    "vol": 1000.0,
                    "turnover": 289.0,
                    "confirmed": True,
                    "provider": "bybit",
                    "category": "linear",
                }
            ],
            fetch_bybit_ticker=lambda symbol, category="linear": {
                "symbol": symbol,
                "price": 0.29,
                "timestamp": 1_779_328_800_000,
                "provider": "bybit",
                "source": "bybit_rest",
                "category": category,
            },
        )
    )

    resp = client.get("/api/candles?symbol=TRX/USDT&tf=H4&limit=10")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["candle_provider"] == "bybit"
    assert payload["scoring_provider"] == "binance"
    assert payload["provider_mismatch"] is True
    assert "scoring_feed_binance_chart_feed_bybit" in payload["provider_mismatch_reason"]


def test_crypto_chart_vwap_uses_typical_price_when_turnover_missing():
    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
            },
            fetch_bybit_klines=lambda *_args, **_kwargs: [
                {
                    "open_time": 1_779_300_000_000 + idx * 14_400_000,
                    "time": f"2026-05-{20 + idx:02d}T00:00:00+00:00",
                    "open": 100 + idx,
                    "high": 102 + idx,
                    "low": 99 + idx,
                    "close": 101 + idx,
                    "volume": 1000 + idx,
                    "vol": 1000 + idx,
                    "turnover": 0.0,
                    "confirmed": True,
                    "provider": "bybit",
                    "category": "linear",
                }
                for idx in range(30)
            ],
            fetch_bybit_ticker=lambda symbol, category="linear": {
                "symbol": symbol,
                "price": 130.0,
                "timestamp": 1_779_876_000_000,
                "provider": "bybit",
                "source": "bybit_rest",
                "category": category,
            },
        )
    )

    resp = client.get("/api/candles?symbol=BTCUSDT&tf=H4&limit=30")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert (
        payload["vwap_formula"]
        == "session_anchored_utc_day:cumulative_typical_price_times_volume_divided_by_cumulative_volume"
    )


def test_crypto_chart_binance_fallback_is_visible_when_bybit_unavailable():
    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "CRYPTO_CHART_BYBIT_FALLBACK_ENABLED": True,
            },
            fetch_bybit_klines=lambda *_args, **_kwargs: None,
            fetch_candles=lambda _pair, _tf, _limit: [
                {
                    "time": "2026-05-21T00:00:00Z",
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                    "volume": 50.0,
                }
            ],
        )
    )

    resp = client.get("/api/candles?symbol=BTC/USDT&tf=H4&limit=1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["chart_provider"] == "binance"
    assert payload["candle_provider"] == "binance"
    assert payload["provider_mismatch"] is True
    assert payload["provider_mismatch_reason"] == "crypto_execution_provider_bybit_chart_fell_back_to_binance"
    assert payload["fallback_used"] is True
    assert payload["fallback_chain"] == ["bybit", "binance"]
    assert payload["chart_status"] == "non_execution_grade"


def test_forex_chart_behavior_remains_on_shared_fetch_path():
    forex_pair = {
        "symbol": "EURUSD=X",
        "display": "EUR/USD",
        "type": "forex",
        "source": "mt5",
        "enabled": True,
    }
    bybit_called = False

    def _bybit_klines(*_args, **_kwargs):
        nonlocal bybit_called
        bybit_called = True
        return None

    client = _client(
        _runtime(
            CONFIG={
                "CRYPTO_EXECUTION_PROVIDER": "bybit",
                "ENGINE_A_CRYPTO_SIGNAL_FEED": "bybit",
            },
            ALL_PAIRS=[forex_pair],
            ACTIVE_PAIRS=[forex_pair],
            fetch_bybit_klines=_bybit_klines,
            fetch_candles=lambda _pair, _tf, _limit: [
                {
                    "time": "2026-05-21T08:00:00Z",
                    "open": 1.1000,
                    "high": 1.1020,
                    "low": 1.0980,
                    "close": 1.1010,
                    "vol": 100.0,
                }
            ],
        )
    )

    resp = client.get("/api/candles?symbol=EUR/USD&tf=H4&limit=300")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["pairType"] == "forex"
    assert payload["candlesSource"] == "shared"
    assert payload["asset_group"] == "forex"
    assert payload["chart_provider"] == "mt5"
    assert payload["candle_provider"] == "mt5"
    assert payload["live_tick_provider"] == "mt5"
    assert payload["candle_confirmed_policy"] == "confirmed-only"
    assert payload["last_candle_ts"] == "2026-05-21T08:00:00Z"
    assert bybit_called is False


def test_candles_accepts_tradingview_style_forex_alias():
    forex_pair = {
        "symbol": "EURUSD=X",
        "display": "EUR/USD",
        "type": "forex",
        "source": "mt5",
        "enabled": True,
    }

    def _fetch_candles(pair, tf, limit):
        assert pair == forex_pair
        return [
            {
                "time": "2026-05-21T08:00:00Z",
                "open": 1.1000,
                "high": 1.1020,
                "low": 1.0980,
                "close": 1.1010,
                "vol": 100.0,
            }
        ]

    client = _client(
        _runtime(
            ALL_PAIRS=[forex_pair],
            ACTIVE_PAIRS=[forex_pair],
            fetch_candles=_fetch_candles,
        )
    )

    resp = client.get("/api/candles?symbol=EURUSD&tf=H4&limit=300")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["symbol"] == "EURUSD"
    assert payload["display"] == "EUR/USD"
    assert payload["candles"][0]["o"] == pytest.approx(1.1000)
    assert payload["price_precision"]["score_group"] == "forex_majors"
    assert payload["price_precision"]["precision"] == 5


def test_candles_jpy_forex_precision_override():
    jpy_pair = {
        "symbol": "GBPJPY=X",
        "display": "GBP/JPY",
        "type": "forex",
        "source": "mt5",
        "enabled": True,
    }
    client = _client(
        _runtime(
            ALL_PAIRS=[jpy_pair],
            ACTIVE_PAIRS=[jpy_pair],
        )
    )
    resp = client.get("/api/candles?symbol=GBP/JPY&tf=H4&limit=10")
    assert resp.status_code == 200
    pp = resp.get_json()["price_precision"]
    assert pp["score_group"] == "forex_crosses"
    assert pp["precision"] == 3
    assert pp["min_move"] == pytest.approx(0.001)


def test_candles_keeps_mt5_ohlc_isolated_from_forming_ws_merge():
    mt5_pair = {
        "symbol": "EURUSD=X",
        "display": "EUR/USD",
        "type": "forex",
        "source": "mt5",
        "enabled": True,
    }

    def _fetch_candles(pair, tf, limit):
        assert pair == mt5_pair
        assert tf == "H4"
        assert limit == 300
        return [
            {
                "time": "2026-05-21T08:00:00Z",
                "open": 1.1000,
                "high": 1.1020,
                "low": 1.0980,
                "close": 1.1010,
                "vol": 100.0,
            }
        ]

    def _wrong_source_merge(candles, *_args, **_kwargs):
        return [
            {
                "time": "2026-05-21T08:00:00Z",
                "open": 9.0,
                "high": 9.0,
                "low": 9.0,
                "close": 9.0,
                "vol": 9.0,
            }
        ], "replaced"

    client = _client(
        _runtime(
            ALL_PAIRS=[mt5_pair],
            ACTIVE_PAIRS=[mt5_pair],
            fetch_candles=_fetch_candles,
            merge_forex_forming_ws=_wrong_source_merge,
        )
    )

    resp = client.get("/api/candles?symbol=EUR/USD&tf=H4&limit=300")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["candlesSource"] == "shared"
    assert payload["candles"][0]["o"] == pytest.approx(1.1000)
    assert payload["candles"][0]["h"] == pytest.approx(1.1020)
    assert payload["candles"][0]["l"] == pytest.approx(1.0980)
    assert payload["candles"][0]["c"] == pytest.approx(1.1010)


def test_bulk_prices_uses_runtime_http_client(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return [{"code": "SPY.US", "close": 500, "volume": 123}]

    http = SimpleNamespace(get=lambda *_args, **_kwargs: _Resp())
    monkeypatch.setenv("EODHD_KEY", "test-key")
    client = _client(_runtime(http_requests=http))

    resp = client.get("/api/bulk-prices?symbols=SPY.US,QQQ.US")

    assert resp.status_code == 200
    assert resp.get_json()["prices"]["SPY.US"]["price"] == 500


def test_market_hours_exposes_timezone_offsets_for_prime_windows():
    """Prime windows use GMT+2 anchor; MT5 broker offset explains the +1h chart gap."""
    client = _client(
        _runtime(
            CONFIG={
                "SERVER_TZ_OFFSET_HOURS": 2,
                "MT5_BROKER_UTC_OFFSET": 3,
            }
        )
    )

    resp = client.get("/api/market-hours")
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["windowAnchorUtcOffsetHours"] == 2
    assert data["mt5BrokerUtcOffset"] == 3
    assert "GMT+2" in data["localTime"]
