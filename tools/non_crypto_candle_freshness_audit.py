#!/usr/bin/env python
"""Non-crypto candle freshness forensic audit (read-only).

Usage:
    python tools/non_crypto_candle_freshness_audit.py --asset-types forex,commodity,index,stock,etf --timeframes D1,H4,H1 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_athena_module():
    import importlib.util

    if not hasattr(_load_athena_module, "_cached"):
        spec = importlib.util.spec_from_file_location("athena_main", ROOT / "athena.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _load_athena_module._cached = mod  # type: ignore[attr-defined]
    return _load_athena_module._cached  # type: ignore[attr-defined]


def _load_all_pairs() -> tuple[list[dict], list[dict]]:
    mod = _load_athena_module()
    return list(mod.ALL_PAIRS), list(mod.ACTIVE_PAIRS)


import config
from athena_app.services.candle_service import engine_a_scoring_candles_from_state
from athena_app.services.data_freshness import build_live_feed_diagnostic
from athena_app.services.engine_b_market_state import (
    engine_b_confirmed_candles_from_raw,
)
from athena_app.services.market_state import (
    candle_freshness_diagnostic,
    candle_timestamp_epoch,
    get_tf_market_state,
    market_state_offset_hours,
)
from candles_cache import get_candle_fetch_meta
from config import scan_candle_limits
from engine_a_v3.profile import baseline_profile
from engine_a_v3.routing import route_specialist
from feature_normalizer import get_normalization_lookback


_FAMILY_ASSET = {
    "forex": "forex",
    "crypto": "crypto",
    "commodity": "commodity",
    "index": "index",
    "equity_etf": "stock",
}


def _required_scoring_bars(pair: dict, tf: str) -> tuple[int, dict]:
    """Derive the controlling Engine A/B scan depth for current indicators."""
    route = route_specialist(pair)
    profile = baseline_profile(route.score_group, "intraday")
    periods = dict(profile.indicator_periods)
    normalized_asset = _FAMILY_ASSET.get(route.family, "other")
    normalization = int(get_normalization_lookback(normalized_asset))
    ema_long = int(periods["ema_long"])
    atr_period = int(periods["atr"])
    adx_period = int(periods["adx"])
    indicator_min = max(
        ema_long,
        normalization + atr_period,
        normalization + (2 * adx_period) + 1,
    )
    core_min = int(config.CONFIG.get(f"ENGINE_A_MIN_{tf}_BARS", 0) or 0)
    required = max(indicator_min, core_min)
    return required, {
        "score_group": route.score_group,
        "normalization_lookback": normalization,
        "ema_long": ema_long,
        "atr_period": atr_period,
        "adx_period": adx_period,
        "engine_a_core_min": core_min,
    }


def _filter_pairs(
    pairs: list[dict],
    asset_types: set[str] | None,
    symbols: set[str] | None,
    sources: set[str] | None,
) -> list[dict]:
    out = []
    for pair in pairs:
        ptype = str(pair.get("type") or "").lower()
        if ptype == "crypto":
            continue
        if asset_types and ptype not in asset_types:
            continue
        if sources and str(pair.get("source") or "").lower() not in sources:
            continue
        if not pair.get("enabled", True):
            continue
        if symbols:
            keys = {
                str(pair.get("display", "")).replace("/", "").upper(),
                str(pair.get("symbol", "")).replace("/", "").replace("=X", "").upper(),
            }
            if not (symbols & keys):
                continue
        out.append(pair)
    return out


def _normalize_symbols(raw: str) -> set[str]:
    return {
        s.strip().replace("/", "").replace("=X", "").upper()
        for s in raw.split(",")
        if s.strip()
    }


def _last_ts(candles: list[dict]) -> int | None:
    if not candles:
        return None
    epoch = candle_timestamp_epoch(candles[-1])
    return int(epoch) if epoch else None


def _engine_c_legacy_chop(raw: list[dict]) -> list[dict]:
    if raw and len(raw) > 1:
        return raw[:-1]
    return raw or []


def _path_row(
    path: str,
    pair: dict,
    tf: str,
    *,
    raw: list[dict],
    scoring: list[dict],
    provider: str,
    meta: dict,
    time_now: float,
) -> dict:
    raw_diag = candle_freshness_diagnostic(
        pair, tf, raw, time_now=time_now, source=provider
    )
    score_diag = candle_freshness_diagnostic(
        pair, tf, scoring, time_now=time_now, source=provider
    )
    severity = score_diag.get("stalenessSeverity") or "unknown"
    stale = severity not in {
        "fresh",
        "stale_1_bucket",
        "d1_calendar_gap_policy_ok",
        "intraday_calendar_gap_policy_ok",
    }
    required_bars, requirement = _required_scoring_bars(pair, tf)
    return {
        "path": path,
        "symbol": pair.get("display") or pair.get("symbol"),
        "asset_type": pair.get("type"),
        "tf": tf,
        "provider": provider,
        "raw_count": len(raw),
        "scoring_count": len(scoring),
        "required_scoring_bars": required_bars,
        "count_sufficient": len(scoring) >= required_bars,
        "count_requirement": requirement,
        "last_raw_ts": _last_ts(raw),
        "last_scoring_ts": _last_ts(scoring),
        "expected_latest_ts": score_diag.get("expectedCurrentBucketEpoch"),
        "lag": score_diag.get("bucketLag"),
        "confirmed_only": True,
        "cache_age": meta.get("cacheAgeSec"),
        "cache_hit": bool(meta.get("cacheHit")),
        "stale": stale,
        "reason": severity,
        "fallback_used": bool(meta.get("fallback_used")),
        "fallback_provider": meta.get("fallback_provider"),
    }


def audit_pair_tf(pair: dict, tf: str, limit: int, time_now: float) -> dict:
    mod = _load_athena_module()
    fetch_candles = mod.fetch_candles

    started = time.perf_counter()
    provider_error = None
    raw: list[dict] = []
    try:
        fetched = fetch_candles(pair, tf, limit)
        raw = list(fetched or [])
    except Exception as exc:
        provider_error = str(exc)

    # A full-universe audit can cross M15/M30 boundaries. Evaluate each fetch
    # against the wall clock observed after that fetch, not a frozen run start.
    time_now = time.time()

    meta = get_candle_fetch_meta(pair, tf, limit) or {}
    provider = meta.get("upstream") or pair.get("source") or "unknown"
    duration_ms = (time.perf_counter() - started) * 1000.0

    state_a = get_tf_market_state(pair, tf, candles=raw)
    engine_a_scoring = engine_a_scoring_candles_from_state(pair, state_a, fallback=raw)
    engine_b_scoring = engine_b_confirmed_candles_from_raw(pair, tf, raw, time_now=time_now)
    engine_c_legacy = _engine_c_legacy_chop(raw)

    diag = build_live_feed_diagnostic(
        pair,
        tf,
        raw,
        fetch_meta=meta,
        source=provider,
        fetch_duration_ms=duration_ms,
        provider_error=provider_error,
        time_now=time_now,
        market_state=state_a,
        scoring_candles=engine_a_scoring,
    )

    path_rows = [
        _path_row(
            "scanner_engine_a",
            pair,
            tf,
            raw=raw,
            scoring=engine_a_scoring,
            provider=provider,
            meta=meta,
            time_now=time_now,
        ),
        _path_row(
            "scanner_engine_b",
            pair,
            tf,
            raw=raw,
            scoring=engine_b_scoring,
            provider=provider,
            meta=meta,
            time_now=time_now,
        ),
        _path_row(
            "engine_c_legacy",
            pair,
            tf,
            raw=raw,
            scoring=engine_c_legacy,
            provider=provider,
            meta=meta,
            time_now=time_now,
        ),
    ]

    ec_mismatch = _last_ts(engine_b_scoring) != _last_ts(engine_c_legacy)

    return {
        "symbol": pair.get("display") or pair.get("symbol"),
        "asset_type": pair.get("type"),
        "source": pair.get("source"),
        "timeframe": tf,
        "diagnostic": diag,
        "path_rows": path_rows,
        "engine_c_b_leg_mismatch": ec_mismatch,
        "partial_fetch": len(raw) < max(10, min(limit, 30) // 3),
        "offset_hours": market_state_offset_hours(pair, tf),
    }


def _summarize(rows: list[dict], path_rows: list[dict]) -> dict:
    stale_by_type: Counter = Counter()
    stale_by_provider: Counter = Counter()
    stale_by_tf: Counter = Counter()
    fallback_count = 0
    cache_stale = 0
    partial_count = 0
    insufficient_count = 0
    ec_mismatch_symbols: list[str] = []
    tf_mismatch: dict[str, set[str]] = defaultdict(set)

    worst: list[tuple[float, dict]] = []

    for row in rows:
        diag = row.get("diagnostic") or {}
        sym = row.get("symbol", "?")
        atype = row.get("asset_type", "?")
        tf = row.get("timeframe", "?")
        provider = diag.get("source") or row.get("source") or "?"

        if row.get("partial_fetch"):
            partial_count += 1
        if diag.get("fallback_used"):
            fallback_count += 1
        if diag.get("isFromCache") and diag.get("stale_status") not in {
            "fresh",
            "stale_1_bucket",
            "d1_calendar_gap_policy_ok",
        }:
            cache_stale += 1

        severity = diag.get("stale_status") or "unknown"
        if severity not in {
            "fresh",
            "stale_1_bucket",
            "d1_calendar_gap_policy_ok",
            "intraday_calendar_gap_policy_ok",
        }:
            stale_by_type[atype] += 1
            stale_by_provider[provider] += 1
            stale_by_tf[tf] += 1

        lag = diag.get("lag_seconds")
        if lag is not None:
            worst.append((float(lag), {"symbol": sym, "tf": tf, "severity": severity}))

        if row.get("engine_c_b_leg_mismatch"):
            ec_mismatch_symbols.append(f"{sym}/{tf}")

    for path_row in path_rows:
        if path_row.get("path") in {"scanner_engine_a", "scanner_engine_b"} and not path_row.get(
            "count_sufficient", False
        ):
            insufficient_count += 1

    # D1/H4/H1 mismatch: same symbol where severities differ across TFs
    by_sym: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rows:
        sym = row.get("symbol", "?")
        tf = row.get("timeframe", "?")
        sev = (row.get("diagnostic") or {}).get("stale_status", "?")
        by_sym[sym][tf] = sev
    for sym, tfs in by_sym.items():
        severities = set(tfs.values())
        if len(severities) > 1 and "fresh" in severities:
            tf_mismatch[sym] = set(tfs.keys())

    worst.sort(key=lambda x: x[0], reverse=True)

    return {
        "total_symbols_checked": len({r.get("symbol") for r in rows}),
        "total_rows": len(rows),
        "stale_by_asset_type": dict(stale_by_type),
        "stale_by_provider": dict(stale_by_provider),
        "stale_by_timeframe": dict(stale_by_tf),
        "partial_fetch_count": partial_count,
        "insufficient_scoring_path_count": insufficient_count,
        "fallback_count": fallback_count,
        "cache_stale_count": cache_stale,
        "engine_c_b_leg_mismatch": ec_mismatch_symbols,
        "d1_h4_h1_mismatch_symbols": {k: sorted(v) for k, v in tf_mismatch.items()},
        "top_10_worst_lags": [item[1] for item in worst[:10]],
        "path_trace_count": len(path_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-crypto candle freshness audit")
    parser.add_argument(
        "--asset-types",
        type=str,
        default="forex,commodity,index,stock,etf",
        help="Comma-separated asset types (default: forex,commodity,index,stock,etf)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Optional comma-separated symbol filter",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="",
        help="Optional comma-separated source filter (for example: mt5)",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default="D1,H4,H1",
        help="Comma-separated timeframes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON only",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Optional path to write JSON report",
    )
    args = parser.parse_args()

    asset_types = {
        s.strip().lower()
        for s in args.asset_types.split(",")
        if s.strip()
    }
    symbols = _normalize_symbols(args.symbols) if args.symbols.strip() else None
    sources = {
        item.strip().lower()
        for item in args.sources.split(",")
        if item.strip()
    } or None
    timeframes = [s.strip().upper() for s in args.timeframes.split(",") if s.strip()]

    _, active_pairs = _load_all_pairs()
    pairs = _filter_pairs(active_pairs, asset_types, symbols, sources)
    if not pairs:
        print("No matching non-crypto pairs found.", file=sys.stderr)
        return 1

    limits = scan_candle_limits()
    time_now = time.time()
    generated_at = datetime.now(timezone.utc).isoformat()

    rows: list[dict] = []
    path_rows: list[dict] = []

    for pair in pairs:
        for tf in timeframes:
            limit = int(limits.get(tf, config.CONFIG.get(f"{tf}_CANDLES", 300)) or 300)
            row = audit_pair_tf(pair, tf, limit, time_now)
            rows.append(row)
            path_rows.extend(row.get("path_rows") or [])

    summary = _summarize(rows, path_rows)

    result = {
        "success": True,
        "generatedAt": generated_at,
        "forexH4ResampleOffsetHours": config.CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS"),
        "mt5BrokerUtcOffset": config.CONFIG.get("MT5_BROKER_UTC_OFFSET"),
        "d1ResampleOffsetHours": config.CONFIG.get("D1_RESAMPLE_OFFSET_HOURS"),
        "rows": rows,
        "path_trace": path_rows,
        "summary": summary,
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\nNon-Crypto Candle Freshness Audit — {generated_at}")
        print(f"Pairs: {summary['total_symbols_checked']} | Rows: {summary['total_rows']}")
        print(f"Stale by type: {summary['stale_by_asset_type']}")
        print(f"Stale by provider: {summary['stale_by_provider']}")
        print(f"Stale by TF: {summary['stale_by_timeframe']}")
        print(f"Partial fetches: {summary['partial_fetch_count']}")
        print(f"Insufficient scoring paths: {summary['insufficient_scoring_path_count']}")
        print(f"Fallback used: {summary['fallback_count']}")
        print(f"Cache-stale: {summary['cache_stale_count']}")
        if summary["engine_c_b_leg_mismatch"]:
            print(f"Engine C legacy mismatches: {summary['engine_c_b_leg_mismatch'][:10]}")
        print("\nTop stale rows:")
        for row in rows:
            d = row.get("diagnostic") or {}
            sev = d.get("stale_status", "?")
            if sev not in {"fresh", "stale_1_bucket", "d1_calendar_gap_policy_ok"}:
                print(
                    f"  {row.get('symbol')} [{row.get('timeframe')}] "
                    f"{sev} lag={d.get('bucket_lag')} provider={d.get('source')}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
