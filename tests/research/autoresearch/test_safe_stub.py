"""safe_stub mode patch generation and evaluator tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from research.autoresearch.safety import extract_files_from_patch

ROOT = Path(__file__).resolve().parents[3]


def test_safe_stub_creates_candidate_patch(monkeypatch):
    from research.autoresearch.autorun_loop import _create_stub_patch

    monkeypatch.setattr(
        "research.autoresearch.autorun_loop._write_stub_config",
        lambda _scenario: None,
    )
    patch = _create_stub_patch("test_safe_stub_cleanup.patch", "candidate_good")
    try:
        assert patch.exists()
        text = patch.read_text(encoding="utf-8")
        assert "candidate_good" in text
        files = extract_files_from_patch(text)
        assert "research/autoresearch/stub/stub_config.yaml" in files
    finally:
        patch.unlink(missing_ok=True)


def test_stub_evaluator_baseline_outputs_metrics():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "research/autoresearch/stub_evaluator.py"), "--scenario", "baseline"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert "metrics" in payload
    assert payload["metrics"]["trade_count"] >= 30


def test_stub_evaluator_reads_patched_scenario():
    from research.autoresearch.stub_evaluator import SCENARIOS

    assert SCENARIOS["candidate_good"]["expR"] > SCENARIOS["baseline"]["expR"]
