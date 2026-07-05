"""Shared fixtures for AutoResearch tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def git_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="autoresearch_git_"))
    stub = repo / "research" / "autoresearch" / "stub" / "stub_config.yaml"
    stub.parent.mkdir(parents=True)
    stub.write_text(
        "# Research-only stub config for AutoResearch safe_stub mode.\n"
        "evaluator_scenario: baseline\n",
        encoding="utf-8",
    )
    research_file = repo / "research" / "autoresearch" / "notes.txt"
    research_file.write_text("baseline\n", encoding="utf-8")

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


@pytest.fixture
def default_config() -> dict:
    from research.autoresearch.config_loader import DEFAULT_CONFIG

    return dict(DEFAULT_CONFIG)
