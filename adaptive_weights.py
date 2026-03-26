"""adaptive_weights.py — Self-adjusting factor weights for Sentinel Pro v4.0.

Queries learning_log to compute win rates per factor per asset class,
then adjusts factor weights based on empirical performance data.
Uses 30% blend rate to avoid overfitting (research-backed).

Called by scoring.py before factor scoring to get adjusted weights.
"""

import json
import logging
import sqlite3
import time

log = logging.getLogger("sentinel.adaptive_weights")

# Cache: { "forex": { "weights": {...}, "ts": time.time() } }
_cache: dict = {}
_CACHE_TTL = 21600  # 6 hours

# Default base weights (from factor_scoring.py)
_BASE_WEIGHTS = {
    "trend": 1.0,
    "momentum": 1.0,
    "volatility": 1.0,
    "volume": 1.0,
    "structure": 1.0,
    "derivatives": 1.0,
    "microstructure": 1.0,
    "carry": 1.0,
}

_BLEND_RATE = 0.3  # 30% blend — proven optimal range (0.25-0.35)


def get_adaptive_weights(db_path: str, asset_type: str, regime: str = "") -> dict:
    """Get factor weights adjusted from trade outcome history.

    Args:
        db_path: Path to SQLite database with learning_log table
        asset_type: e.g., "forex", "crypto", "commodity"
        regime: Current market regime (optional, for regime-specific adaption)

    Returns:
        dict: Factor weights like {"trend": 1.12, "momentum": 0.88, ...}
    """
    cache_key = f"{asset_type}_{regime}"
    if cache_key in _cache and time.time() - _cache[cache_key]["ts"] < _CACHE_TTL:
        return _cache[cache_key]["weights"]

    try:
        weights = _compute_adaptive_weights(db_path, asset_type, regime)
        _cache[cache_key] = {"weights": weights, "ts": time.time()}
        return weights
    except Exception as e:
        log.warning(f"[ADAPT] Error computing weights: {e}")
        return _BASE_WEIGHTS.copy()


def _compute_adaptive_weights(db_path: str, asset_type: str, regime: str) -> dict:
    """Compute adaptive weights from learning_log data."""
    try:
        con = sqlite3.connect(db_path, timeout=15.0)
        con.row_factory = sqlite3.Row

        # Get last 50 closed trades for this asset type
        query = """
            SELECT votes_json, win, r_multiple, regime
            FROM learning_log
            WHERE asset_type = ? AND win IS NOT NULL AND votes_json IS NOT NULL
            ORDER BY ts DESC LIMIT 50
        """
        rows = con.execute(query, (asset_type,)).fetchall()
        con.close()

        if len(rows) < 10:
            log.debug(
                f"[ADAPT] {asset_type}: only {len(rows)} trades, using base weights"
            )
            return _BASE_WEIGHTS.copy()

        # Analyze factor performance
        factor_stats: dict = {}
        for factor in _BASE_WEIGHTS:
            factor_stats[factor] = {
                "positive_wins": 0,
                "positive_total": 0,
                "negative_wins": 0,
                "negative_total": 0,
                "r_when_positive": [],
                "r_when_negative": [],
            }

        for row in rows:
            try:
                votes = json.loads(row["votes_json"])
                win = bool(row["win"])
                r_mult = row["r_multiple"] or 0.0

                # Map vote keys to factors
                factor_map = {
                    "D1 Trend Gate": "trend",
                    "D1 ADX Trend": "trend",
                    "D1 Weinstein Stage": "trend",
                    "H4 MACD Momentum": "momentum",
                    "H4 RSI Zone": "momentum",
                    "H4 Stochastic": "momentum",
                    "H4 Fib Level": "mean_reversion",
                    "H1 EMA Entry": "trend",
                    "H1 BB Pullback": "mean_reversion",
                    "H1 RSI Divergence": "momentum",
                }

                factor_scores: dict = {}
                for vote_key, vote_val in votes.items():
                    factor = factor_map.get(vote_key)
                    if factor:
                        factor_scores.setdefault(factor, [])
                        factor_scores[factor].append(vote_val or 0)

                for factor, vals in factor_scores.items():
                    avg_vote = sum(vals) / len(vals)
                    if factor not in factor_stats:
                        continue

                    if avg_vote > 0:
                        factor_stats[factor]["positive_total"] += 1
                        if win:
                            factor_stats[factor]["positive_wins"] += 1
                        factor_stats[factor]["r_when_positive"].append(r_mult)
                    elif avg_vote < 0:
                        factor_stats[factor]["negative_total"] += 1
                        if win:
                            factor_stats[factor]["negative_wins"] += 1
                        factor_stats[factor]["r_when_negative"].append(r_mult)

            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        # Calculate adjustment multipliers
        weights = _BASE_WEIGHTS.copy()
        adjustments = {}

        for factor, stats in factor_stats.items():
            pos_total = stats["positive_total"]
            if pos_total >= 5:
                win_rate = stats["positive_wins"] / pos_total
                # Adjustment: (win_rate - 0.5) * 2 → range [-1, +1]
                factor_adj = (win_rate - 0.5) * 2
                # Blend: 30% of adjustment applied
                new_weight = _BASE_WEIGHTS[factor] * (1 + _BLEND_RATE * factor_adj)
                weights[factor] = round(max(0.3, min(2.0, new_weight)), 3)
                adjustments[factor] = {
                    "wr": round(win_rate, 2),
                    "adj": round(factor_adj, 2),
                    "weight": weights[factor],
                }

        # Normalize so weights sum stays constant
        base_sum = sum(_BASE_WEIGHTS.values())
        current_sum = sum(weights.values())
        if current_sum > 0:
            scale = base_sum / current_sum
            weights = {k: round(v * scale, 3) for k, v in weights.items()}

        if adjustments:
            log.debug(f"[ADAPT] {asset_type} weights adjusted: {adjustments}")

        return weights

    except sqlite3.Error as e:
        log.warning(f"[ADAPT] DB error: {e}")
        return _BASE_WEIGHTS.copy()


def get_weight_report(db_path: str) -> dict:
    """Get a report of adaptive weights for all asset types (for UI display)."""
    report = {}
    for asset_type in ["forex", "crypto", "commodity", "stock", "index"]:
        weights = get_adaptive_weights(db_path, asset_type)
        is_adapted = weights != _BASE_WEIGHTS
        report[asset_type] = {"weights": weights, "adapted": is_adapted}
    return report
