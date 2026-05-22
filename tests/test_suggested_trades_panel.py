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
