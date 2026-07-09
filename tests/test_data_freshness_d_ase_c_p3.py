"""Phase 3: Engine D / ASE / Engine C stale handoff fail-closed assertions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
from athena_ase.execution.bridge import _ptis_freshness_evidence
from engine_c import apply_vision, compute_consensus
from scalp_engine import _attach_engine_d_data_freshness_to_signal


def test_engine_d_marks_non_executable_when_freshness_blocks(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True
    )
    monkeypatch.setitem(
        config.CONFIG,
        "DATA_FRESHNESS_GATES",
        {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["M5", "H1"],
            "BLOCK_SEVERITIES": ["stale_multi_bucket"],
        },
    )

    signal = {"executable": True, "fail_reasons": []}
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}

    with patch(
        "athena_app.services.data_freshness.build_live_feed_diagnostic",
        side_effect=lambda *_a, **_k: {
            "stalenessSeverity": "stale_multi_bucket",
            "bucketLag": 4,
        },
    ):
        _attach_engine_d_data_freshness_to_signal(
            signal,
            pair_dict=pair,
            candles_by_tf={
                "M5": [{"time": 1, "close": 1.0}],
                "H1": [{"time": 2, "close": 1.0}],
            },
            time_now=1_700_000_000.0,
        )

    assert signal["executable"] is False
    assert signal["dataFreshness"]["allowed"] is False
    assert any("STALE" in str(r) for r in signal["fail_reasons"])


def _forex_gate_config(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG, "SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True
    )
    monkeypatch.setitem(
        config.CONFIG,
        "DATA_FRESHNESS_GATES",
        {
            "BLOCK_EXECUTION_ON_STALE": True,
            "BLOCK_TIMEFRAMES": ["M5", "M15", "H1", "H4", "D1"],
            "BLOCK_SEVERITIES": [
                "missing_current_bucket",
                "stale_1_bucket",
                "stale_multi_bucket",
            ],
        },
    )


def test_engine_d_confirmed_only_one_bucket_lag_not_blocked(monkeypatch):
    """Forming bar dropped by fetch policy => stale_1_bucket is policy lag, not a block."""
    _forex_gate_config(monkeypatch)

    now = 1_700_000_000.0  # Tuesday 22:13 UTC — forex open
    bucket = int(now // 900) * 900
    confirmed_only_m15 = [
        {"time": bucket - 1800, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1},
        {"time": bucket - 900, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1},
    ]

    signal = {"executable": True, "fail_reasons": []}
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    _attach_engine_d_data_freshness_to_signal(
        signal,
        pair_dict=pair,
        candles_by_tf={"M15": confirmed_only_m15},
        time_now=now,
        confirmed_only_tfs={"M15"},
    )

    assert signal["candleConsistency"]["M15"]["status"] == "CONFIRMED_ONLY_OK"
    assert signal["dataFreshness"]["allowed"] is True
    assert signal["executable"] is True
    assert signal["fail_reasons"] == []


def test_engine_d_confirmed_only_multi_bucket_still_blocks(monkeypatch):
    """Confirmed-only declaration must not mask genuinely stale (multi-bucket) feeds."""
    _forex_gate_config(monkeypatch)

    now = 1_700_000_000.0
    bucket = int(now // 900) * 900
    stale_m15 = [
        {"time": bucket - 3600, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1},
        {"time": bucket - 2700, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1},
    ]

    signal = {"executable": True, "fail_reasons": []}
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    _attach_engine_d_data_freshness_to_signal(
        signal,
        pair_dict=pair,
        candles_by_tf={"M15": stale_m15},
        time_now=now,
        confirmed_only_tfs={"M15"},
    )

    assert signal["dataFreshness"]["allowed"] is False
    assert signal["executable"] is False
    assert any("STALE_DATA_BLOCK:M15" in str(r) for r in signal["fail_reasons"])


def test_mt5_scalp_candles_broker_time_normalized_to_utc(monkeypatch):
    """mt5_fetch_scalp_candles must shift broker-stamped bar times to UTC (fetch_mt5 parity)."""
    import sys
    import time as _t

    from scalp_engine import mt5_fetch_scalp_candles

    monkeypatch.setitem(config.CONFIG, "MT5_BROKER_UTC_OFFSET", 3)

    now = _t.time()
    bucket = int(now // 900) * 900
    broker_shift = 3 * 3600
    raw_bars = [
        {"time": bucket - 900 + broker_shift, "open": 1.0, "high": 1.0, "low": 1.0,
         "close": 1.0, "tick_volume": 5, "real_volume": 0},
        {"time": bucket + broker_shift, "open": 1.0, "high": 1.0, "low": 1.0,
         "close": 1.0, "tick_volume": 5, "real_volume": 0},
    ]

    fake_mt5 = SimpleNamespace(
        TIMEFRAME_M1=1,
        TIMEFRAME_M5=5,
        TIMEFRAME_M15=15,
        TIMEFRAME_H1=16385,
        symbol_select=lambda *_a, **_k: True,
        copy_rates_from_pos=lambda *_a, **_k: raw_bars,
        symbol_info_tick=lambda *_a, **_k: SimpleNamespace(time=now + broker_shift),
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    with patch("mt5_executor.mt5_connect", return_value=True):
        candles = mt5_fetch_scalp_candles("EURUSD", "M15", 2, include_forming=False)

    # Forming bar dropped; remaining bar normalized from broker time to UTC.
    assert len(candles) == 1
    assert int(candles[-1]["time"]) == bucket - 900


def test_ase_ptis_multi_bucket_stale_blocks_bridge():
    signal = SimpleNamespace(horizon="intraday", instrument="EURUSD")
    exec_dict = {
        "symbol": "EURUSD",
        "pair": "EUR/USD",
        "type": "forex",
        "source": "mt5",
    }

    fake_series = SimpleNamespace(value_time=[1_700_000_000_000] * 5)

    with (
        patch(
            "athena_ase.data.ptis.PTISStore",
            return_value=object(),
        ),
        patch(
            "athena_ase.data.ptis.default_ptis_root",
            return_value=".",
        ),
        patch(
            "athena_ase.signals.common.load_bar_series",
            return_value=fake_series,
        ),
        patch(
            "athena_app.services.market_state.candle_freshness_diagnostic",
            return_value={
                "stalenessSeverity": "stale_multi_bucket",
                "bucketLag": 5,
            },
        ),
    ):
        ok, reason = _ptis_freshness_evidence(signal, exec_dict)

    assert ok is False
    assert reason.startswith("stale_ptis:")
    assert exec_dict["candleFreshness"]["H1"]["stalenessSeverity"] == "stale_multi_bucket"


def test_ase_ptis_stale_1_bucket_still_allowed():
    signal = SimpleNamespace(horizon="intraday", instrument="EURUSD")
    exec_dict = {
        "symbol": "EURUSD",
        "pair": "EUR/USD",
        "type": "forex",
        "source": "mt5",
    }
    fake_series = SimpleNamespace(value_time=[1_700_000_000_000] * 5)

    with (
        patch(
            "athena_ase.data.ptis.PTISStore",
            return_value=object(),
        ),
        patch(
            "athena_ase.data.ptis.default_ptis_root",
            return_value=".",
        ),
        patch(
            "athena_ase.signals.common.load_bar_series",
            return_value=fake_series,
        ),
        patch(
            "athena_app.services.market_state.candle_freshness_diagnostic",
            return_value={
                "stalenessSeverity": "stale_1_bucket",
                "bucketLag": 1,
            },
        ),
    ):
        ok, reason = _ptis_freshness_evidence(signal, exec_dict)

    assert ok is True
    assert reason == "stale_1_bucket"


def test_engine_c_stale_child_cannot_execute():
    signal_a = {
        "direction": "LONG",
        "confluenceScore": 2.5,
        "maxScore": 3.0,
        "price": 1.1,
        "sl": 1.09,
        "tp1": 1.12,
        "engineATradeEnabled": True,
        "dataFreshness": {
            "allowed": False,
            "reason": "STALE_DATA_BLOCK:H4:stale_multi_bucket",
        },
    }
    signal_b = {
        "direction": "LONG",
        "structural_verdict": "CLEAR",
        "recommended_stop_loss": 1.09,
        "recommended_take_profit": 1.12,
    }
    confidence_b = {
        "passed": True,
        "pct": 90,
        "score": 90,
        "max_possible": 100,
        "zone_ok": True,
        "trigger_ok": True,
        "structure_ok": True,
        "space_ok": True,
        "rr_ok": True,
    }
    result = compute_consensus(
        signal_a,
        signal_b,
        confidence_b=confidence_b,
        entry_price=1.1,
        atr=0.001,
        regime="TRENDING",
    )
    assert result["trade"] is False
    assert result["verdict"] == "STALE_CHILD_DATA"
    assert result["tier"] == "SKIP"
    assert result["decision_state"] == "blocked"


def test_engine_c_vision_stale_cannot_keep_trade():
    consensus = {
        "trade": True,
        "verdict": "ALIGNED",
        "direction": "LONG",
        "conviction": 0.8,
        "tier": "HIGH",
        "sizing_override": 1.0,
        "decision_state": "execute",
        "entry": 1.1,
        "sl": 1.09,
        "tp": 1.12,
    }
    vision = {
        "analysis": "STRONG confirms direction",
        "structured": {
            "rating": "STRONG",
            "confirms_direction": True,
            "sl_flag": "ok",
            "tp_flag": "ok",
        },
        "vision_trade_read": {
            "freshness_status": "stale",
            "allowed_for_execution_context": False,
        },
    }
    out = apply_vision(consensus, vision)
    assert out["trade"] is False
    assert out["verdict"] == "VISION_STALE"
    assert out["tier"] == "SKIP"
    assert out.get("vision_freshness_blocked") is True
