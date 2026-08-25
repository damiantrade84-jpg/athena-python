"""Static UI contract for explicit OX Book scan and manual demo execution."""
from pathlib import Path


SOURCE = Path("static/react-app/app/src/components/panels/OxBookPanel.tsx")


def test_ox_book_panel_keeps_scan_and_execution_explicitly_separate():
    text = SOURCE.read_text(encoding="utf-8")

    assert '"/api/ox-book-scan"' in text or "'/api/ox-book-scan'" in text
    assert '"/api/ox-book-execute"' in text or "'/api/ox-book-execute'" in text
    assert "/api/ox-book-run" not in text
    assert "Scan Book" in text
    assert "Execute Demo Manually" in text
    assert "window.confirm" in text
    assert "Daily cycle" not in text
