"""Evidence tests for the Engine B independent scan fix.

Confirms that Engine B scan output no longer depends on Engine A producing a
signal. Does not import athena.py. Does not mutate execution paths, AI code,
or any Engine A / Engine B thresholds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scanner import (
    ENGINE_B_SOURCE,
    _classify_engine_b_only_signal,
    _engine_b_independent_direction_probe,
    _make_engine_b_only_signal_stub,
    _make_engine_b_only_signal_stub_from_blocked_engine_a,
    _scan_signal_rank,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Engine B runs without an Engine A signal — no early-return coupling
# ---------------------------------------------------------------------------

def test_engine_b_scan_runs_without_engine_a_signal():
    """Source contract: the scan worker no longer drops the pair when
    Engine A is absent; it builds an Engine B-only stub instead.
    """
    text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    idx = text.find("if not sig_a:")
    assert idx >= 0
    region = text[idx : idx + 500]
    assert "_make_engine_b_only_signal_stub" in region
    assert "engine_b_scan_only = True" in region
    assert "return pair, None, None" not in region


def test_engine_b_only_stub_has_required_independent_fields():
    pair = {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto"}
    stub = _make_engine_b_only_signal_stub(pair)
    assert stub["engine_source"] == ENGINE_B_SOURCE
    assert stub["engine"] == "B"
    assert stub["engine_name"] == "Engine B"
    assert stub["pair"] == "BTCUSDT"
    assert stub["symbol"] == "BTCUSDT"
    assert stub["asset_type"] == "crypto"
    assert stub["direction"] is None
    assert stub["confluenceScore"] == 0.0
    assert stub["maxScore"] == 0.0
    assert stub["engine_a_present"] is False
    assert stub["enginesAligned"] is False


def test_blocked_engine_a_row_converts_to_explicit_b_only_stub():
    pair = {"display": "S&P 500", "symbol": "SPX", "type": "index"}
    blocked_a = {
        "engine_source": "ENGINE_A",
        "direction": "neutral",
        "confluenceScore": 0.0,
        "scoreNorm": 0.0,
        "dataFreshness": {
            "allowed": False,
            "reason": "STALE_DATA_PRE_SCORING",
        },
        "candleFetchMeta": {"H4": {"stale": True}},
    }

    stub = _make_engine_b_only_signal_stub_from_blocked_engine_a(pair, blocked_a)

    assert stub["engine_source"] == ENGINE_B_SOURCE
    assert stub["engine"] == "B"
    assert stub["direction"] is None
    assert stub["confluenceScore"] == 0.0
    assert stub["engine_a_present"] is False
    assert stub["engine_a_blocked"] is True
    assert stub["engine_a_block_reason"] == "STALE_DATA_PRE_SCORING"
    assert stub["engine_a_direction"] == "neutral"
    assert stub["engine_a_dataFreshness"] == blocked_a["dataFreshness"]
    assert stub["engine_a_candleFetchMeta"] == blocked_a["candleFetchMeta"]


def test_blocked_engine_a_stub_preserves_v2_abort_diagnostics():
    pair = {"display": "XAU/USD", "symbol": "GC=F", "type": "commodity"}
    blocked_a = {
        "engine_source": "ENGINE_A",
        "direction": "neutral",
        "confluenceScore": 0.0,
        "maxScore": 3.0,
        "threshold": 1.5,
        "scoreNorm": 0.0,
        "factorDiagnostics": {
            "abortReason": "indeterminate_trend",
            "engineVersion": "A_V2",
            "scorerSelected": "factor_scoring.compute_factor_scores",
        },
        "factorScores": {"trend": 0.0, "momentum": 0.0, "addon": 0.0},
    }

    stub = _make_engine_b_only_signal_stub_from_blocked_engine_a(pair, blocked_a)

    assert stub["engine_source"] == ENGINE_B_SOURCE
    assert stub["engine_a_blocked"] is True
    assert stub["engine_a_block_reason"] == "engine_a_abort:indeterminate_trend"
    assert stub["engine_a_confluenceScore"] == 0.0
    assert stub["engine_a_maxScore"] == 3.0
    assert stub["engine_a_threshold"] == 1.5
    assert stub["engine_a_factorDiagnostics"] == blocked_a["factorDiagnostics"]
    assert stub["engine_a_factorScores"] == blocked_a["factorScores"]


def test_blocked_engine_a_v3_stub_preserves_display_fields_for_ui():
    pair = {"display": "SOL/USDT", "symbol": "SOLUSDT", "type": "crypto"}
    blocked_a = {
        "engine": "ENGINE_A_V3",
        "contractVersion": "3.1.0",
        "setupId": "crypto_trend_pullback_continuation",
        "decision": "NO_SIGNAL",
        "direction": "LONG",
        "qualified": False,
        "rejectionReasons": ["trend_context_not_directional"],
        "predicates": [
            {"name": "context_trend_long", "passed": False, "actual": 0, "expected": "> 0"},
        ],
        "family": "crypto",
        "subclass": "alt_majors",
        "horizon": "swing",
        "validationStatus": "UNVALIDATED",
        "executionScope": "DEMO_ONLY",
        "exitPolicy": "SINGLE_TP1",
        "confluenceThreshold": 2.0,
        "scoringProfile": {"profileId": "fixture", "profileSha256": "a" * 64},
        "componentScores": {"trend": {"signal": 1.0, "quality": 0.8, "weight": 0.4, "contribution": 0.32, "available": True}},
    }

    stub = _make_engine_b_only_signal_stub_from_blocked_engine_a(pair, blocked_a)

    assert stub["engine_source"] == ENGINE_B_SOURCE
    assert stub["engine"] == "B"
    assert stub["engine_a_v3_blocked"] is True
    assert stub["engine_a_setupId"] == "crypto_trend_pullback_continuation"
    assert stub["engine_a_decision"] == "NO_SIGNAL"
    assert stub["engine_a_predicates"] == blocked_a["predicates"]
    assert stub["engine_a_rejectionReasons"] == blocked_a["rejectionReasons"]
    assert stub["engine_a_family"] == "crypto"
    assert stub["engine_a_executionScope"] == "DEMO_ONLY"
    assert stub["engine_a_exitPolicy"] == "SINGLE_TP1"
    assert stub["engine_a_confluenceThreshold"] == 2.0
    assert stub["engine_a_scoringProfile"] == blocked_a["scoringProfile"]
    assert stub["engine_a_componentScores"] == blocked_a["componentScores"]


def test_scan_rank_handles_b_only_zero_max_score():
    pair = {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto"}
    stub = _make_engine_b_only_signal_stub(pair)

    assert stub["maxScore"] == 0.0
    assert _scan_signal_rank(stub) == 0.0


# ---------------------------------------------------------------------------
# 2. Engine B direction probe is independent of Engine A
# ---------------------------------------------------------------------------

class _FakeEngine:
    """Minimal Engine B stub used to exercise the direction probe without
    touching the real NakedEngine (which requires live data feeds)."""

    def __init__(self, per_direction_results: dict[str, dict[str, Any]]):
        self._per_direction = per_direction_results

    def set_registry_context(self, _symbol):
        return self

    def analyze_structure(self, *_args, **_kwargs):
        direction = _args[4]
        return dict(self._per_direction.get(direction, {"structural_verdict": "NONE"}))

    def calculate_confidence(self, res, _price, direction, **_kwargs):
        return {
            "score": res.get("_score", 0.0),
            "max_possible": 5.0,
            "passed": bool(res.get("_passed", False)),
            "failed_gate_names": list(res.get("_failed_gates", [])),
            "rr_ok": bool(res.get("_rr_ok", True)),
        }


def test_engine_b_independent_direction_probe_picks_best_passing_direction(monkeypatch):
    # Bypass real Engine B confidence-gate logic — we only care about probe wiring.
    import scanner as scanner_mod

    monkeypatch.setattr(
        scanner_mod,
        "engine_b_confidence_passes",
        lambda conf, *_a, **_kw: (bool(conf.get("passed", False)), 0.0),
    )

    fake = _FakeEngine({
        "LONG": {"structural_verdict": "CLEAR", "_score": 3.5, "_passed": True},
        "SHORT": {"structural_verdict": "CLEAR", "_score": 4.5, "_passed": False},
    })
    direction, res_b, conf_b = _engine_b_independent_direction_probe(
        {"display": "X", "symbol": "X", "type": "crypto"},
        engine=fake,
        d1_candles=[], zone_candles=[], entry_candles=[],
        current_price=100.0, atr=1.0, regime_label="TRENDING",
        style_profile={"fallback_rr": 2.0, "min_rr": 1.5},
        resolved_style="intraday",
        asset_type="crypto",
        d1_snap={}, h4_snap={},
    )
    # Even though SHORT scores higher, LONG passed gate so it wins.
    assert direction == "LONG"
    assert conf_b is not None and conf_b["passed"] is True


def test_engine_b_independent_direction_probe_returns_none_when_no_clear(monkeypatch):
    import scanner as scanner_mod
    monkeypatch.setattr(
        scanner_mod,
        "engine_b_confidence_passes",
        lambda *_a, **_kw: (False, 0.0),
    )
    fake = _FakeEngine({
        "LONG": {"structural_verdict": "UNCLEAR"},
        "SHORT": {"structural_verdict": "NONE"},
    })
    direction, res_b, conf_b = _engine_b_independent_direction_probe(
        {"display": "X", "symbol": "X", "type": "forex"},
        engine=fake,
        d1_candles=[], zone_candles=[], entry_candles=[],
        current_price=1.10, atr=0.001, regime_label="RANGING",
        style_profile={"fallback_rr": 2.0, "min_rr": 1.5},
        resolved_style="intraday",
        asset_type="forex",
        d1_snap={}, h4_snap={},
    )
    assert direction is None
    assert res_b is None
    assert conf_b is None


# ---------------------------------------------------------------------------
# 3. Engine A path remains separate (analyze_pair does not depend on Engine B)
# ---------------------------------------------------------------------------

def test_engine_a_core_does_not_import_engine_b():
    """The Engine A scoring core (scoring.py, factor_scoring.py) must not
    import Engine B's market_structure module — A and B remain isolated.
    """
    for fname in ("scoring.py", "factor_scoring.py"):
        text = (REPO_ROOT / fname).read_text(encoding="utf-8")
        assert "import market_structure" not in text, (
            f"{fname} should not import market_structure (Engine B)"
        )
        assert "from market_structure" not in text, (
            f"{fname} should not from-import market_structure (Engine B)"
        )


# ---------------------------------------------------------------------------
# 4. Engine C is the A+B combiner
# ---------------------------------------------------------------------------

def test_engine_c_combines_engine_a_and_engine_b():
    text = (REPO_ROOT / "engine_c.py").read_text(encoding="utf-8")
    # Engine C must reference both engines' weights and consensus blend.
    assert "ENGINE_C_AB_WEIGHTS" in text
    # The full-scan A/B blend in scanner uses the same Engine C weights —
    # confirms the only A/B blend semantics live in engine_c.
    scanner_text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    assert "from engine_c import ENGINE_C_AB_WEIGHTS" in scanner_text


# ---------------------------------------------------------------------------
# 5. Full scan returns Engine B rows independently
# ---------------------------------------------------------------------------

def test_full_scan_routes_engine_b_only_rows_through_b_classifier_path():
    """Source contract: the post-buffer loop must branch on
    ``engine_source == ENGINE_B_SOURCE`` and call the B-only classifier so
    Engine B rows don't get classified by Engine A thresholds.
    """
    text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    assert 'if sig.get("engine_source") == ENGINE_B_SOURCE:' in text
    assert "_classify_engine_b_only_signal(sig, pair)" in text
    assert "_annotate_engine_b_only_signal_for_scan(" in text


# ---------------------------------------------------------------------------
# 6. Crypto Engine B without Bybit ATR emits a visible rejection
# ---------------------------------------------------------------------------

def test_crypto_engine_b_missing_bybit_atr_reports_rejection_visibly():
    """Engine B classifier must surface a B-specific error (or its skip-stage)
    rather than hiding the failure behind Engine A's absence.
    """
    pair = {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_error"] = "bybit_atr_unavailable"
    sig["scanDiagnostics"] = [
        {"code": "engine_b_error", "detail": "bybit_atr_unavailable"}
    ]
    tier, reason = _classify_engine_b_only_signal(sig, pair)
    assert tier == "skip"
    assert "bybit_atr_unavailable" in reason


def test_crypto_engine_b_skip_stage_surfaced_in_b_only_classifier():
    pair = {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_scan_gate_funnel"] = {
        "engine_b_skip_stage": "crypto_bybit_atr_unavailable",
        "structure_verdict": None,
        "failed_gate_names": [],
    }
    tier, reason = _classify_engine_b_only_signal(sig, pair)
    assert tier == "skip"
    assert "crypto_bybit_atr_unavailable" in reason


# ---------------------------------------------------------------------------
# 7. Engine B classification does not use Engine A threshold
# ---------------------------------------------------------------------------

def test_engine_b_classifier_does_not_consult_engine_a_threshold(monkeypatch):
    """Engine B-only rows have ``confluenceScore=0`` and no ``scanThreshold``.
    The classifier must still route them based on Engine B gates only —
    never via Engine A's threshold-vs-score logic.
    """
    pair = {"display": "EURUSD", "symbol": "EURUSD", "type": "forex", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_verdict"] = "CLEAR"
    sig["engine_b_confidence_passed"] = True
    sig["engine_b_direction"] = "LONG"
    sig["engine_b_score"] = 4.2
    sig["engine_b_max"] = 5.0
    tier, reason = _classify_engine_b_only_signal(sig, pair)
    assert tier == "watchlist"
    assert "Engine B-only watchlist" in reason
    # Sanity: B-only classifier never returns "trade" for a B-only row.
    sig["confluenceScore"] = 999.0  # would smash Engine A threshold; must be ignored.
    tier2, _ = _classify_engine_b_only_signal(sig, pair)
    assert tier2 != "trade"


def test_engine_b_classifier_uses_only_engine_b_gates_when_passed_is_false():
    pair = {"display": "EURUSD", "symbol": "EURUSD", "type": "forex", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_verdict"] = "CLEAR"
    sig["engine_b_confidence_passed"] = False
    sig["engine_b_failed_gate_names"] = ["struct", "loc"]
    tier, reason = _classify_engine_b_only_signal(sig, pair)
    assert tier == "skip"
    assert "Engine B gates failed" in reason
    assert "struct" in reason


# ---------------------------------------------------------------------------
# 8. RR failure is visible in Engine B scan output
# ---------------------------------------------------------------------------

def test_rr_failure_is_visible_in_engine_b_only_classifier():
    pair = {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_verdict"] = "CLEAR"
    sig["engine_b_confidence_passed"] = False
    sig["engine_b_failed_gate_names"] = ["rr=0.8"]
    tier, reason = _classify_engine_b_only_signal(sig, pair)
    assert tier == "skip"
    # RR failure must be labelled explicitly so it doesn't disappear behind
    # "Engine A missing" or a generic gate fail.
    assert "RR" in reason or "rr" in reason
    assert "rr=0.8" in reason


def test_rr_gate_failure_surfaced_from_engine_b_funnel():
    pair = {"display": "XAUUSD", "symbol": "XAUUSD", "type": "commodity", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_verdict"] = "CLEAR"
    sig["engine_b_confidence_passed"] = False
    sig["engine_b_scan_gate_funnel"] = {
        "engine_b_skip_stage": None,
        "structure_verdict": "CLEAR",
        "failed_gate_names": ["rr_gate"],
    }
    tier, reason = _classify_engine_b_only_signal(sig, pair)
    assert tier == "skip"
    assert "rr" in reason.lower()


# ---------------------------------------------------------------------------
# Safety: independence does not promote B-only setups to live auto-trade
# ---------------------------------------------------------------------------

def test_engine_b_only_classifier_never_returns_trade_tier():
    pair = {"display": "EURUSD", "symbol": "EURUSD", "type": "forex", "enabled": True}
    sig = _make_engine_b_only_signal_stub(pair)
    sig["engine_b_verdict"] = "CLEAR"
    sig["engine_b_confidence_passed"] = True
    sig["engine_b_direction"] = "LONG"
    # Even with every Engine A signal field maxed out, B-only rows must NOT
    # be tier="trade". Live auto-trade requires the full A-driven path.
    sig["confluenceScore"] = 99.0
    sig["scoreNorm"] = 1.0
    sig["enginesAligned"] = True
    tier, _ = _classify_engine_b_only_signal(sig, pair)
    assert tier in {"watchlist", "skip"}


# ---------------------------------------------------------------------------
# v3 isolation: Engine B scan path must not branch on v3 overlay
# ---------------------------------------------------------------------------

def test_v3_no_signal_with_direction_does_not_force_b_only_probe():
    """v3 NO_SIGNAL can still carry LONG/SHORT — B must use that direction,
    not the independent LONG+SHORT probe (pre-v3 contract)."""
    text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    assert "_v3_overlay" not in text
    assert (
        '_is_engine_a_v3_signal(sig_a) and sig_a.get("decision") == "NO_SIGNAL"'
        not in text
    )


def test_scanner_tier_loop_applies_b_gates_after_v3_classify():
    """Engine B scan gates must run for v3 rows — not only legacy Engine A rows."""
    text = (REPO_ROOT / "scanner.py").read_text(encoding="utf-8")
    marker = "Engine B scan gates run for all non-B-only rows"
    assert marker in text
    idx = text.index(marker)
    tail = text[idx : idx + 400]
    assert "_apply_engine_b_scan_gate" in tail
    assert "_apply_engine_b_only_watchlist_scan_tier" in tail
    assert "_apply_engine_b_structure_ready_scan_tier" in tail

