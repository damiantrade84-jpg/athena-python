"""CLI entry point: python -m athena_backtest.cli run --symbol EURUSD --engine A --style intraday"""

from __future__ import annotations

import argparse
import json
import logging


def _find_pair(symbol: str) -> dict:
    from athena_backtest.data.fetchers import _load_monolith

    mod = _load_monolith()
    wanted = symbol.strip().upper()
    for pair in getattr(mod, "ALL_PAIRS", []):
        if str(pair.get("symbol") or "").upper() == wanted or str(pair.get("display") or "").upper() == wanted:
            return pair
    raise SystemExit(f"no pair found for symbol/display {symbol!r}")


def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Athena backtest v3 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run one pair backtest")
    run_p.add_argument("--symbol", required=True)
    run_p.add_argument("--engine", required=True, choices=("A", "B"))
    run_p.add_argument("--style", default="intraday", choices=("intraday", "swing"))

    args = parser.parse_args()

    if args.command == "run":
        from athena_backtest.runner import run_pair

        pair = _find_pair(args.symbol)
        result = run_pair(pair, engine=args.engine, horizon=args.style)
        summary = {
            k: result.get(k)
            for k in (
                "totalTrades", "winRate", "sqn", "profitFactor", "totalR",
                "maxDrawdownR", "entryTimeframe", "wallTimeSec",
            )
        }
        metrics = result.get("metricsV3") or {}
        validation = result.get("validation") or {}
        summary["deflatedSqn"] = metrics.get("deflatedSqn")
        summary["bootstrap"] = metrics.get("bootstrap")
        summary["verdict"] = validation.get("verdict")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _main()
