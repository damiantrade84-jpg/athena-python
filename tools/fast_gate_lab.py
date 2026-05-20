from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from athena_research.fast_gate_lab import FastGateConfig, run_fast_gate_lab


def _csv_tuple(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast read-only Engine A/B gate research runner")
    parser.add_argument("--market-group", default="crypto")
    parser.add_argument("--style", default="intra")
    parser.add_argument("--timeframes", default="H1,H4")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--engines", default="A,B")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--max-candidates", type=int, default=250)
    parser.add_argument("--output-dir", default="logs/fast_gate_lab")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config-path", default="configs/vectorbt_research_lab.yaml")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--engine-a-gate-sweep", default="current,-0.2,-0.1,+0.1,+0.2")
    parser.add_argument("--engine-b-gate-sweep", default="current,-0.5,+0.5")
    args = parser.parse_args()

    result = run_fast_gate_lab(
        FastGateConfig(
            market_group=args.market_group,
            style=args.style,
            timeframes=_csv_tuple(args.timeframes) or ("H1", "H4"),
            engines=_csv_tuple(args.engines) or ("A", "B"),
            lookback_days=args.lookback_days,
            symbols=_csv_tuple(args.symbols),
            top_n=args.top_n,
            max_candidates=args.max_candidates,
            output_dir=Path(args.output_dir),
            run_id=args.run_id,
            config_path=Path(args.config_path),
            force_refresh=args.force_refresh,
            engine_a_gate_sweep=_csv_tuple(args.engine_a_gate_sweep) or ("current", "-0.2", "-0.1", "+0.1", "+0.2"),
            engine_b_gate_sweep=_csv_tuple(args.engine_b_gate_sweep) or ("current", "-0.5", "+0.5"),
        )
    )

    cache = result["cache_health"]
    print(f"cache_status={cache.get('status')} cache_dir={cache.get('cache_dir')} engine={cache.get('parquet_engine') or 'none'}")
    print(f"run_id={result['run_id']}")
    print(f"run_dir={result['run_dir']}")
    print(f"screened_rows={result['screened_rows']} top_candidate_rows={result['top_candidate_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
