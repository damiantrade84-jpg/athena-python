"""UI contract pins for Engine A research-only gate (grep-based).

These React/TypeScript surfaces are not exercised by pytest without a Playwright
E2E harness. Behavioral backend coverage lives in tests/test_engine_a_trade_gate.py
(execution, risk, auto_trader, engine_c, apply_fail_closed helper).
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_engine_a_signal_type_exposes_trade_gate_fields():
    src = (ROOT / "static/react-app/app/src/types/athena.ts").read_text(encoding="utf-8")

    assert "engineATradeEnabled?: boolean" in src
    assert "engineATradeGate?:" in src


def test_engine_a_card_surfaces_research_only_gate():
    src = (
        ROOT / "static/react-app/app/src/components/athena/EngineASignalCard.tsx"
    ).read_text(encoding="utf-8")

    assert "engineATradeEnabled === false" in src
    assert "Research-only" in src
    assert "trend engine disabled" in src


def test_manual_execute_helper_blocks_research_only_engine_a_signal():
    src = (ROOT / "static/react-app/app/src/lib/manualExecuteHelpers.ts").read_text(
        encoding="utf-8"
    )

    assert "engineATradeEnabled === false" in src
    assert "Research-only" in src
    assert "evaluateTvChartExecuteBlock" in src
    assert "canExecuteEngineASignalTier" in src


def test_signals_panel_can_execute_row_blocks_engine_a_research_only():
    src = (ROOT / "static/react-app/app/src/components/panels/SignalsPanel.tsx").read_text(
        encoding="utf-8"
    )

    assert "function canExecuteRow" in src
    assert "row.engines.has('A') && row.signal.engineATradeEnabled === false" in src
