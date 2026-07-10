"""Measure real broker costs for the ASE instrument universe (WO T0.4b).

TODO(run-on-host): this script must be run on the host with a live MT5 demo
terminal and network access to Bybit testnet. Until it has been run and
reports/broker_costs_measured.json reviewed, athena_ase/data/costs.py keeps
its conservative placeholder _V0_TABLE values (cm-2026.06.0) — do NOT invent
numbers.

Usage:
    py tools/measure_broker_costs.py [--minutes 120] [--out reports/broker_costs_measured.json]

For each ASE instrument it samples live spread over the sampling window
(default >= 2 hours), reads commission from account/config where available,
and writes a JSON report keyed by (family, subclass) matching _V0_TABLE.
After review, update _V0_TABLE from the report and bump the cost-model
version to cm-2026.07.0.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))

from athena_ase.instruments import DEFAULT_INSTRUMENTS  # noqa: E402

BYBIT_TESTNET = "https://api-testnet.bybit.com"


def _mid_bps(bid: float, ask: float) -> float | None:
    mid = (bid + ask) / 2.0
    if mid <= 0 or ask < bid:
        return None
    return (ask - bid) / mid * 1e4


def sample_mt5(symbols: list[str], minutes: int, interval_s: int = 30) -> dict[str, list[float]]:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError:
        print("MetaTrader5 package not available — skipping MT5 sampling")
        return {}
    if not mt5.initialize():
        print("MT5 initialize failed — skipping MT5 sampling")
        return {}
    acct = mt5.account_info()
    if acct is None or getattr(acct, "trade_mode", None) != 0:
        print("MT5 account is not demo (trade_mode != 0) — refusing to sample")
        mt5.shutdown()
        return {}
    samples: dict[str, list[float]] = {s: [] for s in symbols}
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        for sym in symbols:
            tick = mt5.symbol_info_tick(sym)
            if tick is None:
                continue
            bps = _mid_bps(float(tick.bid), float(tick.ask))
            if bps is not None:
                samples[sym].append(bps)
        time.sleep(interval_s)
    mt5.shutdown()
    return samples


def sample_bybit(symbols: list[str], minutes: int, interval_s: int = 30) -> dict[str, list[float]]:
    import urllib.request

    samples: dict[str, list[float]] = {s: [] for s in symbols}
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        for sym in symbols:
            url = f"{BYBIT_TESTNET}/v5/market/tickers?category=linear&symbol={sym}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = json.loads(resp.read())
                row = data["result"]["list"][0]
                bps = _mid_bps(float(row["bid1Price"]), float(row["ask1Price"]))
                if bps is not None:
                    samples[sym].append(bps)
            except Exception as exc:  # noqa: BLE001
                print(f"bybit sample failed {sym}: {exc}")
        time.sleep(interval_s)
    return samples


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120, help="sampling window (>=120 recommended)")
    ap.add_argument("--out", default="reports/broker_costs_measured.json")
    args = ap.parse_args()

    mt5_syms = [i.symbol for i in DEFAULT_INSTRUMENTS if i.family != "crypto"]
    bybit_syms = [i.symbol for i in DEFAULT_INSTRUMENTS if i.family == "crypto"]

    mt5_samples = sample_mt5(mt5_syms, args.minutes)
    bybit_samples = sample_bybit(bybit_syms, args.minutes)

    inst_by_symbol = {i.symbol: i for i in DEFAULT_INSTRUMENTS}
    per_class: dict[str, list[float]] = {}
    per_instrument: dict[str, dict] = {}
    for sym, vals in {**mt5_samples, **bybit_samples}.items():
        if not vals:
            continue
        inst = inst_by_symbol.get(sym)
        if inst is None:
            continue
        med = statistics.median(vals)
        per_instrument[sym] = {
            "family": inst.family,
            "subclass": inst.subclass,
            "median_spread_bps": round(med, 3),
            "p90_spread_bps": round(sorted(vals)[int(len(vals) * 0.9)], 3),
            "n_samples": len(vals),
        }
        per_class.setdefault(f"{inst.family}/{inst.subclass}", []).append(med)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "sampling_minutes": args.minutes,
        "per_instrument": per_instrument,
        "per_class_median_spread_bps": {
            k: round(statistics.median(v), 3) for k, v in per_class.items()
        },
        "note": (
            "Commission must be read from broker account terms and added manually. "
            "After review, update _V0_TABLE in athena_ase/data/costs.py and bump "
            "version to cm-2026.07.0."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(per_instrument)} instruments)")


if __name__ == "__main__":
    main()
