"""Phase 3: V2 filter ports into Engine A V3."""

from __future__ import annotations

from engine_a_v3.legacy_filters import (
    apply_legacy_filters,
    resolve_v3_trend_state,
)
from config import CONFIG


def test_resolve_v3_trend_state_buckets():
    assert resolve_v3_trend_state(None, "forex") == "UNKNOWN"
    # Very low ADX → DEAD RANGING for typical ranging.dead thresholds
    assert resolve_v3_trend_state(5.0, "forex") in {"DEAD RANGING", "RANGING", "DEVELOPING", "TRENDING"}
    assert resolve_v3_trend_state(50.0, "forex") == "TRENDING"


def test_blocked_trend_states_demote_trade(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_LEGACY_FILTERS",
        {
            "ENABLED": True,
            "FOREX_EMA_CLUSTER": False,
            "CRYPTO_LATE_TREND": False,
            "BLOCKED_TREND_STATES": True,
        },
    )
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_BLOCKED_TREND_STATES",
        ["DEAD RANGING", "DEVELOPING"],
    )
    confluence, decision, diag = apply_legacy_filters(
        asset_type="forex",
        score_group="forex_majors",
        family="forex",
        direction="LONG",
        confluence=2.5,
        decision="TRADE",
        snaps={"H4": {"adx": 10.0, "close": 1.1, "ema21": 1.1, "ema50": 1.09, "ema200": 1.08, "atr": 0.01}},
        candles={"H4": []},
        coherence={"h4": "UP", "d1": "UP"},
        mom_diag={"adxValue": 10.0},
        entry_tf="H1",
    )
    assert diag.get("trendState") in {"DEAD RANGING", "RANGING", "DEVELOPING"}
    if diag.get("trendStateBlocked"):
        assert decision == "WATCH"
    assert confluence == 2.5  # no mult when cluster/late-trend off


def test_legacy_filters_disabled_noop(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_LEGACY_FILTERS",
        {"ENABLED": False},
    )
    confluence, decision, diag = apply_legacy_filters(
        asset_type="crypto",
        score_group="crypto_btc",
        family="crypto",
        direction="LONG",
        confluence=2.4,
        decision="TRADE",
        snaps={},
        candles={},
        coherence={},
        mom_diag={"adxValue": 8.0},
        entry_tf="H1",
    )
    assert decision == "TRADE"
    assert confluence == 2.4
    assert diag.get("legacyFiltersEnabled") is False
