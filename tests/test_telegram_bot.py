from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

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
            "be_trigger_min": 60,
            "close_trigger_min": 90,
            "timed_close_style_enabled": True,
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


def test_position_detail_card_shows_trail_mode():
    card = telegram_bot._fmt_position_detail_card(
        {
            "pair": "EUR/USD",
            "direction": "LONG",
            "profit": 2.0,
            "entry": 1.1,
            "timed_tp_mode": "trailing_atr",
            "trail_activation_r": 0.5,
            "style": "intraday",
        },
        "mt5",
    )
    assert "chandelier trail" in card
    assert "`0.5R`" in card
    assert "Timed exit:" not in card


def test_position_detail_card_scalp_hides_legacy_timers():
    card = telegram_bot._fmt_position_detail_card(
        {
            "pair": "BTC/USDT",
            "direction": "LONG",
            "profit": 1.0,
            "is_engine_d": True,
            "live_milestone_management": True,
            "tp_partial": 101.5,
            "mins_to_be": 0,
            "mins_to_close": 0,
        },
        "bybit",
    )
    assert "TP partial (1R)" in card
    assert "milestone" in card
    assert "Timed exit:" not in card


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
                    ],
                    "audit_unresolved": [{"pair": "GBP/USD", "ticket": "99", "broker_live": False}],
                    "audit_unresolved_count": 1,
                }
            ),
        }
    )

    positions, meta, audit_unresolved = telegram_bot._fetch_open_positions_sync(req)

    assert meta["source"] == "open-trades-timed"
    assert meta["audit_unresolved_count"] == 1
    assert len(audit_unresolved) == 1
    assert audit_unresolved[0]["ticket"] == "99"
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

    positions, meta, audit_unresolved = telegram_bot._fetch_open_positions_sync(req)

    assert meta["source"] == "broker-fallback"
    assert audit_unresolved == []
    assert [(p["pair"], ex) for p, ex in positions] == [
        ("XAU/USD", "mt5"),
        ("BTC/USDT", "bybit"),
    ]


def test_engine_b_card_formats_naked_signal():
    card = telegram_bot._fmt_engine_b_card(
        {
            "display": "BTC/USDT",
            "direction": "LONG",
            "confluenceScore": 72.5,
            "maxScore": 100,
            "score_pct": 72.5,
            "style": "intraday",
            "regime": "TRENDING",
            "price": 65000,
            "sl": 64000,
            "tp1": 68000,
            "rr1": 3.0,
            "naked_data": {"zone_tf": "H4", "entry_tf": "H1"},
        }
    )

    assert "*BTC/USDT* - LONG - Engine B" in card
    assert "Score: `72.5/100.0` (72%)" in card
    assert "Style: `intraday`" in card
    assert "TF `H4/H1`" in card


def test_engine_b_pending_signal_preserves_full_payload_for_execute():
    telegram_bot._pending_signals.clear()
    signal = {
        "display": "ADA/USDT",
        "symbol": "ADAUSDT",
        "type": "crypto",
        "direction": "SHORT",
        "price": 0.1668,
        "sl": 0.176,
        "tp1": 0.148,
        "rr1": 2.0,
        "style": "intraday",
        "naked_data": {
            "passed": True,
            "execution_sl": 0.176,
            "execution_tp": 0.148,
        },
    }

    key = telegram_bot._store_pending_signal(signal)
    payload = telegram_bot._build_quick_execute_payload(
        telegram_bot._pending_signals[key]["signal"],
        "intraday",
    )

    assert payload["signal"]["naked_data"]["execution_sl"] == 0.176
    assert payload["signal"]["display"] == "ADA/USDT"
    assert payload["signal"]["style"] == "intraday"
    assert payload["pip_mode"] == "intraday"


def test_engine_b_command_attaches_execute_keyboard():
    source = Path(telegram_bot.__file__).read_text(encoding="utf-8")
    start = source.index("async def cmd_engineb")
    end = source.index("async def cmd_scalp", start)
    engineb_block = source[start:end]

    assert "_store_pending_signal(sig)" in engineb_block
    assert "reply_markup=_execution_keyboard(key)" in engineb_block


