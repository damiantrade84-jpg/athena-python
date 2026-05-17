"""Read-only pair-specific context for advisory AI surfaces.

This module does not fetch paid data or mutate ATHENA state. It only reads
local audit/learning data and optional existing local helpers when available.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit_db_path() -> str:
    return os.environ.get("ATHENA_AUDIT_DB") or os.path.join(os.path.dirname(__file__), "audit.db")


def _stable(symbol: str, asset_type: str | None, lookback_days: int, warnings: list[str], missing: list[str]) -> dict[str, Any]:
    freshness = "unavailable" if warnings else "partial"
    return {
        "symbol": symbol,
        "asset_type": asset_type or "unknown",
        "lookback_days": int(lookback_days),
        "summary": "Pair-specific context is unavailable from local sources.",
        "recent_news": [],
        "recent_trade_outcomes": [],
        "volatility_state": "unknown",
        "positioning": {},
        "funding": {},
        "freshness_status": freshness,
        "missing_fields": missing,
        "warnings": warnings,
    }


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _recent_learning_rows(db_path: str, symbol: str, limit: int = 5) -> tuple[list[dict[str, Any]], str | None]:
    if not db_path or not os.path.exists(db_path):
        return [], "Audit DB unavailable for recent trade outcomes."
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            if _table_exists(con, "learning_log"):
                cols = _columns(con, "learning_log")
                if {"ts", "pair", "direction", "r_multiple", "win"}.issubset(cols):
                    rows = con.execute(
                        "SELECT * FROM learning_log WHERE pair=? ORDER BY ts DESC LIMIT ?",
                        (symbol, int(limit)),
                    ).fetchall()
                    return [
                        {
                            "ts": row["ts"],
                            "pair": row["pair"],
                            "direction": row["direction"],
                            "r_multiple": row["r_multiple"],
                            "win": bool(row["win"]),
                            "engine": row["engine"] if "engine" in row.keys() else None,
                        }
                        for row in rows
                    ], None
            if _table_exists(con, "audit_log"):
                cols = _columns(con, "audit_log")
                pair_col = "pair" if "pair" in cols else None
                if pair_col:
                    rows = con.execute(
                        f"SELECT * FROM audit_log WHERE {pair_col}=? ORDER BY rowid DESC LIMIT ?",
                        (symbol, int(limit)),
                    ).fetchall()
                    out = []
                    for row in rows:
                        out.append({
                            "pair": row[pair_col],
                            "direction": row["direction"] if "direction" in row.keys() else None,
                            "entry_price": row["entry_price"] if "entry_price" in row.keys() else None,
                            "exit_price": row["exit_price"] if "exit_price" in row.keys() else None,
                            "r_multiple": row["r_multiple"] if "r_multiple" in row.keys() else None,
                        })
                    return out, None
            return [], "No compatible learning_log or audit_log outcome table found."
    except Exception as exc:
        return [], f"Recent trade outcome lookup failed: {exc}"


def _cot_context(symbol: str) -> tuple[dict[str, Any], str | None]:
    try:
        from cot_feed import get_cot_net, get_cot_z

        detail = get_cot_net(symbol)
        z = get_cot_z(symbol)
        if detail is None:
            return {}, "COT has no configured formula for this symbol."
        return {"z": z, "detail": detail}, None
    except Exception as exc:
        return {}, f"COT lookup unavailable: {exc}"


def _funding_context(symbol: str, asset_type: str | None) -> tuple[dict[str, Any], str | None]:
    if str(asset_type or "").lower() != "crypto":
        return {}, None
    market_symbol = symbol.replace("/", "").replace("USDT", "USDT").upper()
    try:
        from data_feeds import _fetch_bybit_funding_rate, _fetch_funding_rate

        bybit = _fetch_bybit_funding_rate(market_symbol)
        binance = _fetch_funding_rate(market_symbol)
        return {"bybit": bybit, "binance": binance}, None
    except Exception as exc:
        return {}, f"Funding lookup unavailable: {exc}"


def build_pair_context(symbol: str, asset_type: str | None = None, lookback_days: int = 5) -> dict[str, Any]:
    """Return stable, read-only context for one pair."""
    symbol = str(symbol or "").strip() or "unknown"
    warnings: list[str] = []
    missing: list[str] = []
    if symbol == "unknown":
        missing.append("symbol")
    if not asset_type:
        missing.append("asset_type")

    trades, trade_warning = _recent_learning_rows(_audit_db_path(), symbol)
    if trade_warning:
        warnings.append(trade_warning)

    positioning, cot_warning = _cot_context(symbol)
    if cot_warning:
        warnings.append(cot_warning)

    funding, funding_warning = _funding_context(symbol, asset_type)
    if funding_warning:
        warnings.append(funding_warning)

    freshness = "partial" if (trades or positioning or funding) else "unavailable"
    summary_bits = []
    if trades:
        summary_bits.append(f"{len(trades)} recent local trade outcome(s) found")
    if positioning:
        summary_bits.append("COT positioning available")
    if funding:
        summary_bits.append("funding context available")
    summary = "; ".join(summary_bits) if summary_bits else "No local pair-specific evidence available."
    return {
        "symbol": symbol,
        "asset_type": asset_type or "unknown",
        "lookback_days": int(lookback_days),
        "summary": summary,
        "recent_news": [],
        "recent_trade_outcomes": trades,
        "volatility_state": "unknown",
        "positioning": positioning,
        "funding": funding,
        "freshness_status": freshness,
        "missing_fields": missing,
        "warnings": warnings,
        "generated_at": _now_iso(),
    }
