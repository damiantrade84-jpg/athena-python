"""Session heat: historical Engine A / Engine B performance by time-of-day bucket.

Answers the operator question "is now a historically good window to trade this
engine's signals?" using **real recorded trade outcomes**, never synthetic or
invented numbers.

Source of truth
---------------
``backtest_trades_v3`` joined to ``backtest_runs_v3`` in the audit database.
Every row is a completed trade with an entry timestamp and a realised R result
produced by the backtesting v2/v3 contract. Nothing here re-scores, re-simulates,
or estimates an outcome.

Runs are de-duplicated before aggregation: when the same
``(engine, symbol, style, contract_version)`` has been re-run, only the newest
run contributes, so a re-run symbol cannot double-count into a session cell.

Session buckets
---------------
Buckets are defined on **fixed UTC hours** so a cell means the same thing in
summer and winter. They are deliberately coarse (5 buckets covering all 24h) so
that per-engine, per-score-group samples stay large enough to mean something.
Each bucket documents the ICT-style killzone it contains; the killzone windows
themselves are the tighter sub-windows quoted in the ``killzone`` field.

    asia       00:00-07:00 UTC   contains the ICT Asian killzone 01:00-05:00 UTC
    london     07:00-12:00 UTC   contains the ICT London killzone 07:00-10:00 UTC
    ny_am      12:00-16:00 UTC   contains the ICT NY AM killzone 12:00-15:00 UTC
                                 and the London/NY overlap
    ny_pm      16:00-21:00 UTC   contains the London-close and ICT NY PM
                                 killzone 18:30-21:00 UTC
    off_hours  21:00-24:00 UTC   rollover / thin liquidity

DST caveat (documented, not silently absorbed): New York shifts by one hour
between EST and EDT, so the ICT killzones drift ~1h against these fixed UTC
edges for part of the year. The buckets are wide enough to contain their
killzone in both regimes. ``session_contract.classify_session`` remains the
authoritative per-asset session-quality classifier for scoring and risk; this
module is a separate, coarser, cross-engine **measurement** layer for the
dashboard and does not feed any gate.

Safety
------
Read-only. Advisory only. Nothing here changes a score, a threshold, a gate, or
an execution decision.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

log = logging.getLogger("sentinel.session_heat")

SESSION_HEAT_VERSION = "session-heat-v1"
SESSION_HEAT_SOURCE = "backtest_trades_v3"


# --- Session bucket contract -------------------------------------------------


@dataclass(frozen=True)
class SessionBucket:
    """One time-of-day bucket. ``start_hour`` inclusive, ``end_hour`` exclusive (UTC)."""

    key: str
    label: str
    start_hour: int
    end_hour: int
    killzone: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "startHourUtc": self.start_hour,
            "endHourUtc": self.end_hour,
            "windowUtc": f"{self.start_hour:02d}:00-{self.end_hour:02d}:00 UTC",
            "killzone": self.killzone,
            "description": self.description,
        }


SESSION_BUCKETS: tuple[SessionBucket, ...] = (
    SessionBucket(
        "asia", "Asia", 0, 7,
        "Asian killzone 01:00-05:00 UTC",
        "Tokyo / Sydney session - range building, lower participation.",
    ),
    SessionBucket(
        "london", "London", 7, 12,
        "London killzone 07:00-10:00 UTC",
        "London open - first institutional expansion of the day.",
    ),
    SessionBucket(
        "ny_am", "NY AM", 12, 16,
        "NY AM killzone 12:00-15:00 UTC",
        "New York morning and the London/NY overlap - deepest liquidity.",
    ),
    SessionBucket(
        "ny_pm", "NY PM", 16, 21,
        "London close 15:00-17:00 UTC + NY PM killzone 18:30-21:00 UTC",
        "New York afternoon - London has left, US flow dominates.",
    ),
    SessionBucket(
        "off_hours", "Off-hours", 21, 24,
        "no killzone",
        "Rollover and Asia pre-open - thin, wide spreads.",
    ),
)

SESSION_KEYS: tuple[str, ...] = tuple(b.key for b in SESSION_BUCKETS)
_BUCKET_BY_KEY: dict[str, SessionBucket] = {b.key: b for b in SESSION_BUCKETS}

# hour (0-23) -> bucket key, built once so classification is an index read.
_HOUR_TO_BUCKET: tuple[str, ...] = tuple(
    next(b.key for b in SESSION_BUCKETS if b.start_hour <= h < b.end_hour)
    for h in range(24)
)

HEAT_STRONG = "STRONG"
HEAT_NEUTRAL = "NEUTRAL"
HEAT_WEAK = "WEAK"
HEAT_INSUFFICIENT = "INSUFFICIENT"

ENGINES: tuple[str, ...] = ("A", "B")


# --- Defaults (overridable from CONFIG.LIVE_DASHBOARD.SESSION_HEAT) ----------

DEFAULTS: dict[str, Any] = {
    "ENABLED": True,
    "LOOKBACK_DAYS": 1095,       # 3y; 0 or negative means "all available history"
    "MIN_SAMPLE": 30,            # below this a cell is INSUFFICIENT, never coloured
    "MIN_DELTA_R": 0.02,         # cell must beat its baseline by at least this much R
    "SIGNIFICANCE_Z": 1.0,       # ...and by this many standard errors of the cell mean
    "HEAT_SCALE_R": 0.15,        # delta R that maps to a fully saturated colour
    "CACHE_TTL_SEC": 900,        # aggregate cache lifetime
    "MAX_TRADES": 400000,        # hard read cap so a runaway table cannot stall a request
}

_NUMERIC_INT_KEYS = ("LOOKBACK_DAYS", "MIN_SAMPLE", "CACHE_TTL_SEC", "MAX_TRADES")
_NUMERIC_FLOAT_KEYS = ("MIN_DELTA_R", "SIGNIFICANCE_Z", "HEAT_SCALE_R")


def resolve_settings(config: dict | None) -> dict[str, Any]:
    """Merge ``CONFIG.LIVE_DASHBOARD.SESSION_HEAT`` over the module defaults."""
    out = dict(DEFAULTS)
    block = ((config or {}).get("LIVE_DASHBOARD") or {}).get("SESSION_HEAT") or {}
    if isinstance(block, dict):
        for key in DEFAULTS:
            if block.get(key) is not None:
                out[key] = block[key]
    out["ENABLED"] = bool(out["ENABLED"])
    for key in _NUMERIC_INT_KEYS:
        try:
            out[key] = int(out[key])
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    for key in _NUMERIC_FLOAT_KEYS:
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    if out["HEAT_SCALE_R"] <= 0:
        out["HEAT_SCALE_R"] = DEFAULTS["HEAT_SCALE_R"]
    if out["MIN_SAMPLE"] < 1:
        out["MIN_SAMPLE"] = DEFAULTS["MIN_SAMPLE"]
    return out


# --- Classification helpers --------------------------------------------------


def _hour_from_iso(raw: Any) -> int | None:
    """UTC hour from an ISO timestamp. Fast path for the stored ``...THH:...`` form."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).hour
    text = str(raw).strip()
    if len(text) >= 14 and text[10] in ("T", " ") and text[13] == ":":
        # Trades are stored UTC-normalised ("2023-05-06T08:00:00+00:00"); only
        # take the fast path when the offset really is zero.
        tail = text[19:]
        if tail in ("", "Z", "+00:00", "-00:00", "+0000", "-0000"):
            try:
                return int(text[11:13])
            except ValueError:
                return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).hour


