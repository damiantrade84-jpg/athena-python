"""KIMI pair-picker host book: live ACTIVE_PAIRS, not the exchange catalog."""
from __future__ import annotations

import types

from tools.kimi_bridge import host_active_pairs


def test_host_active_pairs_reads_main_without_reimporting_athena():
    book = [{"display": "EUR/USD", "enabled": True}]
    fake = types.SimpleNamespace(ACTIVE_PAIRS=book)
    assert host_active_pairs({"__main__": fake}) == book
    assert host_active_pairs({"athena": fake}) == book
    assert host_active_pairs({}) == []
    assert host_active_pairs({"__main__": types.SimpleNamespace()}) == []


def test_host_active_pairs_prefers_athena_module_when_both_exist():
    athena_book = [{"display": "XAU/USD"}]
    main_book = [{"display": "EUR/USD"}]
    got = host_active_pairs({
        "athena": types.SimpleNamespace(ACTIVE_PAIRS=athena_book),
        "__main__": types.SimpleNamespace(ACTIVE_PAIRS=main_book),
    })
    assert got == athena_book
