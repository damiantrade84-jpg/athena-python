from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNALS_PANEL = ROOT / "static" / "react-app" / "app" / "src" / "components" / "panels" / "SignalsPanel.tsx"


def _source() -> str:
    return SIGNALS_PANEL.read_text(encoding="utf-8")


def test_signals_panel_open_and_review_action():
    src = _source()

    assert "Open & Review" in src or "Open &amp; Review" in src
    assert "setTvChartIntent" in src
    assert "setActivePanel('tvChart')" in src
    assert "autoReview: true" in src
    assert "preferredTvChartTf" in src


def test_engine_b_checklist_shows_profile_unavailable_note():
    src = (ROOT / "static" / "react-app" / "app" / "src" / "components" / "athena" / "EngineBChecklistCard.tsx").read_text(
        encoding="utf-8"
    )
    assert "Volume profile (POC/VAH/VAL) not used for this asset" in src
    assert "profileUnavailable" in src


def test_engine_a_execute_surfaces_quick_execute_error_reason():
    src = _source()

    assert "buildQuickExecutePayload" in src
    assert "apiClient.postJson(" in src
    assert "'/api/quick-execute'" in src
    assert "Execution failed: ${err instanceof Error ? err.message : 'unknown'}" in src
