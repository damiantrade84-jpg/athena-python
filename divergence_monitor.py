"""divergence_monitor.py — Live vs Backtest scoring divergence detector.

Runs the backtest scoring path on the SAME candle data that the live scan used,
then compares scores, direction, and regime. Any divergence indicates a code path
mismatch — exactly the class of bug found in the forex BT audit.

Usage:
    from divergence_monitor import check_divergence, divergence_report

    # After a live scan produces a signal:
    div = check_divergence(pair, live_result, d1_candles, h4_candles, h1_candles)
    if div["diverged"]:
        log.warning(f"[DIVERGENCE] {pair}: {div['summary']}")

    # Periodic report:
    report = divergence_report(lookback_hours=24)
"""

import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("sentinel")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
_db_lock = threading.Lock()

# Score divergence thresholds
_SCORE_TOLERANCE = 0.05  # 5% relative difference is noise
_SCORE_WARNING = 0.15    # 15% relative difference is a warning
_SCORE_CRITICAL = 0.30   # 30% relative difference is a critical mismatch


def _init_tables():
    """Create divergence_log table if it doesn't exist."""
    try:
        with sqlite3.connect(_DB_PATH, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS divergence_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    live_score REAL,
                    bt_score REAL,
                    score_diff REAL,
                    live_direction TEXT,
                    bt_direction TEXT,
                    direction_match INTEGER,
                    live_regime TEXT,
                    bt_regime TEXT,
                    regime_match INTEGER,
                    severity TEXT,
                    summary TEXT
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_div_pair_ts
                ON divergence_log(pair, ts)
            """)
            con.commit()
    except Exception as e:
        log.error(f"[DIVERGENCE] Table init failed: {e}")


_init_tables()


def check_divergence(
    pair: dict,
    live_result: dict,
    d1_candles: list,
    h4_candles: list,
    h1_candles: list,
    funding_rate: float | None = None,
    btc_bias: str = "neutral",
    oi_data: dict | None = None,
    oi_context: dict | None = None,
    macro_context: dict | None = None,
    intermarket_context: dict | None = None,
    bar_time: str | None = None,
) -> dict:
    """Compare live scan result against backtest scoring path on same data.

    Args:
        pair: Pair dict with display, type, symbol, score_group
        live_result: The result dict from the live Engine A scan
        d1_candles, h4_candles, h1_candles: Same candle data used by live scan
        funding_rate: Same funding rate used by live scan
        btc_bias: Same BTC bias used by live scan
        oi_data, oi_context, macro_context, intermarket_context: Optional
            contextual inputs from the live scan so the replay path can match
            the current Engine A factor route more closely
        bar_time: Optional explicit bar timestamp. Falls back to the last H4 bar.

    Returns:
        dict with diverged (bool), severity, live_*, bt_*, diffs, and summary
    """
    asset_type = pair.get("type", "stock")
    display = pair.get("display", "")

    # Extract live values
    live_score = live_result.get("score", live_result.get("final_score", 0))
    live_dir = live_result.get("direction", "UNKNOWN")
    live_regime_raw = live_result.get("regime")
    if isinstance(live_regime_raw, dict):
        live_regime = live_regime_raw.get("label", "UNKNOWN")
    elif isinstance(live_regime_raw, str):
        live_regime = live_regime_raw
    else:
        live_regime = "UNKNOWN"

    # Re-run the same Engine A factor path on the same data.
    bt_score = 0.0
    bt_dir = "UNKNOWN"
    bt_regime = "UNKNOWN"

    try:
        from scoring import calc_confluence
        from indicators import (
            calc_indicators_with_normalized,
            calc_stochastic,
            calc_sma,
        )

        d1i = calc_indicators_with_normalized(d1_candles, asset_type)
        h4i = calc_indicators_with_normalized(h4_candles, asset_type)
        h1i = calc_indicators_with_normalized(h1_candles, asset_type)

        vols = [c.get("vol", 0) for c in h1_candles]
        vsma = calc_sma(vols, 20)
        vr = vols[-1] / vsma[-1] if vsma and vsma[-1] and vsma[-1] > 0 else 1.0

        stoch = calc_stochastic(h4_candles, 5, 3, 3)
        replay_bar_time = bar_time or (h4_candles[-1].get("time") if h4_candles else None)

        bt_result = calc_confluence(
            d1i,
            h4i,
            h1i,
            vr,
            stoch,
            pair,
            btc_bias,
            d1_candles=d1_candles,
            h4_candles=h4_candles,
            h1_candles=h1_candles,
            funding_rate=funding_rate,
            bar_time=replay_bar_time,
            oi_data=oi_data,
            oi_context=oi_context,
            macro_context=macro_context,
            intermarket_context=intermarket_context,
        )
        bt_score = bt_result.get("score", 0)
        bt_dir = bt_result.get("direction", "UNKNOWN")
        regime_raw = bt_result.get("regime")
        if isinstance(regime_raw, dict):
            bt_regime = regime_raw.get("label", "UNKNOWN")
        elif isinstance(regime_raw, str):
            bt_regime = regime_raw
    except Exception as e:
        log.debug(f"[DIVERGENCE] BT scoring failed for {display}: {e}")
        return {
            "diverged": False,
            "severity": "error",
            "summary": f"BT scoring failed: {e}",
            "error": True,
        }

    # Compare
    dir_match = live_dir == bt_dir
    regime_match = live_regime.upper() == bt_regime.upper()

    max_score = max(abs(live_score), abs(bt_score), 0.01)
    score_diff = abs(live_score - bt_score)
    score_diff_pct = score_diff / max_score

    # Determine severity
    issues = []
    if not dir_match:
        issues.append(f"DIRECTION FLIP: live={live_dir} bt={bt_dir}")
    if score_diff_pct > _SCORE_CRITICAL:
        issues.append(f"SCORE CRITICAL: live={live_score:.3f} bt={bt_score:.3f} ({score_diff_pct:.0%})")
    elif score_diff_pct > _SCORE_WARNING:
        issues.append(f"SCORE WARNING: live={live_score:.3f} bt={bt_score:.3f} ({score_diff_pct:.0%})")
    if not regime_match:
        issues.append(f"REGIME: live={live_regime} bt={bt_regime}")

    if not dir_match:
        severity = "critical"
    elif score_diff_pct > _SCORE_CRITICAL:
        severity = "critical"
    elif score_diff_pct > _SCORE_WARNING or not regime_match:
        severity = "warning"
    elif score_diff_pct > _SCORE_TOLERANCE:
        severity = "minor"
    else:
        severity = "ok"

    diverged = severity in ("critical", "warning")
    summary = "; ".join(issues) if issues else "OK — live and BT agree"

    # Log to database
    try:
        ts = datetime.now(timezone.utc).isoformat()
        with _db_lock:
            with sqlite3.connect(_DB_PATH, timeout=15.0) as con:
                con.execute("""
                    INSERT INTO divergence_log (
                        ts, pair, asset_type, live_score, bt_score, score_diff,
                        live_direction, bt_direction, direction_match,
                        live_regime, bt_regime, regime_match, severity, summary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ts, display, asset_type, live_score, bt_score, score_diff,
                    live_dir, bt_dir, int(dir_match),
                    live_regime, bt_regime, int(regime_match), severity, summary,
                ))
                con.commit()
    except Exception as e:
        log.debug(f"[DIVERGENCE] DB write failed: {e}")

    if diverged:
        log.warning(f"[DIVERGENCE] {display}: {summary}")

    return {
        "diverged": diverged,
        "severity": severity,
        "pair": display,
        "live_score": round(live_score, 4),
        "bt_score": round(bt_score, 4),
        "score_diff": round(score_diff, 4),
        "score_diff_pct": round(score_diff_pct, 4),
        "live_direction": live_dir,
        "bt_direction": bt_dir,
        "direction_match": dir_match,
        "live_regime": live_regime,
        "bt_regime": bt_regime,
        "regime_match": regime_match,
        "summary": summary,
    }


