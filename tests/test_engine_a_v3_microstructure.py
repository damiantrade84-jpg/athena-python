"""Engine A V3 live microstructure subsystem wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config import CONFIG
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.quant_context import (
    _microstructure_entry,
    build_quant_context,
    microstructure_scoring_enabled,
    resolve_live_microstructure_metrics,
)
from engine_a_v3.subsystems import ST_AVAILABLE, ST_NA, ST_UNAVAILABLE, resolve_subsystem_weights


def _candles(*, count: int = 260, base: float = 100.0, slope: float = 0.15) -> dict[str, list[dict]]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def _rows(step: timedelta) -> list[dict]:
        rows = []
        for idx in range(count):
            close = base + slope * idx
            open_ = close - (0.3 if slope >= 0 else -0.3)
            rows.append(
                {
                    "time": (start + step * idx).isoformat(),
                    "open": open_,
                    "high": max(open_, close) + 0.5,
                    "low": min(open_, close) - 0.5,
                    "close": close,
                    "vol": 1_000 + idx,
                }
            )
        return rows

    return {
        "D1": _rows(timedelta(days=1)),
        "H4": _rows(timedelta(hours=4)),
        "H1": _rows(timedelta(hours=1)),
    }


BTC_PAIR = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "score_group": "crypto_btc"}


def test_microstructure_entry_is_na_for_non_crypto():
    entry = _microstructure_entry("forex", {"orderflow_delta": 0.5})
    assert entry["state"] == ST_NA


def test_microstructure_entry_unavailable_without_metrics():
    entry = _microstructure_entry("crypto", None)
    assert entry["state"] == ST_UNAVAILABLE


def test_microstructure_entry_available_with_bullish_metrics():
    entry = _microstructure_entry(
        "crypto",
        {
            "orderflow_delta": 0.6,
            "order_book_imbalance": 0.4,
            "liquidity_wall_detection": 0.1,
            "liquidity_pressure": 0.2,
        },
    )
    assert entry["state"] == ST_AVAILABLE
    assert entry["signal"] > 0.0
    assert entry["quality"] > 0.0


def test_resolve_live_microstructure_uses_runtime_cache(monkeypatch):
    monkeypatch.setitem(CONFIG, "CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_MICROSTRUCTURE",
        {"ENABLED": True, "MAX_AGE_SEC": 120, "EXCHANGE": "binance"},
    )
    import time

    cache = {
        "BTCUSDT": {
            "orderflow_delta": 0.55,
            "order_book_imbalance": 0.25,
            "_updated_ts": time.time(),
        }
    }
    metrics = resolve_live_microstructure_metrics(BTC_PAIR, runtime_cache=cache)
    assert metrics == {
        "orderflow_delta": 0.55,
        "order_book_imbalance": 0.25,
    }


def test_btc_score_changes_when_microstructure_enabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED", True)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SUBSYSTEMS",
        {
            "ENABLED": True,
            "WEIGHTS_BY_FAMILY": {"crypto": {"microstructure": 0.08}},
        },
    )
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_MICROSTRUCTURE",
        {"ENABLED": True, "MAX_AGE_SEC": 120, "EXCHANGE": "binance"},
    )

    candles = _candles()
    without = evaluate_engine_a_v3(
        BTC_PAIR,
        candles,
        horizon="intraday",
        context=build_quant_context(display="BTC/USDT", pair_type="crypto"),
    )
    with_micro = evaluate_engine_a_v3(
        BTC_PAIR,
        candles,
        horizon="intraday",
        context=build_quant_context(
            display="BTC/USDT",
            pair_type="crypto",
            microstructure_metrics={
                "orderflow_delta": 0.75,
                "order_book_imbalance": 0.55,
                "liquidity_wall_detection": 0.10,
                "liquidity_pressure": 0.20,
            },
        ),
    )

    assert microstructure_scoring_enabled() is True
    assert resolve_subsystem_weights("crypto")["microstructure"] == pytest.approx(0.08)
    assert (
        without.confluenceScore != with_micro.confluenceScore
        or without.conviction != with_micro.conviction
    )
    micro_diag = (with_micro.factorDiagnostics or {}).get("subsystemStates", {})
    assert micro_diag.get("microstructure") == ST_AVAILABLE
