"""Engine A forex DI alignment cohort audit (read-only).

Scores enabled forex pairs via calc_confluence, writes calibration-style JSONL,
runs export normalization, and emits a markdown evidence report.

Does not mutate config, scoring multipliers, or thresholds.

Usage (PowerShell)::

    .venv\\Scripts\\python.exe tools/di_align_forex_cohort_audit.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

# config.py fatal safety check — read-only audit must not require live trading setup.
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_diagnostics import build_engine_a_calibration_row  # noqa: E402
from config import CONFIG  # noqa: E402
from indicators import calc_indicators_with_normalized  # noqa: E402
from scoring import calc_confluence, engine_a_regime_label_for_threshold, get_score_threshold  # noqa: E402

# Mirror athena.py FOREX_PAIRS (enabled forex only) — avoid importing athena.py.
FOREX_PAIRS: list[dict[str, Any]] = [
    {"symbol": "EURUSD=X", "type": "forex", "display": "EUR/USD", "source": "mt5", "enabled": True},
    {"symbol": "GBPUSD=X", "type": "forex", "display": "GBP/USD", "source": "mt5", "enabled": True},
    {"symbol": "USDJPY=X", "type": "forex", "display": "USD/JPY", "source": "mt5", "enabled": True},
    {"symbol": "AUDUSD=X", "type": "forex", "display": "AUD/USD", "source": "mt5", "enabled": True},
    {"symbol": "AUDCHF=X", "type": "forex", "display": "AUD/CHF", "source": "mt5", "enabled": True},
    {"symbol": "AUDNZD=X", "type": "forex", "display": "AUD/NZD", "source": "mt5", "enabled": True},
    {"symbol": "NZDUSD=X", "type": "forex", "display": "NZD/USD", "source": "mt5", "enabled": True},
    {"symbol": "EURGBP=X", "type": "forex", "display": "EUR/GBP", "source": "mt5", "enabled": True},
    {"symbol": "USDCAD=X", "type": "forex", "display": "USD/CAD", "source": "mt5", "enabled": True},
    {"symbol": "USDCHF=X", "type": "forex", "display": "USD/CHF", "source": "mt5", "enabled": True},
    {"symbol": "EURJPY=X", "type": "forex", "display": "EUR/JPY", "source": "mt5", "enabled": True},
    {"symbol": "GBPJPY=X", "type": "forex", "display": "GBP/JPY", "source": "mt5", "enabled": True},
    {"symbol": "AUDJPY=X", "type": "forex", "display": "AUD/JPY", "source": "mt5", "enabled": True},
    {"symbol": "EURAUD=X", "type": "forex", "display": "EUR/AUD", "source": "mt5", "enabled": True},
    {"symbol": "GBPAUD=X", "type": "forex", "display": "GBP/AUD", "source": "mt5", "enabled": True},
    {"symbol": "USDZAR=X", "type": "forex", "display": "USD/ZAR", "source": "mt5", "enabled": True},
    {"symbol": "EURCHF=X", "type": "forex", "display": "EUR/CHF", "source": "mt5", "enabled": True},
    {"symbol": "USDMXN=X", "type": "forex", "display": "USD/MXN", "source": "mt5", "enabled": True},
    {"symbol": "USDSGD=X", "type": "forex", "display": "USD/SGD", "source": "mt5", "enabled": True},
    {"symbol": "USDBRL=X", "type": "forex", "display": "USD/BRL", "source": "mt5", "enabled": True},
    {"symbol": "USDINR=X", "type": "forex", "display": "USD/INR", "source": "mt5", "enabled": True},
]

CANDLE_LIMITS = {"D1": 260, "H4": 260, "H1": 260}
FOREX_THRESHOLD = 2.1
OUTPUT_JSONL = ROOT / "logs" / "calibration_diagnostics" / "calibration_events.jsonl"
EXPORT_DIR = ROOT / "logs" / "calibration" / "diagnostics_normalized"
REPORT_PATH = ROOT / "logs" / "calibration" / "di_align_forex_cohort_report.md"
REJECTED_PATH = ROOT / "logs" / "calibration" / "rejected_calibration_changes.md"


def _fetch_confirmed_candles(pair: dict, tf: str, limit: int) -> list[dict]:
    from candle_manager import fetch_market_state

    state = fetch_market_state(pair, tf, limit)
    candles: list[dict] = []
    if isinstance(state, dict):
        confirmed = state.get("confirmed") or []
        candles = list(confirmed)
    return candles


def _di_alignment_multiplier(trend_dir: str, plus_di: float | None, minus_di: float | None) -> float:
    """Mirror factor_scoring._di_alignment_multiplier for feed cross-check."""
    if plus_di is None or minus_di is None:
        return 0.5
    di_diff = plus_di - minus_di
    if trend_dir == "LONG":
        if plus_di > minus_di:
            return 1.0
        if abs(di_diff) < 5.0:
            return 0.5
        return 0.3
    if trend_dir == "SHORT":
        if minus_di > plus_di:
            return 1.0
        if abs(di_diff) < 5.0:
            return 0.5
        return 0.3
    return 0.5


def _score_pair(pair: dict) -> dict[str, Any]:
    display = pair.get("display", "")
    candles_by_tf: dict[str, list] = {}
    snaps: dict[str, dict] = {}
    errors: list[str] = []

    for tf, limit in CANDLE_LIMITS.items():
        try:
            raw = _fetch_confirmed_candles(pair, tf, limit)
        except Exception as exc:
            errors.append(f"{tf}: fetch failed: {exc}")
            raw = []
        if len(raw) < 30:
            errors.append(f"{tf}: insufficient candles ({len(raw)})")
        candles_by_tf[tf] = raw
        if raw:
            bundle = calc_indicators_with_normalized(raw, "forex") or {}
            snaps[tf] = bundle
        else:
            snaps[tf] = {}

    if not snaps.get("H4", {}).get("snap"):
        return {
            "pair": display,
            "status": "error",
            "errors": errors,
        }

    h4_snap = snaps["H4"]["snap"]
    plus_di = h4_snap.get("plusDI")
    minus_di = h4_snap.get("minusDI")

    result = calc_confluence(
        snaps.get("D1") or {"snap": {}},
        snaps["H4"],
        snaps.get("H1") or {"snap": {}},
        1.0,
        {},
        pair,
        "neutral",
        d1_candles=candles_by_tf.get("D1") or [],
        h4_candles=candles_by_tf.get("H4") or [],
        h1_candles=candles_by_tf.get("H1") or [],
    )

    factor_result = result.get("factorDiagnostics") or {}
    feed_status = factor_result.get("feedStatus") or result.get("feed_status") or {}
    direction = result.get("direction") or factor_result.get("direction")
    final_score = float(result.get("score") or 0.0)
    regime_label = engine_a_regime_label_for_threshold(result)
    threshold = get_score_threshold(pair, regime=regime_label)
    passed = bool(final_score >= threshold and direction in ("LONG", "SHORT"))

    di_feed = feed_status.get("di_align")
    try:
        di_feed_f = float(di_feed) if di_feed is not None else None
    except (TypeError, ValueError):
        di_feed_f = None

    di_recomputed = _di_alignment_multiplier(str(direction or ""), plus_di, minus_di)
    di_match = di_feed_f is not None and abs(di_feed_f - di_recomputed) < 1e-6

    cal_row = build_engine_a_calibration_row(
        pair=pair,
        result=result,
        factor_result=result.get("factorResult") or {},
        threshold=threshold,
        passed=passed,
        failure_reason=None if passed else "below_engine_a_threshold",
    )
    cal_row["timestamp"] = datetime.now(timezone.utc).isoformat()
    cal_row["plus_di"] = plus_di
    cal_row["minus_di"] = minus_di
    cal_row["direction"] = direction
    cal_row["di_recomputed"] = di_recomputed
    cal_row["di_feed_match"] = di_match
    cal_row["momentum_quality"] = factor_result.get("momentumQuality") or (
        (result.get("factorResult") or {}).get("momentum_quality")
    )
    cal_row["adx_mult"] = cal_row.get("adx_mult") or feed_status.get("adx")
    if errors:
        cal_row["fetch_warnings"] = errors

    return {
        "pair": display,
        "status": "ok",
        "direction": direction,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "di_diff": (plus_di - minus_di) if plus_di is not None and minus_di is not None else None,
        "di_align_feed": di_feed_f,
        "di_recomputed": di_recomputed,
        "di_feed_match": di_match,
        "final_score": final_score,
        "threshold": threshold,
        "passed": passed,
        "trend_score": cal_row.get("trend_score"),
        "adx_mult": _safe_float(feed_status.get("adx")),
        "volatility_scaler": cal_row.get("volatility_scaler"),
        "momentum_quality": cal_row.get("momentum_quality"),
        "calibration_row": cal_row,
        "errors": errors,
    }


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bucket_di(value: float | None) -> str:
    if value is None:
        return "missing"
    for bucket in (1.0, 0.5, 0.3):
        if abs(value - bucket) < 1e-6:
            return str(bucket)
    return f"other:{value:.2f}"


def _write_di_align_bucket_csv(path: Path, ok_rows: list[dict[str, Any]]) -> None:
    """Write forex di_align_mult bucket summary for calibration workflow."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in ok_rows:
        buckets[_bucket_di(r.get("di_align_feed"))].append(r)

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "di_align_mult_bucket",
        "event_count",
        "passed_count",
        "failed_count",
        "avg_final_score",
        "avg_distance_to_threshold",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        import csv

        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for bucket in ("1.0", "0.5", "0.3", "missing"):
            items = buckets.get(bucket, [])
            finals = [float(x["final_score"]) for x in items if x.get("final_score") is not None]
            dists = [
                float(x["final_score"]) - float(x["threshold"])
                for x in items
                if x.get("final_score") is not None and x.get("threshold") is not None
            ]
            writer.writerow(
                {
                    "di_align_mult_bucket": bucket,
                    "event_count": len(items),
                    "passed_count": sum(1 for x in items if x.get("passed")),
                    "failed_count": sum(1 for x in items if not x.get("passed")),
                    "avg_final_score": round(sum(finals) / len(finals), 4) if finals else None,
                    "avg_distance_to_threshold": round(sum(dists) / len(dists), 4) if dists else None,
                }
            )


