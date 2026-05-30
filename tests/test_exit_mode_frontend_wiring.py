"""Source-assertion tests for the Plan-4 exit-mode frontend wiring.

The React app has no JS test runner; the repo convention (see
test_signals_panel_execution.py) is to assert on .tsx/.ts source text. These
guard that the per-trade exit selector stays wired into the Engine-A execute
surfaces and that the Exit Strategy tab talks to the backend route.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static" / "react-app" / "app" / "src"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def test_helpers_export_exit_mode_builder_and_thread_into_signal():
    src = _read("lib/manualExecuteHelpers.ts")
    assert "export function buildExitModePayload" in src
    assert "exitMode?: ExitModeSelection" in src
    # Engine-B-only executes must not carry a per-trade exit_mode override
    assert "isEngineBOnly ? {} : buildExitModePayload({ exitMode })" in src
    assert "...exitModePayload" in src


def test_exit_mode_field_offers_default_plus_four_modes():
    src = _read("components/execution/ExitModeField.tsx")
    for value in ("default", "traditional_static", "adaptive_trail", "manual", "time_based"):
        assert f"value: '{value}'" in src


def test_signals_panel_renders_and_threads_exit_mode():
    src = _read("components/panels/SignalsPanel.tsx")
    assert "useExitModeState" in src
    assert "<ExitModeField" in src
    # threaded into the quick-execute payload
    assert "exitMode," in src


def test_tvchart_panel_renders_and_threads_exit_mode():
    src = _read("components/panels/TVChartPanel.tsx")
    assert "useExitModeState" in src
    assert "<ExitModeField" in src
    assert "exitMode," in src


def test_exit_strategy_tab_registered_and_calls_endpoint():
    assert "exitStrategy" in _read("types/index.ts")
    assert "exitStrategy: ExitStrategyPanel" in _read("pages/Home.tsx")
    assert "id: 'exitStrategy'" in _read("components/layout/Sidebar.tsx")
    panel = _read("components/panels/ExitStrategyPanel.tsx")
    assert "/api/exit-mode-config" in panel
    assert "advisablePipByScoreGroup" in panel
