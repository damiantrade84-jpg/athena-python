from __future__ import annotations

from dataclasses import dataclass

import telegram_bot


@dataclass
class _Resp:
    payload: dict
    ok: bool = True
    status_code: int = 200
    text: str = ""

    def json(self):
        return self.payload


class _FakeReq:
    def __init__(self, responses: dict[str, _Resp]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, timeout: int):
        self.calls.append(url)
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        return _Resp({"error": "missing fake response"}, ok=False, status_code=500)


def test_position_detail_card_marks_winning_and_timed_state():
    card = telegram_bot._fmt_position_detail_card(
        {
            "pair": "BTC/USDT",
            "direction": "LONG",
            "exchange": "bybit",
            "profit": 12.345,
            "entry": 100.0,
            "sl": 95.0,
            "tp": 110.0,
            "volume": 0.5,
            "style": "intraday",
            "mins_open": 75,
            "mins_to_be": 0,
            "mins_to_close": 15,
            "be_reached": True,
            "close_reached": False,
            "ticket": "1",
        }
    )

    assert "BTC/USDT" in card
    assert "WINNING" in card
    assert "P&L: `+12.35`" in card
    assert "Open: `1.2h`" in card
    assert "Timed exit: BE `hit` | close `in 15m`" in card


def test_filter_positions_winning_and_losing():
    positions = [
        ({"pair": "A", "profit": 1.0}, "mt5"),
        ({"pair": "B", "profit": -2.0}, "bybit"),
        ({"pair": "C", "profit": 0.0}, "mt5"),
    ]

    assert [p["pair"] for p, _ in telegram_bot._filter_positions(positions, "winning")] == ["A"]
    assert [p["pair"] for p, _ in telegram_bot._filter_positions(positions, "losing")] == ["B"]
    assert [p["pair"] for p, _ in telegram_bot._filter_positions(positions, "flat")] == ["C"]


def test_fetch_open_positions_prefers_timed_endpoint():
    req = _FakeReq(
        {
            "/api/open-trades-timed": _Resp(
                {
                    "positions": [
                        {"pair": "EUR/USD", "exchange": "mt5", "profit": 3.0},
                        {"pair": "ETH/USDT", "exchange": "bybit", "profit": -1.0},
                    ]
                }
            ),
        }
    )

    positions, meta = telegram_bot._fetch_open_positions_sync(req)

    assert meta["source"] == "open-trades-timed"
    assert [(p["pair"], ex) for p, ex in positions] == [
        ("EUR/USD", "mt5"),
        ("ETH/USDT", "bybit"),
    ]
    assert len(req.calls) == 1


def test_fetch_open_positions_falls_back_to_brokers():
    req = _FakeReq(
        {
            "/api/open-trades-timed": _Resp({"error": "MT5 fetch failed"}, ok=False, status_code=500),
            "/api/mt5-positions": _Resp({"positions": [{"pair": "XAU/USD", "profit": 4.0}]}),
            "/api/bybit-status": _Resp({"positions": [{"pair": "BTC/USDT", "profit": -5.0}]}),
        }
    )

    positions, meta = telegram_bot._fetch_open_positions_sync(req)

    assert meta["source"] == "broker-fallback"
    assert [(p["pair"], ex) for p, ex in positions] == [
        ("XAU/USD", "mt5"),
        ("BTC/USDT", "bybit"),
    ]
