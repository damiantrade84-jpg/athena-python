from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_open_trades_panels_poll_broker_positions_frequently():
    dashboard = ROOT / "static/react-app/app/src/components/panels/DashboardPanel.tsx"
    trades = ROOT / "static/react-app/app/src/components/panels/TradesPanel.tsx"

    dashboard_source = dashboard.read_text(encoding="utf-8")
    trades_source = trades.read_text(encoding="utf-8")

    assert "const OPEN_TRADES_POLL_MS = 2000;" in dashboard_source
    assert "const OPEN_TRADES_POLL_MS = 2000;" in trades_source
    assert "useApiPoll<OpenTrade[] | OpenTradesTimedResponse>('/api/open-trades-timed', OPEN_TRADES_POLL_MS)" in dashboard_source
    assert "useApiPoll<OpenTradesTimedResp>('/api/open-trades-timed', OPEN_TRADES_POLL_MS)" in trades_source
