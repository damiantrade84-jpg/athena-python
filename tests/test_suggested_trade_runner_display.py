from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_DISPLAY = ROOT / "static/react-app/app/src/lib/suggestedTradeRunnerDisplay.ts"
RUNNER_HOOK = ROOT / "static/react-app/app/src/hooks/useSuggestedTradeRunnerStatus.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runner_display_helper_exists():
    assert RUNNER_DISPLAY.exists()


def test_runner_display_labels():
    source = _read(RUNNER_DISPLAY)
    assert "runnerBadgeLabel" in source
    assert "runnerBadgeClass" in source
    assert "ACTIVE" in source
    assert "POLLING ONLY" in source
    assert "INACTIVE" in source
    assert "ERROR" in source


def test_runner_status_hook_polls_status_endpoint():
    source = _read(RUNNER_HOOK)
    assert "/api/suggested-trades/status" in source
    assert "useApiPoll" in source
