"""Forex factor workstation frontend wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "static/react-app/app/src/components/panels/ForexFactorPanel.tsx"
SCANNER = ROOT / "static/react-app/app/src/components/panels/forexFactor/ScannerTab.tsx"
SIDEBAR = ROOT / "static/react-app/app/src/components/layout/Sidebar.tsx"
API = ROOT / "static/react-app/app/src/lib/forexFactorApi.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_forex_panel_defaults_to_scanner_and_trading_badges():
    source = _read(PANEL)
    assert 'defaultValue="scanner"' in source
    assert '<TabsTrigger value="scanner"' in source
    assert "FACTOR LIVE" in source
    assert "SCAN" in source
    assert "DEMO EXECUTION" in source
    assert "RESEARCH" not in source


def test_sidebar_badge_is_fx_not_research():
    source = _read(SIDEBAR)
    assert "id: 'forexFactor'" in source
    assert "badge: 'FX'" in source
    assert "badge: 'RESEARCH'" not in source


def test_scanner_tab_calls_scan_and_execute_candidate_endpoints():
    source = _read(SCANNER)
    assert "postForexScan" in source
    assert "postForexExecuteCandidate" in source
    assert "Scan Forex" in source
    assert "Dry-run" in source
    assert "Demo" in source
    assert "Hard Gates" in source
    assert "These gates are not score components" in source


def test_scanner_controls_do_not_push_action_buttons_offscreen():
    source = _read(SCANNER)
    assert "lg:grid-cols-[minmax(260px,1fr)_auto]" not in source
    assert 'data-testid="fx-scanner-controls"' in source
    assert 'data-testid="fx-scanner-actions"' in source
    assert "flex flex-col gap-3" in source
    assert "w-full sm:w-auto" in source
    assert "sm:justify-end" not in source


def test_scanner_candidate_actions_are_near_left_edge():
    source = _read(SCANNER)
    actions_idx = source.index("<TableHead>Actions</TableHead>")
    score_idx = source.index('<TableHead className="text-right">Score</TableHead>')
    reason_idx = source.index("<TableHead>Reason</TableHead>")
    assert actions_idx < score_idx < reason_idx


def test_forex_api_exposes_scan_status_and_execute_helpers():
    source = _read(API)
    assert "'/api/forex/scan'" in source
    assert "'/api/forex/scan/latest'" in source
    assert "'/api/forex/trading-status'" in source
    assert "'/api/forex/execute-candidate'" in source
