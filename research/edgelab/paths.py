"""EdgeLab path helpers."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGELAB_DIR = REPO_ROOT / "research" / "edgelab"
CONFIG_PATH = EDGELAB_DIR / "edgelab_config.yaml"
DB_PATH = EDGELAB_DIR / "edgelab_suggestions.sqlite"
OUT_DIR = EDGELAB_DIR / "out"
LOGS_DIR = EDGELAB_DIR / "logs"
LEDGERS_DIR = EDGELAB_DIR / "ledgers"
CANDIDATES_DIR = EDGELAB_DIR / "candidates"
ACCEPTED_DIR = EDGELAB_DIR / "accepted"
REJECTED_DIR = EDGELAB_DIR / "rejected"
STUB_CONFIG = EDGELAB_DIR / "stub" / "stub_config.yaml"


def ensure_dirs() -> None:
    for d in (
        OUT_DIR,
        LOGS_DIR,
        LEDGERS_DIR,
        CANDIDATES_DIR,
        ACCEPTED_DIR,
        REJECTED_DIR,
        STUB_CONFIG.parent,
    ):
        d.mkdir(parents=True, exist_ok=True)
