"""Integration tests for candidate processing and ledger writes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from research.autoresearch.core import process_candidate
from research.autoresearch.metrics import compute_score
from research.autoresearch.stub_evaluator import SCENARIOS

ROOT = Path(__file__).resolve().parents[3]
STUB_EVAL = ROOT / "research" / "autoresearch" / "stub_evaluator.py"


def _setup_evaluator(repo: Path) -> None:
    dest = repo / "research" / "autoresearch" / "stub_evaluator.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STUB_EVAL, dest)


def _eval_command(repo: Path) -> str:
    stub_cfg = repo / "research" / "autoresearch" / "stub" / "stub_config.yaml"
    return (
        f"{sys.executable} research/autoresearch/stub_evaluator.py "
        f"--scenario evaluate --config {stub_cfg.as_posix()}"
    )


def _patch(repo: Path, scenario: str) -> Path:
    rel = "research/autoresearch/stub/stub_config.yaml"
    candidates = repo / "research" / "autoresearch" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    patch = candidates / f"{scenario}.patch"
    patch.write_text(
        "\n".join(
            [
                f"diff --git a/{rel} b/{rel}",
                f"--- a/{rel}",
                f"+++ b/{rel}",
                "@@ -1,2 +1,2 @@",
                " # Research-only stub config for AutoResearch safe_stub mode.",
                "-evaluator_scenario: baseline",
                f"+evaluator_scenario: {scenario}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return patch


def test_accepted_candidate_writes_ledger_entry(git_repo, default_config):
    repo = git_repo
    _setup_evaluator(repo)
    ledger = repo / "results_ledger.jsonl"
    baseline_metrics = SCENARIOS["baseline"]
    baseline_score = compute_score(baseline_metrics, default_config)

    cfg = dict(default_config)
    cfg["evaluation_command"] = _eval_command(repo)
    cfg["test_command"] = ""

    entry = process_candidate(
        patch_path=_patch(repo, "candidate_good"),
        config=cfg,
        repo=repo,
        ledger_path=ledger,
        baseline_metrics=baseline_metrics,
        baseline_score=baseline_score,
        iteration=1,
        mode="test",
    )

    assert entry["accepted"] is True
    assert ledger.exists()
    saved = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[0])
    assert saved["accepted"] is True
    assert saved["candidate_score"] > saved["baseline_score"]


def test_rejected_candidate_reverts_touched_file(git_repo, default_config):
    repo = git_repo
    _setup_evaluator(repo)
    ledger = repo / "results_ledger.jsonl"
    stub = repo / "research" / "autoresearch" / "stub" / "stub_config.yaml"
    baseline_metrics = SCENARIOS["baseline"]
    baseline_score = compute_score(baseline_metrics, default_config)

    cfg = dict(default_config)
    cfg["evaluation_command"] = _eval_command(repo)
    cfg["test_command"] = ""

    entry = process_candidate(
        patch_path=_patch(repo, "candidate_bad_trades"),
        config=cfg,
        repo=repo,
        ledger_path=ledger,
        baseline_metrics=baseline_metrics,
        baseline_score=baseline_score,
        iteration=1,
        mode="test",
    )

    assert entry["accepted"] is False
    assert "low_trade_count" in entry["rejection_reasons"]
    assert "evaluator_scenario: baseline" in stub.read_text(encoding="utf-8")
