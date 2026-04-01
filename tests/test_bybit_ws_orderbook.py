"""Unit tests for Bybit orderbook.50 snapshot + delta handling."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from athena.datafeeds.bybit_ws import (
    apply_bybit_orderbook_envelope,
)


def _empty_book():
    return {"bids": [], "asks": []}


def test_snapshot_initializes_book_correctly():
    ob = _empty_book()
    msg = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "snapshot",
        "data": {
            "s": "BTCUSDT",
            "b": [["100.0", "1.5"], ["99.5", "2.0"]],
            "a": [["100.5", "1.0"], ["101.0", "3.0"]],
        },
    }
    apply_bybit_orderbook_envelope(ob, msg)
    assert ob["bids"] == [(100.0, 1.5), (99.5, 2.0)]  # desc
    assert ob["asks"] == [(100.5, 1.0), (101.0, 3.0)]  # asc


def test_delta_inserts_new_level():
    ob = _empty_book()
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "snapshot",
            "data": {"b": [["100", "1"]], "a": [["101", "1"]]},
        },
    )
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "delta",
            "data": {"b": [["99.5", "2"]], "a": []},
        },
    )
    assert (99.5, 2.0) in ob["bids"]
    assert ob["bids"][0] == (100.0, 1.0)  # still best bid first (desc)


def test_delta_updates_existing_level():
    ob = _empty_book()
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "snapshot",
            "data": {"b": [["100", "1"]], "a": [["101", "1"]]},
        },
    )
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "delta",
            "data": {"b": [["100", "5"]], "a": []},
        },
    )
    assert ob["bids"] == [(100.0, 5.0)]


def test_delta_removes_level_when_size_zero():
    ob = _empty_book()
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "snapshot",
            "data": {
                "b": [["100", "1"], ["99", "2"]],
                "a": [["101", "1"]],
            },
        },
    )
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "delta",
            "data": {"b": [["99", "0"]], "a": []},
        },
    )
    prices = [p for p, _ in ob["bids"]]
    assert 99.0 not in prices
    assert ob["bids"] == [(100.0, 1.0)]


def test_snapshot_after_deltas_resets_book_cleanly():
    ob = _empty_book()
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "snapshot",
            "data": {"b": [["100", "1"], ["99", "2"]], "a": [["101", "1"]]},
        },
    )
    apply_bybit_orderbook_envelope(
        ob,
        {"type": "delta", "data": {"b": [["98", "1"]], "a": [["102", "1"]]}},
    )
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "snapshot",
            "data": {"b": [["100", "10"]], "a": [["100.2", "10"]]},
        },
    )
    assert ob["bids"] == [(100.0, 10.0)]
    assert ob["asks"] == [(100.2, 10.0)]
    assert len(ob["bids"]) == 1
    assert len(ob["asks"]) == 1


def test_u_equals_one_resets_like_snapshot():
    ob = _empty_book()
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "snapshot",
            "data": {"b": [["100", "1"]], "a": [["101", "1"]]},
        },
    )
    apply_bybit_orderbook_envelope(
        ob,
        {"type": "delta", "data": {"b": [["99", "5"]], "a": []}},
    )
    apply_bybit_orderbook_envelope(
        ob,
        {
            "type": "delta",
            "data": {"u": 1, "b": [["100", "1"]], "a": [["101", "1"]]},
        },
    )
    assert ob["bids"] == [(100.0, 1.0)]
    assert ob["asks"] == [(101.0, 1.0)]
    assert (99.0, 5.0) not in ob["bids"]


def test_missing_type_defaults_to_snapshot_backward_compat():
    ob = _empty_book()
    apply_bybit_orderbook_envelope(
        ob,
        {"data": {"b": [["100", "1"]], "a": [["101", "1"]]}},
    )
    assert ob["bids"] == [(100.0, 1.0)]
    assert ob["asks"] == [(101.0, 1.0)]


def test_bybit_ws_instance_delegates_to_envelope():
    from athena.datafeeds.bybit_ws import BybitWS

    ws = BybitWS(symbol="BTCUSDT")
    ws._handle_orderbook(
        {
            "type": "snapshot",
            "data": {"b": [["50", "1"]], "a": [["51", "1"]]},
        }
    )
    assert ws.orderbook["bids"] == [(50.0, 1.0)]
    assert ws.orderbook["asks"] == [(51.0, 1.0)]
