"""audit_factor_diagnostics_addon.py — Verify addon_unsupported behaviour.

Implements §7.4 of plans/engine-d-scoring-ai-review-audit-8577d0.md.

Audit claim (§2.3, §4.6):
  - Stocks/indices have addon "unsupported" (factor_scoring.py:539).
  - When addon is unsupported, the engine reweights momentum to ~0.80 of
    conviction (now optionally tempered by ADDON_UNSUPPORTED_SPLIT_TO_BASE).
  - The AI prompt only sees `factorScores.addon=0.0` and cannot tell whether
    the addon was unsupported, missing, or strongly opposed (audit §4.6).

This tool reads logs/threshold_audit/signal_funnel.jsonl and reports:
  - For each asset_type, the share of rows where factor_diagnostics shows
    addon_unsupported=True (proxy via the per-row factor_diagnostics block).
  - Distribution of `directionalScore` / `nondirectionalScore` per asset class.
  - Cross-check: which asset classes show non-zero addon_value vs which show
    addon_value == 0.0 with `addon_unsupported=true`.

NOTE: signal_funnel.jsonl emits a *summary* of factor_diagnostics
(directionalScore, nondirectionalScore, trendCoherence) rather than the full
per-factor breakdown.  Where the full block is missing, this tool falls back
to inspecting any `factor_diagnostics.addon_unsupported` flag if present.

Read-only.

Usage:
  python tools/audit_factor_diagnostics_addon.py
  python tools/audit_factor_diagnostics_addon.py --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FUNNEL = ROOT / "logs" / "threshold_audit" / "signal_funnel.jsonl"


def _iter_rows(path: Path, since: str | None) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if since and str(row.get("timestamp", "")) < since:
                continue
            out.append(row)
    return out


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
    }


def _asset_class(symbol: str) -> str:
    """Best-effort asset-class inference from symbol notation in the log."""
    s = (symbol or "").upper()
    if "/" not in s:
        return "stock_or_index"
    base = s.split("/")[0]
    quote = s.split("/")[-1]
    if quote in ("USDT", "USD") and base in (
        "BTC", "ETH", "SOL", "BNB", "ADA", "XRP", "AVAX", "DOT", "LINK",
        "MATIC", "POL", "DOGE", "LTC", "BCH", "ATOM", "ETC", "FIL", "AAVE",
        "ALGO", "APT", "ARB", "HBAR", "ICP", "INJ", "NEAR", "OP", "RENDER",
        "SEI", "SUI", "TRX", "UNI", "XLM",
    ):
        return "crypto"
    # Everything else with "/" treat as forex pair (incl. metals like XAU/USD)
    return "forex_or_metal"


def _summarize(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"total": 0}

    by_asset: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sym = str(r.get("symbol", ""))
        by_asset[_asset_class(sym)].append(r)

    per_asset: dict[str, Any] = {}
    overall_unsupported_seen = 0
    overall_unsupported_inferred = 0

    for asset, sub in by_asset.items():
        n = len(sub)
        unsupported_explicit = 0
        addon_zero = 0
        directional: list[float] = []
        nondirectional: list[float] = []
        coherence_ratios: list[float] = []
        scan_results: Counter = Counter()

        for r in sub:
            fd = r.get("factor_diagnostics") or {}
            if fd.get("addon_unsupported") is True:
                unsupported_explicit += 1
            ds = fd.get("directionalScore")
            nds = fd.get("nondirectionalScore")
            if isinstance(ds, (int, float)):
                directional.append(float(ds))
            if isinstance(nds, (int, float)):
                nondirectional.append(float(nds))
            tc = fd.get("trendCoherence") or {}
            cr = tc.get("coherence_ratio")
            if isinstance(cr, (int, float)):
                coherence_ratios.append(float(cr))
            # Inferred: if addon score literally absent / zero we treat as zero
            if not fd or fd.get("addon_unsupported") is True:
                addon_zero += 1
            scan_results[str(r.get("final_scan_result", "unknown"))] += 1

        per_asset[asset] = {
            "total_rows": n,
            "explicit_addon_unsupported": unsupported_explicit,
            "explicit_addon_unsupported_pct": round(100.0 * unsupported_explicit / n, 2) if n else 0.0,
            "addon_zero_or_unsupported": addon_zero,
            "directional_score":     _stats(directional),
            "nondirectional_score":  _stats(nondirectional),
            "coherence_ratio":       _stats(coherence_ratios),
            "final_scan_result_distribution": dict(scan_results.most_common(8)),
        }
        overall_unsupported_seen += unsupported_explicit
        overall_unsupported_inferred += addon_zero

    return {
        "total": len(rows),
        "asset_classes_seen": list(by_asset.keys()),
        "per_asset_class": per_asset,
        "overall_explicit_unsupported": overall_unsupported_seen,
        "overall_inferred_unsupported_or_zero": overall_unsupported_inferred,
    }


def _print_human(summary: dict[str, Any]) -> None:
    if summary.get("total", 0) == 0:
        print("[audit_factor_diagnostics_addon] no rows found")
        return

    print(f"\n=== Factor Diagnostics — Addon Status by Asset Class ({summary['total']} rows) ===")
    print(f"  asset_classes seen        : {summary['asset_classes_seen']}")
    print(f"  rows w/ addon_unsupported=true (explicit): {summary['overall_explicit_unsupported']}")
    print(f"  rows w/ addon_unsupported=true OR no fd block: {summary['overall_inferred_unsupported_or_zero']}")

    for asset, s in summary["per_asset_class"].items():
        print(f"\n--- [{asset}]  n={s['total_rows']} ---")
        print(f"  addon_unsupported=true (explicit) : {s['explicit_addon_unsupported']:5d} ({s['explicit_addon_unsupported_pct']:.2f}%)")
        print(f"  addon zero or no factor_diagnostics block: {s['addon_zero_or_unsupported']:5d}")
        print(f"  directionalScore stats     : {s['directional_score']}")
        print(f"  nondirectionalScore stats  : {s['nondirectional_score']}")
        print(f"  trendCoherence ratio stats : {s['coherence_ratio']}")
        print(f"  final_scan_result          : {s['final_scan_result_distribution']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=SIGNAL_FUNNEL)
    parser.add_argument("--since", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = _iter_rows(args.path, args.since)
    summary = _summarize(rows)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
