"""Shared fixtures for EdgeLab tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from research.edgelab.config_loader import DEFAULT_CONFIG


@pytest.fixture
def default_config() -> dict:
    return dict(DEFAULT_CONFIG)


@pytest.fixture
def git_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="edgelab_git_"))
    stub = repo / "research" / "edgelab" / "stub" / "stub_config.yaml"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "# EdgeLab safe stub config (research-only).\n"
        "evaluator_scenario: baseline\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
        },
    )
    yield repo
    shutil.rmtree(repo, ignore_errors=True)
