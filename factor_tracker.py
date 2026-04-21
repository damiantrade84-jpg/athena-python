"""factor_tracker.py — Per-factor predictive power tracking for Athena.

Records factor scores at signal time, then links to trade outcomes via learning_log.
Computes rolling per-factor win rate, contribution to winning vs losing trades,
and information coefficient (IC) over configurable lookback windows.

Usage:
    from factor_tracker import record_factor_snapshot, factor_report

    # After compute_factor_scores returns:
    record_factor_snapshot(pair, direction, factor_result, score, asset_type)

    # To get attribution report:
    report = factor_report(asset_type="crypto", lookback_days=90)
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("sentinel")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
_db_lock = threading.Lock()


def _init_tables():
    """Create factor_snapshots table if it doesn't exist."""
    try:
        with sqlite3.connect(_DB_PATH, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS factor_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    final_score REAL,
                    directional_score REAL,
                    nondirectional_score REAL,
                    regime TEXT,
                    -- Individual factor scores (NULL = factor was disabled/unavailable)
                    f_trend REAL,
                    f_momentum REAL,
                    f_derivatives REAL,
                    f_microstructure REAL,
                    f_carry REAL,
                    f_trend_strength REAL,
                    f_volatility REAL,
                    f_volume REAL,
                    f_structure REAL,
                    -- Weights applied
                    w_trend REAL,
                    w_momentum REAL,
                    w_derivatives REAL,
                    w_microstructure REAL,
                    w_carry REAL,
                    w_trend_strength REAL,
                    w_volatility REAL,
                    w_volume REAL,
                    w_structure REAL,
                    -- Directional confidence
                    dir_confidence_mult REAL,
                    trend_coherence_ratio REAL,
                    optional_coverage REAL,
                    -- Outcome (linked later via update_outcome)
                    outcome TEXT,          -- TP1/TP2/SL/BE/OPEN/TIMEOUT
                    r_multiple REAL,
                    outcome_ts TEXT
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_fs_pair_ts
                ON factor_snapshots(pair, ts)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_fs_asset_ts
                ON factor_snapshots(asset_type, ts)
            """)
            _cols = {
                row[1]
                for row in con.execute("PRAGMA table_info(factor_snapshots)").fetchall()
            }
            if "unweighted_dir_sum" not in _cols:
                con.execute(
                    "ALTER TABLE factor_snapshots ADD COLUMN unweighted_dir_sum REAL"
                )
            con.commit()
    except Exception as e:
        log.error(f"[FACTOR_TRACKER] Table init failed: {e}")


_init_tables()


def record_factor_snapshot(
    pair: str,
    direction: str,
    factor_result: dict,
    final_score: float,
    asset_type: str,
) -> Optional[int]:
    """Record factor scores at signal time. Returns row ID or None on failure.

    Call this after compute_factor_scores() returns, for every signal that
    clears the minimum score threshold (no point tracking noise).
    """
    try:
        ts = datetime.now(timezone.utc).isoformat()
        fs = factor_result.get("factor_scores", {})
        ws = factor_result.get("weights", {})
        tc = factor_result.get("trend_coherence", {})
        dir_out = direction if direction is not None else "NEUTRAL"
        uds = factor_result.get("unweighted_directional_sum")

        with _db_lock:
            con = sqlite3.connect(_DB_PATH, timeout=15.0)
            cur = con.execute("""
                INSERT INTO factor_snapshots (
                    ts, pair, asset_type, direction, final_score,
                    directional_score, nondirectional_score, regime,
                    f_trend, f_momentum, f_derivatives, f_microstructure, f_carry,
                    f_trend_strength, f_volatility, f_volume, f_structure,
                    w_trend, w_momentum, w_derivatives, w_microstructure, w_carry,
                    w_trend_strength, w_volatility, w_volume, w_structure,
                    dir_confidence_mult, trend_coherence_ratio, optional_coverage,
                    unweighted_dir_sum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ts, pair, asset_type, dir_out, final_score,
                factor_result.get("directional_score"),
                factor_result.get("nondirectional_score"),
                factor_result.get("regime"),
                fs.get("trend"), fs.get("momentum"), fs.get("derivatives"),
                fs.get("microstructure"), fs.get("carry"),
                fs.get("trend_strength"), fs.get("volatility"),
                fs.get("volume"), fs.get("structure"),
                ws.get("trend"), ws.get("momentum"), ws.get("derivatives"),
                ws.get("microstructure"), ws.get("carry"),
                ws.get("trend_strength"), ws.get("volatility"),
                ws.get("volume"), ws.get("structure"),
                factor_result.get("directional_confidence_multiplier"),
                tc.get("coherence_ratio"),
                factor_result.get("optional_factor_coverage"),
                uds,
            ))
            con.commit()
            row_id = cur.lastrowid
            con.close()
            return row_id
    except Exception as e:
        log.debug(f"[FACTOR_TRACKER] record failed: {e}")
        return None


