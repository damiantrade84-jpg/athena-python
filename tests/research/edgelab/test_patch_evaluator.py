"""Patch evaluator integration tests."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from research.edgelab.database import connect, init_db, list_suggestions
from research.edgelab.patch_evaluator import evaluate_patch
from research.edgelab.stub_evaluator import SCENARIOS

ROOT = Path(__file__).resolve().parents[3]
STUB_EVAL = ROOT / "research" / "edgelab" / "stub_evaluator.py"


def _setup(repo: Path) -> None:
    dest = repo / "research" / "edgelab" / "stub_evaluator.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STUB_EVAL, dest)


def _patch(repo: Path, scenario: str) -> Path:
    candidates = repo / "research" / "edgelab" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    rel = "research/edgelab/stub/stub_config.yaml"
    patch = candidates / f"{scenario}.patch"
    patch.write_text(
        "\n".join(
            [
                f"diff --git a/{rel} b/{rel}",
                f"--- a/{rel}",
                f"+++ b/{rel}",
                "@@ -1,2 +1,2 @@",
                " # EdgeLab safe stub config (research-only).",
                "-evaluator_scenario: baseline",
                f"+evaluator_scenario: {scenario}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return patch


def _eval_cmd(repo: Path) -> str:
    stub = repo / "research" / "edgelab" / "stub" / "stub_config.yaml"
    return (
        f"{sys.executable} research/edgelab/stub_evaluator.py "
        f"--scenario evaluate --config {stub.as_posix()}"
    )


def test_accepted_candidate_writes_suggestion(git_repo, default_config):
    repo = git_repo
    _setup(repo)
    db = Path(tempfile.mkdtemp(prefix="edgelab_pe_")) / "test.sqlite"
    init_db(db)

    cfg = dict(default_config)
    cfg["evaluation_command"] = _eval_cmd(repo)
    cfg["test_command"] = ""

    import research.edgelab.patch_evaluator as pe

    orig = pe.DB_PATH
    pe.DB_PATH = db
    try:
        result = evaluate_patch(_patch(repo, "candidate_good"), config=cfg, repo=repo)
        assert result["accepted"] is True
        with connect(db) as conn:
            rows = list_suggestions(conn, limit=10)
        assert any(r.get("status") == "accepted_research" for r in rows)
    finally:
        pe.DB_PATH = orig


def test_rejected_candidate_reverts_touched_file(git_repo, default_config):
    repo = git_repo
    _setup(repo)
    db = Path(tempfile.mkdtemp(prefix="edgelab_pe_")) / "test.sqlite"
    stub = repo / "research" / "edgelab" / "stub" / "stub_config.yaml"

    cfg = dict(default_config)
    cfg["evaluation_command"] = _eval_cmd(repo)
    cfg["test_command"] = ""

    import research.edgelab.patch_evaluator as pe

    orig = pe.DB_PATH
    pe.DB_PATH = db
    try:
        result = evaluate_patch(_patch(repo, "candidate_bad_trades"), config=cfg, repo=repo)
        assert result["accepted"] is False
        assert "low_trade_count" in result["rejection_reasons"]
        assert "evaluator_scenario: baseline" in stub.read_text(encoding="utf-8")
    finally:
        pe.DB_PATH = orig
