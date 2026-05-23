from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDER_READY = ROOT / "static/react-app/app/src/lib/scalpWorkbenchChart/renderReady.ts"
SCALP_WORKBENCH = ROOT / "static/react-app/app/src/components/panels/ScalpWorkbenchPanel.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_render_ready_requires_native_canvas():
    source = _read(RENDER_READY)

    assert "nativeCanvasReady: boolean" in source
    assert "export function hasScalpNativeCanvas" in source
    assert "missing.push('nativeCanvas')" in source
    assert "timeoutMs = 8000" in source


def test_scalp_workbench_passes_native_canvas_into_render_wait():
    panel = _read(SCALP_WORKBENCH)

    assert "hasScalpNativeCanvas" in panel
    assert "nativeCanvasReady: hasScalpNativeCanvas(captureEl)" in panel
    assert "chart canvas not ready yet" in panel


def test_scalp_workbench_gates_auto_review_and_button_on_chart_ready():
    panel = _read(SCALP_WORKBENCH)

    assert "const [chartReady, setChartReady] = useState(false)" in panel
    assert "setChartReady(true)" in panel
    assert "setChartReady(false)" in panel
    assert "!chartReady" in panel
    assert "disabled={!aiReviewEligible || chartLoading || aiReviewLoading || !chartReady}" in panel

    marker = "if (!pendingAutoReviewRef.current || chartLoading"
    assert marker in panel
    auto_review_line = panel[panel.index(marker): panel.index(marker) + 200]
    assert "!chartReady" in auto_review_line
    assert "chartReady, candlePayload, captureScalpChartForAIReview" in panel
