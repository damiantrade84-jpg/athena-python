"""Tests for Binance crypto WS / micro subscription scope helpers."""

from __future__ import annotations

from athena.crypto_ws_scope import (
    binance_micro_symbol_strings,
    enabled_binance_micro_crypto_pairs,
)


def test_enabled_binance_micro_includes_all_when_scalp_world_narrow():
    """Micro list must not shrink when demand scope would be narrow (regression)."""
    pairs = [
        {
            "symbol": "BTCUSDT",
            "type": "crypto",
            "display": "BTC/USDT",
            "source": "binance",
            "enabled": True,
        },
        {
            "symbol": "XRPUSDT",
            "type": "crypto",
            "display": "XRP/USDT",
            "source": "binance",
            "enabled": True,
        },
        {
            "symbol": "ETHUSDT",
            "type": "crypto",
            "display": "ETH/USDT",
            "source": "binance",
            "enabled": False,
        },
        {
            "symbol": "EURUSD",
            "type": "forex",
            "display": "EUR/USD",
            "source": "mt5",
            "enabled": True,
        },
    ]
    enabled = enabled_binance_micro_crypto_pairs(pairs)
    syms = {str(p["symbol"]).upper() for p in enabled}
    assert syms == {"BTCUSDT", "XRPUSDT"}
    assert binance_micro_symbol_strings(pairs) == ["BTCUSDT", "XRPUSDT"]


def test_skips_non_binance_crypto():
    pairs = [
        {
            "symbol": "BTCUSD",
            "type": "crypto",
            "display": "BTC/USD",
            "source": "other",
            "enabled": True,
        },
    ]
    assert enabled_binance_micro_crypto_pairs(pairs) == []


def test_enabled_defaults_true_without_key():
    pairs = [{"symbol": "SOLUSDT", "type": "crypto", "source": "binance", "display": "SOL/USDT"}]
    out = enabled_binance_micro_crypto_pairs(pairs)
    assert len(out) == 1 and out[0]["symbol"] == "SOLUSDT"


def test_duplicate_symbol_dedupes_last_wins():
    pairs = [
        {"symbol": "BTCUSDT", "type": "crypto", "source": "binance", "enabled": True, "a": 1},
        {"symbol": "BTCUSDT", "type": "crypto", "source": "binance", "enabled": True, "a": 2},
    ]
    assert len(enabled_binance_micro_crypto_pairs(pairs)) == 1
