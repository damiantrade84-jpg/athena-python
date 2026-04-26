import pytest
import os
import json
import shutil
from strategy_lab import (
    classify_asset_group, 
    classify_strategy_family, 
    classify_detailed_regime, 
    normalize_trade, 
    calculate_group_metrics, 
    LabRunner
)


def test_asset_group_classification():
    assert classify_asset_group("BTCUSDT")[0] == "crypto_major"
    assert classify_asset_group("SOLUSDT")[0] == "crypto_major"
    assert classify_asset_group("TRX/USDT")[0] == "crypto_alt"
    assert classify_asset_group("EURUSD")[0] == "forex_major"
    assert classify_asset_group("GBPUSD")[0] == "forex_major"
    assert classify_asset_group("EURGBP")[0] == "forex_cross"
    assert classify_asset_group("EUR/JPY")[0] == "forex_cross"
    assert classify_asset_group("USDZAR")[0] == "forex_exotic"
    assert classify_asset_group("NAS100")[0] == "index"
    assert classify_asset_group("S&P 500")[0] == "index"
    assert classify_asset_group("DAX 40")[0] == "index"
    assert classify_asset_group("GOLD")[0] == "commodity"
    assert classify_asset_group("COFFEE")[0] == "commodity"
    assert classify_asset_group("BRENT OIL")[0] == "commodity"
    assert classify_asset_group("AAPL")[0] == "stock"
    assert classify_asset_group("AMD")[0] == "stock"
    assert classify_asset_group("")[0] == "unknown"


def test_strategy_family_classification():
    sf, reason = classify_strategy_family({"strategy_family": "ENGINE_A_ONLY"})
    assert sf == "ENGINE_A_ONLY"
    assert "explicit field" in reason.lower()
    
    assert classify_strategy_family({"engine": "ENGINE_B_ONLY"})[0] == "ENGINE_B_ONLY"
    assert classify_strategy_family({"consensus_mode": "ENGINE_C_CONSENSUS"})[0] == "ENGINE_C_CONSENSUS"
    assert classify_strategy_family({"setup_type": "ENGINE_D_SCALP"})[0] == "ENGINE_D_SCALP"
    
    # Explicit beats fallback
    explicit, _ = classify_strategy_family({"engine": "ENGINE_C_CONSENSUS", "some_key": "ENGINE_A"})
    assert explicit == "ENGINE_C_CONSENSUS"
    
    sf_unk, reason_unk = classify_strategy_family({"no_clues": "here"})
    assert sf_unk == "UNKNOWN"
    assert "Missing explicit fields" in reason_unk


def test_regime_classification():
    assert classify_detailed_regime({"regime": "trending"})[0] == "trending"
    assert classify_detailed_regime({"regime": "volatility_compression"})[0] == "volatility_compression"
    assert classify_detailed_regime({"regime": "volatility_expansion"})[0] == "volatility_expansion"
    
    # Fallback inference
    assert classify_detailed_regime({"some_field": "range_break"})[0] == "breakout"
    assert classify_detailed_regime({"some_field": "adx"})[0] == "trending"
    
    reg_unk, reason_unk = classify_detailed_regime({})
    assert reg_unk == "unknown"
    assert "Missing explicit fields" in reason_unk


def test_normalize_trade_non_trades():
    raw_reject = {"pair": "EURUSD", "status": "REJECTED_GATE_FAIL"}
    norm_reject = normalize_trade(raw_reject)
    assert norm_reject["is_closed_trade"] is False
    assert norm_reject["row_type"] == "rejected_setup"
    
    raw_skip = {"pair": "GBPUSD", "exit_reason": "SKIP_SIGNAL"}
    norm_skip = normalize_trade(raw_skip)
    assert norm_skip["is_closed_trade"] is False
    assert norm_skip["row_type"] == "skipped_row"


