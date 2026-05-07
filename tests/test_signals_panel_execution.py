from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNALS_PANEL = ROOT / "static" / "react-app" / "app" / "src" / "components" / "panels" / "SignalsPanel.tsx"


def _source() -> str:
    return SIGNALS_PANEL.read_text(encoding="utf-8")


def test_engine_a_execute_surfaces_quick_execute_error_reason():
    src = _source()

    assert "apiClient.postJson(" in src
    assert "'/api/quick-execute'" in src
    assert "Execution failed: ${err instanceof Error ? err.message : 'unknown'}" in src


def test_engine_a_execute_no_longer_replaces_http_error_with_generic_failure():
    src = _source()

    assert "showToast('Execution failed', 'error')" not in src


def test_engine_a_execute_strips_engine_b_overlay_before_submit():
    src = _source()

    assert "function signalForExecutionPayload(signal: EngineASignal, source: ScanSource)" in src
    assert "if (source !== 'A') return signal" in src
    assert "delete payload.engine_b" in src
    assert "delete payload.naked_data" in src
    assert "delete payload.is_naked" in src
    assert "const signalPayload = signalForExecutionPayload(confirmSig, baseSource)" in src
    assert "baseSource === 'B'" in src