def session_bucket_for_hour(hour: int) -> str:
    """Bucket key for a UTC hour (0-23)."""
    return _HOUR_TO_BUCKET[int(hour) % 24]


def session_bucket_for_timestamp(value: Any) -> str | None:
    """Bucket key for an ISO/datetime timestamp, or ``None`` when unparseable."""
    hour = _hour_from_iso(value)
    return None if hour is None else session_bucket_for_hour(hour)


def current_session(now: datetime | None = None) -> dict[str, Any]:
    """Identify the live session bucket plus how far through it the clock is."""
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)
    bucket = _BUCKET_BY_KEY[session_bucket_for_hour(ts.hour)]
    span_min = (bucket.end_hour - bucket.start_hour) * 60
    elapsed_min = (ts.hour - bucket.start_hour) * 60 + ts.minute
    midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    payload = bucket.to_dict()
    payload.update({
        "nowUtc": ts.isoformat().replace("+00:00", "Z"),
        "minutesElapsed": max(0, elapsed_min),
        "minutesRemaining": max(0, span_min - elapsed_min),
        "endsAtUtc": (midnight + timedelta(hours=bucket.end_hour)).isoformat().replace("+00:00", "Z"),
        "isWeekend": ts.weekday() >= 5,
    })
    return payload


def session_buckets_contract() -> list[dict[str, Any]]:
    """Serialisable bucket definitions - the documented X-axis of the heat chart."""
    return [b.to_dict() for b in SESSION_BUCKETS]


