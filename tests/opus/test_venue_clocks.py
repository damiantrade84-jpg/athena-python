"""Crypto quote freshness has to be measurable.

`quote_age` is a hard gate, and it does real work on MT5. On Bybit and Binance
the quote was stamped with `time.time()` at the moment the response was parsed,
which makes the reading self-certifying: a response served from a stale cache,
a venue whose feed has stopped moving, or a connection that took ten seconds to
come back all produce a quote that reads as zero seconds old.

The venue's own clock is the only thing that can contradict us, so that is what
gets stamped - from the payload where the venue publishes one, and from the
HTTP `Date` header where it does not.
"""

from __future__ import annotations

import email.utils
import time

import pytest


class FakeResponse:
    def __init__(self, payload, *, date_header=None):
        self._payload = payload
        self.headers = {}
        if date_header is not None:
            self.headers["Date"] = email.utils.formatdate(date_header, usegmt=True)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


VENUE_TS = 1_700_000_000.0


def test_bybit_quote_carries_the_exchange_timestamp(monkeypatch):
    from opus.data import bybit_feed

    payload = {
        "retCode": 0, "retMsg": "OK", "time": int(VENUE_TS * 1000),
        "result": {"list": [{"bid1Price": "68000.5", "ask1Price": "68001.0"}]},
    }
    monkeypatch.setattr(bybit_feed._session, "get",
                        lambda *a, **kw: FakeResponse(payload))

    quote = bybit_feed.BybitFeed().quote("BTCUSDT")

    assert quote is not None
    assert quote.ts == pytest.approx(VENUE_TS)
    # The whole point: it must NOT be the local clock.
    assert abs(quote.ts - time.time()) > 1_000_000


def test_bybit_falls_back_to_the_http_date_header(monkeypatch):
    from opus.data import bybit_feed

    payload = {
        "retCode": 0, "retMsg": "OK",
        "result": {"list": [{"bid1Price": "68000.5", "ask1Price": "68001.0"}]},
    }
    monkeypatch.setattr(
        bybit_feed._session, "get",
        lambda *a, **kw: FakeResponse(payload, date_header=VENUE_TS),
    )

    quote = bybit_feed.BybitFeed().quote("BTCUSDT")

    assert quote is not None
    assert quote.ts == pytest.approx(VENUE_TS, abs=1.0)


def test_bybit_refuses_a_quote_it_cannot_date(monkeypatch):
    """No venue clock at all: the freshness gate would be inert, so there is
    no honest quote to return."""
    from opus.data import bybit_feed

    payload = {
        "retCode": 0, "retMsg": "OK",
        "result": {"list": [{"bid1Price": "68000.5", "ask1Price": "68001.0"}]},
    }
    monkeypatch.setattr(bybit_feed._session, "get",
                        lambda *a, **kw: FakeResponse(payload))

    assert bybit_feed.BybitFeed().quote("BTCUSDT") is None


def test_binance_quote_uses_the_http_date_header(monkeypatch):
    from opus.data import binance_feed

    payload = {"symbol": "BTCUSDT", "bidPrice": "68000.5", "askPrice": "68001.0"}
    monkeypatch.setattr(
        binance_feed._session, "get",
        lambda *a, **kw: FakeResponse(payload, date_header=VENUE_TS),
    )

    quote = binance_feed.BinanceFeed().quote("BTCUSDT")

    assert quote is not None
    assert quote.ts == pytest.approx(VENUE_TS, abs=1.0)
    assert abs(quote.ts - time.time()) > 1_000_000


def test_binance_refuses_a_quote_it_cannot_date(monkeypatch):
    from opus.data import binance_feed

    payload = {"symbol": "BTCUSDT", "bidPrice": "68000.5", "askPrice": "68001.0"}
    monkeypatch.setattr(binance_feed._session, "get",
                        lambda *a, **kw: FakeResponse(payload))

    assert binance_feed.BinanceFeed().quote("BTCUSDT") is None


def test_a_stale_venue_timestamp_fails_the_freshness_gate(monkeypatch):
    """The end-to-end reason this matters: an old quote must now be rejected."""
    from opus.data import bybit_feed
    from opus.risk.gates import quote_gates

    payload = {
        "retCode": 0, "retMsg": "OK",
        "time": int((time.time() - 600.0) * 1000),
        "result": {"list": [{"bid1Price": "68000.5", "ask1Price": "68001.0"}]},
    }
    monkeypatch.setattr(bybit_feed._session, "get",
                        lambda *a, **kw: FakeResponse(payload))

    quote = bybit_feed.BybitFeed().quote("BTCUSDT")
    age_gate = next(g for g in quote_gates(quote, time.time()) if g.name == "quote_age")

    assert age_gate.passed is False