def update_outcome(
    pair: str,
    signal_ts: str,
    outcome: str,
    r_multiple: float,
) -> bool:
    """Link a trade outcome to the nearest factor snapshot.

    Call this when a trade closes (from outcome monitor or learning_log).
    Matches by pair + closest timestamp within 10 minutes.
    """
    try:
        outcome_ts = datetime.now(timezone.utc).isoformat()
        with _db_lock:
            con = sqlite3.connect(_DB_PATH, timeout=15.0)
            # Find the closest snapshot for this pair within 10 minutes of signal_ts
            row = con.execute("""
                SELECT id FROM factor_snapshots
                WHERE pair = ? AND outcome IS NULL
                AND abs(julianday(ts) - julianday(?)) < (10.0 / 1440.0)
                ORDER BY abs(julianday(ts) - julianday(?))
                LIMIT 1
            """, (pair, signal_ts, signal_ts)).fetchone()
            if row:
                con.execute("""
                    UPDATE factor_snapshots
                    SET outcome = ?, r_multiple = ?, outcome_ts = ?
                    WHERE id = ?
                """, (outcome, r_multiple, outcome_ts, row[0]))
                con.commit()
                con.close()
                return True
            con.close()
            return False
    except Exception as e:
        log.debug(f"[FACTOR_TRACKER] outcome update failed: {e}")
        return False


def factor_report(
    asset_type: str | None = None,
    lookback_days: int = 90,
    min_samples: int = 20,
) -> dict:
    """Compute per-factor predictive power report.

    Returns dict with per-factor metrics:
    - win_rate_when_active: % of winning trades where this factor was active (non-None)
    - avg_score_winners: mean factor score on winning trades
    - avg_score_losers: mean factor score on losing trades
    - information_coefficient: correlation between factor score and R-multiple
    - contribution_spread: avg_score_winners - avg_score_losers (higher = more predictive)
    - sample_count: number of trades with this factor active + outcome known
    - recommendation: "keep", "reduce", "investigate", or "insufficient_data"
    """
    try:
        with _db_lock:
            con = sqlite3.connect(_DB_PATH, timeout=15.0)
            con.row_factory = sqlite3.Row
            cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
            query = """
                SELECT * FROM factor_snapshots
                WHERE outcome IS NOT NULL AND r_multiple IS NOT NULL
                AND ts >= ?
            """
            params = [cutoff]
            if asset_type:
                query += " AND asset_type = ?"
                params.append(asset_type)
            rows = con.execute(query, params).fetchall()
            con.close()

        if len(rows) < min_samples:
            return {
                "available": False,
                "sample_count": len(rows),
                "min_samples": min_samples,
                "factors": {},
            }

        factor_names = [
            "trend", "momentum", "derivatives", "microstructure", "carry",
            "trend_strength", "volatility", "volume", "structure",
        ]

        report = {}
        for factor in factor_names:
            col = f"f_{factor}"
            # Filter to trades where this factor was active (non-None)
            active = [(dict(r)[col], dict(r)["r_multiple"]) for r in rows if dict(r)[col] is not None]
            if len(active) < max(5, min_samples // 4):
                report[factor] = {
                    "recommendation": "insufficient_data",
                    "sample_count": len(active),
                }
                continue

            winners = [(s, r) for s, r in active if r > 0]
            losers = [(s, r) for s, r in active if r <= 0]
            win_rate = len(winners) / len(active) if active else 0
            avg_score_win = sum(s for s, _ in winners) / len(winners) if winners else 0
            avg_score_lose = sum(s for s, _ in losers) / len(losers) if losers else 0
            contribution_spread = avg_score_win - avg_score_lose

            # Information Coefficient: Pearson correlation between factor score and R-multiple
            scores = [s for s, _ in active]
            r_mults = [r for _, r in active]
            ic = _pearson_corr(scores, r_mults)

            # Recommendation logic
            if len(active) < min_samples:
                rec = "insufficient_data"
            elif ic is not None and ic > 0.15 and contribution_spread > 0.1:
                rec = "keep"  # positively predictive
            elif ic is not None and ic < -0.10:
                rec = "investigate"  # negatively correlated — might be hurting
            elif contribution_spread < -0.1:
                rec = "reduce"  # active on losers more than winners
            elif abs(contribution_spread) < 0.05 and (ic is None or abs(ic) < 0.05):
                rec = "reduce"  # no signal — dead weight
            else:
                rec = "keep"

            report[factor] = {
                "sample_count": len(active),
                "win_rate_when_active": round(win_rate, 4),
                "avg_score_winners": round(avg_score_win, 4),
                "avg_score_losers": round(avg_score_lose, 4),
                "contribution_spread": round(contribution_spread, 4),
                "information_coefficient": round(ic, 4) if ic is not None else None,
                "recommendation": rec,
            }

        return {
            "available": True,
            "sample_count": len(rows),
            "asset_type": asset_type or "all",
            "lookback_days": lookback_days,
            "factors": report,
        }
    except Exception as e:
        log.error(f"[FACTOR_TRACKER] report failed: {e}")
        return {"available": False, "error": str(e), "factors": {}}


def _pearson_corr(x: list, y: list) -> float | None:
    """Simple Pearson correlation coefficient."""
    n = len(x)
    if n < 5:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    den = (dx * dy) ** 0.5
    return num / den if den > 0 else 0.0


def factor_leaderboard(lookback_days: int = 90) -> list[dict]:
    """Return all factors ranked by information coefficient across all asset types.

    Useful for a dashboard widget showing which factors are pulling their weight.
    """
    all_report = factor_report(asset_type=None, lookback_days=lookback_days, min_samples=10)
    if not all_report.get("available"):
        return []
    factors = all_report.get("factors", {})
    ranked = []
    for name, data in factors.items():
        if data.get("recommendation") == "insufficient_data":
            continue
        ranked.append({
            "factor": name,
            "ic": data.get("information_coefficient"),
            "spread": data.get("contribution_spread"),
            "win_rate": data.get("win_rate_when_active"),
            "samples": data.get("sample_count"),
            "recommendation": data.get("recommendation"),
        })
    # Sort by IC descending (most predictive first)
    ranked.sort(key=lambda x: x.get("ic") or -999, reverse=True)
    return ranked
