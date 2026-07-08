from config import CONFIG
from guardian import pre_trade_check


def test_guardian_stale_candles_logs_diagnostic_fields(caplog):
    import logging

    signal = {
        "pair": "USD/MXN",
        "direction": "LONG",
        "price": 18.0,
        "sl": 17.9,
        "tp1": 18.1,
        "type": "forex",
        "factorDiagnostics": {},
        "candleFetchMeta": {
            "H1": {
                "lastBarStale": True,
                "lastBarAgeSec": 7154,
                "hasCurrentBucket": False,
                "stalenessSeverity": "stale_1_bucket",
                "lastBarEpoch": 100,
                "offsetHours": 0.0,
                "bucketLag": 1,
            },
            "H4": {
                "lastBarStale": True,
                "lastBarAgeSec": 7154,
                "hasCurrentBucket": False,
                "stalenessSeverity": "stale_1_bucket",
                "lastBarEpoch": 100,
                "offsetHours": 2.0,
                "bucketLag": 1,
            },
        },
    }
    original = CONFIG.get("ENGINE_A_STALE_CANDLE_GUARD", True)
    try:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = True
        caplog.set_level(logging.WARNING)
        ok, reason = pre_trade_check(signal, [], {"balance": 1000.0, "equity": 1000.0})

        assert ok is False
        assert "STALE_CANDLES:" in reason
        assert "7154" in reason
        joined = " ".join(caplog.messages)
        assert "USD/MXN" in joined
        assert "hasCur=False" in joined or "hasCur=False" in "".join(caplog.messages)
    finally:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = original


def _base_signal() -> dict:
    return {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1100,
        "type": "forex",
        "factorDiagnostics": {},
    }


def test_pre_trade_check_blocks_stale_h1_or_h4_candles():
    original = CONFIG.get("ENGINE_A_STALE_CANDLE_GUARD", True)
    try:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = True
        signal = _base_signal()
        signal["candleFetchMeta"] = {
            "H1": {"lastBarStale": True, "lastBarAgeSec": 7200},
            "H4": {"lastBarStale": False, "lastBarAgeSec": 0},
        }

        ok, reason = pre_trade_check(signal, [], {"balance": 1000.0, "equity": 1000.0})

        assert ok is False
        assert reason == "STALE_CANDLES: H1:7200s"
    finally:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = original


def test_pre_trade_check_ignores_d1_only_staleness():
    original = CONFIG.get("ENGINE_A_STALE_CANDLE_GUARD", True)
    try:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = True
        signal = _base_signal()
        signal["candleFetchMeta"] = {
            "D1": {"lastBarStale": True, "lastBarAgeSec": 200000},
            "H4": {"lastBarStale": False, "lastBarAgeSec": 0},
            "H1": {"lastBarStale": False, "lastBarAgeSec": 0},
        }

        ok, reason = pre_trade_check(signal, [], {"balance": 1000.0, "equity": 1000.0})

        assert ok is True
        assert reason == "OK"
    finally:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = original


def test_pre_trade_check_blocks_h4_only_staleness():
    original = CONFIG.get("ENGINE_A_STALE_CANDLE_GUARD", True)
    try:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = True
        signal = _base_signal()
        signal["candleFetchMeta"] = {
            "H1": {"lastBarStale": False, "lastBarAgeSec": 0},
            "H4": {"lastBarStale": True, "lastBarAgeSec": 28800},
        }

        ok, reason = pre_trade_check(signal, [], {"balance": 1000.0, "equity": 1000.0})

        assert ok is False
        assert reason == "STALE_CANDLES: H4:28800s"
    finally:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = original


def test_pre_trade_check_allows_mt5_stock_h4_one_bucket_lag():
    original = CONFIG.get("ENGINE_A_STALE_CANDLE_GUARD", True)
    try:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = True
        signal = _base_signal()
        signal["type"] = "stock"
        signal["pair"] = "PYPL"
        signal["candleFetchMeta"] = {
            "H1": {"lastBarStale": False, "lastBarAgeSec": 0},
            "H4": {
                "lastBarStale": False,
                "lastBarAgeSec": 10449,
                "stalenessSeverity": "stale_1_bucket",
                "bucketLag": 1,
            },
        }

        ok, reason = pre_trade_check(signal, [], {"balance": 1000.0, "equity": 1000.0})

        assert ok is True
        assert reason == "OK"
    finally:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = original


def test_pre_trade_check_blocks_multiple_stale_timeframes():
    original = CONFIG.get("ENGINE_A_STALE_CANDLE_GUARD", True)
    try:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = True
        signal = _base_signal()
        signal["candleFetchMeta"] = {
            "H1": {"lastBarStale": True, "lastBarAgeSec": 7200},
            "H4": {"lastBarStale": True, "lastBarAgeSec": 28800},
        }

        ok, reason = pre_trade_check(signal, [], {"balance": 1000.0, "equity": 1000.0})

        assert ok is False
        assert reason == "STALE_CANDLES: H1:7200s, H4:28800s"
    finally:
        CONFIG["ENGINE_A_STALE_CANDLE_GUARD"] = original
