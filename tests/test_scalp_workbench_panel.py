from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCALP_WORKBENCH = ROOT / "static/react-app/app/src/components/panels/ScalpWorkbenchPanel.tsx"


def _read() -> str:
    return SCALP_WORKBENCH.read_text(encoding="utf-8")


def test_scalp_workbench_builds_compact_ai_snapshot_from_structured_contracts():
    source = _read()

    assert "interface ScalpChartSnapshot" in source
    assert "interface ScalpAIReview" in source
    assert "function buildScalpChartSnapshot" in source
    assert "selectedSignal: {" in source
    assert "marketLocation: {" in source
    assert "aggressionContext: {" in source
    assert "sourceContract: {" in source
    assert "chartCapturedAt: new Date().toISOString()" in source
    assert "lvnLevels: [...ui.marketLocation.lvnLevels]" in source
    assert "hvnLevels: [...ui.marketLocation.hvnLevels]" in source
    assert "vp_lvn_count" not in source[source.index("function buildScalpChartSnapshot"):]


def test_scalp_workbench_ai_capture_is_preview_only_and_hides_base64_by_default():
    source = _read()

    assert "scalpAiReviewPreview" in source
    assert "captureScalpChartForAIReview" in source
    assert "captureNativeChartCanvas" in source
    assert "querySelectorAll('canvas')" in source
    assert "Capture for AI review" in source
    assert "AI review payload preview" in source
    assert "base64 hidden" in source
    assert "screenshotBase64" in source
    assert "expectedResultShape: null" in source
    assert "outputContract: 'scalpAIReview'" in source
    assert "postChartReview" not in source
    assert "/api/ai/chart-review" not in source
    assert "/api/scalp-execute" not in source
    assert "/api/auto-trade" not in source


def test_scalp_workbench_refresh_remains_only_scan_trigger():
    source = _read()

    assert source.count("postScan('/api/scalp-scan'") == 1
    assert "onClick={refreshScan}" in source
    assert "onClick={captureScalpChartForAIReview}" in source
    assert "captureScalpChartForAIReview" in source
    capture_body = source[source.index("const captureScalpChartForAIReview"):]
    capture_body = capture_body[: capture_body.index("useEffect", 1)]
    assert "postScan('/api/scalp-scan'" not in capture_body
    assert "apiClient.post" not in capture_body


def test_scalp_workbench_symbol_select_stays_controlled_before_refresh():
    source = _read()

    assert "EMPTY_SYMBOL_SELECT_VALUE" in source
    assert "value={activeSymbolKey || EMPTY_SYMBOL_SELECT_VALUE}" in source
    assert "if (key === EMPTY_SYMBOL_SELECT_VALUE) return;" in source
    assert '<SelectItem value={EMPTY_SYMBOL_SELECT_VALUE} disabled>' in source
