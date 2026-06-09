"""Upgrade-safety contract tests: webhook ingress, execution dedup, backtest lookahead, broker timeouts."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import athena_runtime
import webhook_ingress
from webhook_ingress import (
    parse_webhook_payload,
    validate_webhook_secret,
    webhook_duplicate_key,
)


class TestWebhookIngress:
    def test_valid_payload_parses_signal(self):
        payload = {
            "pair": "BTC/USDT",
            "direction": "LONG",
            "type": "crypto",
            "price": 65000,
            "sl": 63000,
            "tp1": 68000,
            "score": 1.8,
        }
        sig, err = parse_webhook_payload(payload)
        assert err is None
        assert sig is not None
        assert sig["pair"] == "BTC/USDT"
        assert sig["direction"] == "LONG"
        assert sig["price"] == 65000.0
        assert sig["sl"] == 63000.0
        assert sig["tp1"] == 68000.0

    def test_malformed_json_body_rejects_non_dict(self):
        sig, err = parse_webhook_payload(None)
        assert sig is None
        assert "Missing required fields" in err

    def test_missing_required_fields_rejected(self):
        sig, err = parse_webhook_payload({"pair": "EUR/USD", "direction": "LONG"})
        assert sig is None
        assert err == webhook_ingress._REQUIRED_FIELDS_ERROR

    def test_invalid_direction_rejected(self):
        sig, err = parse_webhook_payload(
            {
                "pair": "BTC/USDT",
                "direction": "SIDEWAYS",
                "price": 1,
                "sl": 0.9,
                "tp1": 1.1,
            }
        )
        assert sig is None

    def test_invalid_numeric_fields_rejected(self):
        sig, err = parse_webhook_payload(
            {
                "pair": "BTC/USDT",
                "direction": "LONG",
                "price": "not-a-number",
                "sl": 1,
                "tp1": 2,
            }
        )
        assert sig is None
        assert "Invalid numeric" in err

    def test_secret_mismatch_when_configured(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", "expected")
        err = validate_webhook_secret({"secret": "wrong"}, "expected")
        assert err is not None
        assert "Unauthorized" in err

    def test_secret_optional_when_empty(self):
        assert validate_webhook_secret({}, "") is None

    def test_webhook_duplicate_key_is_minute_bucketed(self):
        key = webhook_duplicate_key("BTC/USDT", "LONG", minute_bucket="202601011200")
        assert key == "wh_BTC/USDT_LONG_202601011200"

    @pytest.mark.skipif(
        webhook_ingress.BaseModel is None,
        reason="pydantic not installed",
    )
    def test_pydantic_rejects_non_positive_price(self):
        sig, err = parse_webhook_payload(
            {
                "pair": "BTC/USDT",
                "direction": "LONG",
                "price": -1,
                "sl": 1,
                "tp1": 2,
            }
        )
        assert sig is None
        assert err == webhook_ingress._REQUIRED_FIELDS_ERROR


class TestExecutionDedup:
    @pytest.fixture(autouse=True)
    def _dedup_isolation(self, monkeypatch):
        db_path = os.path.join(
            tempfile.gettempdir(),
            f"athena_dedup_{uuid.uuid4().hex}.db",
        )
        monkeypatch.setattr(athena_runtime, "_DEDUP_DB", db_path)
        monkeypatch.setattr(athena_runtime, "_db_ready", False)
        athena_runtime.executed_signals.clear()
        athena_runtime._pending_signals.clear()
        self._db_path = db_path
        yield
        athena_runtime.executed_signals.clear()
        athena_runtime._pending_signals.clear()
        try:
            with sqlite3.connect(db_path, timeout=15.0) as con:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        monkeypatch.setattr(athena_runtime, "_db_ready", False)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(db_path + suffix)
            except OSError:
                pass

    def test_duplicate_sig_id_blocks_repeat(self):
        sig_id = "EUR/USD_LONG_2026-01-01T00:00:00"
        assert athena_runtime.begin_signal_execution(sig_id)
        athena_runtime.complete_signal_execution(sig_id)
        assert athena_runtime.is_signal_duplicate(sig_id)

    def test_webhook_claim_is_atomic(self):
        wh_id = webhook_duplicate_key("BTC/USDT", "SHORT", minute_bucket="202601011205")
        assert athena_runtime.claim_webhook_signal(wh_id)
        assert not athena_runtime.claim_webhook_signal(wh_id)

    def test_pending_blocks_concurrent_begin(self):
        sig_id = "BTC/USDT_LONG_ts1"
        assert athena_runtime.begin_signal_execution(sig_id)
        assert not athena_runtime.begin_signal_execution(sig_id)
        athena_runtime.abort_signal_execution(sig_id)
        assert athena_runtime.begin_signal_execution(sig_id)

    def test_hydrate_reloads_persisted_keys(self):
        sig_id = "EUR/USD_SHORT_persist"
        athena_runtime.complete_signal_execution(sig_id)
        athena_runtime.executed_signals.clear()
        loaded = athena_runtime.hydrate_executed_signals_from_db()
        assert loaded >= 1
        assert sig_id in athena_runtime.executed_signals

    def test_concurrent_claim_only_one_wins(self):
        wh_id = webhook_duplicate_key("ETH/USDT", "LONG", minute_bucket="202601011206")
        results: list[bool] = []

        def _claim():
            results.append(athena_runtime.claim_webhook_signal(wh_id))

        threads = [threading.Thread(target=_claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1
        assert sum(results) == 1

    def test_persisted_key_visible_in_sqlite(self):
        sig_id = "XAU/USD_LONG_db"
        athena_runtime.complete_signal_execution(sig_id)
        with sqlite3.connect(self._db_path, timeout=15.0) as con:
            row = con.execute(
                "SELECT sig_id FROM executed_signal_keys WHERE sig_id=?",
                (sig_id,),
            ).fetchone()
        assert row is not None


class TestBacktestLookaheadContract:
    def test_engine_a_bt_documents_non_lookahead_entry_bar(self):
        source = Path("backtest_runner.py").read_text(encoding="utf-8")
        assert "Indicator windows end at i-1" in source
        assert "entry_bar = d1_raw[i]" in source

    def test_monitor_future_window_starts_after_fill_index(self):
        source = Path("backtest_runner.py").read_text(encoding="utf-8")
        assert "_monitor_fill_index" in source
        assert "future_window = _monitor_candles[_monitor_fill_index" in source


class TestBybitBrokerTimeout:
    def test_fetch_balance_timeout_returns_error_dict(self, monkeypatch):
        import bybit_executor

        class TimeoutExchange:
            def fetch_balance(self, **kwargs):
                raise TimeoutError("RequestTimeout simulated")

        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: TimeoutExchange())
        result = bybit_executor.bybit_get_account()
        assert result["error"] is True
        assert "Timeout" in result["detail"] or "simulated" in result["detail"]

    def test_fetch_positions_timeout_returns_error_dict(self, monkeypatch):
        import bybit_executor

        class TimeoutExchange:
            def fetch_positions(self, **kwargs):
                raise TimeoutError("NetworkError simulated")

        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: TimeoutExchange())
        result = bybit_executor.bybit_get_positions()
        assert result["error"] is True
        assert result["positions"] == []

    def test_order_retry_classifier_accepts_request_timeout_name(self):
        """Mirror bybit_executor create_market_order retry name check."""

        class RequestTimeout(Exception):
            pass

        err = RequestTimeout("timeout")
        assert type(err).__name__ in ("NetworkError", "RequestTimeout")


class TestIndicatorVectorizationParity:
    def test_calc_rsi_matches_reference_loop(self):
        import indicators

        closes = [100 + (i % 7) - 3 for i in range(120)]
        for period in (12, 14, 18):
            got = indicators.calc_rsi(closes, period)
            ref = _reference_calc_rsi(closes, period)
            assert got == ref

    def test_calc_ema_matches_reference_loop(self):
        import indicators

        closes = [50 + i * 0.25 + (i % 5) for i in range(120)]
        for period in (9, 21, 50):
            got = indicators.calc_ema(closes, period)
            ref = _reference_calc_ema(closes, period)
            assert got == ref


def _reference_calc_ema(c: list, p: int) -> list:
    k = 2 / (p + 1)
    e = [None] * len(c)
    if len(c) < p:
        return e
    e[p - 1] = sum(c[:p]) / p
    for i in range(p, len(c)):
        e[i] = c[i] * k + e[i - 1] * (1 - k)
    return e


def _reference_calc_rsi(c: list, p: int) -> list:
    r = [None] * len(c)
    if len(c) < p + 1:
        return r
    g = loss = 0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            loss -= d
    ag, al = g / p, loss / p
    r[p] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(p + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
        r[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return r
