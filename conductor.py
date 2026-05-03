"""Athena AI Conductor — Deterministic router for AI function orchestration.

Replaces the "always run everything" approach with explicit rules that decide
which AI functions to call, when to skip, and how to dynamically weight engines.

Zero LLM calls for routing. Fully auditable. Extends existing ai_context.py.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from config import CONFIG
from ai_context import build_ai_calibration_context

log = logging.getLogger("sentinel.conductor")

# Last conductor result (top signal) stored for dashboard access
_LAST_CONDUCTOR_RESULT: dict | None = None

# All conductor results from latest scan, keyed by pair display name
_ALL_CONDUCTOR_RESULTS: dict[str, dict] = {}

# Which scan produced the current results
_LAST_SCAN_TYPE: str = ""


def reset_scan_results(scan_type: str = "") -> None:
    """Clear per-pair results before a new scan so stale data from a different engine is not mixed in."""
    global _ALL_CONDUCTOR_RESULTS, _LAST_CONDUCTOR_RESULT, _LAST_SCAN_TYPE
    _ALL_CONDUCTOR_RESULTS = {}
    _LAST_CONDUCTOR_RESULT = None
    _LAST_SCAN_TYPE = scan_type

# ── Deterministic Routing Rules ──────────────────────────────────────────────

# Score thresholds for routing decisions
ROUTER_THRESHOLDS = {
    "debate_min_score_pct": 50,    # Below this = too weak, skip debate
    "debate_max_score_pct": 75,    # Above this = strong enough, skip debate
    "vision_divergence_trigger": True,  # Volume divergence or stop-run → run vision
    "sentiment_news_trigger": True,     # News risk detected → run sentiment
}

# Engine weighting by regime (initial — updated from DB over time)
DEFAULT_REGIME_WEIGHTS = {
    "TRENDING": {"engine_a": 0.55, "engine_b": 0.45},
    "RANGING":  {"engine_a": 0.45, "engine_b": 0.55},
    "HIGH_VOLATILITY": {"engine_a": 0.50, "engine_b": 0.50},
    "LOW_VOLATILITY": {"engine_a": 0.60, "engine_b": 0.40},
}


def _get_recent_performance(
    db_path: str,
    pair: str,
    regime: str,
    lookback_trades: int = 20,
) -> dict[str, float]:
    """Query audit.db for recent per-asset-regime win rates.

    Returns {"engine_a_wr": float, "engine_b_wr": float, "sample_size": int}
    """
    import sqlite3
    from datetime import datetime, timezone, timedelta

    result = {"engine_a_wr": 0.0, "engine_b_wr": 0.0, "sample_size": 0}

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row

            # Pair+regime win rate (learning_log has no engine/outcome columns)
            row = con.execute(
                """SELECT COUNT(*) as total, SUM(win) as wins
                   FROM learning_log
                   WHERE pair = ? AND regime = ?
                   AND ts > ? AND (r_multiple IS NULL OR abs(r_multiple) <= 50)""",
                (pair, regime, cutoff),
            ).fetchone()
            if row and row["total"] and row["total"] >= 5:
                wr = row["wins"] / row["total"]
                result["engine_a_wr"] = wr
                result["engine_b_wr"] = wr
                result["sample_size"] = row["total"]

    except Exception as e:
        log.warning("[CONDUCTOR] DB query failed: %s", e)

    return result


def route_ai_functions(
    signal: dict[str, Any],
    regime: str,
    volume_divergence: Optional[dict] = None,
    stop_run: Optional[dict] = None,
    news_risk: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Deterministically decide which AI functions to run.

    Returns a routing dict with:
      - run_debate: bool
      - run_vision: bool
      - run_sentiment: bool
      - engine_weights: {"engine_a": float, "engine_b": float}
      - reasons: list[str]
      - skip_signal: bool (if hard fail)
    """
    cfg = CONFIG.get("CONDUCTOR", {}) or {}
    if not cfg.get("ENABLED", True):
        # Fallback: run everything (legacy behavior)
        return {
            "run_debate": True,
            "run_vision": True,
            "run_sentiment": True,
            "engine_weights": {"engine_a": 0.5, "engine_b": 0.5},
            "reasons": ["conductor_disabled"],
            "skip_signal": False,
        }

    ctx = build_ai_calibration_context(signal, signal.get("engine_source", "UNKNOWN"))
    engine_a = ctx.get("engine_a", {})
    engine_b = ctx.get("engine_b", {})

    score_pct = float(engine_a.get("rawScorePct", 0))
    # Engine B-only signals carry no Engine A score — use their own confidence pct
    if score_pct == 0 or signal.get("is_naked"):
        _b_pct = float(signal.get("score_pct") or signal.get("confluencePct") or 0)
        if _b_pct > 0:
            score_pct = _b_pct
    engine_b_verdict = str(engine_b.get("structural_verdict", "UNKNOWN")).upper()
    pair = str(ctx.get("identity", {}).get("pair", "")).replace("/", "")

    run_debate = False
    run_vision = False
    run_sentiment = False
    reasons = []
    skip_signal = False

    # ── Rule 1: Hard fail → skip everything ────────────────────────────────
    if score_pct < cfg.get("HARD_FAIL_SCORE_PCT", 35):
        skip_signal = True
        reasons.append(f"Hard fail: score {score_pct:.1f}% < {cfg.get('HARD_FAIL_SCORE_PCT', 35)}%")
        return {
            "run_debate": False,
            "run_vision": False,
            "run_sentiment": False,
            "engine_weights": {"engine_a": 0.0, "engine_b": 0.0},
            "reasons": reasons,
            "skip_signal": True,
        }

    # ── Rule 2: Score borderline → run debate ─────────────────────────────
    debate_min = cfg.get("DEBATE_MIN_SCORE_PCT", 50)
    debate_max = cfg.get("DEBATE_MAX_SCORE_PCT", 75)
    if debate_min <= score_pct <= debate_max:
        run_debate = True
        reasons.append(f"Score borderline ({score_pct:.1f}%) — debate for clarity")

    # ── Rule 3: Engine A/B conflict → run debate ──────────────────────────
    if engine_b_verdict == "CONFLICT":
        run_debate = True
        reasons.append("Engine A/B conflict — debate to resolve")

    # ── Rule 4: Strong signal → skip debate (save API cost) ───────────────
    if score_pct >= cfg.get("STRONG_SCORE_PCT", 80) and engine_b_verdict in ("CLEAR", "BULL", "BEAR", "PASS"):
        run_debate = False
        reasons.append(f"Strong signal ({score_pct:.1f}%) + clear B verdict — skip debate")

    # ── Rule 5: News risk → run sentiment ──────────────────────────────────
    if news_risk:
        run_sentiment = True
        reasons.append(f"News risk detected: {news_risk}")

    # ── Rule 6: Volume divergence or stop-run → run vision ──────────────
    if volume_divergence and volume_divergence.get("divergence"):
        run_vision = True
        reasons.append(f"Volume divergence ({volume_divergence.get('type')}) — vision check")
    if stop_run and stop_run.get("stop_run"):
        run_vision = True
        reasons.append(f"Stop-run detected ({stop_run.get('confidence')}) — vision check")

    # ── Rule 7: Explicit vision request from UI ────────────────────────────
    if signal.get("explicit_vision_request") or signal.get("request_vision"):
        run_vision = True
        reasons.append("Explicit vision request")

    # ── Engine Weighting ─────────────────────────────────────────────────
    # Start with default regime weights
    weights = DEFAULT_REGIME_WEIGHTS.get(regime, {"engine_a": 0.5, "engine_b": 0.5}).copy()

    # Adjust from recent DB performance if available
    if db_path and pair and regime:
        perf = _get_recent_performance(db_path, pair, regime)
        if perf["sample_size"] >= 10:
            # Blend default with empirical: 60% default, 40% empirical
            a_wr = perf["engine_a_wr"]
            b_wr = perf["engine_b_wr"]
            total = a_wr + b_wr
            if total > 0:
                empirical_a = a_wr / total
                weights["engine_a"] = 0.6 * weights["engine_a"] + 0.4 * empirical_a
                weights["engine_b"] = 1.0 - weights["engine_a"]
                reasons.append(
                    f"Dynamic weighting: A={weights['engine_a']:.2f} (WR{a_wr:.0%}), "
                    f"B={weights['engine_b']:.2f} (WR{b_wr:.0%}) from {perf['sample_size']} trades"
                )

    # Normalize
    total_w = weights["engine_a"] + weights["engine_b"]
    if total_w > 0:
        weights["engine_a"] /= total_w
        weights["engine_b"] /= total_w

    return {
        "run_debate": run_debate,
        "run_vision": run_vision,
        "run_sentiment": run_sentiment,
        "engine_weights": weights,
        "reasons": reasons,
        "skip_signal": False,
        "score_pct": score_pct,
        "engine_b_verdict": engine_b_verdict,
        "regime": regime,
        "pair": signal.get("display") or signal.get("pair", "?"),
        "direction": str(signal.get("direction", "?")).upper(),
    }


