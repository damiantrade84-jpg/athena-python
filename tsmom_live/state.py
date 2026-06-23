"""Persisted trailing-stop state per instrument (the daily runner owns the trail).

The broker does not store 'highest close since entry', so the runner persists the
PositionState between daily runs. Path is overridable via TSMOM_LIVE_STATE_DIR (tests)."""
from __future__ import annotations

import json
import os

_DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_journal")


def _dir() -> str:
    return os.environ.get("TSMOM_LIVE_STATE_DIR", _DEFAULT_DIR)


def _path(instrument: str) -> str:
    return os.path.join(_dir(), f"state_{instrument}.json")


def load_state(instrument: str) -> dict | None:
    try:
        with open(_path(instrument), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def save_state(instrument: str, st: dict) -> None:
    try:
        os.makedirs(_dir(), exist_ok=True)
        with open(_path(instrument), "w", encoding="utf-8") as fh:
            json.dump(st, fh)
    except Exception:
        pass


def clear_state(instrument: str) -> None:
    try:
        os.remove(_path(instrument))
    except Exception:
        pass
