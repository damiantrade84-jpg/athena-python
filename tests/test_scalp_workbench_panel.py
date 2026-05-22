from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCALP_WORKBENCH = ROOT / "static/react-app/app/src/components/panels/ScalpWorkbenchPanel.tsx"
AI_SCALP_HELPER = ROOT / "static/react-app/app/src/lib/aiScalpChartReview.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_scalp_workbench_builds_compact_ai_snapshot_from_structured_contracts():
    source = _read(SCALP_WORKBENCH)

    assert "interface ScalpChartSnapshot" in source
    assert "function buildScalpChartSnapshot" in source
    assert "selectedSignal: {" in source
    assert "marketLocation: {" in source
    assert "aggressionContext: {" in source
    assert "sourceContract: {" in source
    assert "chartCapturedAt: new Date().toISOString()" in source
    assert "lvnLevels: [...ui.marketLocation.lvnLevels]" in source
    assert "hvnLevels: [...ui.marketLocation.hvnLevels]" in source
    assert "vp_lvn_count" not in source[source.index("function buildScalpChartSnapshot"):]


def test_scalp_workbench_ai_capture_posts_server_trusted_review():
    panel = _read(SCALP_WORKBENCH)
    helper = _read(AI_SCALP_HELPER)

    assert "postScalpChartReview" in panel
    assert "downscaleToCap" in panel
    assert "/api/ai/scalp-chart-review" in helper
    assert "postScalpChartReview" in helper
    assert "ScalpAIReviewCard" in panel
    assert "scalpAiReviewResponse" in panel
    assert "postChartReview" not in panel
    assert "/api/ai/chart-review" not in panel
    assert "/api/auto-trade" not in panel

    capture_body = panel[panel.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body
    assert "execution_tf: executionTf" in capture_body
    assert "chart_timeframe: timeframe" in capture_body


def test_scalp_workbench_refresh_remains_only_scan_trigger():
    source = _read(SCALP_WORKBENCH)

    assert source.count("postScan('/api/scalp-scan'") == 1
    assert "onClick={refreshScan}" in source
    assert "onClick={captureScalpChartForAIReview}" in source
    assert "captureScalpChartForAIReview" in source
    capture_body = source[source.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body


def test_scalp_workbench_defaults_to_m5_display():
    source = _read(SCALP_WORKBENCH)

    assert "preferredScalpDisplayTf" in source
    assert "useState<(typeof TIMEFRAMES)[number]>('M5')" in source
    assert "M5 Context Chart" in source
    assert "Execution TF: M1" in source


def test_scalp_workbench_m1_still_available():
    source = _read(SCALP_WORKBENCH)

    assert "const TIMEFRAMES = ['M1', 'M5', 'M15']" in source
    assert "Display override" in source


def test_scalp_workbench_execution_tf_lock_present():
    source = _read(SCALP_WORKBENCH)

    assert "executionTf" in source
    assert "tfDisplayOverride" in source
    assert "disabled={!tfDisplayOverride}" in source
    assert "setTimeframe(executionTf)" not in source


def test_scalp_workbench_flag_watch_setup_button():
    source = _read(SCALP_WORKBENCH)

    assert "Flag / Watch Setup" in source
    assert "/api/suggested-trades/flag" in source
    assert "View Suggested Trades" in source
    flag_idx = source.index("Flag / Watch Setup")
    flag_section = source[flag_idx:flag_idx + 800]
    assert "postExecute('/api/scalp-execute'" not in flag_section


def test_scalp_workbench_execute_scalp_button():
    source = _read(SCALP_WORKBENCH)

    assert "Execute Scalp" in source
    assert "/api/scalp-execute" in source
    assert "buildScalpExecutePayload" in source
    assert "evaluateScalpExecuteBlock" in source
    assert "Confirm Scalp Execution" in source
    assert "refresh/revalidate before order" in source


def test_scalp_workbench_execute_disabled_unless_executable():
    source = _read(SCALP_WORKBENCH)

    assert "executeBlockReason" in source
    assert "strict_fabio_pass" in source or "Fabio gate failed" in source
    assert "Gate failed" in source or "gate_result" in source


def test_scalp_workbench_consumes_scalp_workbench_intent():
    source = _read(SCALP_WORKBENCH)

    assert "scalpWorkbenchIntent" in source
    assert "appliedIntentIdRef" in source
    assert "Opened from Scalp Lab" in source


def test_scalp_workbench_symbol_select_stays_controlled_before_refresh():
    source = _read(SCALP_WORKBENCH)

    assert "EMPTY_SYMBOL_SELECT_VALUE" in source
    assert "value={activeSymbolKey || EMPTY_SYMBOL_SELECT_VALUE}" in source
    assert "if (key === EMPTY_SYMBOL_SELECT_VALUE) return;" in source
    assert '<SelectItem value={EMPTY_SYMBOL_SELECT_VALUE} disabled>' in source


def test_scalp_workbench_shows_ai_grade_and_score():
    source = _read(SCALP_WORKBENCH)

    assert 'label="AI grade"' in source
    assert 'label="AI score"' in source