def test_scalp_card_includes_strict_fabio_and_gate():
    card = telegram_bot._fmt_scalp_card(
        {
            "pair": "BTC/USDT",
            "direction": "LONG",
            "ai_grade": "B",
            "ai_score": 72,
            "price": 65000,
            "sl": 64500,
            "tp1": 65500,
            "rr1": 1.0,
            "zone_type": "lvn",
            "trigger_type": "absorption",
            "gate_result": "PASS",
            "executable": True,
            "strict_fabio_pass": False,
            "strict_fabio_reason": "missing_aggression",
            "structural_tp": 66000,
            "sessions_active": ["NY"],
        }
    )
    assert "Strict Fabio: `FAIL`" in card
    assert "Gate: `PASS`" in card
    assert "Struct TP: `66000`" in card


def test_telegram_command_specs_include_ops_commands():
    commands = dict(telegram_bot._telegram_command_specs())

    assert commands["engineb"] == "Engine B naked-structure scan"
    assert "scan" in commands
    assert "enginec" in commands
    assert "guardian" in commands
    assert "feeds" in commands
    assert "lastscan" in commands


def test_audit_orphan_reconcileable():
    assert telegram_bot._audit_orphan_reconcileable({"broker_live": False}) is True
    assert telegram_bot._audit_orphan_reconcileable({"broker_live": True}) is False
    assert telegram_bot._audit_orphan_reconcileable({"close_action_enabled": False}) is False


def test_telegram_error_handler_logs_transient_polling_error_without_traceback(caplog):
    class NetworkError(Exception):
        __module__ = "telegram.error"

    context = SimpleNamespace(error=NetworkError("httpx.ReadError"))

    with caplog.at_level(logging.WARNING, logger="sentinel"):
        asyncio.run(telegram_bot._telegram_error_handler(None, context))

    record = next(rec for rec in caplog.records if "Transient polling/update error handled" in rec.message)
    assert record.exc_info is None


def test_telegram_execute_payload_preserves_full_signal_for_quick_execute():
    signal = {
        "pair": "BTC/USDT",
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "direction": "SHORT",
        "price": 100.0,
        "sl": 105.0,
        "tp1": 90.0,
        "timestamp": "2026-06-05T13:17:00+00:00",
        "candleFetchMeta": {"H1": {"stalenessSeverity": "fresh"}},
        "candleFreshness": {"H1": {"severity": "fresh"}},
        "dataFreshness": {"allowed": True, "reason": "fresh"},
    }

    payload = telegram_bot._build_quick_execute_payload(signal, "scalp")

    assert payload["pip_mode"] == "scalp"
    assert payload["signal"]["dataFreshness"] == {"allowed": True, "reason": "fresh"}
    assert payload["signal"]["candleFetchMeta"] == {"H1": {"stalenessSeverity": "fresh"}}
    assert payload["signal"]["style"] == "scalp"
    assert "pair" not in payload


def test_telegram_execute_buttons_use_quick_execute_not_webhook():
    source = Path(telegram_bot.__file__).read_text(encoding="utf-8")
    start = source.index('if action.startswith("exec_"):')
    end = source.index('if action == "ai_analyse":', start)
    execute_block = source[start:end]

    assert 'f"{_BASE}/api/quick-execute"' in execute_block
    assert "_build_quick_execute_payload" in execute_block
    assert "/api/webhook" not in execute_block


def test_telegram_error_handler_logs_update_failure_with_traceback(caplog):
    error = ValueError("bad callback")
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=123))
    context = SimpleNamespace(error=error)

    with caplog.at_level(logging.ERROR, logger="sentinel"):
        asyncio.run(telegram_bot._telegram_error_handler(update, context))

    record = next(rec for rec in caplog.records if "Update handler failed chat_id=123" in rec.message)
    assert record.exc_info[1] is error
