"""BybitMicroMultiWS + venue-scope regressions (crypto volume audit follow-up).

The per-symbol Bybit fan-out was reverted to Binance on 2026-07-02 because
individual sockets went silent for >15 min without tripping the recv timeout.
These cover the parts that make the multiplexed replacement safe to run as the
primary trade-bucket venue: message routing, trade persistence, and the
per-symbol liveness watchdog.
"""

import asyncio

import pytest

from athena.crypto_ws_scope import (
    primary_trade_bucket_exchange,
    should_start_binance_micro_feeds,
    should_start_bybit_micro_feeds,
)
from athena.datafeeds.bybit_ws import (
    BybitMicroMultiWS,
    bybit_micro_topics,
    _parse_bybit_trade,
)


# ── construction / topic scope ──────────────────────────────────────────────

def test_multi_ws_normalises_dedupes_and_sorts_symbols():
    ws = BybitMicroMultiWS(symbols=["ethusdt", "BTC/USDT", "BTCUSDT", "", "ethusdt"])
    assert ws.symbols == ["BTCUSDT", "ETHUSDT"]


def test_multi_ws_rejects_empty_symbol_list():
    with pytest.raises(ValueError):
        BybitMicroMultiWS(symbols=[])


def test_multi_ws_subscribes_both_topics_per_symbol():
    ws = BybitMicroMultiWS(symbols=["BTCUSDT", "ETHUSDT"])
    topics = ws._topics()
    assert topics == [
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
        "orderbook.50.ETHUSDT",
        "publicTrade.ETHUSDT",
    ]


def test_multi_ws_maps_rebranded_symbol_to_stream_name():
    """Bybit renamed the MATIC perpetual to POL; topics use POL, keys stay MATIC."""
    ws = BybitMicroMultiWS(symbols=["MATICUSDT"])
    assert ws._topics() == ["orderbook.50.POLUSDT", "publicTrade.POLUSDT"]
    assert ws.symbols == ["MATICUSDT"]


def test_bybit_micro_topics_helper():
    assert bybit_micro_topics("BTCUSDT") == [
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
    ]


# ── trade parsing ───────────────────────────────────────────────────────────

def test_parse_bybit_trade_reads_v5_fields():
    parsed = _parse_bybit_trade({"p": "100.5", "v": "2.5", "S": "Buy", "T": 1700000000000})
    assert parsed == (100.5, 2.5, "Buy", 1700000000.0)


@pytest.mark.parametrize(
    "trade",
    [
        {"p": "0", "v": "1", "S": "Buy", "T": 1},
        {"p": "1", "v": "0", "S": "Buy", "T": 1},
        {"p": "1", "v": "1", "S": "", "T": 1},
        {"p": "x", "v": "1", "S": "Buy", "T": 1},
    ],
)
def test_parse_bybit_trade_rejects_unusable_rows(trade):
    assert _parse_bybit_trade(trade) is None


# ── message routing + trade bucket persistence ──────────────────────────────

def test_public_trade_routes_to_canonical_symbol_and_stores(monkeypatch):
    """Trade buckets must key off the canonical symbol, not the stream symbol."""
    stored: list[dict] = []
    monkeypatch.setattr(
        "athena.datafeeds.bybit_ws.store_trade",
        lambda **kw: stored.append(kw),
    )
    ws = BybitMicroMultiWS(symbols=["MATICUSDT"])

    asyncio.run(ws._handle_message(
        {
            "topic": "publicTrade.POLUSDT",
            "type": "snapshot",
            "data": [
                {"p": "0.5", "v": "100", "S": "Buy", "T": 1700000000000},
                {"p": "0.5", "v": "40", "S": "Sell", "T": 1700000000500},
            ],
        }
    ))

    assert [s["symbol"] for s in stored] == ["MATICUSDT", "MATICUSDT"]
    assert [s["exchange"] for s in stored] == ["bybit", "bybit"]
    # S=Buy -> buy taker (buyer is NOT maker); S=Sell -> buyer is maker.
    assert [s["is_buyer_maker"] for s in stored] == [False, True]

    st = ws._state["MATICUSDT"]
    assert st.buy_taker_volume == pytest.approx(100.0)
    assert st.sell_taker_volume == pytest.approx(40.0)
    assert st.orderflow_delta == pytest.approx(60.0)


def test_orderbook_snapshot_populates_only_its_own_symbol(monkeypatch):
    monkeypatch.setattr("athena.datafeeds.bybit_ws.store_trade", lambda **kw: None)
    ws = BybitMicroMultiWS(symbols=["BTCUSDT", "ETHUSDT"])

    asyncio.run(ws._handle_message(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "data": {"b": [["100", "2"]], "a": [["101", "3"]], "u": 5},
        }
    ))

    assert ws._state["BTCUSDT"].orderbook["bids"] == [(100.0, 2.0)]
    assert ws._state["ETHUSDT"].orderbook["bids"] == []


