"""Regression tests for the 2026-07-28 forex regime fixes.

Covers the audit findings F1/F2/F4:
- live-scoped ENGINE_A_BLOCKED_TREND_STATES demotes forex TREND trades in
  ranging regimes but exempts mean-reversion signals (regime-appropriate there);
- the fx_range_mean_reversion setup overlay fires for forex when enabled;
- MR setup upgrades skip the trend-side quant confluence floor;
- session direction evidence is mirrored for mean-reversion fades.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from config import CONFIG
from engine_a_v3.legacy_filters import apply_legacy_filters
from engine_a_v3.quant_scorer import QuantScore
from engine_a_v3.routing import SpecialistRoute
from engine_a_v3.session_scoring import session_context_score
from engine_a_v3.setups import SetupCandidate, detect_setup
from scoring import get_blocked_trend_states

# ── helpers ──────────────────────────────────────────────────────────────────

_END = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)  # London session


def _series(closes, *, end=_END, bar_hours=1):
    """Build valid OHLCV rows (strictly increasing tz-aware times)."""
    rows = []
    start = end - timedelta(hours=bar_hours * (len(closes) - 1))
    prev = closes[0]
    for i, close in enumerate(closes):
        open_ = prev if i else closes[0]
        rows.append(
            {
                "time": (start + timedelta(hours=bar_hours * i)).isoformat(),
                "open": open_,
                "high": max(open_, close) + 0.05,
                "low": min(open_, close) - 0.05,
                "close": close,
                "vol": 1000.0,
            }
        )
        prev = close
    return rows


def _flat(n, price=100.0):
    return [price] * n


def _declining_tail(n_flat=60, n_drop=10, step=0.08, base=100.0):
    closes = _flat(n_flat, base)
    return closes + [round(base - step * i, 6) for i in range(1, n_drop + 1)]


def _rising_tail(n_flat=60, n_rise=10, step=0.08, base=100.0):
    closes = _flat(n_flat, base)
    return closes + [round(base + step * i, 6) for i in range(1, n_rise + 1)]


def _choppy_fade_long():
    """Tight zigzag (path-heavy), sharp 3-bar drop, bullish reversal bar."""
    closes = [100.25 if i % 2 == 0 else 100.0 for i in range(44)]
    closes += [100.45 if i % 2 == 0 else 100.0 for i in range(20)]
    closes += [99.4, 98.8, 98.2, 98.55]
    return closes


def _quant(*, direction, confluence, decision, level_style, factor_diagnostics=None):
    return QuantScore(
        direction=direction,
        confluence_score=confluence,
        max_score=3.0,
        score_norm=confluence / 3.0,
        conviction=confluence / 3.0,
        decision=decision,
        threshold=2.1,
        level_style=level_style,
        factor_scores={},
        factor_diagnostics=factor_diagnostics or {},
        components={},
    )


def _candle_stack(primary_closes):
    return {
        "D1": _series(_flat(230), bar_hours=24),
        "H4": _series(list(primary_closes), bar_hours=4),
        "H1": _series(list(primary_closes), bar_hours=1),
    }


def _patch_evaluator(monkeypatch, quant, setup):
    import engine_a_v3.evaluator as evaluator

    monkeypatch.setattr(evaluator, "score_pair", lambda *a, **k: quant)
    monkeypatch.setattr(evaluator, "detect_setup", lambda *a, **k: setup)
    monkeypatch.setattr(
        evaluator,
        "production_registry",
        lambda: SimpleNamespace(
            resolve=lambda *a, **k: SimpleNamespace(
                qualified=False, profile=None, reasons=(), artifact=None
            )
        ),
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EXECUTOR_MODE", "demo")
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SETUP_UPGRADE",
        {"ENABLED": True, "MIN_CONFLUENCE_FRAC": 0.75, "MR_MIN_CONFLUENCE_FRAC": 0.0},
    )
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_V3_SESSION_SCORING", {"ENABLED": True, "MIN_SCORE": 0.40}
    )
    monkeypatch.setitem(
        CONFIG, "ENGINE_A_V3_QUANT_SESSION_GATE", {"ENABLED": True, "MIN_SCORE": 0.40}
    )
    return evaluator


_PAIR = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "score_group": "forex_majors"}

# ── F2: live-scoped blocked trend states ─────────────────────────────────────

_LIVE_SCOPED = {"backtest": ["DEAD RANGING", "DEVELOPING"], "live": {"forex": ["DEAD RANGING", "RANGING"]}}


def _legacy_args(**overrides):
    args = dict(
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
    args.update(overrides)
    return args


def _enable_legacy(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_LEGACY_FILTERS",
        {"ENABLED": True, "FOREX_EMA_CLUSTER": False, "CRYPTO_LATE_TREND": False, "BLOCKED_TREND_STATES": True},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_A_BLOCKED_TREND_STATES", _LIVE_SCOPED)


def test_live_scoped_blocked_states_demote_forex_trend(monkeypatch):
    _enable_legacy(monkeypatch)
    _c, decision, diag = apply_legacy_filters(**_legacy_args())
    assert diag["trendState"] in {"DEAD RANGING", "RANGING"}
    assert diag["trendStateBlocked"] is True
    assert decision == "WATCH"


def test_live_scoped_blocked_states_exempt_mean_reversion(monkeypatch):
    _enable_legacy(monkeypatch)
    _c, decision, diag = apply_legacy_filters(
        **_legacy_args(level_style="mean_reversion")
    )
    assert diag["trendStateBlocked"] is False
    assert diag.get("trendStateBlockedExempt") == "mean_reversion"
    assert decision == "TRADE"


def test_scoped_dict_keeps_backtest_and_non_forex_live(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_A_BLOCKED_TREND_STATES", _LIVE_SCOPED)
    assert get_blocked_trend_states({"type": "forex"}, scope="backtest") == {
        "DEAD RANGING",
        "DEVELOPING",
    }
    assert get_blocked_trend_states({"type": "forex"}, scope="live") == {
        "DEAD RANGING",
        "RANGING",
    }
    assert get_blocked_trend_states({"type": "crypto"}, scope="live") == set()

# ── F1: MR overlay candidate in detect_setup ─────────────────────────────────

_ROUTE = SpecialistRoute("forex_majors", "forex", "majors")
_PERIODS = {"ema_trend": 21, "ema_momentum": 50, "atr": 14}


def _overlay_frames():
    primary = _series(_choppy_fade_long(), bar_hours=1)
    context = _series([100.1 if i % 2 == 0 else 99.9 for i in range(70)], bar_hours=4)
    return {"H1": primary, "H4": context}


def _patch_forex_setup_cfg(monkeypatch, overlay):
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_FOREX_THESIS", "trend")
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_FOREX_SETUP", "trend")
    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_FOREX_MR_OVERLAY_ENABLED", overlay)
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_MEAN_REVERSION",
        {"Z_ENTRY": 1.5, "EFF_MAX": 0.35, "LOOKBACK": 20},
    )


def test_mr_overlay_candidate_fires_in_range(monkeypatch):
    _patch_forex_setup_cfg(monkeypatch, True)
    candidate = detect_setup(
        _ROUTE,
        "intraday",
        _overlay_frames(),
        display="EUR/USD",
        indicator_periods=_PERIODS,
        entry_tf_override="H1",
    )
    assert candidate.setup_id == "fx_range_mean_reversion"
    assert candidate.decision == "TRADE"
    assert candidate.direction == "LONG"
    assert candidate.level_style == "mean_reversion"


def test_mr_overlay_disabled_keeps_trend_only(monkeypatch):
    _patch_forex_setup_cfg(monkeypatch, False)
    candidate = detect_setup(
        _ROUTE,
        "intraday",
        _overlay_frames(),
        display="EUR/USD",
        indicator_periods=_PERIODS,
        entry_tf_override="H1",
    )
    assert candidate.setup_id != "fx_range_mean_reversion"
    assert candidate.decision != "TRADE"

# ── F4: session direction evidence mirrored for MR ───────────────────────────


def test_session_score_mean_reversion_alignment_mirrored():
    rising = _series([99.0 + 0.05 * i for i in range(25)], bar_hours=1)
    trend_score, trend_comp = session_context_score(
        rising, direction="SHORT", level_style="trend"
    )
    mr_score, mr_comp = session_context_score(
        rising, direction="SHORT", level_style="mean_reversion"
    )
    # SHORT against a rising 20-bar net: misaligned for trend, aligned for a fade.
    assert mr_comp["direction_evidence"] > trend_comp["direction_evidence"]
    assert mr_score > trend_score

# ── evaluator: MR floor skip + blocked-state exemption ───────────────────────


def test_mr_setup_upgrade_skips_quant_confluence_floor(monkeypatch):
    from engine_a_v3.evaluator import evaluate_engine_a_v3

    quant = _quant(direction="FLAT", confluence=0.2, decision="WATCH", level_style="trend")
    setup = SetupCandidate(
        "fx_range_mean_reversion", "TRADE", "LONG", (), (), "mean_reversion"
    )
    _patch_evaluator(monkeypatch, quant, setup)
    signal = evaluate_engine_a_v3(
        _PAIR, _candle_stack(_declining_tail()), horizon="intraday"
    )
    assert signal.decision == "TRADE"
    assert signal.direction == "LONG"
    assert signal.setupId == "fx_range_mean_reversion"
    assert "setup_upgrade_below_quant_quality_floor" not in signal.rejectionReasons


def test_trend_setup_upgrade_still_floor_blocked(monkeypatch):
    from engine_a_v3.evaluator import evaluate_engine_a_v3

    quant = _quant(direction="FLAT", confluence=0.2, decision="WATCH", level_style="trend")
    setup = SetupCandidate("fx_trend_pullback", "TRADE", "LONG", (), (), "trend")
    _patch_evaluator(monkeypatch, quant, setup)
    signal = evaluate_engine_a_v3(
        _PAIR, _candle_stack(_declining_tail()), horizon="intraday"
    )
    assert signal.decision == "WATCH"
    assert "setup_upgrade_below_quant_quality_floor" in signal.rejectionReasons


def test_blocked_trend_state_does_not_demote_mr_quant_trade(monkeypatch):
    from engine_a_v3.evaluator import evaluate_engine_a_v3

    quant = _quant(
        direction="SHORT",
        confluence=2.2,
        decision="TRADE",
        level_style="mean_reversion",
        factor_diagnostics={"legacyFilters": {"trendStateBlocked": True}},
    )
    setup = SetupCandidate(None, "NO_SIGNAL", None, (), ("no_setup",), "trend")
    _patch_evaluator(monkeypatch, quant, setup)
    signal = evaluate_engine_a_v3(
        _PAIR, _candle_stack(_rising_tail()), horizon="intraday"
    )
    assert signal.decision == "TRADE"
    assert "blocked_trend_state" not in signal.rejectionReasons


def test_blocked_trend_state_demotes_trend_quant_trade(monkeypatch):
    from engine_a_v3.evaluator import evaluate_engine_a_v3

    quant = _quant(
        direction="SHORT",
        confluence=2.2,
        decision="TRADE",
        level_style="trend",
        factor_diagnostics={"legacyFilters": {"trendStateBlocked": True}},
    )
    setup = SetupCandidate(None, "NO_SIGNAL", None, (), ("no_setup",), "trend")
    _patch_evaluator(monkeypatch, quant, setup)
    signal = evaluate_engine_a_v3(
        _PAIR, _candle_stack(_rising_tail()), horizon="intraday"
    )
    assert signal.decision == "WATCH"
    assert "blocked_trend_state" in signal.rejectionReasons


# ── MR stop floor + per-asset spread geometry (2026-07-28) ───────────────────


def _mr_fade_primary():
    closes = [100.0] * 25 + [100.2, 100.4, 100.6, 100.8, 101.0]
    return _series(closes, bar_hours=1)


def test_mr_levels_min_risk_atr_floors_stop_distance():
    from engine_a_v3.levels import build_mean_reversion_levels
    from engine_a_v3.setups import atr_for_levels

    primary = _mr_fade_primary()
    atr = atr_for_levels(primary, 14)
    base = build_mean_reversion_levels(primary, direction="SHORT", min_rr=1.0)
    floored = build_mean_reversion_levels(
        primary, direction="SHORT", min_rr=1.0, min_risk_atr=1.0
    )
    assert base is not None and floored is not None
    base_risk = base.invalidation - 101.0
    floored_risk = floored.invalidation - 101.0
    assert base_risk < atr  # legacy swing+buffer is tighter than 1x ATR here
    assert floored_risk >= 1.0 * atr - 1e-9
    assert floored_risk > base_risk
    # The floor widens the stop; rr2 shrinks but still clears min_rr.
    assert floored.targets[-1].rr < base.targets[-1].rr
    assert floored.targets[-1].rr >= 1.0


def test_mr_min_risk_resolves_per_asset(monkeypatch):
    from engine_a_v3.evaluator import _build_levels

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_MEAN_REVERSION",
        {
            "SL_BUFFER_ATR": 0.5,
            "MIN_RISK_ATR": 0.0,
            "BY_ASSET": {"forex": {"MIN_RISK_ATR": 1.0}},
        },
    )
    primary = _mr_fade_primary()
    forex = _build_levels(
        primary, direction="SHORT", level_style="mean_reversion", asset_type="forex"
    )
    crypto = _build_levels(
        primary, direction="SHORT", level_style="mean_reversion", asset_type="crypto"
    )
    assert forex is not None and crypto is not None
    assert (forex.invalidation - 101.0) > (crypto.invalidation - 101.0)


def test_forex_structural_geometry_checked_in_values():
    from engine_a_v3.levels import resolve_structural_geometry

    forex = resolve_structural_geometry("forex")
    assert forex["sl_min_atr_mult"] == 1.2
    assert forex["tp1_rr"] == 1.5
    # Other classes keep the historical globals.
    crypto = resolve_structural_geometry("crypto")
    assert crypto["sl_min_atr_mult"] == 0.8
    assert crypto["tp1_rr"] == 1.0
