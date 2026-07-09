"""Phase 4: per-family synthetic stale-data spot-checks (no live brokers)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import risk_engine
from athena_app.services.data_freshness import (
    evaluate_execution_data_freshness,
    evaluate_live_quote_age,
)
from atr_diagnostics import evaluate_freshness_from_config
from risk_engine import join_peak_audit_queue, risk_check
from scanner import _engine_b_scan_freshness_stale_tfs


FAMILY_PAIRS = {
    "forex": {
        "display": "EUR/USD",
        "type": "forex",
        "source": "mt5",
        "score_group": "forex_majors",
    },
    "crypto": {
        "display": "BTC/USDT",
        "type": "crypto",
        "source": "bybit",
        "score_group": "crypto_btc",
    },
    "commodity": {
        "display": "XAU/USD",
        "type": "commodity",
        "source": "mt5",
        "score_group": "precious_trackers",
    },
    "index": {
        "display": "S&P 500",
        "type": "index",
        "source": "mt5",
        "score_group": "us_indices_trackers",
    },
    "equity": {
        "display": "AAPL",
        "type": "stock",
        "source": "eodhd",
        "score_group": "us_stock_single",
    },
}


@pytest.fixture(autouse=True)
def _reset_peak_equity():
    with risk_engine._peak_lock:
        old = dict(risk_engine._peak_equity)
        risk_engine._peak_equity = {}
    with risk_engine._daily_lock:
        old_daily_pnl = dict(risk_engine._daily_pnl)
        old_daily_pnl_date = risk_engine._daily_pnl_date
        old_daily_start_balance = dict(risk_engine._daily_start_balance)
        risk_engine._daily_pnl = {}
        risk_engine._daily_pnl_date = ""
        risk_engine._daily_start_balance = {}
    yield
    with risk_engine._peak_lock:
        risk_engine._peak_equity = old
    with risk_engine._daily_lock:
        risk_engine._daily_pnl = old_daily_pnl
        risk_engine._daily_pnl_date = old_daily_pnl_date
        risk_engine._daily_start_balance = old_daily_start_balance
    join_peak_audit_queue(timeout=5.0)


def _fresh_candles():
    return [{"time": 1}] * 5, [{"time": 2}] * 5, [{"time": 3}] * 5


@pytest.mark.parametrize("family", list(FAMILY_PAIRS))
def test_family_stale_h4_blocks_engine_b_scan(family):
    """Stale H4 with fresh H1/D1 must block Engine B scan for every family.

    Calendar-gap grace is disabled here so the assertion targets genuine
    multi-bucket feed lag (not weekend/session policy).
    """
    pair = FAMILY_PAIRS[family]
    d1, h4, h1 = _fresh_candles()

    with (
        patch(
            "athena_app.services.market_state.candle_freshness_diagnostic"
        ) as mock_diag,
        patch(
            "athena_app.services.data_freshness.pre_scoring_allows_intraday_calendar_gap",
            return_value=False,
        ),
    ):
        mock_diag.side_effect = lambda _pair, tf, _candles, **_: {
            "stalenessSeverity": "stale_multi_bucket" if tf == "H4" else "fresh",
            "bucketLag": 4 if tf == "H4" else 0,
        }
        stale, diag = _engine_b_scan_freshness_stale_tfs(
            pair,
            d1,
            h4,
            h1,
            config={"ENGINE_B_SCAN_FRESHNESS_GATE": True},
            score_group=pair["score_group"],
            style="intraday",
        )

    assert "H4:stale_multi_bucket" in stale
    assert diag["H4"]["stalenessSeverity"] == "stale_multi_bucket"
    assert diag["H1"]["stalenessSeverity"] == "fresh"


@pytest.mark.parametrize("family", list(FAMILY_PAIRS))
def test_family_stale_h4_blocks_execution_freshness(family):
    """Execution freshness gate rejects stale H4 for every family."""
    pair = FAMILY_PAIRS[family]
    signal = {
        "type": pair["type"],
        "scoreGroup": pair["score_group"],
        "horizon": "intraday",
        "candleFreshness": {
            "D1": {"stalenessSeverity": "fresh"},
            "H4": {"stalenessSeverity": "stale_multi_bucket", "bucketLag": 4},
            "H1": {"stalenessSeverity": "fresh"},
        },
    }
    cfg = {
        "DATA_FRESHNESS_GATES": {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
            "BLOCK_SEVERITIES": ["stale_multi_bucket"],
        }
    }
    result = evaluate_execution_data_freshness(signal, cfg)
    assert result["allowed"] is False
    assert "H4" in (result.get("reason") or "")


@pytest.mark.parametrize(
    "asset_type,max_age",
    [
        ("crypto", 15),
        ("forex", 60),
        ("commodity", 60),
        ("index", 60),
        ("stock", 90),
    ],
)
def test_family_stale_live_quote_would_block(asset_type, max_age):
    cfg = {
        "LIVE_PRICE_MAX_AGE_SEC": {asset_type: max_age},
        "LIVE_PRICE_BLOCK_EXECUTION": True,
        "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE": True,
    }
    stale = evaluate_live_quote_age({"type": asset_type}, float(max_age) + 30.0, cfg)
    unknown = evaluate_live_quote_age({"type": asset_type}, None, cfg)
    fresh = evaluate_live_quote_age({"type": asset_type}, 1.0, cfg)
    assert stale["would_block"] is True
    assert unknown["would_block"] is True
    assert fresh["would_block"] is False


def test_stale_atr_blocks_risk_check(monkeypatch):
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", False)
    atr = evaluate_freshness_from_config(
        {"atr_tf": "H4", "atr_age_seconds": 20000.0, "atr_value": 0.005},
        {
            "ENABLED": True,
            "BLOCK_EXECUTION_ON_STALE_ATR": True,
            "MAX_AGE_SECONDS": {"H4": 18000},
        },
    )
    assert atr["would_block"] is True

    result = risk_check(
        {
            "pair": "BTCUSDT",
            "direction": "LONG",
            "price": 60000,
            "sl": 59000,
            "tp1": 62000,
            "tp2": 64000,
            "type": "crypto",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confluenceScore": 5.0,
            "quoteAgeSec": 1.0,
            "atrFreshness": atr,
            "candleFetchMeta": {
                "D1": {"stalenessSeverity": "fresh"},
                "H4": {"stalenessSeverity": "fresh"},
                "H1": {"stalenessSeverity": "fresh"},
            },
        },
        10000,
        10000,
        [],
    )
    assert result.approved is False
    assert result.reason.startswith("STALE_ATR:")


def test_null_live_quote_age_blocks_risk_when_enabled(monkeypatch):
    monkeypatch.setitem(
        risk_engine.CONFIG,
        "LIVE_PRICE_MAX_AGE_SEC",
        {"crypto": 15, "forex": 60},
    )
    monkeypatch.setitem(risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION", True)
    monkeypatch.setitem(
        risk_engine.CONFIG, "LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE", True
    )

    result = risk_check(
        {
            "pair": "EURUSD",
            "direction": "LONG",
            "price": 1.1,
            "sl": 1.09,
            "tp1": 1.12,
            "tp2": 1.13,
            "type": "forex",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confluenceScore": 5.0,
            "candleFetchMeta": {
                "D1": {"stalenessSeverity": "fresh"},
                "H4": {"stalenessSeverity": "fresh"},
                "H1": {"stalenessSeverity": "fresh"},
            },
        },
        10000,
        10000,
        [],
    )
    assert result.approved is False
    assert "AGE_UNKNOWN" in result.reason
