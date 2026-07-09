"""Offline replayer for the Engine A v3 shadow journal.

Manual/on-demand tool: reads journal entries written by
engine_a_v3.shadow_journal.record_shadow_entry (opt-in via
ENGINE_A_V3_SHADOW_JOURNAL_ENABLED), re-fetches candles for the same symbol
up to the journaled confirmed-cutoff from the live candle cache, re-scores
with evaluate_engine_a_v3, and reports where live and replay disagree.

Provenance-sha mismatches mean the candle history has since changed (cache
eviction, provider backfill/correction) — those rows are reported but cannot
be used as reconciliation evidence and are excluded from the drift-rate
summary.

Usage:
    python scripts/diagnostics/engine_a_v3_shadow_replay.py <journal.jsonl> [--csv out.csv]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _confirmed_cutoff_slice(candles: list[dict], cutoff_iso: str) -> list[dict]:
    return [row for row in candles if str(row.get("time") or row.get("datetime")) <= cutoff_iso]


def replay_entry(entry: dict) -> dict:
    from engine_a_v3.evaluator import evaluate_engine_a_v3
    from engine_a_v3.shadow_journal import compare_entry_to_rescore

    display = entry.get("display") or entry.get("symbol")
    pair = {"display": display, "symbol": entry.get("symbol"), "type": entry.get("type")}
    cutoff = str(entry.get("lastConfirmedCandleTs") or "")

    try:
        from athena_research.data_loader import load_ohlcv
    except Exception as exc:
        return {"symbol": entry.get("symbol"), "error": f"data_loader_unavailable:{exc}"}

    candles: dict[str, list[dict]] = {}
    for tf in ("D1", "H4", "H1"):
        frame, _ = load_ohlcv(display, tf, limit=2000, allow_yfinance=False)
        if frame is None:
            return {"symbol": entry.get("symbol"), "error": f"no_data:{tf}"}
        rows = [
            {
                "time": ts.isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "vol": float(row.get("volume", 0.0)),
            }
            for ts, row in frame.iterrows()
        ]
        candles[tf] = _confirmed_cutoff_slice(rows, cutoff)

    signal = evaluate_engine_a_v3(
        pair, candles, horizon=entry.get("horizon") or "swing"
    ).to_dict()
    signal["_candles"] = candles
    return compare_entry_to_rescore(entry, signal)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", help="Path to a shadow journal .jsonl file")
    parser.add_argument("--csv", help="Optional CSV output path")
    args = parser.parse_args()

    from engine_a_v3.shadow_journal import read_journal

    entries = read_journal(args.journal)
    if not entries:
        print("no entries in journal")
        return 0

    results = [replay_entry(entry) for entry in entries]
    comparable = [r for r in results if "error" not in r and r.get("provenanceMatch")]
    drifted = [r for r in comparable if not r["matched"]]

    print(f"entries={len(results)} comparable={len(comparable)} drifted={len(drifted)}")
    if comparable:
        print(f"drift_rate={len(drifted) / len(comparable):.1%}")
    for r in drifted:
        print(f"  DRIFT {r['symbol']} {r['lastConfirmedCandleTs']}: {r['drift']}")
    stale = [r for r in results if "error" not in r and not r.get("provenanceMatch")]
    if stale:
        print(f"provenance_mismatch (excluded from drift rate) = {len(stale)}")
    errored = [r for r in results if "error" in r]
    if errored:
        print(f"errors = {len(errored)}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "symbol", "horizon", "lastConfirmedCandleTs",
                    "provenanceMatch", "matched", "drift", "error",
                ],
            )
            writer.writeheader()
            for r in results:
                writer.writerow({k: r.get(k) for k in writer.fieldnames})
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
