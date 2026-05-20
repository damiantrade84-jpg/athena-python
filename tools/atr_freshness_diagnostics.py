#!/usr/bin/env python
"""Read-only ATR freshness diagnostics for live Athena signals.

Queries the running Athena server for Engine A / B / C / D signals and reports
ATR provenance (timeframe, source, last-candle timestamp, age) along with the
resolved SL/TP/RR levels and execution rebase status. It does NOT call brokers,
place orders, or mutate runtime state.

The tool reads `atrDiagnostics` blocks emitted by analyze_pair (Engine A),
scanner Engine B overlay, engine_c consensus, and scalp_engine Engine D
signals (F2 audit additive observability fields).

Usage:
    python tools/atr_freshness_diagnostics.py --asset-types forex,crypto \
        --engines A,B,C,D --base http://127.0.0.1:5000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable


DEFAULT_BASE = "http://127.0.0.1:5000"
DEFAULT_TIMEOUT_S = 30.0


def _fetch_json(url: str, *, timeout_s: float, payload: dict | None = None) -> dict[str, Any]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _signal_rows_from_scan(payload: dict | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ("tradeSignals", "watchlist", "signals"):
        items = payload.get(key)
        if isinstance(items, list):
            out.extend(item for item in items if isinstance(item, dict))
    return out


def _consensus_rows(payload: dict | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for bucket in ("aligned", "a_only", "b_only", "conflict", "skipped"):
        rows = payload.get(bucket)
        if isinstance(rows, list):
            out.extend(row for row in rows if isinstance(row, dict))
    return out


def _scalp_rows(payload: dict | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    signals = payload.get("signals")
    if isinstance(signals, list):
        return [row for row in signals if isinstance(row, dict)]
    return []


def _extract_atr_block(row: dict[str, Any]) -> dict[str, Any]:
    diag = row.get("atrDiagnostics")
    if isinstance(diag, dict):
        return diag
    return {}


def _classify_stale(diag: dict[str, Any], thresholds_sec: dict[str, float]) -> tuple[bool, str | None]:
    """Return (stale, reason). Diagnostic-only — does not gate execution."""
    age = diag.get("atr_age_seconds")
    tf = str(diag.get("atr_tf") or "").upper()
    if age is None:
        return True, "atr_age_unknown"
    try:
        age_f = float(age)
    except (TypeError, ValueError):
        return True, "atr_age_invalid"
    limit = thresholds_sec.get(tf)
    if limit is None:
        return False, None
    if age_f > limit:
        return True, f"atr_age_{int(age_f)}s_exceeds_{tf}_limit_{int(limit)}s"
    return False, None


def _row_summary(
    engine: str,
    row: dict[str, Any],
    thresholds_sec: dict[str, float],
) -> dict[str, Any]:
    diag = _extract_atr_block(row)
    sl = row.get("sl")
    tp = row.get("tp") or row.get("tp1")
    rr = row.get("rr") or row.get("rr1")
    signal_price = row.get("price")
    stale, reason = _classify_stale(diag, thresholds_sec)
    return {
        "engine": engine,
        "symbol": row.get("display") or row.get("symbol") or row.get("pair"),
        "asset_type": row.get("type"),
        "atr_value": diag.get("atr_value") if diag else row.get("atr"),
        "atr_tf": diag.get("atr_tf"),
        "atr_source": diag.get("atr_source"),
        "atr_candle_last_ts": diag.get("atr_candle_last_ts"),
        "atr_age_seconds": diag.get("atr_age_seconds"),
        "stale": stale,
        "stale_reason": reason,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "signal_price": signal_price,
    }


def _engines_filter(text: str) -> set[str]:
    return {part.strip().upper() for part in text.split(",") if part.strip()}


def _assets_filter(text: str) -> set[str]:
    return {part.strip().lower() for part in text.split(",") if part.strip()}


def _matches_asset(asset_type: str | None, allowed: set[str]) -> bool:
    if not allowed:
        return True
    if not asset_type:
        return False
    return str(asset_type).lower() in allowed


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ATR freshness diagnostics.")
    parser.add_argument(
        "--base", default=DEFAULT_BASE, help="Athena server base URL (default: %(default)s)"
    )
    parser.add_argument(
        "--asset-types",
        default="forex,crypto,commodity,index,stock,etf",
        help="Comma-separated asset types to include.",
    )
    parser.add_argument(
        "--engines",
        default="A,B,C,D",
        help="Comma-separated engines to query (A,B,C,D).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="HTTP timeout per request (default: %(default)s).",
    )
    parser.add_argument(
        "--max-age",
        default="M1=180,M5=600,M15=1800,H1=7200,H4=18000,D1=172800",
        help="Per-TF stale threshold overrides (key=seconds).",
    )
    parser.add_argument(
        "--style", default="swing", help="Scan style hint (scalp|intraday|swing)."
    )
    parser.add_argument(
        "--asset-class",
        default="all",
        help="Scan asset_class filter (passed to /api/scan if supported).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable table.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    engines = _engines_filter(args.engines)
    assets = _assets_filter(args.asset_types)
    thresholds: dict[str, float] = {}
    for token in args.max_age.split(","):
        token = token.strip()
        if not token or "=" not in token:
            continue
        k, v = token.split("=", 1)
        try:
            thresholds[k.strip().upper()] = float(v.strip())
        except ValueError:
            continue

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    base = args.base.rstrip("/")

    if "A" in engines or "B" in engines:
        try:
            scan_url = (
                f"{base}/api/scan?style={args.style}&asset_class={args.asset_class}"
            )
            scan = _fetch_json(scan_url, timeout_s=args.timeout_s)
            for sig in _signal_rows_from_scan(scan):
                if not _matches_asset(sig.get("type"), assets):
                    continue
                engine_a_diag = sig.get("atrDiagnostics") or {}
                if "A" in engines and engine_a_diag.get("atr_source_engine") in (
                    None,
                    "engine_a",
                ):
                    rows.append(_row_summary("engine_a", sig, thresholds))
                if "B" in engines:
                    overlay = sig.get("engine_b") or sig.get("naked_data") or {}
                    b_diag = overlay.get("atrDiagnostics") if isinstance(overlay, dict) else None
                    if isinstance(b_diag, dict):
                        b_row = dict(sig)
                        b_row["atrDiagnostics"] = b_diag
                        b_row["sl"] = overlay.get("execution_sl") or overlay.get(
                            "recommended_stop_loss"
                        )
                        b_row["tp"] = overlay.get("execution_tp") or overlay.get(
                            "recommended_take_profit"
                        )
                        b_row["rr"] = overlay.get("rr")
                        rows.append(_row_summary("engine_b", b_row, thresholds))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            errors.append(f"scan_failed: {exc}")

    if "C" in engines:
        try:
            consensus_url = f"{base}/api/engine-c-scan?style={args.style}"
            consensus = _fetch_json(consensus_url, timeout_s=args.timeout_s, payload={})
            for cs in _consensus_rows(consensus):
                if not _matches_asset(cs.get("type"), assets):
                    continue
                rows.append(_row_summary("engine_c", cs, thresholds))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            errors.append(f"engine_c_scan_failed: {exc}")

    if "D" in engines:
        try:
            scalp = _fetch_json(f"{base}/api/scalp-scan", timeout_s=args.timeout_s, payload={})
            for srow in _scalp_rows(scalp):
                if not _matches_asset(srow.get("type") or srow.get("asset_type"), assets):
                    continue
                rows.append(_row_summary("engine_d", srow, thresholds))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            errors.append(f"scalp_scan_failed: {exc}")

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "engines": sorted(engines),
        "asset_types": sorted(assets) if assets else None,
        "thresholds_sec": thresholds,
        "total_signals_checked": len(rows),
        "stale_count_by_engine": dict(
            Counter(r["engine"] for r in rows if r.get("stale"))
        ),
        "stale_count_by_asset_type": dict(
            Counter(
                str(r.get("asset_type") or "unknown").lower()
                for r in rows
                if r.get("stale")
            )
        ),
        "stale_count_by_tf": dict(
            Counter(
                str(r.get("atr_tf") or "unknown").upper()
                for r in rows
                if r.get("stale")
            )
        ),
        "missing_atr": sum(
            1
            for r in rows
            if r.get("atr_value") in (None, 0, 0.0) or r.get("atr_tf") is None
        ),
        "top_ages_seconds": sorted(
            (
                {"symbol": r["symbol"], "engine": r["engine"], "tf": r.get("atr_tf"), "age_seconds": r.get("atr_age_seconds")}
                for r in rows
                if r.get("atr_age_seconds") is not None
            ),
            key=lambda x: x["age_seconds"] or 0,
            reverse=True,
        )[:10],
        "errors": errors,
    }

    if args.json:
        json.dump({"summary": summary, "rows": rows}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"ATR freshness diagnostics @ {summary['generated_at']}")
    print(f"  base: {summary['base']}")
    print(f"  engines: {summary['engines']}")
    print(f"  asset_types: {summary['asset_types']}")
    print(f"  thresholds_sec: {summary['thresholds_sec']}")
    print(f"  total_signals_checked: {summary['total_signals_checked']}")
    print(f"  missing_atr: {summary['missing_atr']}")
    print(f"  stale_count_by_engine: {summary['stale_count_by_engine']}")
    print(f"  stale_count_by_asset_type: {summary['stale_count_by_asset_type']}")
    print(f"  stale_count_by_tf: {summary['stale_count_by_tf']}")
    if errors:
        print(f"  errors: {errors}")

    if rows:
        print()
        header = (
            "engine | symbol | asset_type | atr_tf | atr_source | atr_value | "
            "age_sec | stale | sl | tp | rr"
        )
        print(header)
        print("-" * len(header))
        for r in rows:
            print(
                f"{r['engine']} | {r['symbol']} | {r.get('asset_type') or ''} | "
                f"{r.get('atr_tf') or ''} | {r.get('atr_source') or ''} | "
                f"{r.get('atr_value')} | {r.get('atr_age_seconds')} | "
                f"{'YES' if r.get('stale') else 'no'} | {r.get('sl')} | "
                f"{r.get('tp')} | {r.get('rr')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
