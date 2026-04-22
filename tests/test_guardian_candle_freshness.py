from config import CONFIG
from guardian import pre_trade_check


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
