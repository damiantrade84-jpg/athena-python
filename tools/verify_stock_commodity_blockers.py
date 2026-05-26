#!/usr/bin/env python3
"""Verify stock/commodity scan blocker fixes (diagnostics only, no scoring changes)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_audit(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def verify_stock_pre_scoring(audit_path: Path) -> dict:
    from athena_app.services.data_freshness import pre_scoring_allows_intraday_calendar_gap

    audit = _load_audit(audit_path)
    results = []
    rows = audit.get("rows") or audit.get("pairs") or audit.get("results") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        display = row.get("display") or row.get("pair") or row.get("symbol")
        pair = {
            "type": row.get("asset_type") or row.get("type") or "stock",
            "source": row.get("source") or "mt5",
            "display": display,
            "symbol": row.get("symbol"),
        }
        tf = str(row.get("timeframe") or "").upper()
        diag = row.get("diagnostic") if isinstance(row.get("diagnostic"), dict) else {}
        if tf not in ("H4", "H1"):
            for tf_key in ("H4", "H1"):
                sub = (row.get("timeframes") or {}).get(tf_key) or row.get(tf_key) or {}
                if isinstance(sub, dict):
                    severity = sub.get("stalenessSeverity") or sub.get("stale_status")
                    allowed = pre_scoring_allows_intraday_calendar_gap(
                        pair,
                        tf_key,
                        {
                            "stalenessSeverity": severity,
                            "bucketLag": sub.get("bucketLag") or sub.get("bucket_lag"),
                        },
                    )
                    results.append(
                        {
                            "pair": display,
                            "tf": tf_key,
                            "severity": severity,
                            "pre_scoring_allowed": allowed,
                        }
                    )
            continue
        severity = diag.get("stalenessSeverity") or diag.get("stale_status")
        allowed = pre_scoring_allows_intraday_calendar_gap(
            pair,
            tf,
            {
                "stalenessSeverity": severity,
                "bucketLag": diag.get("bucketLag") or diag.get("bucket_lag"),
            },
        )
        results.append(
            {
                "pair": display,
                "tf": tf,
                "severity": severity,
                "pre_scoring_allowed": allowed,
            }
        )
    ok = all(r.get("pre_scoring_allowed") for r in results if r.get("severity") == "stale_multi_bucket")
    return {"stock_pre_scoring": results, "all_stale_multi_allowed": ok}


def verify_forensic_artifacts(base: Path) -> dict:
    found = list(base.glob("commodity_trend_forensic_*.json"))
    found.extend((base / "forensics").glob("commodity_trend_forensic_*.json"))
    summaries = []
    for path in found:
        data = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(
            {
                "file": path.name,
                "score_group": data.get("score_group"),
                "abort_reason": data.get("abort_reason"),
                "direction": data.get("direction"),
                "final_score": data.get("final_score"),
                "trend_timeframes": data.get("trend_timeframes"),
            }
        )
    return {"forensic_files": summaries, "count": len(summaries)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        default="logs/audit_stock_freshness.json",
        help="Output from non_crypto_candle_freshness_audit.py",
    )
    parser.add_argument(
        "--calibration",
        default="logs/calibration_diagnostics/calibration_events.jsonl",
        help="Calibration log to sample (optional)",
    )
    args = parser.parse_args()

    report = {
        "audit_path": args.audit,
        "stock": verify_stock_pre_scoring(Path(args.audit)),
        "commodity_forensics": verify_forensic_artifacts(
            Path("logs/calibration_diagnostics")
        ),
    }

    cal_path = Path(args.calibration)
    if cal_path.is_file():
        stock_engine_a = 0
        abort_skips = 0
        energy_indet = 0
        energy_total = 0
        with cal_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("engine") != "engine_a":
                    continue
                sg = row.get("score_group") or ""
                if sg in ("us_stock_single", "stock_other", "bond_tlt", "smallcap_em_etf") or row.get("asset_class") == "stock":
                    stock_engine_a += 1
                if row.get("abort_reason"):
                    abort_skips += 1
                if sg == "energy_oil":
                    energy_total += 1
                    if row.get("failure_reason") == "indeterminate_trend":
                        energy_indet += 1
        report["calibration_sample"] = {
            "stock_engine_a_rows": stock_engine_a,
            "engine_a_abort_skip_rows": abort_skips,
            "energy_oil_indeterminate_rate": (
                round(energy_indet / energy_total, 3) if energy_total else None
            ),
            "energy_oil_total": energy_total,
        }

    out = Path("logs/verify_stock_commodity_blockers.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nWrote {out}")
    return 0 if report["stock"].get("all_stale_multi_allowed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
