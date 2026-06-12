"""Standalone ASE backtest path."""

from __future__ import annotations


def test_backtest_pair_ase_uses_ase_engine_type():
    import backtest_runner

    pair = {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"}
    trades = [{"resultR": 1.0, "date": "2024-01-01T00:00:00+00:00"}]
    result = backtest_runner._format_backtest_results(trades, pair, engine_type="ASE")
    assert result["engine"] == "ASE"
    assert result["totalTrades"] == 1


def test_ase_backtest_config_defaults():
    from config import CONFIG

    assert CONFIG.get("ASE_BT_ENABLED") is True
    assert int(CONFIG.get("ASE_BT_LOOKBACK_DAYS", 0) or 0) >= 30
