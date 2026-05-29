from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "static" / "react-app" / "app" / "src" / "components" / "panels" / "LotteryLabPanel.tsx"


def _panel_source() -> str:
    return PANEL.read_text(encoding="utf-8")


def test_lottery_lab_react_exposes_non_destructive_endpoint_coverage():
    src = _panel_source()
    expected_endpoints = [
        "/api/lottery/dashboard",
        "/api/lottery/frequency",
        "/api/lottery/draws",
        "/api/lottery/import",
        "/api/lottery/add-draw",
        "/api/lottery/generate",
        "/api/lottery/ai-analysis",
        "/api/lottery/pairs",
        "/api/lottery/triplets",
        "/api/lottery/distributions",
        "/api/lottery/sum-range",
        "/api/lottery/positional",
        "/api/lottery/rolling-frequency",
        "/api/lottery/pair-lift",
        "/api/lottery/anomalous-draws",
        "/api/lottery/bonus-intelligence",
        "/api/lottery/wheel",
        "/api/lottery/score-ticket",
        "/api/lottery/simulate",
        "/api/lottery/compare-modes",
    ]

    missing = [endpoint for endpoint in expected_endpoints if endpoint not in src]
    assert missing == []


def test_lottery_lab_react_does_not_expose_destructive_routes():
    src = _panel_source()
    assert "/api/lottery/clear" not in src
    assert "/api/lottery/delete-draw" not in src


def test_lottery_lab_react_refreshes_read_models_after_data_mutations():
    src = _panel_source()
    assert "refreshDashboard" in src
    assert "refreshFrequency" in src
    assert "refreshDraws" in src
    assert "refreshReadModels" in src
    assert src.count("refreshReadModels()") >= 2


def test_lottery_lab_react_has_shared_filter_state():
    src = _panel_source()
    for token in ["startDate", "endDate", "includeBonus", "limit", "windowSize"]:
        assert token in src
