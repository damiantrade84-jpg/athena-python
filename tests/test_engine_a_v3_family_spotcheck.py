"""Phase 5: one-pair-per-family V3 spot-check (synthetic candles, paper-only).

Verifies the full edge stack still scores every family without crashing and
that TRADE decisions (when any) clear the tightened quality gates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.promotion import demo_unvalidated_registry


FAMILY_PAIRS = {
    "forex": {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "score_group": "forex_majors"},
    "crypto": {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "score_group": "crypto_btc"},
    "commodity": {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity", "score_group": "precious_trackers"},
    "index": {"display": "S&P 500", "symbol": "US500", "type": "index", "score_group": "us_indices_trackers"},
    "equity": {"display": "AAPL", "symbol": "AAPL.US", "type": "stock", "score_group": "us_stock_single"},
}


def _candles(*, base: float = 100.0, slope: float = 0.08) -> dict[str, list[dict]]:
    start = datetime(2025, 3, 3, 8, 0, tzinfo=timezone.utc)  # Monday London hours
    frames: dict[str, list[dict]] = {}
    for tf, step, count in (
        ("D1", timedelta(days=1), 260),
        ("H4", timedelta(hours=4), 300),
        ("H1", timedelta(hours=1), 320),
    ):
        rows = []
        for idx in range(count):
            close = base + slope * idx
            rows.append(
                {
                    "time": (start + step * idx).isoformat(),
                    "open": close - 0.15,
                    "high": close + 0.6,
                    "low": close - 0.6,
                    "close": close,
                    "vol": 2_000 + idx * 3,
                }
            )
        frames[tf] = rows
    return frames


def test_family_spotcheck_all_groups_score():
    registry = demo_unvalidated_registry()
    summary: dict[str, dict] = {}
    for family, pair in FAMILY_PAIRS.items():
        signal = evaluate_engine_a_v3(
            pair,
            _candles(base=100.0 if family != "crypto" else 50_000.0, slope=0.1),
            horizon="intraday",
            registry=registry,
        )
        assert signal.engine == "ENGINE_A_V3"
        assert signal.decision in {"TRADE", "WATCH", "NO_SIGNAL"}
        assert signal.confluenceScore is not None
        assert 0.0 <= float(signal.confluenceScore) <= 3.0
        fd = signal.factorDiagnostics or {}
        # Edge stack diagnostics present
        assert "directionalRampMult" in fd or signal.decision == "NO_SIGNAL"
        if signal.decision == "TRADE":
            assert signal.qualified is True
            assert signal.sl is not None and signal.tp1 is not None
            # Tightened setup upgrade floor / min-directional must not be bypassed
            assert "min_directional_failed" not in (signal.rejectionReasons or ())
            thr = float(signal.confluenceThreshold or 0)
            assert float(signal.confluenceScore) + 1e-9 >= thr * 0.75
        summary[family] = {
            "decision": signal.decision,
            "score": signal.confluenceScore,
            "threshold": signal.confluenceThreshold,
            "direction": signal.direction,
            "trendState": fd.get("trendState"),
            "setupId": signal.setupId,
        }
    # All five families must produce a scored result (not crash / empty).
    assert set(summary) == set(FAMILY_PAIRS)
    # At least the continuous scorer should leave most as WATCH on synthetic data
    # (TRADE is optional; absence is fine — edge gates intentionally reduce count).
    assert all(row["score"] is not None for row in summary.values())


def test_family_spotcheck_edge_config_active():
    from config import CONFIG

    upgrade = CONFIG.get("ENGINE_A_V3_SETUP_UPGRADE") or {}
    assert float(upgrade.get("MIN_CONFLUENCE_FRAC", 0)) >= 0.75
    # Ramp deliberately disabled 2026-07-09: V2-calibrated min/span thresholds
    # applied to V3 dir_sum crushed confluence and emptied the TRADE tier.
    ramp = CONFIG.get("ENGINE_A_V3_DIRECTIONAL_RAMP") or {}
    assert ramp.get("ENABLED", True) is False
    legacy = CONFIG.get("ENGINE_A_V3_LEGACY_FILTERS") or {}
    assert legacy.get("ENABLED", False) is True
    assert (CONFIG.get("ENGINE_A_V3_EQUITY_VOLUME_FLOOR") or {}).get("ENABLED", False) is True
