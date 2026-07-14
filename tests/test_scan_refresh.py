from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from athena_app.services.scan_backtest_service import handle_scan_request
from candles_cache import _candle_cache, _candle_cache_lock, fetch_candles


def _current_h1_candle() -> list[dict]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        {
            "time": now.isoformat(),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "vol": 100.0,
        }
    ]


def test_forced_candle_refresh_bypasses_existing_ttl_entry():
    pair = {"display": "TEST", "symbol": "TEST", "type": "stock", "source": "eodhd"}
    calls = {"eodhd": 0}

    def fetch_eodhd(*_args):
        calls["eodhd"] += 1
        return _current_h1_candle()

    with _candle_cache_lock:
        _candle_cache.clear()
    common = {
        "fetch_candles_live": lambda *_args: None,
        "fetch_binance": lambda *_args: None,
        "fetch_eodhd": fetch_eodhd,
        "fetch_polygon": lambda *_args: None,
        "fetch_yfinance": lambda *_args: None,
        "tf_b": {},
    }

    fetch_candles(pair, "H1", 1, **common)
    fetch_candles(pair, "H1", 1, force_refresh=True, **common)

    assert calls["eodhd"] == 2


def test_scan_request_forwards_manual_market_refresh_flag():
    received = {}

    handle_scan_request(
        {"style": "intraday", "asset_class": "crypto", "refresh_market_data": True},
        run_full_scan=lambda **kwargs: received.update(kwargs) or {"success": True},
    )

    assert received == {
        "style": "intraday",
        "asset_class": "crypto",
        "refresh_market_data": True,
    }


def test_engine_a_manual_scan_and_chart_review_request_fresh_market_data():
    root = Path(__file__).resolve().parents[1]
    panel = (
        root
        / "static"
        / "react-app"
        / "app"
        / "src"
        / "components"
        / "panels"
        / "SignalsPanel.tsx"
    ).read_text(encoding="utf-8")
    scan_a = panel.split("const scanEngineA", 1)[1].split("const scanEngineB", 1)[0]
    athena = (root / "athena.py").read_text(encoding="utf-8")
    ast.parse(athena)
    review_runtime = athena.split("register_ai_chart_review_routes(", 1)[1].split(
        "register_ai_scalp_chart_review_routes(", 1
    )[0]
    analyze_pair = athena.split("def analyze_pair(", 1)[1].split(
        "def ensure_runtime_services_started(", 1
    )[0]

    assert "refresh_market_data: true" in scan_a
    assert "refresh_market_data: false" not in scan_a
    assert "refresh_market_data=True" in review_runtime
    assert analyze_pair.count("force_refresh=refresh_market_data") >= 4


def test_engine_a_v3_review_pass_requires_current_trade_qualification():
    from ai_review.engine_a_context import _engine_a_passed

    signal = {
        "engine": "ENGINE_A_V3",
        "contractVersion": "3.1.0",
        "direction": "LONG",
        "decision": "TRADE",
        "qualified": True,
        "engineATradeEnabled": True,
        "dataFreshness": {"allowed": True},
        "confluenceScore": 2.5,
        "threshold": 2.0,
    }
    assert _engine_a_passed(signal) is True

    signal["decision"] = "WATCH"
    assert _engine_a_passed(signal) is False
