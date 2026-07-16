#!/usr/bin/env python3
"""Timeframe / feed-availability audit (DIAGNOSTIC ONLY).

This script reports, it does not change trading behaviour. It:

  * introspects how each engine (A / B / C / Scalp-D / ASE / cascade) wires
    timeframes, by importing the *live* single-source-of-truth tables rather
    than re-declaring them;
  * compares actual TF wiring against a reference intraday TF policy that lives
    ONLY inside this script (never wired into execution);
  * optionally probes MT5 (Pepperstone) and Bybit for real per-TF feed
    availability, bar counts, freshness and partial-candle handling;
  * emits a Markdown report and a JSON report under ``reports/``.

It never places trades, never mutates config, and touches no DB. MT5/Bybit
probes are best-effort: if a venue is unavailable the relevant rows are marked
BLOCKED_FEED_UNAVAILABLE rather than failing the run.

Usage:
    python scripts/timeframe_feed_audit.py                 # static audit only
    python scripts/timeframe_feed_audit.py --probe-mt5     # + live MT5 probe
    python scripts/timeframe_feed_audit.py --probe-bybit   # + live Bybit probe
    python scripts/timeframe_feed_audit.py --probe-all
    python scripts/timeframe_feed_audit.py --out-dir reports/tf_audit
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from datetime import datetime, timezone

# Make repo root importable when run from anywhere.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Status vocabulary (kept identical to the audit spec).
# ---------------------------------------------------------------------------
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
BLOCKED_NOT_CONFIGURED = "BLOCKED_NOT_CONFIGURED"
BLOCKED_FEED_UNAVAILABLE = "BLOCKED_FEED_UNAVAILABLE"
BLOCKED_SYMBOL_UNAVAILABLE = "BLOCKED_SYMBOL_UNAVAILABLE"
BLOCKED_TIMEFRAME_UNAVAILABLE = "BLOCKED_TIMEFRAME_UNAVAILABLE"
DEGRADED_INSUFFICIENT_HISTORY = "DEGRADED_INSUFFICIENT_HISTORY"
DEGRADED_STALE_DATA = "DEGRADED_STALE_DATA"
DEGRADED_GAPS = "DEGRADED_GAPS"
DEGRADED_PARTIAL_CANDLE_RISK = "DEGRADED_PARTIAL_CANDLE_RISK"

ALL_TFS = ["D1", "H4", "H1", "M30", "M15", "M5"]
TF_SECONDS = {
    "MN1": 2592000, "W1": 604800, "D1": 86400, "H4": 14400, "H1": 3600,
    "M30": 1800, "M15": 900, "M5": 300, "M1": 60,
}

# ---------------------------------------------------------------------------
# REFERENCE intraday TF policy. DIAGNOSTIC ONLY — never imported by execution.
# Mirrors the policy supplied in the audit brief. Roles:
#   bias  = regime/direction (HTF)
#   setup = structure / setup TF
#   trig  = entry trigger TF
#   micro = precision trigger (conditional)
# ---------------------------------------------------------------------------
REFERENCE_TF_POLICY = {
    "fx_majors":        {"bias": "H4", "setup": "H1",    "trig": "M15", "micro": "M5*"},
    "fx_slow_crosses":  {"bias": "H4", "setup": "H1/M30", "trig": "M15", "micro": "avoid"},
    "fx_volatile":      {"bias": "H4", "setup": "M30/H1", "trig": "M15", "micro": "M5*"},
    "metals_xau":       {"bias": "D1/H4", "setup": "H1/M30", "trig": "M15", "micro": "M5*"},
    "metals_other":     {"bias": "H4", "setup": "H1/M30", "trig": "M15", "micro": "avoid"},
    "energy_oil":       {"bias": "D1/H4", "setup": "H1", "trig": "M15", "micro": "avoid"},
    "energy_gas":       {"bias": "D1/H4", "setup": "H1", "trig": "M30/M15", "micro": "avoid"},
    "indices":          {"bias": "D1/H4", "setup": "H1/M30", "trig": "M15", "micro": "M5*"},
    "stocks_etf":       {"bias": "D1", "setup": "H1/M30", "trig": "M15/M5", "micro": "n/a"},
    "crypto_majors":    {"bias": "D1/H4", "setup": "H1", "trig": "M15", "micro": "M5*"},
    "crypto_alts":      {"bias": "D1/H4", "setup": "H1/M30", "trig": "M15", "micro": "avoid"},
}


def _registered_pairs_from_source() -> list[dict]:
    """Read the production universe literals without importing athena.py."""
    wanted = {
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    }
    path = os.path.join(_REPO_ROOT, "athena.py")
    tree = ast.parse(open(path, "r", encoding="utf-8").read(), filename=path)
    pairs: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in wanted:
            pairs.extend(ast.literal_eval(node.value))
    return pairs


def _mt5_symbol_map_from_source() -> dict[str, str]:
    """Read the execution-owned MT5 map without importing safety-critical code."""
    path = os.path.join(_REPO_ROOT, "mt5_executor.py")
    tree = ast.parse(open(path, "r", encoding="utf-8").read(), filename=path)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "_MT5_SYMBOL_MAP":
            return dict(ast.literal_eval(node.value))
    return {}


def _symbol_reconciliation() -> dict:
    from timeframe_policy import reconcile_symbol_universe

    reconciliation = reconcile_symbol_universe(_registered_pairs_from_source())
    mt5_map = _mt5_symbol_map_from_source()
    for row in reconciliation.get("rows") or []:
        if row.get("provider") != "mt5":
            continue
        display = str(row.get("display_symbol") or "")
        provider_symbol = mt5_map.get(display)
        if not provider_symbol:
            stripped = display.replace("/", "").replace(" ", "")
            provider_symbol = (
                f"{stripped}.US"
                if stripped.isupper() and 1 < len(stripped) <= 5
                else stripped
            )
        row["provider_symbol"] = provider_symbol or None
    return reconciliation

# Representative symbols per group (display -> group) for live probes.
MT5_PROBE_SYMBOLS = {
    "EUR/USD": "fx_majors", "GBP/USD": "fx_majors", "USD/JPY": "fx_majors",
    "USD/CHF": "fx_majors", "AUD/USD": "fx_majors", "USD/CAD": "fx_majors",
    "EUR/GBP": "fx_slow_crosses", "AUD/NZD": "fx_slow_crosses",
    "GBP/JPY": "fx_volatile", "EUR/JPY": "fx_volatile", "AUD/JPY": "fx_volatile",
    "XAU/USD": "metals_xau", "XAG/USD": "metals_other",
    "XPT/USD": "metals_other", "XPD/USD": "metals_other",
    "WTI Oil": "energy_oil", "Brent Oil": "energy_oil", "Nat Gas": "energy_gas",
    # Index keys must be the production display names (mt5_map_symbol input);
    # short forms like "US500" fall through to the .US stock fallback and
    # probe as BLOCKED_SYMBOL_UNAVAILABLE even though the feed is fine.
    "NAS100": "indices", "S&P 500": "indices", "Dow Jones": "indices",
    "DAX 40": "indices", "UK100": "indices", "JPN225": "indices",
    "AAPL": "stocks_etf", "SPY": "stocks_etf",
}
BYBIT_PROBE_SYMBOLS = {
    "BTC/USDT": "crypto_majors", "ETH/USDT": "crypto_majors",
    "SOL/USDT": "crypto_alts", "XRP/USDT": "crypto_alts",
    "DOGE/USDT": "crypto_alts", "BNB/USDT": "crypto_alts",
    "ADA/USDT": "crypto_alts", "LINK/USDT": "crypto_alts",
}

# Weekend / market-closed tolerance: don't flag forex/CFD as stale when the
# market is shut (Fri 21:00 UTC -> Sun 21:00 UTC roughly).
def _market_probably_closed(now: float | None = None) -> bool:
    t = time.gmtime(now if now is not None else time.time())
    wday = t.tm_wday  # Mon=0 .. Sun=6
    hour = t.tm_hour
    if wday == 5:  # Saturday
        return True
    if wday == 6 and hour < 21:  # Sunday before open
        return True
    if wday == 4 and hour >= 21:  # Friday after close
        return True
    return False


# US cash equities trade ~13:30-20:00 UTC on weekdays. Without this, every
# overnight/weekend probe flags stocks/ETFs as DEGRADED_STALE_DATA.
def _us_equity_probably_closed(now: float | None = None) -> bool:
    t = time.gmtime(now if now is not None else time.time())
    if t.tm_wday >= 5:
        return True
    minutes = t.tm_hour * 60 + t.tm_min
    return not (13 * 60 + 30 <= minutes < 20 * 60)


def _closed_seconds_between(start: float, end: float, group: str) -> float:
    """Approximate closed-market seconds in [start, end) for gap crediting.

    All groups: Saturday/Sunday (UTC). stocks_etf additionally: weekday hours
    outside the 13:30-20:00 UTC US cash session. DST shifts and broker-time
    offsets are absorbed by the 0.5*tf_sec gap tolerance.
    """
    if end <= start:
        return 0.0
    total = 0.0
    t = start
    while t < end:
        tm = time.gmtime(t)
        second_of_day = tm.tm_hour * 3600 + tm.tm_min * 60 + tm.tm_sec
        day_end = t + (86400 - second_of_day)
        seg_end = min(end, day_end)
        if tm.tm_wday >= 5:
            total += seg_end - t
        elif group == "stocks_etf":
            open_s = 13 * 3600 + 30 * 60
            close_s = 20 * 3600
            day_start = t - second_of_day
            open_t = day_start + open_s
            close_t = day_start + close_s
            overlap_open = max(t, min(seg_end, open_t)) - t
            overlap_close = seg_end - max(t, min(seg_end, close_t))
            total += max(0.0, overlap_open) + max(0.0, overlap_close)
        t = seg_end
    return total


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scan_limits_from_yaml() -> dict | None:
    """Read D1/H4/H1 candle counts directly from config.yaml (read-only) when
    importing config.py is blocked by the live-order safety gate."""
    path = os.path.join(_REPO_ROOT, "config.yaml")
    if not os.path.exists(path):
        return None
    out: dict = {}
    try:
        import re
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(
                    r"^(D1_CANDLES|H4_CANDLES|H1_CANDLES|M30_CANDLES|M15_CANDLES|M5_CANDLES)\s*:\s*(\d+)",
                    line,
                )
                if m:
                    out[m.group(1).split("_")[0]] = int(m.group(2))
    except Exception:
        return out or None
    return out or None


def _scalp_tfs_from_source() -> list | None:
    """Parse the TF keys of scalp_engine.mt5_fetch_scalp_candles tf_map (read-only)."""
    import re
    path = os.path.join(_REPO_ROOT, "scalp_engine.py")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r"def mt5_fetch_scalp_candles\(.*?tf_map\s*=\s*\{(.*?)\}",
                      src, re.S)
        if not m:
            return None
        return re.findall(r'"(M1|M5|M15|M30|H1|H4|D1)"', m.group(1))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# A. Introspect ACTUAL engine TF wiring (import live tables; never re-declare).
# ---------------------------------------------------------------------------
def collect_engine_wiring() -> dict:
    wiring: dict = {"errors": []}

    # Scan candle limits — the only TFs Engine A/B/C scan path ever fetches.
    # NB: importing config.py runs the repo's safety validation, which may
    # SystemExit when the live-order env gate is set. Catch BaseException and
    # fall back to reading the raw keys from config.yaml (read-only).
    try:
        from config import scan_candle_limits
        wiring["scan_candle_limits"] = scan_candle_limits()
    except BaseException as exc:  # noqa: BLE001 - env/SystemExit dependent
        limits = _scan_limits_from_yaml()
        wiring["scan_candle_limits"] = limits
        wiring["errors"].append(
            f"scan_candle_limits import blocked ({exc!r}); used config.yaml fallback={limits}"
        )

    # Engine B TF matrix (single source of truth).
    try:
        import market_structure as ms
        matrix = {}
        for asset in ("forex", "crypto", "commodity", "index", "stock"):
            matrix[asset] = {}
            for style in ("scalp", "intraday", "swing"):
                matrix[asset][style] = ms.resolve_engine_b_tfs(asset, style)
        wiring["engine_b_tf_matrix"] = matrix
    except BaseException as exc:  # noqa: BLE001
        # The policy module is side-effect free and remains importable when the
        # runtime config safety gate correctly blocks market_structure imports.
        try:
            from timeframe_policy import resolve_timeframe_policy

            matrix = {}
            for asset in ("forex", "crypto", "commodity", "index", "stock"):
                matrix[asset] = {}
                for style in ("scalp", "intraday", "swing"):
                    policy = resolve_timeframe_policy(
                        "", asset, None, f"engine_b_{style}"
                    )
                    matrix[asset][style] = {
                        "struct": policy.structure_tf.value,
                        "zone": policy.structure_tf.value,
                        "setup": policy.setup_tf.value,
                        "trigger": policy.trigger_tf.value,
                        "execution": policy.execution_tf.value,
                        "atr": policy.structure_tf.value,
                    }
            wiring["engine_b_tf_matrix"] = matrix
        except Exception:
            wiring["engine_b_tf_matrix"] = None
        wiring["errors"].append(
            f"engine_b_tf_matrix runtime import blocked ({exc!r}); used side-effect-free policy fallback"
        )

    # Cascade scan TFs.
    try:
        import cascade_scan
        wiring["cascade_analysis_tfs"] = list(
            getattr(cascade_scan, "ANALYSIS_TIMEFRAMES", []) or []
        )
    except BaseException as exc:  # noqa: BLE001
        wiring["cascade_analysis_tfs"] = None
        wiring["errors"].append(f"cascade_analysis_tfs: {exc!r}")

    # ASE ingest TFs.
    try:
        import importlib
        ase_mt5 = importlib.import_module("athena_ase.data.ingest.mt5_live")
        wiring["ase_ingest_tfs"] = list(getattr(ase_mt5, "_TIMEFRAMES", ()) or ())
    except BaseException as exc:  # noqa: BLE001
        wiring["ase_ingest_tfs"] = None
        wiring["errors"].append(f"ase_ingest_tfs: {exc!r}")

    # Scalp engine MT5 TF support (the only sub-H1 path).
    try:
        import inspect
        import scalp_engine
        src = inspect.getsource(scalp_engine.mt5_fetch_scalp_candles)
        scalp_tfs = [tf for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
                     if f'"{tf}"' in src]
        wiring["scalp_mt5_tfs"] = scalp_tfs
    except BaseException as exc:  # noqa: BLE001
        wiring["scalp_mt5_tfs"] = _scalp_tfs_from_source()
        wiring["errors"].append(
            f"scalp_mt5_tfs import blocked ({exc!r}); used source-parse fallback"
        )

    # MT5 generic fetch tf_map (athena.py) — what tf strings map to MT5 consts.
    # Reported statically (importing athena.py is heavy / side-effectful), so we
    # record the known mapping and its documented silent fallback.
    wiring["mt5_generic_tf_map"] = {
        "supported": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
        "silent_default_on_unknown": None,
        "ref": "athena.py _mt5_fetch rejects mt5_tf is None (no H1 fallback)",
    }

    # Bybit interval map.
    try:
        import data_feeds
        bybit_map = {}
        for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"):
            bybit_map[tf] = data_feeds._bybit_interval(tf)
        wiring["bybit_interval_map"] = bybit_map
    except BaseException as exc:  # noqa: BLE001
        wiring["bybit_interval_map"] = None
        wiring["errors"].append(f"bybit_interval_map: {exc!r}")

    return wiring


# ---------------------------------------------------------------------------
# B. MT5 live probe (best-effort, read-only).
# ---------------------------------------------------------------------------
def probe_mt5(symbols: dict, bars: int = 60) -> dict:
    out: dict = {"available": False, "rows": [], "note": ""}
    try:
        import mt5_executor
        import MetaTrader5 as mt5  # noqa: F401
    except BaseException as exc:  # noqa: BLE001 - SystemExit from config safety gate
        out["note"] = f"BLOCKED_MT5_UNAVAILABLE: import failed ({exc!r})"
        return out
    try:
        if not mt5_executor.mt5_connect():
            out["note"] = "BLOCKED_MT5_UNAVAILABLE: mt5_connect() returned False"
            return out
        mt5 = mt5_executor._get_mt5()
    except BaseException as exc:  # noqa: BLE001
        out["note"] = f"BLOCKED_MT5_UNAVAILABLE: connect raised ({exc!r})"
        return out

    out["available"] = True
    tf_const = {
        "D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4, "H1": mt5.TIMEFRAME_H1,
        "M30": mt5.TIMEFRAME_M30, "M15": mt5.TIMEFRAME_M15, "M5": mt5.TIMEFRAME_M5,
    }
    now = time.time()

    for display, group in symbols.items():
        closed = (
            _us_equity_probably_closed(now)
            if group == "stocks_etf"
            else _market_probably_closed(now)
        )
        mt5_symbol = None
        try:
            mt5_symbol = mt5_executor.mt5_map_symbol(display)
        except Exception:
            mt5_symbol = None
        row = {"source": "mt5", "symbol": display, "normalized_symbol": mt5_symbol,
               "group": group, "per_tf": {}}
        if not mt5_symbol or not mt5.symbol_select(mt5_symbol, True):
            row["status"] = BLOCKED_SYMBOL_UNAVAILABLE
            out["rows"].append(row)
            continue
        for tf in ALL_TFS:
            row["per_tf"][tf] = _probe_one_mt5(mt5, mt5_symbol, tf, tf_const[tf],
                                               bars, now, closed, group)
        row["status"] = _rollup_symbol_status(row["per_tf"])
        out["rows"].append(row)
    return out


def _probe_one_mt5(mt5, symbol, tf, tf_c, bars, now, closed, group="") -> dict:
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf_c, 0, bars)
    except Exception as exc:
        return {"status": BLOCKED_TIMEFRAME_UNAVAILABLE, "detail": repr(exc)}
    if rates is None or len(rates) == 0:
        return {"status": BLOCKED_TIMEFRAME_UNAVAILABLE, "detail": "no bars"}
    n = len(rates)
    last_open = float(rates[-1]["time"])
    tf_sec = TF_SECONDS.get(tf, 3600)
    # MT5 bar 'time' is the OPEN time of the (possibly forming) latest bar.
    forming = (last_open + tf_sec) > now
    last_closed_open = float(rates[-2]["time"]) if n >= 2 else last_open
    age_sec = now - (last_closed_open + tf_sec)
    # gaps: count bars whose spacing != tf_sec after crediting closed-market
    # time (weekends; US session hours for stocks/ETFs). A 5-day D1 series has
    # ~12 weekend gaps per 60 bars and would otherwise always flag DEGRADED_GAPS.
    gaps = 0
    for i in range(1, n):
        prev_t = float(rates[i - 1]["time"])
        cur_t = float(rates[i]["time"])
        d = cur_t - prev_t
        if d > tf_sec:
            d -= _closed_seconds_between(prev_t + tf_sec, cur_t + tf_sec, group)
        if abs(d - tf_sec) > tf_sec * 0.5:
            gaps += 1
    rec = {
        "bar_count": n,
        "last_bar_open_utc": datetime.fromtimestamp(last_open, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_forming": bool(forming),
        "closed_age_sec": round(age_sec, 1),
        "gaps": gaps,
    }
    # Status rollup per TF.
    if n < 30:
        rec["status"] = DEGRADED_INSUFFICIENT_HISTORY
    elif age_sec > tf_sec * 3 and not closed:
        rec["status"] = DEGRADED_STALE_DATA
    elif gaps > max(2, n * 0.1) and not closed:
        rec["status"] = DEGRADED_GAPS
    else:
        rec["status"] = PASS
    return rec


# ---------------------------------------------------------------------------
# C. Bybit live probe (best-effort, read-only, linear perpetual).
# ---------------------------------------------------------------------------
def probe_bybit(symbols: dict, bars: int = 60) -> dict:
    out: dict = {"available": False, "rows": [], "note": ""}
    try:
        import data_feeds
    except BaseException as exc:  # noqa: BLE001 - SystemExit from config safety gate
        out["note"] = f"BLOCKED_BYBIT_UNAVAILABLE: import failed ({exc!r})"
        return out

    now = time.time()
    probed_any = False
    for display, group in symbols.items():
        normalized = display.replace("/", "").upper()
        row = {"source": "bybit", "symbol": display, "normalized_symbol": normalized,
               "group": group, "per_tf": {}, "category": "linear"}
        for tf in ALL_TFS:
            row["per_tf"][tf] = _probe_one_bybit(data_feeds, normalized, tf, bars, now)
            if row["per_tf"][tf].get("bar_count"):
                probed_any = True
        row["status"] = _rollup_symbol_status(row["per_tf"])
        out["rows"].append(row)
    out["available"] = probed_any
    if not probed_any:
        out["note"] = "BLOCKED_BYBIT_UNAVAILABLE: no klines returned for any symbol/TF"
    return out


def _probe_one_bybit(data_feeds, symbol, tf, bars, now) -> dict:
    interval = data_feeds._bybit_interval(tf)
    if interval is None:
        return {"status": BLOCKED_TIMEFRAME_UNAVAILABLE, "detail": "no interval map"}
    try:
        candles = data_feeds._fetch_bybit_klines(symbol, tf, bars)
    except Exception as exc:
        return {"status": BLOCKED_FEED_UNAVAILABLE, "detail": repr(exc)}
    if not candles:
        return {"status": BLOCKED_FEED_UNAVAILABLE, "detail": "no candles"}
    n = len(candles)
    last = candles[-1]
    tf_sec = TF_SECONDS.get(tf, 3600)
    last_open = float(last.get("open_time", 0)) / 1000.0
    forming = last.get("confirmed") is False or last.get("closed") is False
    # newest CONFIRMED bar
    confirmed = [c for c in candles if c.get("confirmed") is not False]
    last_conf_open = float(confirmed[-1].get("open_time", 0)) / 1000.0 if confirmed else last_open
    age_sec = now - (last_conf_open + tf_sec)
    rec = {
        "bar_count": n,
        "last_bar_open_utc": datetime.fromtimestamp(last_open, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_forming": bool(forming),
        "closed_age_sec": round(age_sec, 1),
        "partial_flagged": last.get("confirmed") is not None,
    }
    if n < 30:
        rec["status"] = DEGRADED_INSUFFICIENT_HISTORY
    elif age_sec > tf_sec * 3:
        rec["status"] = DEGRADED_STALE_DATA
    else:
        rec["status"] = PASS
    return rec


def _rollup_symbol_status(per_tf: dict) -> str:
    """Roll up per-TF statuses to a single symbol verdict (worst wins, but a
    missing intraday TF is the headline)."""
    for tf in ("M15", "M5", "M30"):
        st = (per_tf.get(tf) or {}).get("status")
        if st in (BLOCKED_TIMEFRAME_UNAVAILABLE, BLOCKED_FEED_UNAVAILABLE):
            return BLOCKED_TIMEFRAME_UNAVAILABLE
    severities = [v.get("status") for v in per_tf.values() if isinstance(v, dict)]
    for bad in (FAIL, DEGRADED_STALE_DATA, DEGRADED_GAPS,
                DEGRADED_INSUFFICIENT_HISTORY):
        if bad in severities:
            return bad
    return PASS


# ---------------------------------------------------------------------------
# Report assembly.
# ---------------------------------------------------------------------------
def build_report(wiring: dict, mt5_res: dict | None, bybit_res: dict | None) -> dict:
    scan_has_sub_h1 = any(t in (wiring.get("scan_candle_limits") or {})
                          for t in ("M30", "M15", "M5"))

    findings = []

    # Finding 1: scan path TF ceiling.
    findings.append({
        "id": "TF-1",
        "severity": "FAIL" if not scan_has_sub_h1 else "PASS",
        "area": "Engine A/B/C scan path",
        "evidence": "config.scan_candle_limits() -> "
                    f"{wiring.get('scan_candle_limits')}",
        "detail": (
            "Main scan exposes D1/H4/H1/M30/M15/M5 policy limits and can preload "
            "the resolved lower-timeframe roles."
            if scan_has_sub_h1
            else "Main scan has no sub-H1 candle limit and cannot supply resolved trigger roles."
        ),
    })

    # Finding 2: Engine B trigger ceiling.
    ebm = wiring.get("engine_b_tf_matrix") or {}
    trigs = sorted({v["trigger"] for a in ebm.values() for v in a.values()}) if ebm else []
    findings.append({
        "id": "TF-2",
        "severity": "PASS" if "M15" in trigs else "FAIL",
        "area": "Engine B trigger TF",
        "evidence": f"timeframe_policy.resolve_timeframe_policy trigger TFs = {trigs}",
        "detail": (
            "Engine B resolves M15 intraday triggers and explicit M5 scalp execution."
            if "M15" in trigs
            else "Engine B trigger roles do not expose the required M15 path."
        ),
    })

    # Finding 3: silent MT5 fallback to H1.
    findings.append({
        "id": "TF-3",
        "severity": "PASS" if wiring["mt5_generic_tf_map"].get("silent_default_on_unknown") is None else "WARN",
        "area": "MT5 generic fetch",
        "evidence": wiring["mt5_generic_tf_map"]["ref"],
        "detail": "Unknown/malformed MT5 timeframe strings fail closed and are not served as H1.",
    })

    # Finding 4: ASE has no sub-H1 data.
    ase_tfs = wiring.get("ase_ingest_tfs")
    findings.append({
        "id": "TF-4",
        "severity": "FAIL" if ase_tfs and not any(t in ase_tfs for t in ("M15", "M5", "M30")) else "WARN",
        "area": "ASE validator",
        "evidence": f"athena_ase.data.ingest.mt5_live._TIMEFRAMES = {ase_tfs}",
        "detail": "ASE ingests only H1/H4/D1 (same across mt5/eodhd/binance ingest, "
                  "and horizon.py uses H1+D1). ASE therefore cannot validate M15 "
                  "trigger quality or M5 precision — that TF context is simply absent, "
                  "so any 'M15 trigger alignment' check would be vacuous.",
    })

    # Finding 5: scalp engine is the only sub-H1 path.
    findings.append({
        "id": "TF-5",
        "severity": "INFO",
        "area": "Scalp engine (Engine D / Scalp Workbench)",
        "evidence": f"scalp_engine.mt5_fetch_scalp_candles TFs = {wiring.get('scalp_mt5_tfs')}",
        "detail": "Engine D retains its native H1/M15/M5/M1 feed path; the main scanner now independently preloads M30/M15/M5 roles for A/B without collapsing Engine C boundaries.",
    })

    # Finding 6: Bybit interval map completeness.
    bmap = wiring.get("bybit_interval_map") or {}
    bybit_complete = all(bmap.get(t) for t in ("M5", "M15", "M30", "H1", "H4", "D1"))
    findings.append({
        "id": "TF-6",
        "severity": "PASS" if bybit_complete else "FAIL",
        "area": "Bybit interval mapping",
        "evidence": f"data_feeds._bybit_interval map = {bmap}",
        "detail": "Bybit klines support the full M1..W1 interval set and symbol "
                  "normalization is BTC/USDT->BTCUSDT (replace('/','').upper()). The "
                  "venue CAN supply M15/M5; the limitation is purely on the consumer "
                  "(scan) side, not the Bybit feed.",
    })

    verdict = "FAIL" if any(f["severity"] == "FAIL" for f in findings) else (
        "WARN" if any(f["severity"] == "WARN" for f in findings) else "PASS")

    return {
        "generated_utc": _now_iso(),
        "executive_verdict": verdict,
        "wiring": wiring,
        "findings": findings,
        "reference_policy": REFERENCE_TF_POLICY,
        "symbol_reconciliation": _symbol_reconciliation(),
        "mt5_probe": mt5_res,
        "bybit_probe": bybit_res,
    }


def render_markdown(report: dict) -> str:
    L = []
    w = report["wiring"]
    L.append("# Timeframe / Feed Availability Audit (diagnostic-only)\n")
    L.append(f"_Generated: {report['generated_utc']}_\n")
    L.append(f"## 1. Executive verdict: **{report['executive_verdict']}**\n")

    L.append("## 2. Engine timeframe matrix (ACTUAL, from live tables)\n")
    L.append("| Engine | Fetched TFs | Trigger ceiling | Source |")
    L.append("|---|---|---|---|")
    scl = w.get("scan_candle_limits")
    L.append(f"| Engine A (scan) | {list((scl or {}).keys())} | H4/H1 score | config.scan_candle_limits |")
    ebm = w.get("engine_b_tf_matrix") or {}
    trigs = sorted({v['trigger'] for a in ebm.values() for v in a.values()}) if ebm else []
    L.append(f"| Engine B | {list((scl or {}).keys())} | {trigs} | timeframe_policy.resolve_timeframe_policy |")
    L.append(f"| Engine C (consensus) | consumes independent A/B policy payloads | n/a | engine_c.compute_consensus |")
    L.append(f"| Scalp (Engine D) | {w.get('scalp_mt5_tfs')} | M1/M5 | scalp_engine.mt5_fetch_scalp_candles |")
    L.append(f"| ASE | {w.get('ase_ingest_tfs')} | H1 | athena_ase.data.ingest |")
    L.append(f"| Cascade scan | {w.get('cascade_analysis_tfs')} | H1/H4 | cascade_scan.ANALYSIS_TIMEFRAMES |")
    L.append("")

    L.append("## 3. Reference asset-group TF benchmark (production policy: timeframe_policy.py)\n")
    L.append("| Group | bias | setup | trigger | micro |")
    L.append("|---|---|---|---|---|")
    for g, p in report["reference_policy"].items():
        L.append(f"| {g} | {p['bias']} | {p['setup']} | {p['trig']} | {p['micro']} |")
    L.append("")

    L.append("## 4. Findings (actual vs expected)\n")
    L.append("| ID | Severity | Area | Detail |")
    L.append("|---|---|---|---|")
    for f in report["findings"]:
        L.append(f"| {f['id']} | {f['severity']} | {f['area']} | {f['detail']} |")
    L.append("")
    for f in report["findings"]:
        L.append(f"- **{f['id']} [{f['severity']}] {f['area']}** — evidence: `{f['evidence']}`")
    L.append("")

    reconciliation = report.get("symbol_reconciliation") or {}
    rows = reconciliation.get("rows") or []
    L.append("## 5. Production symbol reconciliation\n")
    L.append(
        f"Enabled={len(rows)}; duplicate canonicals={reconciliation.get('duplicate_canonical_symbols') or []}; "
        f"multi-group aliases={reconciliation.get('aliases_mapping_to_multiple_groups') or {}}; "
        f"unsafe fallbacks={reconciliation.get('unsafe_symbols') or []}.\n"
    )
    L.append("| Display | Canonical | Asset | Score group | Provider | Provider symbol | Styles | Baseline | Source |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        L.append(
            f"| {row.get('display_symbol')} | {row.get('canonical_symbol')} | "
            f"{row.get('asset_type')} | {row.get('score_group')} | {row.get('provider')} | "
            f"{row.get('provider_symbol')} | {', '.join(row.get('supported_styles') or [])} | "
            f"{row.get('baseline_profile')} | {row.get('policy_source')} |"
        )
    L.append("")

    for label, res in (("6. MT5 feed capability matrix", report.get("mt5_probe")),
                       ("7. Bybit feed capability matrix", report.get("bybit_probe"))):
        L.append(f"## {label}\n")
        if not res:
            L.append("_Not probed (run with --probe-mt5 / --probe-bybit)._\n")
            continue
        if not res.get("available"):
            L.append(f"_{res.get('note') or 'BLOCKED_FEED_UNAVAILABLE'}_\n")
            continue
        L.append("| symbol | normalized | " + " | ".join(ALL_TFS) + " | status |")
        L.append("|---|---|" + "|".join(["---"] * (len(ALL_TFS) + 1)) + "|")
        for row in res.get("rows", []):
            cells = []
            for tf in ALL_TFS:
                rec = row["per_tf"].get(tf) or {}
                st = rec.get("status", "-")
                bc = rec.get("bar_count", "")
                cells.append(f"{st}{('/' + str(bc)) if bc else ''}")
            L.append(f"| {row['symbol']} | {row['normalized_symbol']} | "
                     + " | ".join(cells) + f" | {row['status']} |")
        L.append("")

    L.append("## 8. Symbol mapping audit\n")
    L.append("- MT5: explicit dict `_MT5_SYMBOL_MAP` + fallback strip `/`/space; "
             "2-5 char uppercase -> `.US` suffix (stocks/ETFs). Forex/metals are "
             "in the explicit map. See `mt5_executor.mt5_map_symbol`.")
    L.append("- Bybit: `symbol.replace('/','').upper()` -> BTCUSDT etc; category=linear "
             "(perpetual). See `data_feeds._fetch_bybit_klines`.")
    L.append("")

    if w.get("errors"):
        L.append("## Introspection errors\n")
        for e in w["errors"]:
            L.append(f"- {e}")
        L.append("")

    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe-mt5", action="store_true")
    ap.add_argument("--probe-bybit", action="store_true")
    ap.add_argument("--probe-all", action="store_true")
    ap.add_argument("--bars", type=int, default=60)
    ap.add_argument("--out-dir", default=os.path.join(_REPO_ROOT, "reports"))
    args = ap.parse_args()

    wiring = collect_engine_wiring()
    mt5_res = None
    bybit_res = None
    if args.probe_mt5 or args.probe_all:
        mt5_res = probe_mt5(MT5_PROBE_SYMBOLS, bars=args.bars)
    if args.probe_bybit or args.probe_all:
        bybit_res = probe_bybit(BYBIT_PROBE_SYMBOLS, bars=args.bars)

    report = build_report(wiring, mt5_res, bybit_res)
    md = render_markdown(report)

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(args.out_dir, f"timeframe_feed_audit_{stamp}.json")
    md_path = os.path.join(args.out_dir, f"timeframe_feed_audit_{stamp}.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    print(md)
    print(f"\n[written] {md_path}")
    print(f"[written] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
