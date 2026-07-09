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
