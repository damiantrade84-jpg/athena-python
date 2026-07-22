"""Unit tests for MT5 terminal path resolution (no live MT5 required)."""

from __future__ import annotations

import mt5_executor


def test_resolve_mt5_terminal_path_prefers_env(monkeypatch):
    monkeypatch.setenv("MT5_PATH", r"C:\Program Files\ATFX\terminal64.exe")
    monkeypatch.setitem(
        mt5_executor.CONFIG, "MT5_PATH", r"C:\other\terminal64.exe"
    )
    monkeypatch.setattr(mt5_executor.os.path, "isdir", lambda _p: False)

    resolved = mt5_executor._resolve_mt5_terminal_path()

    assert resolved == "C:/Program Files/ATFX/terminal64.exe"


def test_resolve_mt5_terminal_path_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("MT5_PATH", raising=False)
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "MT5_PATH",
        r"C:\Program Files\ATFXGM16 MT5 Terminal\terminal64.exe",
    )
    monkeypatch.setattr(mt5_executor.os.path, "isdir", lambda _p: False)

    resolved = mt5_executor._resolve_mt5_terminal_path()

    assert resolved == "C:/Program Files/ATFXGM16 MT5 Terminal/terminal64.exe"
    assert "\\" not in resolved


def test_resolve_mt5_terminal_path_expands_directory(monkeypatch):
    monkeypatch.delenv("MT5_PATH", raising=False)
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "MT5_PATH",
        r"C:\Program Files\ATFXGM16 MT5 Terminal",
    )
    monkeypatch.setattr(mt5_executor.os.path, "isdir", lambda _p: True)
    monkeypatch.setattr(mt5_executor.os.path, "isfile", lambda _p: True)

    resolved = mt5_executor._resolve_mt5_terminal_path()

    assert resolved.endswith("terminal64.exe")
    assert "\\" not in resolved
