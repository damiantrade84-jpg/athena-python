"""Tests for crypto WS / micro subscription scope helpers."""

from __future__ import annotations

from athena.crypto_ws_scope import (
    binance_micro_symbol_strings,
    enabled_crypto_micro_pairs,
    enabled_binance_micro_crypto_pairs,
    should_start_binance_micro_feeds,
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


def test_enabled_crypto_micro_without_source_includes_bybit_and_binance():
    pairs = [
        {"symbol": "BTCUSDT", "type": "crypto", "display": "BTC/USDT", "source": "binance", "enabled": True},
        {"symbol": "ETHUSDT", "type": "crypto", "display": "ETH/USDT", "source": "bybit", "enabled": True},
        {"symbol": "EURUSD", "type": "forex", "display": "EUR/USD", "source": "mt5", "enabled": True},
    ]

    syms = {str(p["symbol"]).upper() for p in enabled_crypto_micro_pairs(pairs)}

    assert syms == {"BTCUSDT", "ETHUSDT"}


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


def test_binance_micro_disabled_when_bybit_is_primary_without_override():
    cfg = {
        "MICROSTRUCTURE_BINANCE_COMBINED_STREAM": True,
        "SCALP_ENGINE": {"TRADE_BUCKET_EXCHANGE": "bybit"},
    }

    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=True) is False


def test_binance_micro_explicit_override_can_enable_parallel_feed():
    cfg = {
        "MICROSTRUCTURE_BINANCE_FEEDS_ENABLED": True,
        "MICROSTRUCTURE_BINANCE_COMBINED_STREAM": True,
        "SCALP_ENGINE": {"TRADE_BUCKET_EXCHANGE": "bybit"},
    }

    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=True) is True


def test_binance_micro_enabled_when_binance_is_primary():
    cfg = {
        "MICROSTRUCTURE_BINANCE_COMBINED_STREAM": True,
        "SCALP_ENGINE": {"TRADE_BUCKET_EXCHANGE": "binance"},
    }

    assert should_start_binance_micro_feeds(cfg, has_binance_pairs=True) is True
