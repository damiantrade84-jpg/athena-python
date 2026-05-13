from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PANEL = ROOT / "static" / "react-app" / "app" / "src" / "components" / "panels" / "DashboardPanel.tsx"
SIDEBAR = ROOT / "static" / "react-app" / "app" / "src" / "components" / "layout" / "Sidebar.tsx"


def test_dashboard_auto_trade_toggle_posts_to_server():
    src = DASHBOARD_PANEL.read_text(encoding="utf-8")

    assert "const { showToast } = useStore()" in src
    assert "postAutoTrade('/api/auto-trade', { action })" in src
    assert "const serverAutoTradeEnabled = Boolean(autoTrade?.enabled)" in src
    assert "onClick={handleAutoTradeToggle}" in src
    assert "variant={serverAutoTradeEnabled ? 'default' : 'outline'}" in src
    assert "toggleAutoTrade" not in src
    assert "isAutoTrade" not in src


def test_sidebar_auto_trade_switch_posts_to_server():
    src = SIDEBAR.read_text(encoding="utf-8")

    assert "useApiPoll<{ enabled: boolean }>('/api/auto-trade', 15000)" in src
    assert "postAutoTrade('/api/auto-trade', { action: checked ? 'on' : 'off' })" in src
    assert "checked={serverAutoTradeEnabled}" in src
    assert "onCheckedChange={handleAutoTradeToggle}" in src
    assert "toggleAutoTrade" not in src
    assert "isAutoTrade" not in src
