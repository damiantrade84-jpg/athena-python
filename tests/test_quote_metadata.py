"""Targeted shape tests for live-quote observability fields.

Covers Fix #2: quote metadata surfaced to /api/prices and execution audit
factors blob. Pure shape checks — no broker calls, no scoring, no gates.
"""

import threading
import time

import pytest


def test_api_prices_adds_age_sec_to_each_entry(monkeypatch):
    """api_prices must decorate each live-price entry with ageSec.

    Normalizes ms vs seconds-since-epoch by magnitude. Non-dict entries
    and entries with no ts pass through unchanged.
    """
    from flask import Flask

    from athena_app.api import routes_market_data

    fake_now_s = 1_716_200_000.0  # arbitrary fixed wall clock for the assertion

    monkeypatch.setattr(
        routes_market_data,
        "_live_prices",
        {
            "BTC/USDT": {"price": 67000.0, "ts": fake_now_s - 5.0, "source": "binance_rest"},
            "EUR/USD": {"price": 1.085, "ts": (fake_now_s - 12.0) * 1000.0, "source": "eodhd_ws"},
            "USD/JPY": {"price": 155.5, "source": "mt5"},  # no ts
            "ETH/USDT": "not_a_dict",  # pass-through
        },
    )
    monkeypatch.setattr(routes_market_data, "_live_prices_lock", threading.Lock())
    monkeypatch.setattr(time, "time", lambda: fake_now_s)

    app = Flask(__name__)
    app.add_url_rule("/api/prices", "api_prices", routes_market_data.api_prices)

    client = app.test_client()
    resp = client.get("/api/prices")
    assert resp.status_code == 200
    body = resp.get_json()

    prices = body["prices"]
    # Seconds-since-epoch ts → ageSec computed directly
    assert prices["BTC/USDT"]["ageSec"] == pytest.approx(5.0, abs=0.001)
    assert prices["BTC/USDT"]["source"] == "binance_rest"
    # Millisecond ts → normalized to seconds before age subtraction
    assert prices["EUR/USD"]["ageSec"] == pytest.approx(12.0, abs=0.001)
    assert prices["EUR/USD"]["source"] == "eodhd_ws"
    # Missing ts → ageSec is None, not raised
    assert prices["USD/JPY"]["ageSec"] is None
    # Non-dict entry passes through unchanged
    assert prices["ETH/USDT"] == "not_a_dict"

    assert body["count"] == 4


def test_quick_audit_context_embeds_quote_metadata():
    """_quick_audit_context must embed quote freshness fields under factors.quote.

    These are read by the audit blob writer in api_quick_execute via factors_json
    and let post-mortem analysis correlate stale ticks to fills.
    """
    from execution import _quick_audit_context

    sig = {
        "confluenceScore": 2.4,
        "maxScore": 3.0,
        "direction": "long",
        "trendState": "UP",
        "regimeName": "TREND_UP",
        "price": 1.0855,
        "priceSource": "live_quote",
        "quoteSource": "mt5",
        "quoteTs": 1_716_200_000.0,
        "quoteAgeSec": 4.521,
        "factor_scores": {"trend": 1.0},
        "factor_weights": {"trend": 0.5},
        "disabledFactors": [],
    }

    audit = _quick_audit_context(sig, engine_b=None)

    quote = audit["factors"]["quote"]
    assert quote["priceSource"] == "live_quote"
    assert quote["quoteSource"] == "mt5"
    assert quote["quoteTs"] == 1_716_200_000.0
    assert quote["quoteAgeSec"] == 4.521
    assert quote["signalPrice"] == 1.0855


def test_quick_audit_context_quote_metadata_handles_missing_signal_fields():
    """factors.quote must populate with None values when sig has no quote metadata.

    Older signal payloads (pre-Fix #2 or replayed from cache) lack these fields;
    the audit context must not raise and must record None placeholders so the
    factors_json blob remains self-describing.
    """
    from execution import _quick_audit_context

    sig = {
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "direction": "short",
        "price": 1.085,
    }
    audit = _quick_audit_context(sig, engine_b=None)

    quote = audit["factors"]["quote"]
    assert quote["priceSource"] is None
    assert quote["quoteSource"] is None
    assert quote["quoteTs"] is None
    assert quote["quoteAgeSec"] is None
    assert quote["signalPrice"] == 1.085