def divergence_report(lookback_hours: int = 24, min_severity: str = "warning") -> dict:
    """Summarize recent divergence events.

    Returns:
        dict with total_checks, divergence_count, critical_count, warning_count,
        pairs_affected, and recent_events list.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        with _db_lock:
            with sqlite3.connect(_DB_PATH, timeout=15.0) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("""
                    SELECT * FROM divergence_log
                    WHERE ts >= ?
                    ORDER BY ts DESC
                """, (cutoff,)).fetchall()

        total = len(rows)
        critical = sum(1 for r in rows if r["severity"] == "critical")
        warning = sum(1 for r in rows if r["severity"] == "warning")
        diverged = sum(1 for r in rows if r["severity"] in ("critical", "warning"))
        pairs = sorted(set(r["pair"] for r in rows if r["severity"] in ("critical", "warning")))

        # Direction flip rate
        dir_checks = sum(1 for r in rows if r["live_direction"] != "UNKNOWN")
        dir_flips = sum(1 for r in rows if not r["direction_match"] and r["live_direction"] != "UNKNOWN")
        dir_flip_rate = dir_flips / dir_checks if dir_checks > 0 else 0

        # Recent events (most severe first)
        severity_order = {"critical": 0, "warning": 1, "minor": 2, "ok": 3}
        filtered = [dict(r) for r in rows if severity_order.get(r["severity"], 3) <= severity_order.get(min_severity, 1)]
        filtered.sort(key=lambda x: severity_order.get(x.get("severity", "ok"), 3))

        return {
            "lookback_hours": lookback_hours,
            "total_checks": total,
            "divergence_count": diverged,
            "critical_count": critical,
            "warning_count": warning,
            "direction_flip_rate": round(dir_flip_rate, 4),
            "pairs_affected": pairs,
            "recent_events": filtered[:20],  # cap at 20
        }
    except Exception as e:
        log.error(f"[DIVERGENCE] report failed: {e}")
        return {"error": str(e), "total_checks": 0}
