from __future__ import annotations

from dataclasses import dataclass

import requests

import telegram_bot
import telegram_notify
from telegram_bot import _fmt_signal_card


@dataclass
class _Resp:
    ok: bool
    status_code: int = 200
    payload: dict | None = None
    text: str = ""

    def json(self):
        return self.payload if self.payload is not None else {"ok": self.ok}


def _set_config(monkeypatch, **overrides):
    monkeypatch.setattr(telegram_notify, "_dotenv_loaded", True)
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_ENABLED",
        "TELEGRAM_TIMEOUT_SEC",
        "TELEGRAM_RETRY_ATTEMPTS",
        "TELEGRAM_RETRY_BACKOFF_SEC",
        "TELEGRAM_QUEUE_MAX_SIZE",
        "TELEGRAM_MAX_MESSAGE_CHARS",
        "TELEGRAM_PARSE_MODE",
        "TELEGRAM_FALLBACK_PLAINTEXT",
        "TELEGRAM_NOTIFY_EXIT_REASON",
        "TELEGRAM_NOTIFY_TRAIL_ONLY",
    ):
        monkeypatch.delenv(name, raising=False)
    config = {
        "enabled": True,
        "token": "token",
        "chat_id": "123",
        "timeout_sec": 1,
        "retry_attempts": 1,
        "retry_backoff_sec": 0,
        "queue_max_size": 10,
        "max_message_chars": 3900,
        "parse_mode": "Markdown",
        "fallback_plaintext": True,
    }
    config.update(overrides)
    monkeypatch.setattr(telegram_notify, "_config", config)
    with telegram_notify._delivery_state_lock:
        telegram_notify._delivery_state.update(
            {
                "queued": 0,
                "sent": 0,
                "failed": 0,
                "dropped": 0,
                "last_success_time": None,
                "last_failure_time": None,
                "last_error": "",
            }
        )
    return config


def test_deliver_message_retries_transient_post_failure(monkeypatch):
    _set_config(monkeypatch, retry_attempts=2)
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        if len(calls) == 1:
            raise requests.Timeout("temporary timeout")
        return _Resp(ok=True)

    monkeypatch.setattr(telegram_notify.requests, "post", fake_post)

    assert telegram_notify._deliver_message("hello") is True
    assert len(calls) == 2
    assert telegram_notify.get_delivery_state()["sent"] == 1


def test_deliver_message_falls_back_to_plaintext_on_markdown_error(monkeypatch):
    _set_config(monkeypatch)
    calls = []

    def fake_post(url, json, timeout):
        calls.append(dict(json))
        if len(calls) == 1:
            return _Resp(
                ok=False,
                status_code=400,
                payload={"ok": False, "description": "Bad Request: can't parse entities"},
                text="Bad Request: can't parse entities",
            )
        return _Resp(ok=True)

    monkeypatch.setattr(telegram_notify.requests, "post", fake_post)

    assert telegram_notify._deliver_message("*broken") is True
    assert calls[0]["parse_mode"] == "Markdown"
    assert "parse_mode" not in calls[1]


def test_send_message_async_disabled_does_not_start_worker(monkeypatch):
    _set_config(monkeypatch, enabled=False)

    def fail_worker(config):
        raise AssertionError("worker should not start when Telegram is disabled")

    monkeypatch.setattr(telegram_notify, "_ensure_delivery_worker", fail_worker)

    assert telegram_notify._send_message_async("hello") is False


def test_tlt_telegram_cards_distinguish_momentum_from_policy_trigger(monkeypatch):
    signal = {
        "pair": "TLT",
        "direction": "LONG",
        "confluenceScore": 2.4,
        "maxScore": 3.0,
        "factorDiagnostics": {
            "scoringTimeframes": {"momentum": "D1", "trigger": "M15"}
        },
    }

    card = _fmt_signal_card(signal)
    assert "Momentum `D1` | Trigger `M15`" in card

    _set_config(monkeypatch)
    sent = []
    monkeypatch.setattr(
        telegram_notify,
        "_send_message_async",
        lambda message: sent.append(message) or True,
    )
    telegram_notify.send_signal_alert(
        {**signal, "momentum_tf": "D1", "trigger_tf": "M15"}
    )
    assert "Momentum `D1` | Trigger `M15`" in sent[0]