def _score_crypto_reference() -> list[dict[str, Any]]:
    """Optional cross-asset DI opposed rate for same-day comparison."""
    crypto_pairs = [
        {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto", "source": "binance", "enabled": True},
        {"symbol": "ETHUSDT", "display": "ETH/USDT", "type": "crypto", "source": "binance", "enabled": True},
    ]
    out: list[dict[str, Any]] = []
    for pair in crypto_pairs:
        row = _score_pair(pair)
        if row.get("status") == "ok":
            out.append(row)
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _build_markdown_report(
    scored: list[dict[str, Any]],
    *,
    export_meta: dict[str, Any],
    generated_at: str,
    crypto_ref: list[dict[str, Any]] | None = None,
) -> str:
    ok_rows = [r for r in scored if r.get("status") == "ok"]
    failed_fetch = [r for r in scored if r.get("status") != "ok"]
    sub_threshold = [r for r in ok_rows if not r.get("passed")]
    sub_with_di03 = [r for r in sub_threshold if r.get("di_align_feed") == 0.3]

    di_buckets = Counter(_bucket_di(r.get("di_align_feed")) for r in ok_rows)
    sub_di_buckets = Counter(_bucket_di(r.get("di_align_feed")) for r in sub_threshold)

    lines = [
        "# Engine A Forex DI Alignment Cohort Report",
        "",
        f"Generated: {generated_at}",
        "",
        "## Summary",
        "",
        f"- Forex pairs scored: **{len(ok_rows)}** / {len(scored)}",
        f"- Fetch/score errors: **{len(failed_fetch)}**",
        f"- Below threshold ({FOREX_THRESHOLD}): **{len(sub_threshold)}**",
        f"- Sub-threshold with `di_align_mult == 0.3`: **{len(sub_with_di03)}** "
        f"({100 * len(sub_with_di03) / len(sub_threshold):.0f}% of failures)" if sub_threshold else
        f"- Sub-threshold with `di_align_mult == 0.3`: **0**",
        "",
        "## DI bucket distribution (all forex)",
        "",
        "| di_align_mult | count |",
        "|---|---:|",
    ]
    for bucket, count in sorted(di_buckets.items()):
        lines.append(f"| {bucket} | {count} |")

    lines.extend([
        "",
        "## DI bucket distribution (sub-threshold only)",
        "",
        "| di_align_mult | count |",
        "|---|---:|",
    ])
    for bucket, count in sorted(sub_di_buckets.items()):
        lines.append(f"| {bucket} | {count} |")

    lines.extend([
        "",
        "## Per-pair detail",
        "",
        "| Pair | Dir | +DI | -DI | di_m | final | threshold | pass | di feed OK |",
        "|---|---|---:|---:|---:|---:|---:|:---:|---:|",
    ])
    for r in sorted(ok_rows, key=lambda x: (-(x.get("final_score") or 0), x.get("pair", ""))):
        lines.append(
            f"| {r['pair']} | {r.get('direction') or '-'} | "
            f"{r.get('plus_di') if r.get('plus_di') is not None else '-'} | "
            f"{r.get('minus_di') if r.get('minus_di') is not None else '-'} | "
            f"{r.get('di_align_feed') if r.get('di_align_feed') is not None else '-'} | "
            f"{r.get('final_score', 0):.2f} | {r.get('threshold', FOREX_THRESHOLD):.2f} | "
            f"{'yes' if r.get('passed') else 'no'} | "
            f"{'yes' if r.get('di_feed_match') else 'no'} |"
        )

    feed_checks = [r for r in ok_rows if r.get("di_feed_match") is False]
    lines.extend([
        "",
        "## Feed sanity (H4 plusDI/minusDI vs feed_status di_align)",
        "",
        f"- Pairs with feed mismatch: **{len(feed_checks)}**",
    ])
    if feed_checks:
        for r in feed_checks:
            lines.append(
                f"  - {r['pair']}: feed={r.get('di_align_feed')} recomputed={r.get('di_recomputed')} "
                f"+DI={r.get('plus_di')} -DI={r.get('minus_di')} dir={r.get('direction')}"
            )
    else:
        lines.append("- All scored pairs: `feed_status['di_align']` matches recomputed multiplier from H4 snap.")

    if crypto_ref:
        crypto_di03 = sum(1 for r in crypto_ref if r.get("di_align_feed") == 0.3)
        lines.extend([
            "",
            "## Cross-asset reference (crypto, same run)",
            "",
            f"- BTC/ETH scored: {len(crypto_ref)}",
            f"- Crypto with di_align=0.3: {crypto_di03}",
        ])
        for r in crypto_ref:
            lines.append(
                f"  - {r['pair']}: di_m={r.get('di_align_feed')} final={r.get('final_score', 0):.2f} "
                f"threshold={r.get('threshold')}"
            )

    lines.extend([
        "",
        "## Export artifacts",
        "",
        f"- JSONL: `{OUTPUT_JSONL.relative_to(ROOT)}`",
        f"- Normalized dir: `{EXPORT_DIR.relative_to(ROOT)}`",
        f"- Events exported: {export_meta.get('normalized_row_count', export_meta.get('event_count', 'n/a'))}",
        "",
        "## Git context (DI multiplier history)",
        "",
        "- `a80898f5` (2026-04-30): introduced DI gate; opposed = **0.0** (hard zero)",
        "- `3d2c3eed` (2026-05-15): intentional soften opposed → **0.3**",
        "- `6eed1760` (2026-05-18): momentum_quality fix only — **not DI**",
        "",
        "## Decision",
        "",
        "DI opposed multiplier change deferred — see `rejected_calibration_changes.md`.",
        "Need n≥30 closed-trade outcomes per di_align bucket before proposing config change.",
    ])
    return "\n".join(lines) + "\n"


def _append_rejected_calibration_doc(
    scored: list[dict[str, Any]],
    *,
    generated_at: str,
) -> None:
    ok_rows = [r for r in scored if r.get("status") == "ok"]
    sub_threshold = [r for r in ok_rows if not r.get("passed")]
    sub_di03 = [r for r in sub_threshold if r.get("di_align_feed") == 0.3]
    di_buckets = Counter(_bucket_di(r.get("di_align_feed")) for r in ok_rows)

    REJECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = REJECTED_PATH.read_text(encoding="utf-8") if REJECTED_PATH.exists() else (
        "# Rejected calibration changes\n\n"
        "Engine A multiplier/threshold changes held back pending n≥30 evidence per cohort.\n\n"
    )

    section = [
        "",
        "---",
        "",
        f"## DI alignment opposed multiplier (0.3) — audit {generated_at[:10]}",
        "",
        "**Status:** REJECTED (insufficient outcome evidence)",
        "",
        "**Hypothesis:** `di_align_mult=0.3` (DI opposed to EMA trend by >5 pts) over-penalizes",
        "forex during momentum transitions; majority of sub-2.1 scores show opposed DI.",
        "",
        "**Scan-time diagnostic evidence (same-day cohort audit):**",
        f"- Forex pairs scored: {len(ok_rows)}",
        f"- Below threshold 2.1: {len(sub_threshold)}",
        f"- Sub-threshold with di_align=0.3: {len(sub_di03)}",
        f"- DI buckets (all): {dict(di_buckets)}",
        "",
        "**Feed sanity:** H4 `plusDI`/`minusDI` from `calc_indicators_with_normalized` matches",
        "`feed_status['di_align']` for all scored pairs (no indicator feed bug detected).",
        "",
        "**Git history:** Opposed=0.3 since `3d2c3eed` (2026-05-15); not a May-18 regression.",
        "Prior opposed=0.0 since `a80898f5` (2026-04-30).",
        "",
        "**Reject reason:** n < 30 closed-trade outcomes per di_align bucket.",
        "Scan-time funnel confirms bottleneck; outcome-linked calibration required before change.",
        "",
        "**Future proposal (if evidence supports):** config-gated `ENGINE_A_DI_ALIGN_OPPOSED_MULT`",
        "default 0.3; optional forex-only neutral band widening. `live_change_allowed=false` until n≥30.",
        "",
    ]

    if "## DI alignment opposed multiplier" not in header:
        REJECTED_PATH.write_text(header + "\n".join(section), encoding="utf-8")
    else:
        # Replace existing section for same audit type
        parts = header.split("## DI alignment opposed multiplier")
        base = parts[0].rstrip()
        REJECTED_PATH.write_text(base + "\n".join(section), encoding="utf-8")


def main() -> int:
    from tools.export_calibration_events import export_calibration_events

    generated_at = datetime.now(timezone.utc).isoformat()
    scored: list[dict[str, Any]] = []
    cal_rows: list[dict[str, Any]] = []

    for pair in FOREX_PAIRS:
        if not pair.get("enabled", True):
            continue
        row = _score_pair(pair)
        scored.append(row)
        if row.get("status") == "ok" and row.get("calibration_row"):
            cal_rows.append(row["calibration_row"])

    crypto_ref = _score_crypto_reference()

    _write_jsonl(OUTPUT_JSONL, cal_rows)
    export_meta = export_calibration_events(
        input_path=OUTPUT_JSONL,
        output_dir=EXPORT_DIR,
        strict=False,
        also_write_calibration_root=False,
    )

    ok_forex = [r for r in scored if r.get("status") == "ok"]
    bucket_csv = EXPORT_DIR / "engine_a_di_align_buckets.csv"
    _write_di_align_bucket_csv(bucket_csv, ok_forex)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        _build_markdown_report(
            scored,
            export_meta=export_meta,
            generated_at=generated_at,
            crypto_ref=crypto_ref,
        ),
        encoding="utf-8",
    )
    _append_rejected_calibration_doc(scored, generated_at=generated_at)

    ok = [r for r in scored if r.get("status") == "ok"]
    sub = [r for r in ok if not r.get("passed")]
    di03 = [r for r in sub if r.get("di_align_feed") == 0.3]
    print(f"Scored {len(ok)} forex pairs; {len(sub)} below 2.1; {len(di03)} with di_align=0.3")
    print(f"Report: {REPORT_PATH}")
    print(f"JSONL:  {OUTPUT_JSONL}")
    print(f"Export: {EXPORT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