# --- Score-group resolution --------------------------------------------------

_CRYPTO_QUOTE_SUFFIXES = ("/USDT", "/USDC", "/BUSD", "USDT")
_FOREX_CODES = frozenset({
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "ZAR", "MXN",
    "BRL", "INR", "SGD", "HKD", "CNH", "NOK", "SEK", "DKK", "PLN", "HUF", "CZK",
})
_METAL_SYMBOLS = frozenset({"XAU/USD", "XAG/USD", "XPT/USD", "XPD/USD"})
_COMMODITY_WORDS = (
    "OIL", "GAS", "GASOLINE", "COPPER", "ALUMINIUM", "LEAD", "NICKEL", "ZINC",
    "WHEAT", "CORN", "SUGAR", "COFFEE", "COCOA", "COTTON", "CATTLE", "SOYBEANS",
)
_INDEX_SYMBOLS = frozenset({
    "NASDAQ-100", "S&P 500", "DOW JONES", "DAX 40", "UK100", "ASX 200",
    "NIKKEI 225", "HANG SENG", "US2000", "USDX", "EURX", "JPYX", "CHINA A50",
    "SPAIN 35", "FRANCE 40", "ITALY 40",
})


def infer_asset_type(symbol: str) -> str:
    """Best-effort asset type for a backtest symbol when no pair dict is available.

    Fallback only: ``build_session_heat`` prefers the real ``ALL_PAIRS`` entry
    supplied through ``pair_lookup`` so the score group matches live Engine A
    routing exactly.
    """
    upper = str(symbol or "").strip().upper()
    if not upper:
        return "unknown"
    if upper in _INDEX_SYMBOLS:
        return "index"
    if any(upper.endswith(suffix) for suffix in _CRYPTO_QUOTE_SUFFIXES):
        return "crypto"
    if upper in _METAL_SYMBOLS:
        return "commodity"
    if any(word in upper for word in _COMMODITY_WORDS):
        return "commodity"
    if "/" in upper:
        base, _, quote = upper.partition("/")
        if base in _FOREX_CODES and quote in _FOREX_CODES:
            return "forex"
        return "commodity"
    if upper.isalpha() and 1 <= len(upper) <= 5:
        return "stock"
    return "unknown"


def _resolve_score_group(symbol: str, pair_lookup: Callable[[str], dict | None] | None) -> str:
    pair: dict | None = None
    if pair_lookup is not None:
        try:
            pair = pair_lookup(symbol)
        except Exception:  # one bad lookup must not break the whole aggregate
            pair = None
    if not pair:
        pair = {"display": symbol, "type": infer_asset_type(symbol)}
    try:
        from engine_a_groups import resolve_score_group_by_type

        group = resolve_score_group_by_type(pair)
    except Exception:
        group = "unknown"
    return str(group or "unknown")


# --- Aggregation -------------------------------------------------------------


