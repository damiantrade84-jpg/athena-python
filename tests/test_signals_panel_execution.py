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


def test_engine_b_tab_excludes_engine_a_only_and_dual_flagged_rows():
    src = _source()

    assert "isEngineBOnlyRow" in src
    assert "rowMatchesFeed" in src
    assert "!row.engines.has('A')" in src
    assert "feedRows.find((r) => r.id === selectedId)" in src
    assert "handleFeedEngineChange" in src
    assert "handleFeedEngineChange('A')" in src
    assert "handleFeedEngineChange('B')" in src
    assert "runScanA" in src and "runScanB" in src
    # Scan handlers must route tab switch through handleFeedEngineChange (clears stale selection).
    assert "const b = await scanEngineB();\n    handleFeedEngineChange('B');" in src


def test_engine_b_naked_scan_signals_use_b_presentation():
    card_src = (ROOT / "static" / "react-app" / "app" / "src" / "components" / "athena" / "EngineASignalCard.tsx").read_text(
        encoding="utf-8"
    )
    panel_src = _source()

    assert "isNakedScan" in card_src
    assert "signal.is_naked" in card_src
    assert "feedEngine === 'B' && isEngineBOnlyRow(selectedRow)" in panel_src
    assert "EngineBChecklistCard" in panel_src
