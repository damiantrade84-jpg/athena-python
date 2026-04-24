#!/usr/bin/env python3
"""Generate paper soak report from JSONL logs."""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def load_jsonl(filepath: Path) -> list[dict]:
    """Load JSONL file into list of dicts."""
    if not filepath.exists():
        return []
    
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def generate_report(log_dir: Path) -> dict[str, any]:
    """Generate report metrics from paper soak logs."""
    signals = load_jsonl(log_dir / "signals.jsonl")
    execution_decisions = load_jsonl(log_dir / "execution_decisions.jsonl")
    freshness_blocks = load_jsonl(log_dir / "freshness_blocks.jsonl")
    risk_blocks = load_jsonl(log_dir / "risk_blocks.jsonl")
    engine_consensus = load_jsonl(log_dir / "engine_consensus.jsonl")
    
    # Total metrics
    total_scans = len(signals)  # Each signal represents a scan result
    total_signals = len([s for s in signals if s.get("engine_a", {}).get("passed") or s.get("engine_b", {}).get("passed")])
    
    # Execution decisions
    allowed_paper_trades = len([d for d in execution_decisions if d.get("decision") == "ALLOW"])
    blocked_stale_data = len(freshness_blocks)
    blocked_risk_signals = len(risk_blocks)
    
    # Engine breakdown
    engine_a_only = 0
    engine_b_only = 0
    aligned_a_b = 0
    conflict = 0
    
    for signal in signals:
        engine_a = signal.get("engine_a", {})
        engine_b = signal.get("engine_b", {})
        
        a_passed = engine_a.get("passed", False)
        b_passed = engine_b.get("passed", False)
        
        if a_passed and b_passed:
            aligned_a_b += 1
        elif a_passed and not b_passed:
            engine_a_only += 1
        elif not a_passed and b_passed:
            engine_b_only += 1
        else:
            conflict += 1
    
    # Symbol breakdown
    symbol_counts = Counter(s.get("symbol") for s in signals)
    
    # Timeframe breakdown (from signals)
    timeframe_counts = Counter()
    for signal in signals:
        tfs = signal.get("timeframe_set", [])
        for tf in tfs:
            timeframe_counts[tf] += 1
    
    # Feed/provider errors
    feed_errors = len([b for b in freshness_blocks if "provider" in b.get("block_reason", "").lower()])
    
    # Freshness gate pass rate
    freshness_allowed = len([d for d in execution_decisions if d.get("decision") == "ALLOW"])
    freshness_total = len(execution_decisions) + len(freshness_blocks)
    freshness_pass_rate = (freshness_allowed / freshness_total * 100) if freshness_total > 0 else 0
    
    return {
        "total_scans": total_scans,
        "total_signals": total_signals,
        "allowed_paper_trades": allowed_paper_trades,
        "blocked_stale_data": blocked_stale_data,
        "blocked_risk_signals": blocked_risk_signals,
        "engine_a_only": engine_a_only,
        "engine_b_only": engine_b_only,
        "aligned_a_b": aligned_a_b,
        "conflict": conflict,
        "symbol_breakdown": dict(symbol_counts.most_common()),
        "timeframe_breakdown": dict(timeframe_counts.most_common()),
        "feed_errors": feed_errors,
        "freshness_pass_rate": round(freshness_pass_rate, 2),
        "duplicate_trade_blocks": 0,  # Would need additional logging
        "execution_rejects": len([d for d in execution_decisions if d.get("decision") == "BLOCK"]),
    }


def write_markdown_report(metrics: dict[str, any], output_path: Path):
    """Write markdown report."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Paper Soak Report\n\n")
        f.write(f"**Generated:** {datetime.now().isoformat()}\n\n")
        
        f.write("## Summary Metrics\n\n")
        f.write("| Metric | Value |\n")
        f.write("|--------|-------|\n")
        f.write(f"| Total Scans | {metrics['total_scans']} |\n")
        f.write(f"| Total Signals | {metrics['total_signals']} |\n")
        f.write(f"| Allowed Paper Trades | {metrics['allowed_paper_trades']} |\n")
        f.write(f"| Blocked (Stale Data) | {metrics['blocked_stale_data']} |\n")
        f.write(f"| Blocked (Risk) | {metrics['blocked_risk_signals']} |\n")
        f.write(f"| Execution Rejects | {metrics['execution_rejects']} |\n")
        f.write(f"| Freshness Gate Pass Rate | {metrics['freshness_pass_rate']}% |\n")
        f.write("\n")
        
        f.write("## Engine Consensus\n\n")
        f.write("| Type | Count |\n")
        f.write("|------|-------|\n")
        f.write(f"| Engine A Only | {metrics['engine_a_only']} |\n")
        f.write(f"| Engine B Only | {metrics['engine_b_only']} |\n")
        f.write(f"| Aligned A+B | {metrics['aligned_a_b']} |\n")
        f.write(f"| Conflict | {metrics['conflict']} |\n")
        f.write("\n")
        
        f.write("## Symbol Breakdown\n\n")
        f.write("| Symbol | Count |\n")
        f.write("|--------|-------|\n")
        for symbol, count in metrics['symbol_breakdown'].items():
            f.write(f"| {symbol} | {count} |\n")
        f.write("\n")
        
        f.write("## Timeframe Breakdown\n\n")
        f.write("| Timeframe | Count |\n")
        f.write("|-----------|-------|\n")
        for tf, count in metrics['timeframe_breakdown'].items():
            f.write(f"| {tf} | {count} |\n")
        f.write("\n")
        
        f.write("## Errors and Issues\n\n")
        f.write("| Type | Count |\n")
        f.write("|------|-------|\n")
        f.write(f"| Feed/Provider Errors | {metrics['feed_errors']} |\n")
        f.write(f"| Duplicate Trade Blocks | {metrics['duplicate_trade_blocks']} |\n")
        f.write("\n")
        
        f.write("## Notes\n\n")
        f.write("- Win/loss tracking requires exit logging (not yet implemented)\n")
        f.write("- Average R ratio requires exit logging (not yet implemented)\n")
        f.write("- Max drawdown requires P&L tracking (not yet implemented)\n")


def main():
    parser = argparse.ArgumentParser(description="Generate paper soak report")
    parser.add_argument("--log-dir", default="logs/paper_soak", help="Path to paper soak log directory")
    parser.add_argument("--output", default="docs/diagnostics/paper_soak_report.md", help="Output markdown file")
    args = parser.parse_args()
    
    log_dir = Path(args.log_dir)
    output_path = Path(args.output)
    
    if not log_dir.exists():
        print(f"Error: Log directory not found: {log_dir}")
        return
    
    metrics = generate_report(log_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_markdown_report(metrics, output_path)
    
    print(f"Generated paper soak report at {output_path}")
    print(f"Total scans: {metrics['total_scans']}")
    print(f"Allowed paper trades: {metrics['allowed_paper_trades']}")
    print(f"Blocked (stale data): {metrics['blocked_stale_data']}")
    print(f"Blocked (risk): {metrics['blocked_risk_signals']}")


if __name__ == "__main__":
    main()
