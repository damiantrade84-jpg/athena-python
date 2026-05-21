from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TV_PANEL = ROOT / "static/react-app/app/src/components/panels/TVChartPanel.tsx"

ENGINE_A_DIAGNOSTIC_FIXTURE = {
    "type": "forex",
    "entry": 1.1000,
    "sl": 1.0950,
    "tp": 1.1100,
    "atr": 0.0025,
    "factorScores": {"addon": 0},
    "factorDiagnostics": {
        "directionalRampMult": 1.0,
        "minDirectional": 0.25,
        "min_directional_threshold": 0.25,
        "effective_min_directional": 0.3,
        "trendCoherence": {"agreement_count": 3, "coherence_ratio": 1.0},
        "feedStatus": {"addon": "missing"},
        "addon_value": 0,
        "engineAAssetDiagnostics": {"carry": "neutral"},
    },
    "atrDiagnostics": {"atr_tf": "H4", "atr_source": "engine_a"},
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tv_chart_panel_renders_existing_component_with_advanced_chart_embed():
    source = _read(TV_PANEL)

    assert "export default function TVChartPanel" in source
    assert "embed-widget-advanced-chart.js" in source
    assert "buildTradingViewWidgetHtml" in source
    assert "srcDoc={widgetHtml}" in source
    assert "EngineASidePanel" in source


def test_indicator_toggles_update_documented_studies_config():
    source = _read(TV_PANEL)

    assert "EMA@tv-basicstudies(20)" not in source
    assert "EMA@tv-basicstudies(50)" not in source
    assert "EMA@tv-basicstudies(200)" not in source
    assert "MAExp@tv-basicstudies" in source
    assert "DoubleEMA@tv-basicstudies" in source
    assert "ATR@tv-basicstudies" in source
    assert "RSI@tv-basicstudies" in source
    assert "inputs: { length: 20 }" in source
    assert "inputs: { length: 50 }" in source
    assert "inputs: { length: 200 }" in source
    assert "inputs: { length: 14 }" in source


def test_engine_a_review_layout_enables_required_lean_indicators():
    source = _read(TV_PANEL)

    assert "Engine A Review Layout" in source
    assert "setEma20(true)" in source
    assert "setEma50(true)" in source
    assert "setEma200(true)" in source
    assert "setAtr14(true)" in source
    assert "setRsi14(true)" in source
    assert "setDema200(false)" in source
    assert "MACD@tv-basicstudies" not in source
    assert "BB@tv-basicstudies" not in source
    assert "Stochastic@tv-basicstudies" not in source


def test_visual_review_state_is_not_consumed_by_execution_paths():
    execution_files = [
        ROOT / "execution.py",
        ROOT / "risk_engine.py",
        ROOT / "guardian.py",
        ROOT / "auto_trader.py",
        ROOT / "mt5_executor.py",
        ROOT / "bybit_executor.py",
    ]
    forbidden = [
        "TVChartPanel",
        "Engine A Review Layout",
        "buildTradingViewStudies",
        "visualReview",
        "visual_review",
    ]

    for path in execution_files:
        source = _read(path)
        for token in forbidden:
            assert token not in source, f"{token} unexpectedly referenced by {path}"


def test_directional_ramp_mult_fixture_uses_current_backend_key():
    source = _read(TV_PANEL)
    assert ENGINE_A_DIAGNOSTIC_FIXTURE["factorDiagnostics"]["directionalRampMult"] == 1.0

    assert "diagnostics.directionalRampMult" in source
    assert "diagnostics.directionalRampMultiplier" in source
    assert "diagnostics.directional_ramp_multiplier" in source
    assert "firstNumber(diagnostics.directionalRampMultiplier, diagnostics.directional_ramp_multiplier)" not in source


def test_trend_coherence_fixture_fields_are_visible():
    source = _read(TV_PANEL)
    coherence = ENGINE_A_DIAGNOSTIC_FIXTURE["factorDiagnostics"]["trendCoherence"]

    assert coherence["agreement_count"] == 3
    assert coherence["coherence_ratio"] == 1.0
    assert 'label="Agreement count" value={trendCoherence.agreement_count}' in source
    assert 'label="Coherence ratio" value={trendCoherence.coherence_ratio}' in source
    assert 'label="Trend coherence" value={trendCoherence}' in source


def test_carry_addon_fixture_displays_feed_status_when_missing_or_neutral():
    source = _read(TV_PANEL)
    assert ENGINE_A_DIAGNOSTIC_FIXTURE["type"] == "forex"
    assert ENGINE_A_DIAGNOSTIC_FIXTURE["factorDiagnostics"]["feedStatus"]["addon"] == "missing"

    assert "Carry addon" in source
    assert "feedStatus.addon" in source
    assert 'label="Feed addon" value={feedStatus.addon}' in source
    assert "firstString(feedStatus.addon)" in source


def test_missing_prior_swing_levels_keeps_sl_tp_and_atr_only_explanation():
    source = _read(TV_PANEL)
    assert "priorSwingLevels" not in ENGINE_A_DIAGNOSTIC_FIXTURE

    assert 'label="SL" value={signal?.sl}' in source
    assert 'label="TP" value={firstNumber(signal?.tp, signal?.tp1)}' in source
    assert "Unavailable — Engine A SL/TP is ATR-based; no structural swing levels supplied." in source


def test_key_engine_a_diagnostics_are_not_truncated_or_hidden():
    source = _read(TV_PANEL)

    assert 'className="truncate text-right text-foreground"' not in source
    assert "DiagnosticBlock" in source
    assert "JSON.stringify(record, null, 2)" in source
    assert 'label="Feed status" value={feedStatus}' in source
    assert 'label="Engine A asset diagnostics" value={engineAAssetDiagnostics}' in source
    assert 'label="ATR diagnostics" value={atrDiagnostics}' in source
    assert "engineAAssetDiagnostics" in source
    assert "atrDiagnostics.atr_source" in source
