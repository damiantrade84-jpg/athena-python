from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "static" / "react-app" / "app" / "src"


def _read(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def test_engine_a_signal_type_exposes_v3_contract():
    source = _read("types/athena.ts")
    for field in (
        "contractVersion",
        "signalId",
        "family",
        "subclass",
        "horizon",
        "setupId",
        "decision",
        "qualified",
        "entryZone",
        "invalidation",
        "targets",
        "predicates",
        "rejectionReasons",
        "validationArtifact",
    ):
        assert f"{field}?" in source


def test_signal_and_tv_chart_render_v3_setup_contract():
    card = _read("components/athena/EngineASignalCard.tsx")
    chart = _read("components/panels/TVChartPanel.tsx")

    assert "EngineAV3SignalCard" in card
    assert "Trigger zone" in card
    assert "Predicates" in card
    assert "validationArtifact.artifactId" in card
    assert "EngineAV3SidePanel" in chart
    assert "Deterministic predicates" in chart
    assert "AI review is advisory only" in chart


def test_trend_coherence_display_uses_dynamic_policy_timeframes():
    card = _read("components/athena/EngineASignalCard.tsx")
    helper = _read("lib/engineADiagnosticsDisplay.ts")
    lab = _read("components/panels/ExperimentLabPanel.tsx")

    assert "resolveTrendCoherenceTimeframeRows(fd)" in card
    assert "trendCoherence.d1" not in card
    assert "trendCoherence.h4" not in card
    assert "trendCoherence.h1" not in card
    assert "trendCoherence.per_tf" in helper
    assert "timeframeComparison" in lab
    assert "Trend coherence TFs" in lab


def test_v3_manual_execute_uses_decision_and_ai_cannot_veto():
    source = _read("lib/manualExecuteHelpers.ts")
    v3_branch = source.index("if (isV3) {")
    decision_gate = source.index("signal.decision === 'TRADE'", v3_branch)
    ai_gate = source.index("aiReviewBlocksManualExecute(aiReview)")

    assert v3_branch < decision_gate < ai_gate
    assert "signal.qualified === true" in source
    assert "signal.engineATradeEnabled === true" in source
    assert "if (isV3) return null;" in source


def test_v3_headline_score_uses_canonical_decision_score():
    card = _read("components/athena/EngineASignalCard.tsx")
    helper = _read("lib/athenaFormat.ts")

    assert "scoreBreakdown?.decisionScore ??" in card
    assert "const decisionScore = canonicalScore;" in helper
    assert "label=\"Adjusted display\"" in card


def test_signals_panel_does_not_offer_style_override_for_v3():
    source = _read("components/panels/SignalsPanel.tsx")
    assert "V3 execution re-validates the fixed" in source
    assert "s.horizon === 'intraday' ? 'intraday' : 'swing'" in source
