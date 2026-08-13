"""Realised cost drag for Engine A / Engine B backtest trades.

Companion to athena_research/ase/sleeve_edge.py, asking the same question of
the deterministic engines: how much of the gross edge survives costs, and
where does the cost actually land?

Read-only. Opens both trade stores read-only and touches no scoring, config,
or execution path.

Two sources, deliberately kept separate because they disagree
-------------------------------------------------------------
* **catalog** (`backtesting_v2/catalog.sqlite3`) records ``gross_r``,
  ``cost_r`` and ``net_r`` per trade, so the decomposition is exact. Narrow:
  Engine A only, 6 symbols, but with real walk-forward/holdout splits.
* **v3** (`audit.db.backtest_trades_v3`) is far broader (Engine A and B, 116
  symbols) but stores only ``resultR``. That value is **net**: costs are
  applied at engine_a_v3/backtest.py (``result_r -= cost_r``). The
  ``costSpec`` field is null on Engine A runs because the spec was not
  recorded there, NOT because costs were skipped.

The headline finding both sources agree on is that cost in R is governed by
stop width, mechanically: ``cost_r = (entry * total_bps / 1e4) / |entry - sl|``.
A stop half as wide pays twice the cost in R. They disagree on whether
filtering to wide stops is *sufficient* to reach profitability — see the
per-source output rather than pooling them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import numpy as np

IS_SPLITS = {"IS", "WF1_PURGED"}


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home())


def catalog_path() -> Path:
    return _local_appdata() / "Athena" / "backtesting_v2" / "catalog.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _stats(net: Iterable[float]) -> dict[str, Any]:
    r = np.asarray(list(net), dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 2:
        return {"n": n}
    mean = float(r.mean())
    std = float(r.std(ddof=1))
    return {
        "n": n,
        "net": mean,
        "total_r": float(r.sum()),
        "sqn100": mean / std * math.sqrt(min(n, 100)) if std > 0 else float("nan"),
    }


def load_catalog(path: Path, *, oos_only: bool = True) -> list[dict[str, Any]]:
    """Trades with an exact gross/cost/net decomposition."""
    rows: list[dict[str, Any]] = []
    with _connect(path) as conn:
        for symbol, payload in conn.execute("select symbol, payload_json from trades"):
            try:
                d = json.loads(payload)
                entry = float(d["entry_price"])
                stop = float(d["stop_price"])
            except (TypeError, ValueError, KeyError):
                continue
            if entry <= 0:
                continue
            width = abs(entry - stop) / abs(entry)
            if width <= 0:
                continue
            if oos_only and d.get("split") in IS_SPLITS:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "split": d.get("split"),
                    "width": width,
                    "gross": float(d["gross_r"]),
                    "cost": float(d.get("cost_r") or 0.0),
                    "net": float(d["net_r"]),
                }
            )
    return rows


def load_v3(path: Path, engine: str) -> list[dict[str, Any]]:
    """Broad trade set. resultR is net; no gross/cost decomposition is stored."""
    rows: list[dict[str, Any]] = []
    query = (
        "select t.trade_json from backtest_trades_v3 t "
        "join backtest_runs_v3 r on r.id = t.run_id where r.engine = ?"
    )
    with _connect(path) as conn:
        for (payload,) in conn.execute(query, (engine,)):
            if not payload:
                continue
            try:
                d = json.loads(payload)
                entry = float(d["entry"])
                stop = float(d["sl"])
                net = float(d["resultR"])
            except (TypeError, ValueError, KeyError):
                continue
            if entry <= 0:
                continue
            width = abs(entry - stop) / abs(entry)
            if width <= 0:
                continue
            rows.append({"width": width, "net": net, "oos": bool(d.get("oos"))})
    return rows


def _quintile_table(rows: list[dict[str, Any]], *, has_gross: bool) -> None:
    widths = np.array([r["width"] for r in rows], dtype=float)
    edges = np.quantile(widths, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    header = "   quintile  median SL      cost_R     gross_R" if has_gross else "   quintile  median SL"
    print(f"{header}       net_R   SQN100      N")
    for i in range(5):
        lo, hi = edges[i], edges[i + 1]
        cell = [r for r in rows if lo <= r["width"] <= hi]
        if len(cell) < 20:
            continue
        s = _stats(r["net"] for r in cell)
        median_w = float(np.median([r["width"] for r in cell])) * 100.0
        mid = ""
        if has_gross:
            mid = (
                f"   {float(np.mean([r['cost'] for r in cell])):.4f}"
                f"   {float(np.mean([r['gross'] for r in cell])):+.4f}"
            )
        print(
            f"   Q{i + 1}        {median_w:6.2f}%{mid}   {s['net']:+.4f}   {s['sqn100']:+5.2f}  {s['n']:6d}"
        )


def report(*, catalog: Path, audit: Path, thresholds: tuple[float, ...]) -> None:
    print("Realised cost drag — Engine A / Engine B\n")

    if catalog.exists():
        rows = load_catalog(catalog, oos_only=True)
        if rows:
            overall = _stats(r["net"] for r in rows)
            gross = float(np.mean([r["gross"] for r in rows]))
            cost = float(np.mean([r["cost"] for r in rows]))
            print("=" * 88)
            print(f"SOURCE: backtesting_v2 catalog — Engine A, OOS+HOLDOUT, exact costs")
            print("=" * 88)
            print(
                f"  overall  N={overall['n']}  gross={gross:+.4f}R  "
                f"cost={cost:.4f}R  net={overall['net']:+.4f}R  SQN100={overall['sqn100']:+.2f}"
            )
            print()
            _quintile_table(rows, has_gross=True)
            print("\n  effect of a minimum stop-width filter:")
            for thr in thresholds:
                kept = _stats(r["net"] for r in rows if r["width"] >= thr)
                if kept.get("n", 0) < 2:
                    continue
                print(
                    f"    SL >= {thr * 100:.2f}%   N={kept['n']:6d}  net={kept['net']:+.4f}R  "
                    f"SQN100={kept['sqn100']:+5.2f}  totalR={kept['total_r']:+8.1f}"
                )
            print()
    else:
        print(f"catalog not found: {catalog}\n")

    if not audit.exists():
        print(f"audit db not found: {audit}")
        return

    for engine in ("A", "B"):
        rows = load_v3(audit, engine)
        if len(rows) < 40:
            print(f"Engine {engine}: {len(rows)} usable v3 trades (insufficient)\n")
            continue
        overall = _stats(r["net"] for r in rows)
        print("=" * 88)
        print(f"SOURCE: audit.db backtest_trades_v3 — Engine {engine}, resultR is NET")
        print("=" * 88)
        print(
            f"  overall  N={overall['n']}  net={overall['net']:+.4f}R  "
            f"SQN100={overall['sqn100']:+.2f}  totalR={overall['total_r']:+.1f}"
        )
        print()
        _quintile_table(rows, has_gross=False)
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(catalog_path()))
    parser.add_argument("--audit", default="audit.db")
    parser.add_argument(
        "--thresholds",
        default="0.003,0.004,0.005",
        help="comma-separated minimum stop widths as a fraction of price",
    )
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(",") if x.strip())
    report(catalog=Path(args.catalog), audit=Path(args.audit), thresholds=thresholds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
