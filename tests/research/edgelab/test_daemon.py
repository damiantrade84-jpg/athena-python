"""Export and daemon tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_export_suggestions_creates_json():
    base = Path(tempfile.mkdtemp(prefix="edgelab_export_"))
    db = base / "edgelab.sqlite"
    out = base / "out" / "suggestions.json"

    from research.edgelab.database import connect, init_db, insert_suggestion
    from research.edgelab.export_suggestions import export_suggestions

    init_db(db)
    with connect(db) as conn:
        insert_suggestion(
            conn,
            {
                "engine": "edgelab",
                "title": "Test suggestion",
                "summary": "export test",
                "status": "new",
            },
        )
        conn.commit()

    path = export_suggestions(db_path=db, out_path=out)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["research_only"] is True
    assert len(payload["suggestions"]) >= 1


def test_daemon_once_completes_safely():
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "research" / "edgelab" / "edgelab_daemon.py"),
            "--dry-run",
            "--once",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_safe_stub_mode_works():
    from research.edgelab.candidate_generator import create_safe_stub_patch

    repo = Path(tempfile.mkdtemp(prefix="edgelab_stub_"))
    stub = repo / "research" / "edgelab" / "stub" / "stub_config.yaml"
    stub.parent.mkdir(parents=True)
    stub.write_text("evaluator_scenario: baseline\n", encoding="utf-8")
    candidates = repo / "research" / "edgelab" / "candidates"

    patch = create_safe_stub_patch(
        candidates_dir=candidates,
        stub_config=stub,
        repo=repo,
        name="test.patch",
        scenario="candidate_good",
    )
    assert patch.exists()
    assert "candidate_good" in patch.read_text(encoding="utf-8")