def test_normalize_trade_closed_executions():
    raw_win = {"pair": "EURUSD", "trade_status": "closed", "resultR": 2.5}
    norm_win = normalize_trade(raw_win)
    assert norm_win["is_closed_trade"] is True
    assert norm_win["win"] is True
    
    raw_loss = {"pair": "EURUSD", "trade_status": "closed", "resultR": -1.0}
    norm_loss = normalize_trade(raw_loss)
    assert norm_loss["is_closed_trade"] is True
    assert norm_loss["win"] is False


def test_metrics_rejections_and_zero_losses():
    trades = [
        {"is_closed_trade": True, "win": True, "r_multiple": 2.0, "symbol": "EURUSD"},
        {"is_closed_trade": True, "win": True, "r_multiple": 3.0, "symbol": "EURUSD"},
        {"is_closed_trade": False, "row_type": "rejected_setup", "symbol": "EURUSD"}
    ]
    metrics = calculate_group_metrics(trades)
    
    assert metrics["trade_count"] == 2
    assert metrics["closed_trade_count"] == 2
    assert metrics["rejected_setup_count"] == 1
    assert metrics["profit_factor"] == "inf"


def test_max_drawdown_timestamp_order():
    trades = [
        {"is_closed_trade": True, "win": True, "r_multiple": 5.0, "timestamp": "2026-02-01", "symbol": "EURUSD"},
        {"is_closed_trade": True, "win": False, "r_multiple": -3.0, "timestamp": "2026-01-01", "symbol": "EURUSD"},
        {"is_closed_trade": True, "win": True, "r_multiple": 2.0, "timestamp": "2026-01-15", "symbol": "EURUSD"},
    ]
    metrics = calculate_group_metrics(trades)
    assert metrics["max_drawdown_R"] == 3.0
    assert not metrics["warnings"]


def test_max_drawdown_missing_timestamps_warning():
    trades = [
        {"is_closed_trade": True, "win": True, "r_multiple": 5.0, "symbol": "EURUSD"},
        {"is_closed_trade": True, "win": False, "r_multiple": -3.0, "symbol": "EURUSD"},
    ]
    metrics = calculate_group_metrics(trades)
    assert "Missing timestamps" in metrics["warnings"][0]


def test_outlier_tracking():
    trades = [
        {"is_closed_trade": True, "win": True, "r_multiple": 50.0, "symbol": "EURUSD"}
    ]
    metrics = calculate_group_metrics(trades)
    assert metrics["outlier_count"] == 1
    assert metrics["gross_profit_R"] == 50.0


def test_report_generation():
    import tempfile
    out_dir = tempfile.mkdtemp()
    runner = LabRunner()
    runner.output_dir = out_dir
    runner.config["write_reports"] = True
    
    dummy_trades = [
        {"pair": "EURUSD", "trade_status": "closed", "resultR": 2.5, "engine": "ENGINE_A_ONLY", "timeframe": "H1", "exit_reason": "MANUAL_OPERATOR_CLOSE"},
        {"pair": "AMD", "trade_status": "closed", "resultR": -1.0, "engine": "ENGINE_B_ONLY", "timeframe": "H4"},
        {"pair": "COFFEE", "trade_status": "closed", "resultR": -1.5, "strategy_family": "UNKNOWN"}
    ]
    
    # mock load
    runner.load_json_trades = lambda x: [(t, "dummy.json") for t in dummy_trades]
    runner.load_db_trades = lambda x: []
    
    runner.run_evaluation()
    
    files = os.listdir(out_dir)
    assert "strategy_lab_by_strategy_family.json" in files
    assert "strategy_lab_by_timeframe.json" in files
    assert "strategy_lab_unknown_classification.json" in files
    assert "strategy_lab_schema_coverage.json" in files
    assert "strategy_lab_manual_close.json" in files
    assert "strategy_lab_outliers.json" in files
    
    import shutil
    shutil.rmtree(out_dir)
