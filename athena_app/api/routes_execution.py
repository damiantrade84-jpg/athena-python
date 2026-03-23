"""Execution route helper functions."""

from __future__ import annotations


def normalize_pip_mode(raw_mode: str | None) -> str:
    mode = (raw_mode or "swing")
    if isinstance(mode, str):
        mode = mode.strip().lower()
    if mode not in ("scalp", "intraday", "swing"):
        mode = "swing"
    return mode