def test_unknown_topic_and_subscribe_ack_are_ignored(monkeypatch):
    monkeypatch.setattr("athena.datafeeds.bybit_ws.store_trade", lambda **kw: None)
    ws = BybitMicroMultiWS(symbols=["BTCUSDT"])
    before = ws._state["BTCUSDT"].last_msg_ts

    asyncio.run(ws._handle_message({"op": "subscribe", "success": True, "req_id": "1"}))
    asyncio.run(ws._handle_message({"topic": "publicTrade.SOLUSDT", "data": []}))
    asyncio.run(ws._handle_message({}))

    assert ws._state["BTCUSDT"].last_msg_ts == before


def test_any_routed_message_refreshes_symbol_liveness(monkeypatch):
    monkeypatch.setattr("athena.datafeeds.bybit_ws.store_trade", lambda **kw: None)
    ws = BybitMicroMultiWS(symbols=["BTCUSDT"])
    assert ws._state["BTCUSDT"].last_msg_ts == 0.0

    asyncio.run(ws._handle_message(
        {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "data": {"b": [], "a": []}}
    ))

    assert ws._state["BTCUSDT"].last_msg_ts > 0.0


# ── per-symbol liveness watchdog ────────────────────────────────────────────

def test_watchdog_silent_before_connection_is_old_enough():
    """Cold start must not trip the watchdog."""
    ws = BybitMicroMultiWS(symbols=["BTCUSDT"], stale_symbol_seconds=120.0)
    now = 1_000_000.0
    ws._connected_at = now - 10.0
    ws._state["BTCUSDT"].last_msg_ts = now - 600.0

    assert ws._check_stale_symbols(now) == []


def test_watchdog_flags_symbol_silent_past_threshold():
    """The failure the per-symbol path could not see: one dead topic on a live socket."""
    ws = BybitMicroMultiWS(symbols=["BTCUSDT", "ETHUSDT"], stale_symbol_seconds=120.0)
    now = 1_000_000.0
    ws._connected_at = now - 600.0
    ws._state["BTCUSDT"].last_msg_ts = now - 5.0
    ws._state["ETHUSDT"].last_msg_ts = now - 300.0

    assert ws._check_stale_symbols(now) == ["ETHUSDT"]


def test_watchdog_quiet_when_every_symbol_is_live():
    ws = BybitMicroMultiWS(symbols=["BTCUSDT", "ETHUSDT"], stale_symbol_seconds=120.0)
    now = 1_000_000.0
    ws._connected_at = now - 600.0
    for st in ws._state.values():
        st.last_msg_ts = now - 5.0

    assert ws._check_stale_symbols(now) == []


# ── venue scope: exactly one primary feed ───────────────────────────────────

def _cfg(exchange: str, **overrides) -> dict:
    cfg = {"SCALP_ENGINE": {"TRADE_BUCKET_EXCHANGE": exchange}}
    cfg.update(overrides)
    return cfg


def test_primary_exchange_resolves_and_defaults_to_bybit():
    assert primary_trade_bucket_exchange(_cfg("bybit")) == "bybit"
    assert primary_trade_bucket_exchange(_cfg("binance")) == "binance"
    assert primary_trade_bucket_exchange(_cfg("kraken")) == "bybit"
    assert primary_trade_bucket_exchange(None) == "bybit"


def test_bybit_primary_starts_bybit_and_leaves_binance_off():
    """CLAUDE.md: Binance microstructure is off by default when Bybit is primary."""
    cfg = _cfg("bybit", MICROSTRUCTURE_BINANCE_FEEDS_ENABLED=False)
    assert should_start_bybit_micro_feeds(cfg, has_bybit_pairs=True) is True
    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=True) is False


def test_binance_primary_starts_binance_and_leaves_bybit_off():
    cfg = _cfg("binance", MICROSTRUCTURE_BYBIT_FEEDS_ENABLED=False)
    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=True) is True
    assert should_start_bybit_micro_feeds(cfg, has_bybit_pairs=True) is False


def test_parallel_feed_override_is_explicit_only():
    cfg = _cfg("bybit", MICROSTRUCTURE_BINANCE_FEEDS_ENABLED=True)
    assert should_start_bybit_micro_feeds(cfg, has_bybit_pairs=True) is True
    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=True) is True


def test_no_pairs_means_no_feed_on_either_venue():
    cfg = _cfg("bybit")
    assert should_start_bybit_micro_feeds(cfg, has_bybit_pairs=False) is False
    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=False) is False
