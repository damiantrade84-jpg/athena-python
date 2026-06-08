"""Fail-closed tests for execution_lifecycle paper soak gate."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import execution_lifecycle


def test_paper_soak_blocks_when_config_import_fails(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "config":
            raise ImportError("config unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert execution_lifecycle._paper_soak_blocks_real_orders("bybit") is True


def test_paper_soak_blocks_when_paper_soak_is_bool_true(monkeypatch):
    import config

    monkeypatch.setitem(config.CONFIG, "PAPER_SOAK", True)
    monkeypatch.setattr(
        execution_lifecycle,
        "_demo_broker_execution_requested",
        lambda _venue: False,
    )
    assert execution_lifecycle._paper_soak_blocks_real_orders("bybit") is True


def test_paper_soak_blocks_when_paper_soak_enabled_dict(monkeypatch):
    import config

    monkeypatch.setitem(
        config.CONFIG,
        "PAPER_SOAK",
        {"ENABLED": True, "REAL_ORDERS_ALLOWED": False},
    )
    monkeypatch.setattr(
        execution_lifecycle,
        "_demo_broker_execution_requested",
        lambda _venue: False,
    )
    assert execution_lifecycle._paper_soak_blocks_real_orders("bybit") is True


def test_paper_soak_blocks_when_paper_soak_malformed(monkeypatch):
    import config

    monkeypatch.setitem(config.CONFIG, "PAPER_SOAK", "enabled")
    monkeypatch.setattr(
        execution_lifecycle,
        "_demo_broker_execution_requested",
        lambda _venue: False,
    )
    assert execution_lifecycle._paper_soak_blocks_real_orders("bybit") is True


def test_paper_soak_demo_venue_bypasses_block(monkeypatch):
    import config

    monkeypatch.setitem(
        config.CONFIG,
        "PAPER_SOAK",
        {"ENABLED": True, "REAL_ORDERS_ALLOWED": False},
    )
    monkeypatch.setattr(
        execution_lifecycle,
        "_demo_broker_execution_requested",
        lambda _venue: True,
    )
    assert execution_lifecycle._paper_soak_blocks_real_orders("bybit") is False


def test_paper_soak_allows_when_disabled_and_real_orders_allowed(monkeypatch):
    import config

    monkeypatch.setitem(
        config.CONFIG,
        "PAPER_SOAK",
        {"ENABLED": False, "REAL_ORDERS_ALLOWED": True},
    )
    monkeypatch.setattr(
        execution_lifecycle,
        "_demo_broker_execution_requested",
        lambda _venue: False,
    )
    assert execution_lifecycle._paper_soak_blocks_real_orders("bybit") is False
