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
    assert "/api/scalp-execute" not in panel
    assert "/api/auto-trade" not in panel

    capture_body = panel[panel.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body
    assert "executionTf" in capture_body
    assert "execution_tf" in capture_body


def test_scalp_workbench_refresh_remains_only_scan_trigger():
    source = _read(SCALP_WORKBENCH)

    assert source.count("postScan('/api/scalp-scan'") == 1
    assert "onClick={refreshScan}" in source
    assert "onClick={captureScalpChartForAIReview}" in source
    assert "captureScalpChartForAIReview" in source
    capture_body = source[source.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body


def test_scalp_workbench_execution_tf_lock_present():
    source = _read(SCALP_WORKBENCH)

    assert "executionTf" in source
    assert "tfDisplayOverride" in source
    assert "Display override" in source
    assert "disabled={!tfDisplayOverride}" in source
    assert "setTimeframe(executionTf)" in source


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