def build_conductor_context_packet(
    signal: dict[str, Any],
    regime: str,
    routing: dict[str, Any],
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Build the full context packet for AI functions.

    Extends ai_context.build_ai_calibration_context with:
      - regime
      - conductor routing decisions
      - dynamic engine weights
      - recent performance (if available)
    """
    ctx = build_ai_calibration_context(signal, signal.get("engine_source", "UNKNOWN"))

    # Add regime
    ctx["regime"] = regime
    ctx["market_state"] = regime  # alias for compatibility

    # Add conductor routing
    ctx["conductor"] = {
        "run_debate": routing.get("run_debate"),
        "run_vision": routing.get("run_vision"),
        "run_sentiment": routing.get("run_sentiment"),
        "reasons": routing.get("reasons", []),
        "engine_weights": routing.get("engine_weights", {}),
    }

    # Add recent performance if DB available
    if db_path:
        pair = str(ctx.get("identity", {}).get("pair", "")).replace("/", "")
        perf = _get_recent_performance(db_path, pair, regime)
        ctx["recent_performance"] = perf

    # Add portfolio context if available
    ctx["portfolio"] = {
        "heat": signal.get("portfolio_heat"),
        "max_dd": signal.get("max_dd"),
        "consecutive_losses": signal.get("consecutive_losses"),
        "daily_net_r": signal.get("daily_net_r"),
    }

    return ctx


def _normalise_regime(regime: Any) -> str:
    """Coerce regime to a plain string — Engine A signals carry a dict, Engine C a string."""
    if isinstance(regime, dict):
        return str(regime.get("label", "UNKNOWN")).upper()
    return str(regime).upper() if regime else "UNKNOWN"


def conductor_orchestrate(
    signal: dict[str, Any],
    regime: Any,
    db_path: str,
    news_ctx: dict | None = None,
    volume_divergence: dict | None = None,
    stop_run: dict | None = None,
    news_risk: str | None = None,
) -> dict[str, Any]:
    """Full conductor workflow: route + build context + return execution plan.

    This is the entry point athena.py calls. Returns everything needed
    to execute the signal with the right AI functions.
    """
    regime = _normalise_regime(regime)

    # Step 1: Route
    routing = route_ai_functions(
        signal,
        regime,
        volume_divergence=volume_divergence,
        stop_run=stop_run,
        news_risk=news_risk,
        db_path=db_path,
    )

    # Step 2: Build context packet
    context = build_conductor_context_packet(signal, regime, routing, db_path)

    result = {
        "routing": routing,
        "context": context,
        "execute": not routing.get("skip_signal", False),
    }

    # Store for dashboard access
    global _LAST_CONDUCTOR_RESULT, _ALL_CONDUCTOR_RESULTS
    _LAST_CONDUCTOR_RESULT = result
    pair_key = signal.get("display") or signal.get("pair", "")
    if pair_key:
        _ALL_CONDUCTOR_RESULTS[pair_key] = result

    return result
