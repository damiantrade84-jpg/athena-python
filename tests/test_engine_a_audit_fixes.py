"""Regression tests for the 2026-06 Engine A audit fixes.

Covers (one focused test group per confirmed finding):
1. Bybit levels ATR must exclude the forming (unconfirmed) kline bucket.
2. Provider-internal fallback candles (EODHD/MT5 -> polygon/yfinance) must be
   labelled in candle fetch meta instead of masquerading as primary data.
3. /api/quick-execute must fail closed when a stale signal cannot be refreshed
   (parity with /api/execute).
4. /api/webhook must run guardian.pre_trade_check before order placement.
5. Engine A backtest indicator calls must pass score_group (live parity).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_feeds import trim_unconfirmed_tail_candles

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


# ── 1. Forming-bar exclusion for Bybit levels ATR ────────────────────────────


def test_trim_unconfirmed_tail_candles_drops_forming_bar():
    candles = [
        {"close": 1.0, "confirmed": True},
        {"close": 2.0, "confirmed": True},
        {"close": 3.0, "confirmed": False},  # forming bucket
    ]
    out = trim_unconfirmed_tail_candles(candles)
    assert [c["close"] for c in out] == [1.0, 2.0]
    # Input list must not be mutated.
    assert len(candles) == 3


def test_trim_unconfirmed_tail_candles_keeps_legacy_and_confirmed():
    # Candles without the flag are treated as confirmed (legacy providers).
    legacy = [{"close": 1.0}, {"close": 2.0}]
    assert trim_unconfirmed_tail_candles(legacy) == legacy
    # Fully confirmed series is untouched.
    confirmed = [{"close": 1.0, "confirmed": True}]
    assert trim_unconfirmed_tail_candles(confirmed) == confirmed
    # Empty/None safe.
    assert trim_unconfirmed_tail_candles(None) == []


def test_bybit_atr_for_levels_call_site_trims_forming_bar():
    """Source pin: athena._bybit_atr_for_levels must trim unconfirmed klines.

    athena.py must not be imported in tests, so pin the call-site wiring.
    """
    src = open(os.path.join(_REPO_ROOT, "athena.py"), encoding="utf-8").read()
    start = src.index("def _bybit_atr_for_levels(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    fetch_pos = body.index("_fetch_bybit_klines(")
    trim_pos = body.index("trim_unconfirmed_tail_candles(")
    atr_pos = body.index("calc_atr(")
    assert fetch_pos < trim_pos < atr_pos, (
        "Bybit levels ATR must trim the forming bucket between kline fetch and calc_atr"
    )


# ── 2. Provider-internal fallback labelling in fetch meta ────────────────────


def _recent_daily_candles(n: int = 3) -> list[dict]:
    base = datetime.now(timezone.utc) - timedelta(days=n)
    out = []
    for i in range(n):
        t = (base + timedelta(days=i)).strftime("%Y-%m-%dT00:00:00+00:00")
        out.append(
            {"time": t, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "vol": 100.0}
        )
    return out


def test_fetch_candles_labels_provider_internal_fallback():
    import candles_cache

    pair = {
        "symbol": "AUDIT_FB_TEST",
        "display": "AUDIT/FBTEST",
        "type": "stock",
        "source": "eodhd",
    }
    fb_candles = _recent_daily_candles()

    def _fake_eodhd(_pair, _tf, _limit):
        # Mirrors athena.fetch_eodhd when EODHD fails and an internal
        # Polygon fallback succeeds.
        return {
            "error": True,
            "symbol": "AUDIT/FBTEST",
            "detail": "EODHD daily unavailable (using fallback)",
            "candles": list(fb_candles),
            "fallback_provider": "polygon",
        }

    candles = candles_cache.fetch_candles(
        pair,
        "D1",
        3,
        fetch_candles_live=lambda *_a, **_k: None,
        fetch_binance=lambda *_a, **_k: None,
        fetch_eodhd=_fake_eodhd,
        fetch_polygon=lambda *_a, **_k: None,
        fetch_yfinance=lambda *_a, **_k: None,
        yfinance_symbol_for_pair=lambda _p: None,
        tf_b={"D1": "1d"},
    )

    assert candles and len(candles) == 3
    meta = candles_cache.get_candle_fetch_meta(pair, "D1", 3)
    assert meta is not None
    assert meta["fallback_used"] is True
    assert meta["fallback_provider"] == "polygon"
    assert meta["fallback"] == "polygon"
    assert meta["primary_provider"] == "eodhd"
    assert meta["fallback_reason"]


def test_fetch_candles_no_fallback_fields_when_primary_succeeds():
    import candles_cache

    pair = {
        "symbol": "AUDIT_FB_OK",
        "display": "AUDIT/FBOK",
        "type": "stock",
        "source": "eodhd",
    }

    candles = candles_cache.fetch_candles(
        pair,
        "D1",
        3,
        fetch_candles_live=lambda *_a, **_k: None,
        fetch_binance=lambda *_a, **_k: None,
        fetch_eodhd=lambda *_a, **_k: {
            "error": False,
            "symbol": "AUDIT/FBOK",
            "detail": "",
            "candles": _recent_daily_candles(),
        },
        fetch_polygon=lambda *_a, **_k: None,
        fetch_yfinance=lambda *_a, **_k: None,
        yfinance_symbol_for_pair=lambda _p: None,
        tf_b={"D1": "1d"},
    )

    assert candles and len(candles) == 3
    meta = candles_cache.get_candle_fetch_meta(pair, "D1", 3)
    assert meta is not None
    assert meta["fallback_used"] is False
    assert "fallback_provider" not in meta
    assert meta["upstream"] == "eodhd"


# ── 3. Quick-execute stale refresh must fail closed ──────────────────────────


def _make_rt():
    m = MagicMock()
    m.CONFIG = {"EXECUTION_ENABLED": True, "SIGNAL_MAX_AGE_SEC": 300}
    m.kill_switch.return_value = False
    m.ALL_PAIRS = [{"display": "EUR/USD", "type": "forex", "symbol": "EURUSD"}]
    return m


def _stale_signal_payload() -> dict:
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    return {
        "signal": {
            "pair": "EUR/USD",
            "display": "EUR/USD",
            "type": "forex",
            "direction": "LONG",
            "price": 1.1,
            "timestamp": stale_ts,
        },
        "pip_mode": "intraday",
    }


def test_quick_execute_rejects_stale_signal_when_refresh_fails():
    import execution
    from flask import Flask

    rt_mock = _make_rt()
    rt_mock.analyze_pair.side_effect = RuntimeError("feed down")

    with patch.object(execution, "rt", return_value=rt_mock):
        app = Flask(__name__)
        execution.register_execution_routes(app)
        app.config["TESTING"] = True
        client = app.test_client()
        resp = client.post("/api/quick-execute", json=_stale_signal_payload())

    assert resp.status_code == 409
    data = resp.get_json()
    assert "STALE_SIGNAL_REFRESH_FAILED" in data["error"]


def test_quick_execute_rejects_stale_signal_for_unknown_pair():
    import execution
    from flask import Flask

    rt_mock = _make_rt()
    rt_mock.ALL_PAIRS = []  # pair cannot be refreshed

    with patch.object(execution, "rt", return_value=rt_mock):
        app = Flask(__name__)
        execution.register_execution_routes(app)
        app.config["TESTING"] = True
        client = app.test_client()
        resp = client.post("/api/quick-execute", json=_stale_signal_payload())

    assert resp.status_code == 409
    data = resp.get_json()
    assert "STALE_SIGNAL_REFRESH_FAILED" in data["error"]
    rt_mock.analyze_pair.assert_not_called()


# ── 4. Webhook guardian gate ──────────────────────────────────────────────────


def test_webhook_runs_guardian_between_risk_check_and_execution():
    """Source pin: /api/webhook must call guardian pre_trade_check before
    run_managed_execution (athena.py cannot be imported in tests)."""
    src = open(os.path.join(_REPO_ROOT, "athena.py"), encoding="utf-8").read()
    start = src.index("def api_webhook(")
    exec_pos = src.index("result = run_managed_execution(_exec_venue, sig, approval)", start)
    body = src[start:exec_pos]
    assert "_guardian_pre_trade(sig, positions, account, _pos_resp)" in body, (
        "/api/webhook must run guardian.pre_trade_check before placing orders"
    )
    risk_pos = body.index("approval = risk_check(")
    guardian_pos = body.index("_guardian_pre_trade(sig, positions, account, _pos_resp)")
    assert guardian_pos > risk_pos
    # Guardian block must fail closed (reject, not just log).
    guardian_block = body[guardian_pos:]
    assert "GUARDIAN BLOCKED" in guardian_block
    assert "return jsonify" in guardian_block


# ── 5. Backtest Engine A indicators must pass score_group ────────────────────
# Both score_group tests removed: they pinned the retired legacy backtester's
# _bt_indicators_from_cache / backtest_pair internals (now archive/backtest_legacy/).
# The v3 rebuild calls evaluate_engine_a_v3 directly, which resolves score_group
# itself — covered by tests/test_engine_a_v3_backtest_parity.py.
