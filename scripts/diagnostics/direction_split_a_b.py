"""Read-only Engine A/B direction split over frozen backtest candles."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import os
import socket
import sys
from pathlib import Path


PAIRS = ("EUR/USD", "GBP/USD", "BTC/USDT", "ETH/USDT", "XAU/USD", "XAG/USD", "MSFT")


def _block_network() -> None:
    def blocked(*_args, **_kwargs):
        raise RuntimeError("NETWORK_BLOCKED_DIRECTION_DIAGNOSTIC")
    socket.create_connection = blocked
    try:
        import requests
        requests.sessions.Session.request = blocked
    except ImportError:
        pass


def _load_athena(root: Path):
    spec = importlib.util.spec_from_file_location("athena_direction_diagnostic", root / "athena.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load athena.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _verdict(n: int, expectancy: float, sqn: float) -> str:
    if n < 30:
        return "INSUFFICIENT_SAMPLE"
    if -0.03 <= expectancy <= 0.03:
        return "FLAT_NO_EDGE"
    if expectancy < 0 and sqn < 0:
        return "BAD_DIRECTION"
    if sqn >= 2.0:
        return "GOOD_EDGE_CANDIDATE"
    if sqn >= 1.5:
        return "TRADABLE_AVERAGE"
    if expectancy > 0:
        return "WEAK_POSITIVE"
    return "BAD_DIRECTION"


def _metrics(engine: str, pair: str, direction: str, trades: list[dict]) -> dict:
    values = [float(t.get("resultR", t.get("r_multiple", 0.0)) or 0.0) for t in trades]
    n = len(values)
    wins = [r for r in values if r > 0]
    losses = [r for r in values if r <= 0]
    expectancy = sum(values) / n if n else 0.0
    variance = sum((r - expectancy) ** 2 for r in values) / (n - 1) if n > 1 else 0.0
    sqn = expectancy / math.sqrt(variance) * math.sqrt(n) if variance > 0 else 0.0
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    peak = equity = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "engine": engine, "pair": pair, "direction": direction, "n": n,
        "win_pct": round(100 * len(wins) / n, 1) if n else 0.0,
        "expR": round(expectancy, 3), "SQN": round(max(-10, min(10, sqn)), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avg_win_R": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss_R": round(sum(losses) / len(losses), 3) if losses else None,
        "max_drawdown_R": round(max_dd, 2),
        "verdict": _verdict(n, expectancy, sqn),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-05-30")
    parser.add_argument("--out", default="reports/direction_split_a_b_2026-05-30")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["BACKTEST_DATA_AS_OF"] = args.as_of
    os.environ["ATHENA_DIAGNOSTIC_MODE"] = "1"
    athena = _load_athena(root)
    from athena_runtime import rt
    rt().AUDIT_DB = str(root / "tmp" / "direction_split_diagnostic.db")
    _block_network()
    pair_map = {p.get("display"): p for p in athena.ALL_PAIRS}
    rows = []
    for pair_name in PAIRS:
        pair = pair_map[pair_name]
        for engine, runner in (("Engine A", athena.backtest_pair), ("Engine B", athena.backtest_pair_naked)):
            result = runner(dict(pair), style="auto")
            trades = result.get("trades", [])
            for direction in ("LONG", "SHORT"):
                subset = [t for t in trades if str(t.get("direction", "")).upper() == direction]
                rows.append(_metrics(engine, pair_name, direction, subset))
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    lines = []
    display = fields[1:]
    for engine in ("Engine A", "Engine B"):
        lines += [f"## {engine} LONG vs SHORT by pair", "", "| " + " | ".join(display) + " |",
                  "|" + "|".join("---" for _ in display) + "|"]
        for row in rows:
            if row["engine"] == engine:
                lines.append("| " + " | ".join(str(row[k]) if row[k] is not None else "N/A" for k in display) + " |")
        lines.append("")
    out.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(out.with_suffix(".csv"))
    print(out.with_suffix(".md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
