#!/usr/bin/env python
"""
Athena Research Lab CLI
-----------------------
Run VectorBT-based strategy discovery from the command line.

Usage:
    python tools/vectorbt_research_lab.py --mode tiny
    python tools/vectorbt_research_lab.py --mode full --families trend_momentum pullback
    python tools/vectorbt_research_lab.py --mode tiny --ai-review
    python tools/vectorbt_research_lab.py --list-runs

Install requirements (if missing):
    pip install vectorbt yfinance pandas numpy pyyaml pyarrow

Safety:
    - Does NOT import live execution modules.
    - Does NOT modify live thresholds or config.yaml.
    - All output is written to logs/research_lab/<run_id>/.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_FORBIDDEN = {"athena", "execution", "mt5_executor", "bybit_executor", "auto_trader"}


def _abort_if_live_execution_modules_loaded() -> None:
    """CLI safety guard; imports remain safe for tests and registry inspection."""
    already = [m for m in _FORBIDDEN if m in sys.modules]
    if already:
        print(f"ERROR: Live execution modules detected in sys.modules: {already}", file=sys.stderr)
        sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("research_lab")


def _check_deps() -> bool:
    """Check required packages and warn about optional ones."""
    ok = True
    try:
        import pandas
    except ImportError:
        log.error("pandas not installed: pip install pandas")
        ok = False
    try:
        import numpy
    except ImportError:
        log.error("numpy not installed: pip install numpy")
        ok = False
    try:
        import yaml
    except ImportError:
        log.error("pyyaml not installed: pip install pyyaml")
        ok = False

    # Optional but recommended
    try:
        import vectorbt
        log.info("vectorbt %s — using VectorBT for portfolio simulation", vectorbt.__version__)
    except ImportError:
        log.warning("vectorbt not installed — using pandas fallback backtester")
        log.warning("For faster results: pip install vectorbt")

    try:
        import yfinance
        log.info("yfinance %s — data downloads enabled", yfinance.__version__)
    except ImportError:
        log.error("yfinance not installed: pip install yfinance")
        ok = False

    return ok


def _print_run_summary(result: dict) -> None:
    print("\n" + "=" * 60)
    print("RESEARCH LAB RUN COMPLETE")
    print("=" * 60)
    print(f"Run ID    : {result['run_id']}")
    print(f"Mode      : {result['mode']}")
    print(f"Symbols   : {len(result.get('symbols', []))}")
    print(f"Timeframes: {result.get('timeframes', [])}")
    print(f"Families  : {result.get('families', [])}")
    print(f"Results   : {result['results_count']} strategy evaluations")
    print(f"Output    : {result['run_dir']}")
    print("")

    if result.get("ai_review"):
        ai = result["ai_review"]
        if ai.get("error"):
            print(f"AI Review : FAILED — {ai['error']}")
        else:
            print(f"AI Review : {'AI-powered' if ai.get('ai_powered') else 'Deterministic'} ({ai.get('model', '?')})")
            print(f"Verdict   : {ai.get('overall_verdict', '')[:120]}")
            top = ai.get("top_candidates", [])
            if top:
                print(f"Top pick  : {top[0].get('strategy', '?')} on {top[0].get('symbol', '?')} {top[0].get('tf', '?')} [{top[0].get('label', '?')}]")

    print("\nOutput files:")
    output_dir = Path(result["run_dir"])
    for fname in sorted(output_dir.glob("*.csv")) + sorted(output_dir.glob("*.md")) + sorted(output_dir.glob("*.json")):
        size = fname.stat().st_size
        print(f"  {fname.name:45s}  {size:>8,} bytes")


def _list_runs(output_dir: str = "logs/research_lab") -> None:
    from athena_research.run_manager import list_runs
    runs = list_runs(output_dir)
    if not runs:
        print("No research runs found.")
        return
    print(f"\n{'Run ID':40s} {'Mode':8s} {'Symbols':8s} {'Strong':8s} {'Weak':8s}")
    print("-" * 80)
    for r in runs:
        print(f"{r['run_id']:40s} {r.get('mode', '?'):8s} "
              f"{r.get('symbol_count', '?'):8s} {r.get('strong', '?'):8s} {r.get('weak', '?'):8s}")


def _family_choices() -> list[str]:
    """Return strategy family choices from the shared research registry."""
    from athena_research.strategies import FAMILY_STRATEGIES
    return sorted(FAMILY_STRATEGIES)


def main():
    parser = argparse.ArgumentParser(
        description="Athena Research Lab — VectorBT strategy discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", choices=["tiny", "full"], default="tiny",
        help="tiny = validation run (limited symbols/TFs); full = complete sweep",
    )
    parser.add_argument(
        "--families", nargs="+",
        choices=_family_choices(),
        help="Override strategy families to test (default: from config)",
    )
    parser.add_argument(
        "--symbols", nargs="+",
        help="Override symbols (e.g. BTC/USDT EUR/USD)",
    )
    parser.add_argument(
        "--timeframes", nargs="+",
        choices=["M5", "M15", "H1", "H4", "D1"],
        help="Override timeframes",
    )
    parser.add_argument(
        "--direction", choices=["long", "short", "both"], default="both",
        help="Test long only, short only, or both directions",
    )
    parser.add_argument(
        "--ai-review", action="store_true",
        help="Run AI analyst after backtest completes",
    )
    parser.add_argument(
        "--force-refresh", action="store_true",
        help="Force re-download of data (bypass cache)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel workers",
    )
    parser.add_argument(
        "--output-dir", default="logs/research_lab",
        help="Base output directory",
    )
    parser.add_argument(
        "--config", default="configs/vectorbt_research_lab.yaml",
        help="Config file path",
    )
    parser.add_argument(
        "--list-runs", action="store_true",
        help="List completed research runs and exit",
    )
    parser.add_argument(
        "--run-ai-only", metavar="RUN_ID",
        help="Run AI analyst on existing run_id and exit",
    )

    args = parser.parse_args()

    if args.list_runs:
        _list_runs(args.output_dir)
        return

    if args.run_ai_only:
        from athena_research.ai_analyst import run_ai_analysis
        run_dir = Path(args.output_dir) / args.run_ai_only
        if not run_dir.exists():
            print(f"ERROR: Run not found: {run_dir}", file=sys.stderr)
            sys.exit(1)
        result = run_ai_analysis(run_dir=run_dir)
        print(f"AI review written to {result.get('files_written', {})}")
        print(f"Verdict: {result.get('overall_verdict', '')}")
        return

    # Check dependencies
    if not _check_deps():
        print("\nInstall missing packages and retry.", file=sys.stderr)
        sys.exit(1)

    # Build override config
    from athena_research.run_manager import load_config, run_research

    config_path = args.config
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        print(f"ERROR: Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    log.info("Starting Research Lab run | mode=%s | workers=%d", args.mode, args.workers)

    result = run_research(
        mode=args.mode,
        config_path=config_path,
        output_dir=args.output_dir,
        force_refresh=args.force_refresh,
        max_workers=args.workers,
        direction=args.direction,
        run_ai_review=args.ai_review,
        symbols=args.symbols if args.symbols else None,
        timeframes=args.timeframes if args.timeframes else None,
        families=args.families if args.families else None,
    )

    _print_run_summary(result)

    print(f"\nOpen report: {result['run_dir']}\\research_report.md")
    print("Done.\n")


if __name__ == "__main__":
    _abort_if_live_execution_modules_loaded()
    main()
