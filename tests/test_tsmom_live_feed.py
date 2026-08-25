"""Authoritative closed-D1 feed contract for the live TSMOM/OX Book scanner."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tsmom_live import feed
from tsmom_live.signal import INSTRUMENTS


def _candles(now: datetime, count: int = 90) -> list[dict]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=count - 1)
    rows = []
    for index in range(count):
        stamp = start + timedelta(days=index)
        price = 100.0 + index
        rows.append({
            "time": stamp.isoformat(),
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
        })
    return rows


def test_closed_daily_bars_use_authoritative_mt5_fetcher_and_drop_forming_bar(monkeypatch):
    now = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    calls = []

    def fetch_mt5(pair, tf, limit):
        calls.append((pair, tf, limit))
        return {"error": False, "candles": _candles(now)}

    monkeypatch.setattr(feed, "_authoritative_candle_fetcher", fetch_mt5, raising=False)
    snapshot = feed.load_daily_bars(INSTRUMENTS["gold"], time_now=now.timestamp())

    assert calls == [(
        {"symbol": "XAUUSD", "display": "XAU/USD", "type": "commodity", "source": "mt5"},
        "D1",
        400,
    )]
    assert snapshot.source == "mt5"
    assert snapshot.error is None
    assert snapshot.freshness_ok is True
    assert snapshot.freshness_reason == "fresh"
    assert snapshot.frame is not None
    assert len(snapshot.frame) == 89
    assert snapshot.frame.iloc[-1]["time"].isoformat().startswith("2026-08-24")


def test_authoritative_feed_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(feed, "_authoritative_candle_fetcher", None, raising=False)
    monkeypatch.setattr(feed, "_runtime_candle_fetcher", lambda: None, raising=False)

    snapshot = feed.load_daily_bars(INSTRUMENTS["gold"])

    assert snapshot.frame is None
    assert snapshot.freshness_ok is False
    assert snapshot.error == "authoritative_mt5_fetcher_unavailable"
