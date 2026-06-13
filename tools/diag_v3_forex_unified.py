"""Unified Engine A v3 forex diagnostic — single evidence chain.

Uses frozen candles + run_v3_backtest directly (no athena.py import).

Usage:
  python tools/diag_v3_forex_unified.py
  python tools/diag_v3_forex_unified.py --setup london_open --pairs EUR/USD,GBP/USD
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DATA_AS_OF = "2026-05-30"
FOREX_MAJORS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "NZD/USD",
    "USD/CAD",
    "USD/CHF",
]
PAIR_SELECTION = ["EUR/GBP", "USD/CHF", "USD/MXN"]
PAIR_META = {
    "EUR/USD": {"display": "EUR/USD", "symbol": "EURUSD", "source": "mt5", "type": "forex"},
    "GBP/USD": {"display": "GBP/USD", "symbol": "GBPUSD", "source": "mt5", "type": "forex"},
    "USD/JPY": {"display": "USD/JPY", "symbol": "USDJPY", "source": "mt5", "type": "forex"},
    "AUD/USD": {"display": "AUD/USD", "symbol": "AUDUSD", "source": "mt5", "type": "forex"},
    "NZD/USD": {"display": "NZD/USD", "symbol": "NZDUSD", "source": "mt5", "type": "forex"},
    "USD/CAD": {"display": "USD/CAD", "symbol": "USDCAD", "source": "mt5", "type": "forex"},
    "USD/CHF": {"display": "USD/CHF", "symbol": "USDCHF", "source": "mt5", "type": "forex"},
    "EUR/GBP": {"display": "EUR/GBP", "symbol": "EURGBP", "source": "mt5", "type": "forex"},
    "USD/MXN": {"display": "USD/MXN", "symbol": "USDMXN", "source": "mt5", "type": "forex"},
}


def _load_candles(data_as_of: str, pair: dict) -> dict[str, list[dict]]:
    from frozen_data import read_frozen_candles

    provider = "mt5"
    return {
        "D1": read_frozen_candles(data_as_of, pair, "D1", provider, min_bars=60),
        "H4": read_frozen_candles(data_as_of, pair, "H4", provider, min_bars=80),
        "H1": read_frozen_candles(data_as_of, pair, "H1", provider, min_bars=80),
    }


def _run_pair(data_as_of: str, pair: dict, setup: str) -> dict:
    from config import CONFIG
    from engine_a_v3.backtest import run_v3_backtest
    from engine_a_v3.diagnostics import summarize_v3_trades
    from engine_a_v3.promotion import demo_unvalidated_registry

    CONFIG["ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED"] = True
    CONFIG["ENGINE_A_V3_FOREX_SETUP"] = setup
    CONFIG["ENGINE_A_V3_FOREX_THESIS"] = "trend"
    costs = CONFIG.get("ENGINE_A_V3_BACKTEST") or {}
    candles = _load_candles(data_as_of, pair)
    registry = demo_unvalidated_registry(REPO / "tmp" / "v3_demo_registry")
    result = run_v3_backtest(
        pair,
        candles,
        horizon="intraday",
        registry=registry,
        spread_bps=float(costs.get("SPREAD_BPS", 2.0)),
        commission_bps=float(costs.get("COMMISSION_BPS", 1.0)),
        slippage_bps=float(costs.get("SLIPPAGE_BPS", 1.0)),
        swap_bps_per_day=float(costs.get("SWAP_BPS_PER_DAY", 0.5)),
        max_hold_bars=int(costs.get("MAX_HOLD_BARS", 24)),
    )
    trades = result.get("trades") or []
    diagnostics = summarize_v3_trades(trades)
    return {
        "totalTrades": result.get("totalTrades"),
        "sqn": result.get("sqn"),
        "expectancy": result.get("expectancy"),
        "winRate": result.get("winRate"),
        "error": result.get("error"),
        "diagnostics": diagnostics,
        "trades": trades,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", default="trend", choices=("trend", "london_open"))
    parser.add_argument("--pairs", default="forex_majors", help="forex_majors | pair_selection | comma list")
    parser.add_argument("--as-of", default=DATA_AS_OF)
    args = parser.parse_args()

    os.environ["BACKTEST_DATA_AS_OF"] = args.as_of
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
    os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
    os.chdir(REPO)

    if args.pairs == "forex_majors":
        displays = FOREX_MAJORS
    elif args.pairs == "pair_selection":
        displays = PAIR_SELECTION
    else:
        displays = [p.strip() for p in args.pairs.split(",") if p.strip()]

    from engine_a_v3.diagnostics import summarize_v3_trades

    pooled_trades: list[dict] = []
    per_pair: dict = {}

    for display in displays:
        pair = PAIR_META.get(display)
        if not pair:
            per_pair[display] = {"error": "pair_not_found"}
            continue
        print(f"BT[v3-unified] {display} setup={args.setup} ...", flush=True)
        try:
            result = _run_pair(args.as_of, pair, args.setup)
        except Exception as exc:
            per_pair[display] = {"error": repr(exc)}
            traceback.print_exc()
            continue
        trades = result.pop("trades", [])
        for trade in trades:
            trade["_pair"] = display
        pooled_trades.extend(trades)
        per_pair[display] = result
        print(json.dumps({display: {k: v for k, v in result.items() if k != "trades"}}, sort_keys=True), flush=True)

    summary = summarize_v3_trades(pooled_trades)
    out = {
        "data_as_of": args.as_of,
        "setup": args.setup,
        "pairs": displays,
        "pooled": summary,
        "per_pair": per_pair,
        "setup_passes_gate": bool(
            summary.get("n", 0) >= 100 and (summary.get("expectancyR") or 0) > 0
        ),
    }
    tag = args.as_of.replace("-", "")
    out_path = REPO / "tmp" / f"diag_v3_forex_unified_{tag}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out_path}", flush=True)
    print(
        json.dumps({"pooled": summary, "passes_gate": out["setup_passes_gate"]}, indent=2),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
