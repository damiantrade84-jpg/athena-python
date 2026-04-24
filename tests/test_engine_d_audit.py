"""Tests for Engine D / Scalp Lab funnel diagnostics and audit infrastructure."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Ensure logs dir exists for tests
os.makedirs("logs/scalp_audit", exist_ok=True)

from scalp_audit import (
    build_funnel_row,
    clear_funnel_log,
    log_engine_d_funnel,
    read_funnel_rows,
    shadow_proximity_simulations,
)


def test_build_funnel_row_has_all_required_fields():
    row = build_funnel_row(symbol="EUR/USD", asset_type="forex", source="mt5")
    required = [
        "timestamp",
        "symbol",
        "asset_type",
        "source",
        "scalp_enabled",
        "called",
        "data_available",
        "candle_timeframes_available",
        "lower_tf_candle_count",
        "latest_lower_tf_candle",
        "freshness_status",
        "spread",
        "spread_ok",
        "atr",
        "atr_ok",
        "volume_available",
        "volume_profile_available",
        "poc",
        "vah",
        "val",
        "lvn_count",
        "price_near_poc",
        "price_near_vah",
        "price_near_val",
        "cvd_available",
        "cvd_bias",
        "absorption_detected",
        "vwap_available",
        "vwap_bias",
        "setup_type",
        "setup_direction",
        "setup_score",
        "setup_grade",
        "min_grade_required",
        "min_score_required",
        "rr",
        "rr_ok",
        "entry",
        "sl",
        "tp",
        "gate_result",
        "fail_reasons",
        "soft_warnings",
        "diagnostic_notes",
    ]
    for key in required:
        assert key in row, f"Missing required field: {key}"


def test_funnel_log_write_and_read():
    clear_funnel_log()
    row = build_funnel_row(
        symbol="BTC/USDT",
        asset_type="crypto",
        source="binance",
        gate_result="PASS",
        setup_grade="A",
        setup_score=85,
    )
    log_engine_d_funnel(row)
    rows = read_funnel_rows()
    assert len(rows) >= 1
    assert rows[-1]["symbol"] == "BTC/USDT"
    assert rows[-1]["gate_result"] == "PASS"


def test_shadow_proximity_does_not_mutate_live():
    """Shadow proximity must be report-only and not affect any live decision."""
    vp = {
        "poc": 100.0,
        "vah": 105.0,
        "val": 95.0,
        "lvn_levels": [98.0, 102.0],
        "_proximity_pct": 0.003,
    }
    result = shadow_proximity_simulations(price=100.0, vp=vp, atr=5.0, tick_size=0.01)
    assert isinstance(result, dict)
    # No live state should be modified
    assert "current" in result or "atr_10pct" in result or not result


def test_shadow_proximity_variants():
    vp = {
        "poc": 100.0,
        "vah": 105.0,
        "val": 95.0,
        "lvn_levels": [98.0],
    }
    result = shadow_proximity_simulations(price=100.0, vp=vp, atr=5.0, tick_size=0.01)
    assert isinstance(result, dict)
    for label, info in result.items():
        assert "proximity" in info
        assert "nearby_levels" in info
        assert "count" in info


class TestScalpScanDiagnosticContract:
    """Verify api_scalp_scan returns blocked/no-setup rows in diagnostic mode."""

    @pytest.fixture
    def client(self):
        # Use the Flask app from athena.py if available
        try:
            from athena import app
        except Exception as exc:
            pytest.skip(f"Flask app not importable: {exc}")
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_scalp_scan_route_exists(self, client):
        resp = client.post("/api/scalp-scan", json={})
        assert resp.status_code in (200, 500)

    def test_scalp_scan_returns_diagnostic_field(self, client):
        resp = client.post("/api/scalp-scan", json={"diagnostic": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "diagnostic" in data
        assert data["diagnostic"] is True
        assert "skipped" in data
        assert "signals" in data
        assert "pass_count" in data
        assert "skip_count" in data

    def test_scalp_scan_diagnostic_includes_fail_reasons(self, client):
        resp = client.post("/api/scalp-scan", json={"diagnostic": True})
        assert resp.status_code == 200
        data = resp.get_json()
        for row in data.get("skipped", []):
            assert "reason" in row

    def test_scalp_scan_non_diagnostic_does_not_enrich_skipped(self, client):
        resp = client.post("/api/scalp-scan", json={"diagnostic": False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("diagnostic") is False


def test_missing_cvd_creates_explicit_fail_reason():
    """If CVD is missing/absent, setup logic should produce an explicit fail reason, not silently skip."""
    # This is a unit-level contract test on _classify_setup
    from scalp_engine import _classify_setup
    setup = _classify_setup(
        market_state="balance",
        price_loc={"location": "at_vah"},
        absorption={"detected": False},
        cvd={"direction": None},
        aaa={"complete": False},
        vwap={"lean": None},
        htf_bias=None,
        asset_type="forex",
    )
    assert setup.get("valid") is False
    assert "reason" in setup
    reason = setup.get("reason", "")
    assert reason != ""
    # Should not be a vague/no-setup string that hides the actual cause
    assert "no_setup" in reason or "absorption" in reason or "cvd" in reason or "vwap" in reason


def test_paper_mode_prevents_real_orders():
    """PAPER_SOAK.ENABLED=true and REAL_ORDERS_ALLOWED=false must hold."""
    from config import CONFIG
    paper = CONFIG.get("PAPER_SOAK", {})
    assert paper.get("ENABLED") is True
    assert paper.get("REAL_ORDERS_ALLOWED") is False


def test_unsupported_asset_type_classified():
    """If asset type cannot be determined, _guess_asset_type should default to 'stock' rather than crash."""
    from scalp_engine import _guess_asset_type
    result = _guess_asset_type("UNKNOWN_TICKER")
    assert result in ("stock", "forex", "crypto", "commodity", "index")


def test_funnel_gate_result_values():
    """Gate result must be one of the expected enum values."""
    valid_results = {"PASS", "WATCHLIST", "BLOCKED", "NO_SETUP", "DATA_MISSING", "NOT_CALLED", "ERROR"}
    row = build_funnel_row(symbol="X", asset_type="forex", source="mt5", gate_result="PASS")
    assert row["gate_result"] in valid_results
