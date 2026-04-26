#!/usr/bin/env python3
"""
strategy_lab.py — Athena Strategy Lab v1.1
Safe, read-only analysis suite that groups backtest and live execution historical performance.
Audited version: Telemetry Integrity.
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime
import statistics

def load_config():
    """Dynamically load configuration gates without live system dependencies."""
    config_path = "config.yaml"
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
                return cfg.get("strategy_lab", {})
        except Exception:
            pass
    return {}


def is_closed_trade(raw_trade: dict) -> tuple[bool, str]:
    """Evaluates multiple indicators to determine if a row represents a finalized trade execution."""
    status = str(raw_trade.get("status") or raw_trade.get("trade_status") or "").upper().strip()
    if "CLOSED" in status:
        return True, f"Status is {status}"
        
    outcome = str(raw_trade.get("outcome") or raw_trade.get("exit_reason") or "").upper().strip()
    if outcome and outcome not in ["", "UNKNOWN", "NONE"]:
        if any(w in outcome for w in ["REJECT", "SKIP", "WATCHLIST", "FAIL_GATE"]):
            return False, f"Outcome {outcome} is non-trade"
        return True, f"Outcome {outcome} indicates closure"
        
    if "entry_price" in raw_trade and ("exit_price" in raw_trade or "close_price" in raw_trade):
        return True, "Has entry and exit prices"
        
    if any(k in raw_trade for k in ["r_multiple", "resultR", "R"]) and any(k in raw_trade for k in ["pnl", "profit", "profit_loss"]):
        return True, "Has R and PnL fields"
        
    result = str(raw_trade.get("result") or "").upper().strip()
    if result in ["WIN", "LOSS"]:
        return True, f"Result is {result}"
        
    return False, "No definitive closed trade fields found"


def classify_non_trade_row(raw_trade: dict) -> str:
    """Categorizes audit signals that never initiated live capital exposure."""
    status = str(raw_trade.get("status") or raw_trade.get("trade_status") or "").upper().strip()
    if "REJECT" in status or "FAIL_GATE" in status:
        return "rejected_setup"
    if "SKIP" in status:
        return "skipped_row"
    if "WATCHLIST" in status:
        return "watchlist_row"
        
    outcome = str(raw_trade.get("outcome") or raw_trade.get("exit_reason") or "").upper().strip()
    if "REJECT" in outcome or "FAIL_GATE" in outcome:
        return "rejected_setup"
    if "SKIP" in outcome:
        return "skipped_row"
    if "WATCHLIST" in outcome:
        return "watchlist_row"
        
    reason = str(raw_trade.get("reason") or raw_trade.get("failure_reason") or "").upper().strip()
    if "REJECT" in reason or "FAIL_GATE" in reason:
        return "rejected_setup"
    if "SKIP" in reason:
        return "skipped_row"
        
    return "unknown_row"


def classify_asset_group(symbol: str, asset_class: str | None = None) -> tuple[str, str]:
    """Maps trade symbols defensively based on strict instructions."""
    if not symbol or str(symbol).upper() == "UNKNOWN":
        return "unknown", "Symbol is missing or unknown"
        
    sym_raw = str(symbol).strip().upper()
    sym = sym_raw.replace("/", "").replace("-", "").split(".")[0].strip()
    
    # 1. Stocks
    stocks = {"AMD", "NVDA", "AAPL", "MSFT", "TSLA"}
    if sym_raw in stocks or sym in stocks:
        return "stock", "Matched exact stock ticker"
        
    # 2. Indices
    indices = {"S&P 500", "SPX", "SPY", "NAS100", "NASDAQ", "US30", "DAX 40", "GER40"}
    if sym_raw in indices or sym in [i.replace(" ", "") for i in indices]:
        return "index", "Matched exact index ticker"
        
    # 3. Commodities
    commodities = {"GOLD", "XAU/USD", "XAUUSD", "SILVER", "XAG/USD", "XAGUSD", "COFFEE", "BRENT OIL", "BRENTOIL", "WTI", "USOIL"}
    if sym_raw in commodities or sym in [c.replace(" ", "") for c in commodities]:
        return "commodity", "Matched exact commodity ticker"
        
    # 4. Crypto Majors
    crypto_majors = {"BTC/USDT", "BTCUSDT", "ETH/USDT", "ETHUSDT", "SOL/USDT", "SOLUSDT"}
    if sym_raw in crypto_majors or sym in crypto_majors:
        return "crypto_major", "Matched exact crypto major ticker"
        
    # 5. Crypto Alts
    # ADA/USDT, TRX/USDT, DOGE/USDT, XRP/USDT and other non-major crypto pairs
    if "USDT" in sym_raw or "USDT" in sym or str(asset_class).lower() == "crypto":
        return "crypto_alt", "Matched crypto alt pattern"
        
    # 6. Forex Majors
    forex_majors = {"EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "NZD/USD", "USD/CAD"}
    if sym_raw in forex_majors or sym in [f.replace("/", "") for f in forex_majors]:
        return "forex_major", "Matched exact forex major ticker"
        
    # 7. Forex Cross
    forex_cross = {"EUR/JPY", "EUR/GBP", "GBP/JPY", "GBP/AUD", "EUR/AUD", "AUD/JPY", "CAD/JPY", "CHF/JPY"}
    if sym_raw in forex_cross or sym in [f.replace("/", "") for f in forex_cross]:
        return "forex_cross", "Matched exact forex cross ticker"
        
    # 8. Forex Exotics / Fallback
    if "/" in sym_raw or len(sym) == 6:
        return "forex_exotic", "Fallback for unmatched pair pattern"
        
    return "unknown", "No match found for asset group"


def classify_strategy_family(trade: dict) -> tuple[str, str]:
    """Explicit checks to resolve execution source algorithms."""
    explicit_fields = [
        "strategy_family", "engine", "engine_name", "signal_engine", 
        "source_engine", "decision_source", "consensus_mode", 
        "consensus_type", "setup_type", "trade_type", "signal_type", "scanner_source"
    ]
    
    for field in explicit_fields:
        if field in trade and trade[field]:
            val = str(trade[field]).upper().strip()
            # If we get a reasonably known format, return it
            if "ENGINE" in val or "BASELINE" in val or "SCALP" in val:
                return val, f"Found in explicit field: {field}"
                
    # Fallback inference
    raw_str = " ".join(str(v).upper() for k,v in trade.items() if isinstance(v, (str, int, float, bool))).upper()
    
    if "ENGINE_A" in raw_str or "ENGINE A" in raw_str:
        return "ENGINE_A_ONLY", "Fallback inference: ENGINE_A pattern found"
    if "ENGINE_B" in raw_str or "ENGINE B" in raw_str:
        return "ENGINE_B_ONLY", "Fallback inference: ENGINE_B pattern found"
    if "ENGINE_C" in raw_str or "ENGINE C" in raw_str:
        return "ENGINE_C_CONSENSUS", "Fallback inference: ENGINE_C pattern found"
    if "ENGINE_D" in raw_str or "ENGINE D" in raw_str:
        return "ENGINE_D_SCALP", "Fallback inference: ENGINE_D pattern found"
        
    missing = [f for f in explicit_fields if f not in trade or not trade[f]]
    return "UNKNOWN", f"Missing explicit fields: {','.join(missing[:3])}"


def classify_detailed_regime(trade: dict) -> tuple[str, str]:
    """Detects cycles and regimes defensively."""
    explicit_fields = [
        "regime", "market_regime", "detailed_regime", 
        "volatility_regime", "structure_regime", "trend_state", 
        "setup_type", "trigger_type"
    ]
    
    # Use explicit fields first
    for field in explicit_fields:
        if field in trade and trade[field]:
            val = str(trade[field]).lower().strip()
            if val in ["trending", "ranging", "volatility_expansion", "volatility_compression", "breakout", "sweep_reversal"]:
                return val, f"Found exact match in explicit field: {field}"
                
    # Fallback inference
    raw_vals = " ".join(str(trade.get(f, "")).lower() for f in explicit_fields)
    all_vals = " ".join(str(v).lower() for k,v in trade.items() if isinstance(v, (str, bool)))
    
    if any(w in raw_vals or w in all_vals for w in ["sweep", "sweep_reversal", "liquidity_sweep", "reclaim"]):
        return "sweep_reversal", "Fallback inference: sweep keywords"
    if any(w in raw_vals or w in all_vals for w in ["breakout", "range_break", "expansion_break"]):
        return "breakout", "Fallback inference: breakout keywords"
    if any(w in raw_vals or w in all_vals for w in ["adx", "trend", "ema_alignment", "trending"]):
        return "trending", "Fallback inference: trending keywords"
    if any(w in raw_vals or w in all_vals for w in ["range", "ranging", "mean_reversion"]):
        return "ranging", "Fallback inference: ranging keywords"
    if any(w in raw_vals or w in all_vals for w in ["atr_expansion", "volatility_expansion"]):
        return "volatility_expansion", "Fallback inference: volatility expansion keywords"
    if any(w in raw_vals or w in all_vals for w in ["squeeze", "compression", "low_volatility"]):
        return "volatility_compression", "Fallback inference: volatility compression keywords"

    missing = [f for f in explicit_fields if f not in trade or not trade[f]]
    return "unknown", f"Missing explicit fields: {','.join(missing[:3])}"


def normalize_trade(raw_trade: dict, source_file: str = "unknown", row_id: int = 0) -> dict:
    """Extracts key variables defensively."""
    import json
    
    # Extract telemetry from warnings_json if present (for manual/audit rows)
    if "warnings_json" in raw_trade and isinstance(raw_trade["warnings_json"], str):
        try:
            wj = json.loads(raw_trade["warnings_json"])
            if isinstance(wj, dict) and "telemetry_version" in wj:
                raw_trade.update(wj)
        except Exception:
            pass
            
    # Also check factors_json just in case
    if "factors_json" in raw_trade and isinstance(raw_trade["factors_json"], str):
        try:
            fj = json.loads(raw_trade["factors_json"])
            if isinstance(fj, dict) and "telemetry_version" in fj:
                raw_trade.update(fj)
        except Exception:
            pass

    base = {
        "is_closed_trade": False,
        "row_type": "unknown_row",
        "symbol": "UNKNOWN",
        "asset_class": None,
        "asset_group": "unknown",
        "asset_group_reason": "",
        "timeframe": "UNKNOWN",
        "engine": "UNKNOWN",
        "strategy_family": "UNKNOWN",
        "strategy_family_reason": "",
        "regime": "unknown",
        "regime_reason": "",
        "r_multiple": 0.0,
        "pnl": 0.0,
        "win": False,
        "exit_reason": "UNKNOWN",
        "failure_reason": "UNKNOWN",
        "failure_reason_source": "UNKNOWN",
        "timestamp": None,
        "source_file": source_file,
        "source_row_id": row_id,
        "closed_trade_evidence": "",
        "_raw": raw_trade # keep a ref for reports
    }
    
    # 1. Closed Trade Isolation
    closed, evidence = is_closed_trade(raw_trade)
    if closed:
        base["is_closed_trade"] = True
        base["row_type"] = "closed_trade"
        base["closed_trade_evidence"] = evidence
    else:
        base["row_type"] = classify_non_trade_row(raw_trade)
        
    base["symbol"] = str(raw_trade.get("pair") or raw_trade.get("symbol") or "UNKNOWN").upper().strip()
    base["asset_class"] = raw_trade.get("asset_class")
    
    ag, ag_reason = classify_asset_group(base["symbol"], base["asset_class"])
    base["asset_group"] = ag
    base["asset_group_reason"] = ag_reason
    
    sf, sf_reason = classify_strategy_family(raw_trade)
    base["strategy_family"] = sf
    base["strategy_family_reason"] = sf_reason
    
    rg, rg_reason = classify_detailed_regime(raw_trade)
    base["regime"] = rg
    base["regime_reason"] = rg_reason
    
    base["timeframe"] = str(raw_trade.get("timeframe") or "UNKNOWN").upper().strip()
    
    base["engine"] = sf.replace("_ONLY", "").replace("_CONSENSUS", "").replace("_SCALP", "").upper().strip()
    if base["engine"] == "UNKNOWN":
        base["engine"] = "UNKNOWN"
        
    # Numeric Extraction
    for key in ["resultR", "r_multiple", "R"]:
        if key in raw_trade and raw_trade[key] is not None:
            try:
                base["r_multiple"] = float(raw_trade[key])
                break
            except (ValueError, TypeError):
                pass
                
    for key in ["pnl", "profit", "profit_loss"]:
        if key in raw_trade and raw_trade[key] is not None:
            try:
                base["pnl"] = float(raw_trade[key])
                break
            except (ValueError, TypeError):
                pass
                
    if "win" in raw_trade and raw_trade["win"] is not None:
        base["win"] = bool(raw_trade["win"])
    else:
        base["win"] = base["r_multiple"] > 0.0
        
    base["exit_reason"] = str(raw_trade.get("outcome") or raw_trade.get("exit_reason") or "UNKNOWN").upper().strip()
    if not base["win"]:
        for key in ["failure_reason", "reason", "outcome", "exit_reason"]:
            if key in raw_trade and raw_trade[key]:
                base["failure_reason"] = str(raw_trade[key]).upper().strip()
                base["failure_reason_source"] = key
                break
                
    base["setup_type"] = str(raw_trade.get("setup_type") or "UNKNOWN").strip()
    base["source_module"] = str(raw_trade.get("source_module") or "UNKNOWN").strip()
    base["telemetry_version"] = str(raw_trade.get("telemetry_version") or "UNKNOWN").strip()
        
    base["timestamp"] = raw_trade.get("date") or raw_trade.get("timestamp") or raw_trade.get("opened_at")
    if base["timestamp"]:
        base["timestamp"] = str(base["timestamp"]).strip()
        
    return base


def calculate_max_drawdown(trades: list[dict]) -> tuple[float, list[str]]:
    """Calculates peak-to-trough sequence drops chronologically."""
    warnings = []
    closed_trades = [t for t in trades if t.get("is_closed_trade")]
    if not closed_trades:
        return 0.0, warnings
        
    has_timestamps = all(t.get("timestamp") for t in closed_trades)
    
    if has_timestamps:
        try:
            sorted_trades = sorted(closed_trades, key=lambda x: str(x["timestamp"]))
        except Exception:
            sorted_trades = closed_trades
            warnings.append("Inconsistent timestamp formats; preserving input order for drawdown calculation.")
    else:
        sorted_trades = closed_trades
        if any(t.get("timestamp") for t in closed_trades):
            warnings.append("Partial missing timestamps; preserving input order for drawdown calculation.")
        else:
            warnings.append("Missing timestamps; preserving input order for drawdown calculation.")
            
    max_dd = 0.0
    cum_R = 0.0
    peak = 0.0
    
    for t in sorted_trades:
        cum_R += t.get("r_multiple", 0.0)
        if cum_R > peak:
            peak = cum_R
        dd = peak - cum_R
        if dd > max_dd:
            max_dd = dd
            
    return float(max_dd), warnings


def calculate_group_metrics(trades: list[dict], min_trades_for_verdict: int = 20, pass_thresholds: dict = None) -> dict:
    """Aggregates metrics without contamination risk."""
    closed_trades = [t for t in trades if t.get("is_closed_trade")]
    non_trades = [t for t in trades if not t.get("is_closed_trade")]
    
    trade_count = len(closed_trades)
    closed_trade_count = trade_count
    rejected_setup_count = sum(1 for t in trades if t.get("row_type") == "rejected_setup")
    skipped_row_count = sum(1 for t in trades if t.get("row_type") == "skipped_row")
    unknown_row_count = sum(1 for t in trades if t.get("row_type") == "unknown_row")
    
    outliers = [t for t in closed_trades if abs(t.get("r_multiple", 0.0)) > 30.0]
    outlier_count = len(outliers)
    
    winning_trades = [t for t in closed_trades if t["win"]]
    losing_trades = [t for t in closed_trades if not t["win"]]
    
    win_rate = len(winning_trades) / trade_count if trade_count > 0 else 0.0
    gross_profit_R = float(sum(t["r_multiple"] for t in winning_trades if t["r_multiple"] > 0))
    gross_loss_R = float(sum(abs(t["r_multiple"]) for t in losing_trades if t["r_multiple"] < 0))
    
    # Strict Profit Factor rules
    if not closed_trades:
        profit_factor = None
    elif gross_loss_R == 0.0:
        profit_factor = "inf" if gross_profit_R > 0.0 else 0.0
    else:
        profit_factor = float(gross_profit_R / gross_loss_R)
        
    average_R = float(sum(t["r_multiple"] for t in closed_trades) / trade_count if trade_count > 0 else 0.0)
    average_win_R = float(sum(t["r_multiple"] for t in winning_trades) / len(winning_trades) if winning_trades else 0.0)
    average_loss_R = float(sum(t["r_multiple"] for t in losing_trades) / len(losing_trades) if losing_trades else 0.0)
    
    max_drawdown_R, warnings = calculate_max_drawdown(trades)
    
    symbol_R = {}
    for t in closed_trades:
        s = t.get("symbol", "UNKNOWN")
        symbol_R[s] = symbol_R.get(s, 0.0) + t.get("r_multiple", 0.0)
        
    best_symbol = max(symbol_R, key=symbol_R.get) if symbol_R else "N/A"
    worst_symbol = min(symbol_R, key=symbol_R.get) if symbol_R else "N/A"
    
    failure_reason_counts = {}
    for t in losing_trades:
        reason = t.get("failure_reason", "UNKNOWN")
        failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + 1
        
    for t in non_trades:
        reason = t.get("failure_reason") or t.get("exit_reason") or "UNKNOWN"
        failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + 1
        
    most_common_failure_reason = max(failure_reason_counts, key=failure_reason_counts.get) if failure_reason_counts else "N/A"
    
    verdict = "FAIL"
    if pass_thresholds and trade_count >= min_trades_for_verdict:
        pf_ok = profit_factor >= pass_thresholds.get("min_profit_factor", 1.20) if isinstance(profit_factor, (int, float)) else False
        wr_ok = win_rate >= pass_thresholds.get("min_win_rate", 0.45)
        dd_ok = max_drawdown_R <= pass_thresholds.get("max_drawdown_R", 10.0)
        avg_ok = average_R >= pass_thresholds.get("min_average_R", 0.05)
        if pf_ok and wr_ok and dd_ok and avg_ok:
            verdict = "PASS"
            
    # filter out _raw before returning
    
    return {
        "trade_count": trade_count,
        "closed_trade_count": closed_trade_count,
        "rejected_setup_count": rejected_setup_count,
        "skipped_row_count": skipped_row_count,
        "unknown_row_count": unknown_row_count,
        "outlier_count": outlier_count,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "average_R": average_R,
        "max_drawdown_R": max_drawdown_R,
        "average_win_R": average_win_R,
        "average_loss_R": average_loss_R,
        "gross_profit_R": gross_profit_R,
        "gross_loss_R": gross_loss_R,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "failure_reason_counts": failure_reason_counts,
        "most_common_failure_reason": most_common_failure_reason,
        "warnings": warnings,
        "verdict": verdict
    }


class LabRunner:
    def __init__(self):
        self.config = load_config()
        self.output_dir = self.config.get("output_dir", "logs/strategy_lab")
        self.min_trades = self.config.get("min_trades_for_verdict", 20)
        self.thresholds = self.config.get("pass_thresholds", {})
        
    def load_json_trades(self, file_path: str) -> list:
        if not os.path.exists(file_path):
            return []
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
            flat = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "trades" in item:
                        flat.extend([(t, file_path) for t in item["trades"]])
                    elif isinstance(item, dict):
                        flat.append((item, file_path))
            elif isinstance(data, dict):
                for pairs in data.get("by_class", {}).values():
                    for p in pairs:
                        if isinstance(p, dict) and "trades" in p:
                            flat.extend([(t, file_path) for t in p["trades"]])
            return flat
        except Exception:
            return []
            
    def load_db_trades(self, file_path: str) -> list:
        if not os.path.exists(file_path):
            return []
        trades = []
        try:
            conn = sqlite3.connect(file_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]
            
            if "audit_log" in tables:
                cursor.execute("PRAGMA table_info(audit_log)")
                cols = [col[1] for col in cursor.fetchall()]
                cursor.execute("SELECT * FROM audit_log")
                for row in cursor.fetchall():
                    trades.append((dict(zip(cols, row)), file_path))
            conn.close()
        except Exception:
            pass
        return trades

    def build_schema_coverage(self, normalized: list[dict]) -> dict:
        total = len(normalized)
        if total == 0:
            return {}
            
        closed = [t for t in normalized if t["is_closed_trade"]]
        n_closed = len(closed)
        n_rej = sum(1 for t in normalized if t["row_type"] == "rejected_setup")
        n_skip = sum(1 for t in normalized if t["row_type"] == "skipped_row")
        n_unk = sum(1 for t in normalized if t["row_type"] == "unknown_row")
        
        has_eng = sum(1 for t in normalized if t["engine"] != "UNKNOWN")
        has_sf = sum(1 for t in normalized if t["strategy_family"] != "UNKNOWN")
        has_reg = sum(1 for t in normalized if t["regime"] != "unknown")
        has_fail = sum(1 for t in normalized if t["failure_reason"] != "UNKNOWN")
        has_exit = sum(1 for t in normalized if t["exit_reason"] != "UNKNOWN")
        has_setup = sum(1 for t in normalized if t["setup_type"] != "UNKNOWN")
        has_tf = sum(1 for t in normalized if t["timeframe"] != "UNKNOWN")
        has_src = sum(1 for t in normalized if t.get("source_module", "UNKNOWN") != "UNKNOWN")
        has_ver = sum(1 for t in normalized if t.get("telemetry_version", "UNKNOWN") != "UNKNOWN")
        has_ts = sum(1 for t in normalized if t["timestamp"])
        has_r = sum(1 for t in normalized if t["r_multiple"] != 0.0)
        has_sym = sum(1 for t in normalized if t["symbol"] != "UNKNOWN")
        
        # Determine top missing fields
        # Look at _raw of normalized
        all_keys = set()
        for t in normalized:
            all_keys.update(t["_raw"].keys())
            
        return {
            "total_rows": total,
            "closed_trade_rows": n_closed,
            "rejected_rows": n_rej,
            "skipped_rows": n_skip,
            "unknown_rows": n_unk,
            "percent_with_engine": (has_eng/total)*100,
            "percent_with_strategy_family": (has_sf/total)*100,
            "percent_with_regime": (has_reg/total)*100,
            "percent_with_failure_reason": (has_fail/total)*100,
            "percent_with_exit_reason": (has_exit/total)*100,
            "percent_with_setup_type": (has_setup/total)*100,
            "percent_with_timeframe": (has_tf/total)*100,
            "percent_with_source_module": (has_src/total)*100,
            "percent_with_telemetry_version": (has_ver/total)*100,
            "percent_with_timestamp": (has_ts/total)*100,
            "percent_with_r_multiple": (has_r/total)*100,
            "percent_with_symbol": (has_sym/total)*100,
            "top_missing_fields": [],
            "top_unknown_reasons": []
        }
        
    def build_unknown_report(self, normalized: list[dict]) -> list[dict]:
        report = []
        for t in normalized:
            if t["strategy_family"] == "UNKNOWN" or t["regime"] == "unknown":
                raw = t["_raw"]
                engine_like = {k: v for k,v in raw.items() if any(x in k for x in ["engine", "strategy", "signal", "source", "type"])}
                regime_like = {k: v for k,v in raw.items() if any(x in k for x in ["regime", "trend", "setup", "trigger"])}
                
                report.append({
                    "symbol": t["symbol"],
                    "timestamp": t["timestamp"],
                    "source_file": t["source_file"],
                    "source_row_id": t["source_row_id"],
                    "reason_strategy_family_unknown": t["strategy_family_reason"] if t["strategy_family"] == "UNKNOWN" else "",
                    "reason_regime_unknown": t["regime_reason"] if t["regime"] == "unknown" else "",
                    "available_keys": list(raw.keys()),
                    "raw_engine_like_fields": engine_like,
                    "raw_regime_like_fields": regime_like
                })
        return report

    def build_manual_close_report(self, normalized: list[dict]) -> dict:
        mc_trades = [t for t in normalized if "MANUAL" in t["exit_reason"] or "MANUAL" in t["failure_reason"]]
        if not mc_trades:
            return {"count": 0}
            
        r_mults = [t["r_multiple"] for t in mc_trades]
        wins = sum(1 for r in r_mults if r > 0)
        
        symbols = list(set(t["symbol"] for t in mc_trades))
        engines = list(set(t["engine"] for t in mc_trades))
        avg_R = sum(r_mults)/len(r_mults)
        wr = wins/len(mc_trades)
        
        return {
            "count": len(mc_trades),
            "symbols": symbols,
            "engines": engines,
            "average_R": avg_R,
            "win_rate": wr,
            "is_profitable": avg_R > 0,
            "sample_rows": [{
                "symbol": t["symbol"],
                "engine": t["engine"],
                "r_multiple": t["r_multiple"],
                "exit_reason": t["exit_reason"]
            } for t in mc_trades[:10]]
        }
        
    def build_outlier_report(self, normalized: list[dict]) -> list[dict]:
        closed = [t for t in normalized if t["is_closed_trade"]]
        if not closed:
            return []
            
        pnls = [t["pnl"] for t in closed if t["pnl"] != 0.0]
        pnls.sort()
        median_pnl = pnls[len(pnls)//2] if pnls else 0.0
        
        # Max DD contributor
        peak = 0.0
        cum = 0.0
        max_dd = 0.0
        worst_t = None
        
        has_timestamps = all(t.get("timestamp") for t in closed)
        sorted_trades = sorted(closed, key=lambda x: str(x["timestamp"])) if has_timestamps else closed
        for t in sorted_trades:
            cum += t["r_multiple"]
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
                worst_t = t
                
        outliers = []
        for t in closed:
            flags = []
            if abs(t["r_multiple"]) > 5.0:
                flags.append(f"abs(R) > 5: {t['r_multiple']}")
            if median_pnl != 0.0 and abs(t["pnl"]) > abs(median_pnl) * 10:
                flags.append(f"Extreme PNL: {t['pnl']} vs Median: {median_pnl}")
            if t["failure_reason"] == "UNKNOWN" and abs(t["r_multiple"]) > 3.0:
                flags.append(f"Missing exit reason but large R: {t['r_multiple']}")
            if t == worst_t:
                flags.append(f"Max DD contributor")
                
            if flags:
                outliers.append({
                    "symbol": t["symbol"],
                    "r_multiple": t["r_multiple"],
                    "pnl": t["pnl"],
                    "timestamp": t["timestamp"],
                    "flags": flags
                })
        return outliers

    def run_evaluation(self, input_source: str = None) -> dict:
        """Runs the categorization matrix logic safely."""
        all_raw_trades = []
        if input_source and os.path.exists(input_source):
            if input_source.endswith(".db"):
                all_raw_trades.extend(self.load_db_trades(input_source))
            else:
                all_raw_trades.extend(self.load_json_trades(input_source))
        else:
            sources = self.config.get("input_sources", {})
            bt_json = sources.get("bt_results_json", "logs/backtest/bt_results.json")
            db_path = sources.get("audit_db", "audit.db")
            all_raw_trades.extend(self.load_json_trades(bt_json))
            all_raw_trades.extend(self.load_db_trades(db_path))

        normalized = []
        for i, (t, fpath) in enumerate(all_raw_trades):
            normalized.append(normalize_trade(t, source_file=fpath, row_id=i))
        
        by_group, by_symbol, by_engine, by_regime = {}, {}, {}, {}
        by_family, by_timeframe = {}, {}
        failures = []
        
        for t in normalized:
            g_key = t["asset_group"]
            s_key = t["symbol"]
            e_key = t["engine"]
            r_key = t["regime"]
            sf_key = t["strategy_family"]
            tf_key = t["timeframe"]
            
            by_group.setdefault(g_key, []).append(t)
            by_symbol.setdefault(s_key, []).append(t)
            by_engine.setdefault(e_key, []).append(t)
            by_regime.setdefault(r_key, []).append(t)
            by_family.setdefault(sf_key, []).append(t)
            by_timeframe.setdefault(tf_key, []).append(t)
            
            if not t.get("is_closed_trade") or not t.get("win"):
                failures.append(t)
                
        def map_groups(target_dict):
            out = {}
            for k, trs in target_dict.items():
                out[k] = calculate_group_metrics(trs, self.min_trades, self.thresholds)
            return out
            
        by_group_metrics = map_groups(by_group)
        by_symbol_metrics = map_groups(by_symbol)
        by_engine_metrics = map_groups(by_engine)
        by_regime_metrics = map_groups(by_regime)
        by_family_metrics = map_groups(by_family)
        by_timeframe_metrics = map_groups(by_timeframe)
        
        overall_metrics = calculate_group_metrics(normalized, self.min_trades, self.thresholds)
        
        failure_stats = {
            "overall_failures": len(failures),
            "failure_reason_breakdown": overall_metrics.get("failure_reason_counts", {})
        }

        # Integrity warnings
        integrity_warnings = self._build_integrity_warnings(normalized, by_engine_metrics)

        if self.config.get("write_reports", True):
            os.makedirs(self.output_dir, exist_ok=True)
            with open(os.path.join(self.output_dir, "strategy_lab_summary.json"), "w") as f:
                json.dump({"timestamp": datetime.now().isoformat(), "metrics": overall_metrics}, f, indent=2)
            with open(os.path.join(self.output_dir, "strategy_lab_by_group.json"), "w") as f:
                json.dump(by_group_metrics, f, indent=2)
            with open(os.path.join(self.output_dir, "strategy_lab_by_symbol.json"), "w") as f:
                json.dump(by_symbol_metrics, f, indent=2)
            with open(os.path.join(self.output_dir, "strategy_lab_by_engine.json"), "w") as f:
                json.dump(by_engine_metrics, f, indent=2)
            with open(os.path.join(self.output_dir, "strategy_lab_by_regime.json"), "w") as f:
                json.dump(by_regime_metrics, f, indent=2)
            with open(os.path.join(self.output_dir, "strategy_lab_failures.json"), "w") as f:
                json.dump(failure_stats, f, indent=2)
                
            # New reports
            with open(os.path.join(self.output_dir, "strategy_lab_by_strategy_family.json"), "w") as f:
                json.dump(by_family_metrics, f, indent=2)
            with open(os.path.join(self.output_dir, "strategy_lab_by_timeframe.json"), "w") as f:
                json.dump(by_timeframe_metrics, f, indent=2)
                
            outliers = self.build_outlier_report(normalized)
            with open(os.path.join(self.output_dir, "strategy_lab_outliers.json"), "w") as f:
                json.dump(outliers, f, indent=2)
                
            schema_cov = self.build_schema_coverage(normalized)
            with open(os.path.join(self.output_dir, "strategy_lab_schema_coverage.json"), "w") as f:
                json.dump(schema_cov, f, indent=2)
                
            unknown_rep = self.build_unknown_report(normalized)
            with open(os.path.join(self.output_dir, "strategy_lab_unknown_classification.json"), "w") as f:
                json.dump(unknown_rep, f, indent=2)
                
            manual_close = self.build_manual_close_report(normalized)
            with open(os.path.join(self.output_dir, "strategy_lab_manual_close.json"), "w") as f:
                json.dump(manual_close, f, indent=2)
            
            # Write integrity report
            with open(os.path.join(self.output_dir, "strategy_lab_integrity.json"), "w") as f:
                json.dump({"timestamp": datetime.now().isoformat(), "warnings": integrity_warnings}, f, indent=2)

        # Print human-readable report to stdout
        self._print_report(overall_metrics, by_engine_metrics, by_group_metrics, integrity_warnings)

        return overall_metrics

    def _build_integrity_warnings(self, normalized: list[dict], by_engine_metrics: dict) -> list[str]:
        warnings = []
        closed = [t for t in normalized if t.get("is_closed_trade")]
        total_closed = len(closed)
        
        # 1. NULL / UNKNOWN / blank engine labels
        unknown_engine = [t for t in closed if t.get("engine") in (None, "", "UNKNOWN")]
        if unknown_engine:
            warnings.append(f"INTEGRITY: {len(unknown_engine)} closed trades have NULL/UNKNOWN/blank engine labels")
        
        # 2. Sum of engine trades != total closed trades
        engine_sum = sum(m.get("trade_count", 0) for m in by_engine_metrics.values())
        if engine_sum != total_closed:
            warnings.append(f"INTEGRITY: engine trade count sum ({engine_sum}) != total closed trades ({total_closed})")
        
        # 3. Missing ENGINE_A
        if "ENGINE_A" not in by_engine_metrics or by_engine_metrics["ENGINE_A"].get("trade_count", 0) == 0:
            warnings.append("INTEGRITY: ENGINE_A is missing from engine breakdown — raw data may not have been ingested")
        
        # 4. Incomplete ENGINE_C coverage
        engine_c_count = by_engine_metrics.get("ENGINE_C", {}).get("trade_count", 0)
        if engine_c_count < 20:
            warnings.append(f"INTEGRITY: ENGINE_C has only {engine_c_count} trades (< 20 minimum for statistical confidence)")
        
        return warnings

    def _print_report(self, overall_metrics: dict, by_engine_metrics: dict, by_group_metrics: dict, integrity_warnings: list[str]) -> None:
        print("\n" + "=" * 70)
        print("  ATHENA STRATEGY LAB v1.2 - MATRIX REPORT")
        print("=" * 70)
        print(f"  Total rows processed      : {overall_metrics.get('trade_count', 0) + overall_metrics.get('rejected_setup_count', 0) + overall_metrics.get('skipped_row_count', 0) + overall_metrics.get('unknown_row_count', 0)}")
        print(f"  Total simulated outcomes  : {overall_metrics.get('trade_count', 0)}")
        print(f"  Win rate                  : {overall_metrics.get('win_rate', 0):.2%}")
        print(f"  Profit factor             : {overall_metrics.get('profit_factor', 'N/A')}")
        print(f"  Average R                 : {overall_metrics.get('average_R', 0):.3f}")
        print(f"  Max drawdown (R)          : {overall_metrics.get('max_drawdown_R', 0):.3f}")
        print("-" * 70)
        print("  ENGINE BREAKDOWN")
        print("-" * 70)
        print(f"  {'Engine':<12} {'Trades':>8} {'Win Rate':>10} {'PF':>8} {'Avg R':>8} {'Verdict':>8}")
        print("-" * 70)
        for engine in sorted(by_engine_metrics.keys()):
            m = by_engine_metrics[engine]
            pf = m.get("profit_factor")
            pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
            print(f"  {engine:<12} {m.get('trade_count', 0):>8} {m.get('win_rate', 0):>9.1%} {pf_str:>8} {m.get('average_R', 0):>8.3f} {m.get('verdict', 'N/A'):>8}")
        print("-" * 70)
        print("  ASSET GROUP BREAKDOWN")
        print("-" * 70)
        print(f"  {'Group':<18} {'Trades':>8} {'Win Rate':>10} {'PF':>8} {'Avg R':>8}")
        print("-" * 70)
        for group in sorted(by_group_metrics.keys()):
            m = by_group_metrics[group]
            pf = m.get("profit_factor")
            pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
            print(f"  {group:<18} {m.get('trade_count', 0):>8} {m.get('win_rate', 0):>9.1%} {pf_str:>8} {m.get('average_R', 0):>8.3f}")
        print("-" * 70)
        if integrity_warnings:
            print("  INTEGRITY WARNINGS")
            print("-" * 70)
            for w in integrity_warnings:
                print(f"  [WARN] {w}")
            print("-" * 70)
        print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Athena Strategy Lab CLI Tool")
    parser.add_argument("--input", type=str, help="Override input file (JSON or DB)")
    parser.add_argument("--output", type=str, help="Override output directory")
    args = parser.parse_args()
    
    runner = LabRunner()
    if args.output:
        runner.output_dir = args.output
        
    runner.run_evaluation(args.input)
