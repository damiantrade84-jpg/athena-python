from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from athena_research.fast_live_gate_replay import LiveGateReplayConfig, run_fast_live_gate_replay


def _csv_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return tuple()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _csv_int_tuple(value: str | None) -> tuple[int, ...]:
    if not value:
        return tuple()
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast read-only replay of current Engine A/B live gates")
    parser.add_argument("--market-group", default="crypto")
    parser.add_argument("--style", default="intra")
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,AVAX/USDT,LINK/USDT,DOGE/USDT,XRP/USDT")
    parser.add_argument("--engines", default="A,B")
    parser.add_argument("--timeframes", default="H4")
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--output-dir", default="logs/fast_live_gate_replay")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--bar-stride", type=int, default=1)
    parser.add_argument("--max-bars-per-symbol-tf", type=int, default=None)
    parser.add_argument("--outcome-bars", default="6,12,24", help="Forward bars used for WR/SQN outcome checks")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-symbol progress output")
    args = parser.parse_args()

    result = run_fast_live_gate_replay(
        LiveGateReplayConfig(
            market_group=args.market_group,
            style=args.style,
            symbols=_csv_tuple(args.symbols),
            engines=_csv_tuple(args.engines),
            timeframes=_csv_tuple(args.timeframes),
            lookback_days=args.lookback_days,
            output_dir=Path(args.output_dir),
            run_id=args.run_id,
            force_refresh=args.force_refresh,
            bar_stride=max(1, args.bar_stride),
            max_bars_per_symbol_tf=args.max_bars_per_symbol_tf,
            outcome_bars=_csv_int_tuple(args.outcome_bars) or (6, 12, 24),
            progress=not args.quiet,
        )
    )
    print(f"run_id={result['run_id']}")
    print(f"run_dir={result['run_dir']}")
    print(f"summary_rows={result['summary_rows']} detail_rows={result['detail_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