@dataclass
class _Acc:
    """Running accumulator for one (engine, scope, session) cell."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    sum_r: float = 0.0
    sum_r2: float = 0.0
    gross_win_r: float = 0.0
    gross_loss_r: float = 0.0
    symbols: set[str] = field(default_factory=set)

    def add(self, result_r: float, symbol: str) -> None:
        self.trades += 1
        self.sum_r += result_r
        self.sum_r2 += result_r * result_r
        if result_r > 0:
            self.wins += 1
            self.gross_win_r += result_r
        elif result_r < 0:
            self.losses += 1
            self.gross_loss_r += -result_r
        else:
            self.scratches += 1
        self.symbols.add(symbol)


_EMPTY_METRICS: dict[str, Any] = {
    "trades": 0, "wins": 0, "losses": 0, "winRate": None,
    "expectancyR": None, "profitFactor": None, "totalR": None,
    "stdErrR": None, "symbols": 0,
}


def _metrics(acc: _Acc | None) -> dict[str, Any]:
    if acc is None or acc.trades == 0:
        return dict(_EMPTY_METRICS)
    n = acc.trades
    mean = acc.sum_r / n
    variance = max(0.0, (acc.sum_r2 / n) - (mean * mean))
    std_err = math.sqrt(variance / n) if n > 1 else None
    decided = acc.wins + acc.losses
    # Profit factor is undefined without a losing side; report None rather than
    # an infinity that would read as a perfect window.
    profit_factor = round(acc.gross_win_r / acc.gross_loss_r, 4) if acc.gross_loss_r > 0 else None
    return {
        "trades": n,
        "wins": acc.wins,
        "losses": acc.losses,
        "winRate": round(acc.wins / decided, 4) if decided else None,
        "expectancyR": round(mean, 4),
        "profitFactor": profit_factor,
        "totalR": round(acc.sum_r, 3),
        "stdErrR": round(std_err, 4) if std_err is not None else None,
        "symbols": len(acc.symbols),
    }


def _classify_heat(
    cell: dict[str, Any],
    baseline: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[str, float | None, str]:
    """Grade a cell against its scope baseline. Returns (heat, heatScore, reason)."""
    n = int(cell.get("trades") or 0)
    min_sample = settings["MIN_SAMPLE"]
    if n < min_sample:
        return HEAT_INSUFFICIENT, None, f"sample {n} < required {min_sample}"
    cell_exp = cell.get("expectancyR")
    base_exp = baseline.get("expectancyR")
    if cell_exp is None or base_exp is None:
        return HEAT_INSUFFICIENT, None, "expectancy unavailable"

    delta = cell_exp - base_exp
    std_err = cell.get("stdErrR") or 0.0
    needed = max(settings["MIN_DELTA_R"], settings["SIGNIFICANCE_Z"] * std_err)
    score = max(-1.0, min(1.0, delta / settings["HEAT_SCALE_R"]))
    reason = (
        f"{delta:+.3f}R vs baseline {base_exp:+.3f}R "
        f"(needs {needed:.3f}R for a call, n={n})"
    )
    if delta >= needed:
        return HEAT_STRONG, round(score, 3), reason
    if delta <= -needed:
        return HEAT_WEAK, round(score, 3), reason
    return HEAT_NEUTRAL, round(score, 3), reason


def _load_trades(
    db_path: str,
    settings: dict[str, Any],
    *,
    engines: Iterable[str] = ENGINES,
) -> tuple[list[tuple[str, str, str, float]], dict[str, Any]]:
    """Read de-duplicated ``(engine, symbol, entry_time, result_r)`` rows from audit.db."""
    engine_set = {str(e).upper() for e in engines}
    meta: dict[str, Any] = {
        "runsConsidered": 0, "runsUsed": 0, "rowsRead": 0, "rowsSkipped": 0,
        "cutoffIso": None,
    }

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        runs = con.execute(
            "SELECT id, created_at, symbol, engine, style, contract_version "
            "FROM backtest_runs_v3 ORDER BY created_at"
        ).fetchall()
        meta["runsConsidered"] = len(runs)

        # De-duplicate: a re-run of the same scope replaces the earlier run, so a
        # re-run symbol cannot contribute its trades twice to one session cell.
        newest: dict[tuple, sqlite3.Row] = {}
        for row in runs:
            engine = str(row["engine"] or "").upper()
            if engine not in engine_set:
                continue
            key = (engine, row["symbol"], row["style"], row["contract_version"])
            prev = newest.get(key)
            if prev is None or (str(row["created_at"] or ""), int(row["id"])) >= (
                str(prev["created_at"] or ""), int(prev["id"])
            ):
                newest[key] = row
        meta["runsUsed"] = len(newest)
        if not newest:
            return [], meta

        run_meta = {
            int(r["id"]): (str(r["engine"] or "").upper(), str(r["symbol"] or ""))
            for r in newest.values()
        }
        run_ids = list(run_meta)

        cutoff_iso: str | None = None
        if settings["LOOKBACK_DAYS"] > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings["LOOKBACK_DAYS"])
            cutoff_iso = cutoff.isoformat()
        meta["cutoffIso"] = cutoff_iso

        out: list[tuple[str, str, str, float]] = []
        budget = int(settings["MAX_TRADES"])
        chunk = 400  # stay well under SQLITE_MAX_VARIABLE_NUMBER
        for start in range(0, len(run_ids), chunk):
            if budget <= 0:
                break
            ids = run_ids[start:start + chunk]
            placeholders = ",".join("?" * len(ids))
            sql = (
                f"SELECT run_id, entry_time, result_r FROM backtest_trades_v3 "
                f"WHERE run_id IN ({placeholders}) AND result_r IS NOT NULL"
            )
            params: list[Any] = list(ids)
            if cutoff_iso:
                sql += " AND entry_time >= ?"
                params.append(cutoff_iso)
            sql += " LIMIT ?"
            params.append(budget)
            for run_id, entry_time, result_r in con.execute(sql, params):
                meta["rowsRead"] += 1
                budget -= 1
                engine, symbol = run_meta.get(int(run_id), ("", ""))
                if not engine:
                    meta["rowsSkipped"] += 1
                    continue
                try:
                    r_val = float(result_r)
                except (TypeError, ValueError):
                    meta["rowsSkipped"] += 1
                    continue
                if math.isnan(r_val) or math.isinf(r_val):
                    meta["rowsSkipped"] += 1
                    continue
                out.append((engine, symbol, str(entry_time), r_val))
    return out, meta


def build_session_heat(
    db_path: str,
    *,
    settings: dict[str, Any] | None = None,
    config: dict | None = None,
    pair_lookup: Callable[[str], dict | None] | None = None,
    engines: Iterable[str] = ENGINES,
) -> dict[str, Any]:
    """Aggregate recorded trades into the engine x session heat matrix.

    Always returns a serialisable payload. A missing table, an empty table, or a
    table too thin to grade are all normal outcomes reported through ``status``
    (``ERROR`` / ``NO_DATA`` / ``INSUFFICIENT`` / ``OK``) rather than raised.
    """
    cfg = settings or resolve_settings(config)
    started = time.perf_counter()

    try:
        rows, load_meta = _load_trades(db_path, cfg, engines=engines)
        load_error = None
    except sqlite3.Error as exc:
        rows, load_meta, load_error = [], {}, str(exc)
        log.warning("[SESSION-HEAT] trade load failed: %s", exc)

    # engine -> scope -> session -> accumulator ("__ALL__" scope = whole engine)
    acc: dict[str, dict[str, dict[str, _Acc]]] = {}
    engine_totals: dict[str, _Acc] = {}
    scope_totals: dict[tuple[str, str], _Acc] = {}
    group_cache: dict[str, str] = {}
    unbucketed = 0
    first_iso: str | None = None
    last_iso: str | None = None

    for engine, symbol, entry_time, result_r in rows:
        bucket = session_bucket_for_timestamp(entry_time)
        if bucket is None:
            unbucketed += 1
            continue
        if first_iso is None or entry_time < first_iso:
            first_iso = entry_time
        if last_iso is None or entry_time > last_iso:
            last_iso = entry_time

        group = group_cache.get(symbol)
        if group is None:
            group = _resolve_score_group(symbol, pair_lookup)
            group_cache[symbol] = group

        by_scope = acc.setdefault(engine, {})
        for scope in ("__ALL__", group):
            by_scope.setdefault(scope, {}).setdefault(bucket, _Acc()).add(result_r, symbol)
            scope_totals.setdefault((engine, scope), _Acc()).add(result_r, symbol)
        engine_totals.setdefault(engine, _Acc()).add(result_r, symbol)

    matrix: list[dict[str, Any]] = []
    for engine in sorted(acc):
        engine_baseline = _metrics(engine_totals.get(engine))
        for scope in sorted(acc[engine], key=lambda s: (s != "__ALL__", s)):
            scope_metrics = _metrics(scope_totals.get((engine, scope)))
            cells: dict[str, Any] = {}
            for key in SESSION_KEYS:
                cell = _metrics(acc[engine][scope].get(key))
                # Cells are graded against their own scope's baseline so a weak
                # score group is not painted uniformly cold, and vice versa.
                heat, score, reason = _classify_heat(cell, scope_metrics, cfg)
                cell.update({"session": key, "heat": heat, "heatScore": score, "heatReason": reason})
                cells[key] = cell
            ranked = [
                c for c in cells.values()
                if c["heat"] != HEAT_INSUFFICIENT and c["expectancyR"] is not None
            ]
            ranked.sort(key=lambda c: c["expectancyR"], reverse=True)
            matrix.append({
                "engine": engine,
                "scope": "engine" if scope == "__ALL__" else "scoreGroup",
                "scoreGroup": None if scope == "__ALL__" else scope,
                "baseline": scope_metrics,
                "engineBaseline": engine_baseline,
                "cells": cells,
                "bestSession": ranked[0]["session"] if ranked else None,
                "worstSession": ranked[-1]["session"] if ranked else None,
                "sufficientCells": sum(1 for c in cells.values() if c["heat"] != HEAT_INSUFFICIENT),
            })

    total_trades = sum(a.trades for a in engine_totals.values())
    if load_error:
        status = "ERROR"
    elif total_trades == 0:
        status = "NO_DATA"
    elif not any(m["sufficientCells"] for m in matrix):
        status = "INSUFFICIENT"
    else:
        status = "OK"

    return {
        "version": SESSION_HEAT_VERSION,
        "status": status,
        "error": load_error,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "buildMs": round((time.perf_counter() - started) * 1000.0, 1),
        "source": SESSION_HEAT_SOURCE,
        "settings": {
            "lookbackDays": cfg["LOOKBACK_DAYS"],
            "minSample": cfg["MIN_SAMPLE"],
            "minDeltaR": cfg["MIN_DELTA_R"],
            "significanceZ": cfg["SIGNIFICANCE_Z"],
            "heatScaleR": cfg["HEAT_SCALE_R"],
        },
        "coverage": {
            "trades": total_trades,
            "engines": sorted(engine_totals),
            "scoreGroups": sorted({m["scoreGroup"] for m in matrix if m["scoreGroup"]}),
            "firstEntryIso": first_iso,
            "lastEntryIso": last_iso,
            "unbucketedRows": unbucketed,
            **load_meta,
        },
        "sessions": session_buckets_contract(),
        "matrix": matrix,
    }


# --- Lookup helpers over a built matrix --------------------------------------


def _matrix_index(heat: dict[str, Any]) -> dict[tuple[str, str | None], dict[str, Any]]:
    return {(m.get("engine"), m.get("scoreGroup")): m for m in (heat.get("matrix") or [])}


def cell_for(
    heat: dict[str, Any],
    engine: str,
    session_key: str,
    *,
    score_group: str | None = None,
) -> dict[str, Any] | None:
    """One heat cell, preferring the score-group row and falling back to the engine row.

    The result carries ``scope``/``scopeLabel`` so a caller can always tell the
    user *what population* the number was measured over.
    """
    index = _matrix_index(heat)
    candidates: list[tuple[tuple[str, str | None], str]] = []
    if score_group:
        candidates.append(((engine, score_group), "scoreGroup"))
    candidates.append(((engine, None), "engine"))

    for key, scope in candidates:
        row = index.get(key)
        if row is None:
            continue
        cell = (row.get("cells") or {}).get(session_key)
        if cell is None:
            continue
        if scope == "scoreGroup" and cell.get("heat") == HEAT_INSUFFICIENT:
            continue  # thin score group -> fall through to the engine-wide read
        out = dict(cell)
        out["scope"] = scope
        out["scopeLabel"] = row.get("scoreGroup") or f"Engine {engine} (all groups)"
        out["scoreGroup"] = row.get("scoreGroup")
        out["baselineExpectancyR"] = (row.get("baseline") or {}).get("expectancyR")
        out["bestSession"] = row.get("bestSession")
        out["worstSession"] = row.get("worstSession")
        return out
    return None


_HEAT_TONE = {
    HEAT_STRONG: "strong",
    HEAT_NEUTRAL: "neutral",
    HEAT_WEAK: "weak",
    HEAT_INSUFFICIENT: "unknown",
}
_HEAT_WORD = {
    HEAT_STRONG: "Strong",
    HEAT_NEUTRAL: "Neutral",
    HEAT_WEAK: "Weak",
    HEAT_INSUFFICIENT: "No data",
}


def card_indicator(
    heat: dict[str, Any],
    engine: str,
    session_key: str,
    *,
    score_group: str | None = None,
) -> dict[str, Any]:
    """Compact per-card cue, e.g. ``{"label": "London - Strong", "tone": "strong"}``."""
    bucket = _BUCKET_BY_KEY.get(session_key)
    session_label = bucket.label if bucket else session_key
    cell = cell_for(heat, engine, session_key, score_group=score_group)

    if cell is None:
        return {
            "engine": engine,
            "session": session_key,
            "sessionLabel": session_label,
            "heat": HEAT_INSUFFICIENT,
            "tone": "unknown",
            "label": f"{session_label} · No data",
            "trades": 0,
            "expectancyR": None,
            "profitFactor": None,
            "winRate": None,
            "scope": None,
            "scopeLabel": None,
            "scoreGroup": None,
            "heatScore": None,
            "bestSession": None,
            "worstSession": None,
            "tooltip": f"No recorded {SESSION_HEAT_SOURCE} outcomes for Engine {engine}.",
        }

    heat_label = str(cell.get("heat") or HEAT_INSUFFICIENT)
    exp = cell.get("expectancyR")
    if heat_label == HEAT_INSUFFICIENT or exp is None:
        tooltip = (
            f"Engine {engine} · {cell.get('scopeLabel')} · {session_label}: "
            f"{cell.get('trades') or 0} trades - {cell.get('heatReason') or 'insufficient sample'}."
        )
    else:
        pf = cell.get("profitFactor")
        wr = cell.get("winRate")
        parts = [
            f"Engine {engine} · {cell.get('scopeLabel')} · {session_label}: "
            f"{cell.get('trades')} trades",
            f"expectancy {exp:+.3f}R",
            f"PF {pf:.2f}" if pf is not None else "PF n/a",
        ]
        if wr is not None:
            parts.append(f"WR {wr * 100:.1f}%")
        tooltip = ", ".join(parts) + f". {cell.get('heatReason') or ''}".rstrip()

    return {
        "engine": engine,
        "session": session_key,
        "sessionLabel": session_label,
        "heat": heat_label,
        "tone": _HEAT_TONE.get(heat_label, "unknown"),
        "label": f"{session_label} · {_HEAT_WORD.get(heat_label, heat_label)}",
        "trades": cell.get("trades"),
        "expectancyR": exp,
        "profitFactor": cell.get("profitFactor"),
        "winRate": cell.get("winRate"),
        "scope": cell.get("scope"),
        "scopeLabel": cell.get("scopeLabel"),
        "scoreGroup": cell.get("scoreGroup"),
        "heatScore": cell.get("heatScore"),
        "bestSession": cell.get("bestSession"),
        "worstSession": cell.get("worstSession"),
        "tooltip": tooltip,
    }


# --- Non-blocking cache ------------------------------------------------------
#
# The live snapshot must never pay for an aggregate build. ``get_session_heat``
# with ``block=False`` returns whatever is already in hand (possibly stale, or
# ``None`` on a cold start) and rebuilds on a background daemon thread. Only the
# dedicated Session Heat endpoint blocks on a build, and only when asked to.

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, dict[str, Any]] = {}
_BUILDING: set[tuple] = set()


def _cache_key(db_path: str, cfg: dict[str, Any]) -> tuple:
    return (
        str(db_path),
        cfg["LOOKBACK_DAYS"], cfg["MIN_SAMPLE"], cfg["MIN_DELTA_R"],
        cfg["SIGNIFICANCE_Z"], cfg["HEAT_SCALE_R"], cfg["MAX_TRADES"],
    )


def _store(key: tuple, payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = {"builtAt": time.time(), "payload": payload}
        _BUILDING.discard(key)


def _spawn_build(
    key: tuple,
    db_path: str,
    cfg: dict[str, Any],
    pair_lookup: Callable[[str], dict | None] | None,
) -> bool:
    """Start one background build for *key*. Returns False if one is already running."""
    with _CACHE_LOCK:
        if key in _BUILDING:
            return False
        _BUILDING.add(key)

    def _run() -> None:
        try:
            payload = build_session_heat(db_path, settings=cfg, pair_lookup=pair_lookup)
            _store(key, payload)
        except Exception as exc:  # a background failure must never kill the thread pool
            log.warning("[SESSION-HEAT] background build failed: %s", exc)
            with _CACHE_LOCK:
                _BUILDING.discard(key)

    threading.Thread(target=_run, name="session-heat-build", daemon=True).start()
    return True


def get_session_heat(
    db_path: str,
    *,
    config: dict | None = None,
    settings: dict[str, Any] | None = None,
    pair_lookup: Callable[[str], dict | None] | None = None,
    block: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(payload_or_None, cache_meta)``.

    ``block=False`` (the live-snapshot path) never waits: a fresh entry is
    returned as-is, a stale entry is returned while a rebuild runs in the
    background, and a cold cache returns ``None`` with ``state="WARMING"``.
    ``block=True`` (the dedicated endpoint) builds inline when nothing fresh
    is cached.
    """
    cfg = settings or resolve_settings(config)
    key = _cache_key(db_path, cfg)
    ttl = max(1, cfg["CACHE_TTL_SEC"])
    now = time.time()

    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    age = (now - entry["builtAt"]) if entry else None

    if entry and age is not None and age < ttl:
        return entry["payload"], {"state": "FRESH", "ageSec": round(age, 1), "ttlSec": ttl}

    if block:
        payload = build_session_heat(db_path, settings=cfg, pair_lookup=pair_lookup)
        _store(key, payload)
        return payload, {"state": "BUILT", "ageSec": 0.0, "ttlSec": ttl}

    started = _spawn_build(key, db_path, cfg, pair_lookup)
    if entry:
        return entry["payload"], {
            "state": "STALE",
            "ageSec": round(age or 0.0, 1),
            "ttlSec": ttl,
            "rebuilding": True,
        }
    return None, {
        "state": "WARMING",
        "ageSec": None,
        "ttlSec": ttl,
        "rebuilding": True,
        "buildStarted": started,
    }


def reset_session_heat_cache() -> None:
    """Drop every cached aggregate. Used by tests and by config reloads."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _BUILDING.clear()
