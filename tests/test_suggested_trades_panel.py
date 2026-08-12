from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "static/react-app/app/src/components/panels/SuggestedTradesPanel.tsx"
HOME = ROOT / "static/react-app/app/src/pages/Home.tsx"
SIDEBAR = ROOT / "static/react-app/app/src/components/layout/Sidebar.tsx"
TYPES = ROOT / "static/react-app/app/src/types/index.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_suggested_trades_panel_exists():
    assert PANEL.exists()


def test_suggested_trades_panel_polls_list_endpoint():
    source = _read(PANEL)
    assert "/api/suggested-trades" in source
    assert "getJson('/api/suggested-trades')" in source or 'getJson("/api/suggested-trades")' in source


def test_suggested_trades_panel_evaluate_now_fallback():
    source = _read(PANEL)
    assert "/api/suggested-trades/evaluate-now" in source
    assert "needsFrontendEval" in source


def test_suggested_trades_panel_runner_status_labels():
    source = _read(PANEL)
    assert "Runner:" in source
    assert "Alert-only" in source
    assert "Runner inactive" in source


def test_suggested_trades_panel_open_actions():
    source = _read(PANEL)
    assert "Open in TV Chart" in source
    assert "Open in Scalp Workbench" in source
    assert "Cancel Watch" in source
    assert "Copy watch JSON" in source


def test_suggested_trades_panel_does_not_execute():
    source = _read(PANEL)
    assert "/api/quick-execute" not in source
    assert "/api/scalp-execute" not in source


def test_suggested_trades_panel_keeps_full_watch_table():
    source = _read(PANEL)

    assert "visibleWatches.map" in source or "watches.map" in source or "watches)" in source
    assert "Cancel Watch" in source
    assert "Evaluate now" in source or "evaluateNow" in source
    assert "watchProgressText" in source or "Ready for review" in source or "LEVEL_REACHED" in source


def test_panel_id_includes_suggested_trades():
    source = _read(TYPES)
    assert "'suggestedTrades'" in source


def test_home_registers_suggested_trades_panel():
    source = _read(HOME)
    assert "SuggestedTradesPanel" in source
    assert "suggestedTrades: SuggestedTradesPanel" in source


def test_sidebar_includes_suggested_trades_nav():
    source = _read(SIDEBAR)
    assert "Suggested Trades" in source
    assert "'suggestedTrades'" in source
    assert "useSuggestedTradeRunnerStatus" in source
    assert "runnerBadgeLabel" in source


def test_suggested_trades_panel_ready_cta_deep_links_to_chart():
    source = _read(PANEL)
    assert "Review & Execute" in source
    assert "Ready — review & execute on chart" in source
    # Ready CTA reuses chart intents; execution still happens only on chart panels.
    assert "openInTvChart" in source
    assert "openInScalpWorkbench" in source


def test_suggested_trades_panel_sorts_ready_first():
    source = _read(PANEL)
    assert "STATUS_RANK" in source
    assert "READY_FOR_REVIEW" in source


def test_suggested_trades_panel_clear_finished():
    source = _read(PANEL)
    assert "/api/suggested-trades/clear-terminal" in source
    assert "Clear finished" in source
    assert "Show finished watches" in source


def test_suggested_trades_panel_shows_playbook_warnings():
    source = _read(PANEL)
    assert "Playbook warnings" in source
    assert "data-playbook-warnings" in source
    assert "playbook_warnings" in source


def test_suggested_trades_panel_toasts_on_new_ready():
    source = _read(PANEL)
    assert "Suggested trade ready:" in source
    assert "seenReadyIdsRef" in source


def test_sidebar_shows_suggested_ready_count():
    source = _read(SIDEBAR)
    assert "readyCount" in source
    assert "ready for review" in source
