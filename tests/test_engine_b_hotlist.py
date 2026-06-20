from __future__ import annotations

from athena_app.services.engine_b_hotlist import build_engine_b_hotlist


def _pair(symbol: str, display: str, pair_type: str, source: str = "mt5") -> dict:
    return {
        "symbol": symbol,
        "display": display,
        "type": pair_type,
        "source": source,
        "enabled": True,
    }


def test_hotlist_ranks_strongest_candidate_per_group():
    pairs = [
        _pair("EURUSD=X", "EUR/USD", "forex"),
        _pair("GBPUSD=X", "GBP/USD", "forex"),
        _pair("BTCUSDT", "BTC/USDT", "crypto", source="binance"),
        _pair("ETHUSDT", "ETH/USDT", "crypto", source="binance"),
    ]
    live_prices = {
        "EUR/USD": {"price": 1.101, "bid": 1.1009, "ask": 1.1011, "ts": 1_000.0, "change_pct": 0.55},
        "GBP/USD": {"price": 1.25, "bid": 1.2499, "ask": 1.2502, "ts": 820.0, "change_pct": 0.02},
        "BTC/USDT": {"price": 68_000.0, "bid": 67_995.0, "ask": 68_005.0, "ts": 1_000.0, "change_pct": 3.2},
        "ETH/USDT": {"price": 3_500.0, "bid": 3_499.0, "ask": 3_501.0, "ts": 970.0, "change_pct": 0.5},
    }

    def fetch_candles(pair, _tf, _limit):
        if pair["display"] == "EUR/USD":
            return [
                {"time": "2026-05-21T00:00:00Z", "open": 1.09, "high": 1.091, "low": 1.0895, "close": 1.0905},
                {"time": "2026-05-21T01:00:00Z", "open": 1.0905, "high": 1.094, "low": 1.09, "close": 1.0938},
                {"time": "2026-05-21T02:00:00Z", "open": 1.0938, "high": 1.1012, "low": 1.093, "close": 1.101},
            ]
        if pair["display"] == "BTC/USDT":
            return [
                {"time": "2026-05-21T00:00:00Z", "open": 66_000.0, "high": 66_400.0, "low": 65_900.0, "close": 66_300.0},
                {"time": "2026-05-21T01:00:00Z", "open": 66_300.0, "high": 67_000.0, "low": 66_200.0, "close": 66_950.0},
                {"time": "2026-05-21T02:00:00Z", "open": 66_950.0, "high": 68_100.0, "low": 66_900.0, "close": 68_000.0},
            ]
        return [
            {"time": "2026-05-21T00:00:00Z", "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0},
            {"time": "2026-05-21T01:00:00Z", "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0},
            {"time": "2026-05-21T02:00:00Z", "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0},
        ]

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["forex", "crypto"],
        top_per_group=2,
        timeframe="H1",
        time_now=1_000.0,
    )

    assert payload["groups"]["forex"]["winner"]["symbol"] == "EURUSD"
    assert payload["groups"]["crypto"]["winner"]["symbol"] == "BTCUSDT"
    assert payload["groups"]["forex"]["candidates"][0]["rank"] == 1
    assert payload["groups"]["crypto"]["candidates"][0]["strength_score"] >= payload["groups"]["crypto"]["candidates"][1]["strength_score"]


def test_hotlist_handles_missing_live_prices_and_candles_without_crashing():
    pairs = [
        _pair("XAUUSD", "XAUUSD", "commodity"),
        _pair("NAS100", "NAS100", "index"),
    ]

    def fetch_candles(pair, _tf, _limit):
        if pair["symbol"] == "XAUUSD":
            raise RuntimeError("provider unavailable")
        return []

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices={},
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["commodity", "index"],
        top_per_group=1,
        timeframe="H1",
        time_now=1_000.0,
    )

    assert payload["success"] is True
    assert payload["groups"]["commodity"]["winner"]["symbol"] == "XAUUSD"
    assert payload["groups"]["index"]["winner"]["symbol"] == "NAS100"
    assert payload["groups"]["commodity"]["winner"]["latest_price"] is None


def test_hotlist_returns_selected_symbols_and_read_only_scoring_contract():
    pairs = [
        _pair("EURUSD=X", "EUR/USD", "forex"),
        _pair("BTCUSDT", "BTC/USDT", "crypto", source="binance"),
    ]
    live_prices = {
        "EUR/USD": {"price": 1.101, "ts": 1_000.0, "change_pct": 0.3},
        "BTC/USDT": {"price": 68_000.0, "ts": 1_000.0, "change_pct": 2.2},
    }
    calls: list[tuple[str, str, int]] = []

    def fetch_candles(pair, tf, limit):
        calls.append((pair["display"], tf, limit))
        return [
            {"time": "2026-05-21T00:00:00Z", "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0},
            {"time": "2026-05-21T01:00:00Z", "open": 1.0, "high": 1.02, "low": 0.99, "close": 1.01},
        ]

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["forex", "crypto"],
        top_per_group=1,
        timeframe="M15",
        time_now=1_000.0,
    )

    assert payload["selectedSymbols"] == ["EURUSD", "BTCUSDT"]
    assert payload["scoring"] == {
        "usesAi": False,
        "usesScreenshots": False,
        "usesFullEngineB": False,
    }
    assert calls == [("EUR/USD", "M15", 8), ("BTC/USDT", "M15", 8)]