def test_system_alert_helpers_use_shared_delivery(monkeypatch):
    _set_config(monkeypatch)
    sent = []
    monkeypatch.setattr(telegram_notify, "_send_message_async", lambda message: sent.append(message) or True)

    telegram_notify.notify_bybit_ws_disconnect("BTCUSDT")
    telegram_notify.notify_polygon_rate_limit("XAU/USD")

    assert "Bybit websocket disconnected" in sent[0]
    assert "Polygon rate limit hit" in sent[1]


def test_start_telegram_bot_respects_disabled_config(monkeypatch):
    monkeypatch.setattr(telegram_bot, "get_runtime_config", lambda: {"enabled": False})
    created = []

    def fake_thread(*args, **kwargs):
        created.append((args, kwargs))

    monkeypatch.setattr(telegram_bot.threading, "Thread", fake_thread)

    telegram_bot.start_telegram_bot()

    assert created == []


def test_start_telegram_bot_uses_shared_config_chat_ids(monkeypatch):
    config = {"enabled": True, "token": "token", "chat_id": "123,456"}
    monkeypatch.setattr(telegram_bot, "get_runtime_config", lambda: config)
    monkeypatch.setattr(telegram_bot, "get_configured_chat_ids", lambda cfg: ["123", "456"])
    captured = {}

    class _Thread:
        def __init__(self, target, args, daemon):
            captured["target"] = target
            captured["args"] = args
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(telegram_bot.threading, "Thread", _Thread)

    telegram_bot.start_telegram_bot()

    assert captured["args"] == ("token", ["123", "456"])
    assert captured["daemon"] is True
    assert captured["started"] is True


def test_notify_trade_closed_includes_exit_reason(monkeypatch):
    _set_config(monkeypatch)
    sent = []
    monkeypatch.setattr(telegram_notify, "_send_message_async", lambda message: sent.append(message) or True)

    telegram_notify.notify_trade_closed(
        pair="EUR/USD",
        pnl_r=1.2,
        is_win=True,
        duration_minutes=45,
        exit_reason="TRAIL_CLOSE",
        style="intraday",
        exchange="mt5",
    )

    assert sent
    assert "Chandelier trail" in sent[0]
    assert "Style: `intraday`" in sent[0]
    assert "Exch: `MT5`" in sent[0]


def test_notify_trade_closed_trail_only_skips_timed_close(monkeypatch):
    _set_config(monkeypatch, notify_trail_only=True)
    sent = []
    monkeypatch.setattr(telegram_notify, "_send_message_async", lambda message: sent.append(message) or True)

    telegram_notify.notify_trade_closed(
        pair="EUR/USD",
        pnl_r=-0.5,
        is_win=False,
        duration_minutes=30,
        exit_reason="TIMED_CLOSE",
    )
    assert sent == []

    telegram_notify.notify_trade_closed(
        pair="EUR/USD",
        pnl_r=0.8,
        is_win=True,
        duration_minutes=30,
        exit_reason="TRAIL_GIVEBACK",
    )
    assert len(sent) == 1


def test_notify_trade_opened_includes_style_and_exchange(monkeypatch):
    _set_config(monkeypatch)
    sent = []
    monkeypatch.setattr(telegram_notify, "_send_message_async", lambda message: sent.append(message) or True)

    telegram_notify.notify_trade_opened(
        pair="BTC/USDT",
        direction="LONG",
        entry_price=65000,
        stop_loss=64500,
        take_profit=66000,
        style="scalp",
        engine="scalp",
        exchange="bybit",
    )

    assert "Style: `scalp`" in sent[0]
    assert "Engine: `scalp`" in sent[0]
    assert "Exch: `BYBIT`" in sent[0]
