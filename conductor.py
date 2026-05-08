"""Athena AI Conductor — Deterministic router for AI function orchestration.

Replaces the "always run everything" approach with explicit rules that decide
which AI functions to call, when to skip, and how to dynamically weight engines.

Zero LLM calls for routing. Fully auditable. Extends existing ai_context.py.

Scope: routing and A/B dynamic weights apply to Engine A + B style signals.
Engine C (consensus) and Engine D (scalp) are not separate routing targets; scalp
signals may still populate microstructure (volume divergence / stop-run) for vision hooks.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from config import CONFIG
from ai_context import build_ai_calibration_context

log = logging.getLogger("sentinel.conductor")

# Serialize reads/writes to module-level conductor scan state (concurrent HTTP / scans).
_conductor_state_lock = threading.Lock()

# Last conductor result (top signal) stored for dashboard access
_LAST_CONDUCTOR_RESULT: dict | None = None

# All conductor results from latest scan, keyed by pair display name
_ALL_CONDUCTOR_RESULTS: dict[str, dict] = {}

# Which scan produced the current results
_LAST_SCAN_TYPE: str = ""

# Only these engines contribute to A/B dynamic weighting in learning_log.
_LEARNING_LOG_ENGINES_AB = ("engine_a", "engine_b")


def reset_scan_results(scan_type: str = "") -> None:
    """Clear per-pair results before a new scan so stale data from a different engine is not mixed in."""
    global _ALL_CONDUCTOR_RESULTS, _LAST_CONDUCTOR_RESULT, _LAST_SCAN_TYPE
    with _conductor_state_lock:
        _ALL_CONDUCTOR_RESULTS = {}
        _LAST_CONDUCTOR_RESULT = None
        _LAST_SCAN_TYPE = scan_type


def _conductor_lookback_days() -> int:
    c = CONFIG.get("CONDUCTOR", {}) or {}
    if c.get("LOOKBACK_DAYS") is not None:
        try:
            return max(1, int(c["LOOKBACK_DAYS"]))
        except (TypeError, ValueError):
            pass
    try:
        return max(1, int(CONFIG.get("LEARNING_LOOKBACK_DAYS", 90) or 90))
    except (TypeError, ValueError):
        return 90


def extract_conductor_microstructure(signal: dict[str, Any]) -> tuple[Optional[dict], Optional[dict]]:
    """Best-effort volume_divergence + stop_run dicts from a scanner/execution signal payload."""
    if not isinstance(signal, dict):
        return None, None

    vol: Optional[dict] = None
    sr: Optional[dict] = None

    def _as_dict(d: Any) -> Optional[dict]:
        return d if isinstance(d, dict) else None

    vol = _as_dict(signal.get("volume_divergence"))
    sr = _as_dict(signal.get("stop_run"))

    if vol is None or sr is None:
        nd = _as_dict(signal.get("naked_data"))
        if nd:
            if vol is None:
                vol = _as_dict(nd.get("volume_divergence"))
            if sr is None:
                sr = _as_dict(nd.get("stop_run"))

    if vol is None or sr is None:
        setup = _as_dict(signal.get("setup"))
        if setup:
            if vol is None:
                vol = _as_dict(setup.get("volume_divergence"))
            if sr is None:
                sr = _as_dict(setup.get("stop_run"))

    for key in ("vp_setup", "scalp_setup", "engine_d", "grading"):
        sub = _as_dict(signal.get(key))
        if not sub:
            continue
        if vol is None:
            vol = _as_dict(sub.get("volume_divergence"))
        if sr is None:
            sr = _as_dict(sub.get("stop_run"))
        if vol is not None and sr is not None:
            break

    return vol, sr


# ── Deterministic Routing Rules ──────────────────────────────────────────────

# Defaults when YAML is absent; routing reads CONFIG["CONDUCTOR"] first.
ROUTER_THRESHOLDS = {
    "debate_min_score_pct": 50,
    "debate_max_score_pct": 75,
    "vision_divergence_trigger": True,
    "sentiment_news_trigger": True,
}

# Engine weighting by regime (initial — updated from DB over time)
DEFAULT_REGIME_WEIGHTS = {
    "TRENDING": {"engine_a": 0.55, "engine_b": 0.45},
    "RANGING": {"engine_a": 0.45, "engine_b": 0.55},
    "HIGH_VOLATILITY": {"engine_a": 0.50, "engine_b": 0.50},
    "LOW_VOLATILITY": {"engine_a": 0.60, "engine_b": 0.40},
}


def _get_recent_performance(
    db_path: str,
    pair: str,
    regime: str,
    lookback_days: Optional[int] = None,
) -> dict[str, float]:
    """Query audit.db for recent per-asset-regime win rates (engine_a / engine_b only).

    Returns {"engine_a_wr": float, "engine_b_wr": float, "sample_size": int}
    """
    import sqlite3
    from datetime import datetime, timezone, timedelta

    result = {"engine_a_wr": 0.0, "engine_b_wr": 0.0, "sample_size": 0}

    days = lookback_days if lookback_days is not None else _conductor_lookback_days()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        eng_placeholders = ",".join("?" * len(_LEARNING_LOG_ENGINES_AB))
        base_where = (
            f"pair = ? AND regime = ? AND ts > ? "
            f"AND engine IN ({eng_placeholders}) "
            f"AND (r_multiple IS NULL OR abs(r_multiple) <= 50)"
        )
        base_params = (pair, regime, cutoff, *_LEARNING_LOG_ENGINES_AB)

        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row

            rows = con.execute(
                f"""SELECT engine, COUNT(*) as total, SUM(win) as wins
                   FROM learning_log
                   WHERE {base_where}
                   GROUP BY engine""",
                base_params,
            ).fetchall()

            total_sample = 0
            engine_a_wr = 0.0
            engine_b_wr = 0.0

            for row in rows:
                eng = row["engine"] or ""
                total = row["total"] or 0
                wins = row["wins"] or 0

                total_sample += total

                if total >= 3:
                    wr = wins / total
                    if eng == "engine_a":
                        engine_a_wr = wr
                    elif eng == "engine_b":
                        engine_b_wr = wr

            if total_sample >= 5 and engine_a_wr == 0.0 and engine_b_wr == 0.0:
                agg_row = con.execute(
                    f"""SELECT COUNT(*) as total, SUM(win) as wins
                       FROM learning_log
                       WHERE {base_where}""",
                    base_params,
                ).fetchone()
                if agg_row and agg_row["total"] and agg_row["total"] >= 5:
                    wr = (agg_row["wins"] or 0) / agg_row["total"]
                    engine_a_wr = wr
                    engine_b_wr = wr

            result["engine_a_wr"] = engine_a_wr
            result["engine_b_wr"] = engine_b_wr
            result["sample_size"] = total_sample

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
      - run_marcus: bool
      - engine_weights: {"engine_a": float, "engine_b": float}
      - reasons: list[str]
      - skip_signal: bool (if hard fail)
    """
    cfg = CONFIG.get("CONDUCTOR", {}) or {}
    vision_div_cfg = cfg.get("VISION_ON_DIVERGENCE", ROUTER_THRESHOLDS.get("vision_divergence_trigger", True))
    vision_sr_cfg = cfg.get("VISION_ON_STOP_RUN", True)
    sentiment_news_cfg = cfg.get("SENTIMENT_ON_NEWS", ROUTER_THRESHOLDS.get("sentiment_news_trigger", True))

    if not cfg.get("ENABLED", True):
        return {
            "run_debate": True,
            "run_vision": True,
            "run_sentiment": True,
            "run_marcus": True,
            "engine_weights": {"engine_a": 0.5, "engine_b": 0.5},
            "reasons": ["conductor_disabled"],
            "skip_signal": False,
        }

    ctx = build_ai_calibration_context(signal, signal.get("engine_source", "UNKNOWN"))
    engine_a = ctx.get("engine_a", {})
    engine_b = ctx.get("engine_b", {})

    _raw_a = engine_a.get("rawScorePct", 0)
    try:
        score_pct = float(_raw_a or 0)
    except (TypeError, ValueError):
        score_pct = 0.0
    if score_pct == 0 or signal.get("is_naked"):
        try:
            _b_pct = float(signal.get("score_pct") or signal.get("confluencePct") or 0)
        except (TypeError, ValueError):
            _b_pct = 0.0
        if _b_pct > 0:
            score_pct = _b_pct
    engine_b_verdict = str(engine_b.get("structural_verdict", "UNKNOWN")).upper()
    pair = str(ctx.get("identity", {}).get("pair", "")).replace("/", "")

    run_debate = False
    run_vision = False
    run_sentiment = False
    reasons = []
    skip_signal = False

    if score_pct < cfg.get("HARD_FAIL_SCORE_PCT", 35):
        skip_signal = True
        reasons.append(f"Hard fail: score {score_pct:.1f}% < {cfg.get('HARD_FAIL_SCORE_PCT', 35)}%")
        return {
            "run_debate": False,
            "run_vision": False,
            "run_sentiment": False,
            "run_marcus": False,
            "engine_weights": {"engine_a": 0.0, "engine_b": 0.0},
            "reasons": reasons,
            "skip_signal": True,
        }

    debate_min = cfg.get("DEBATE_MIN_SCORE_PCT", 50)
    debate_max = cfg.get("DEBATE_MAX_SCORE_PCT", 75)
    if debate_min <= score_pct <= debate_max:
        run_debate = True
        reasons.append(f"Score borderline ({score_pct:.1f}%) — debate for clarity")

    if engine_b_verdict == "CONFLICT":
        run_debate = True
        reasons.append("Engine A/B conflict — debate to resolve")

    # Above borderline band + clear structure → skip debate (no gap vs STRONG_SCORE_PCT).
    if (
        engine_b_verdict != "CONFLICT"
        and score_pct > debate_max
        and engine_b_verdict in ("CLEAR", "BULL", "BEAR", "PASS")
    ):
        run_debate = False
        reasons.append(f"Score {score_pct:.1f}% above debate band + clear B verdict — skip debate")

    if news_risk and sentiment_news_cfg:
        run_sentiment = True
        reasons.append(f"News risk detected: {news_risk}")

    if vision_div_cfg and volume_divergence and volume_divergence.get("divergence"):
        run_vision = True
        reasons.append(f"Volume divergence ({volume_divergence.get('type')}) — vision check")
    if vision_sr_cfg and stop_run and stop_run.get("stop_run"):
        run_vision = True
        reasons.append(f"Stop-run detected ({stop_run.get('confidence')}) — vision check")

    if signal.get("explicit_vision_request") or signal.get("request_vision"):
        run_vision = True
        reasons.append("Explicit vision request")

    weights = DEFAULT_REGIME_WEIGHTS.get(regime, {"engine_a": 0.5, "engine_b": 0.5}).copy()

    dyn_on = cfg.get("DYNAMIC_WEIGHTING_ENABLED", True)
    min_n = cfg.get("MIN_SAMPLE_SIZE", 10)
    try:
        min_n = max(1, int(min_n))
    except (TypeError, ValueError):
        min_n = 10
    try:
        blend = float(cfg.get("DYNAMIC_WEIGHT_BLEND", 0.40))
    except (TypeError, ValueError):
        blend = 0.40
    blend = max(0.0, min(1.0, blend))
    default_w = 1.0 - blend

    if dyn_on and db_path and pair and regime:
        perf = _get_recent_performance(db_path, pair, regime)
        if perf["sample_size"] >= min_n:
            a_wr = perf["engine_a_wr"]
            b_wr = perf["engine_b_wr"]
            total = a_wr + b_wr
            if total > 0:
                empirical_a = a_wr / total
                weights["engine_a"] = default_w * weights["engine_a"] + blend * empirical_a
                weights["engine_b"] = 1.0 - weights["engine_a"]
                reasons.append(
                    f"Dynamic weighting: A={weights['engine_a']:.2f} (WR{a_wr:.0%}), "
                    f"B={weights['engine_b']:.2f} (WR{b_wr:.0%}) from {perf['sample_size']} trades"
                )

    total_w = weights["engine_a"] + weights["engine_b"]
    if total_w > 0:
        weights["engine_a"] /= total_w
        weights["engine_b"] /= total_w

    always_marcus = cfg.get("RUN_MARCUS_ON_ANALYZE", True)
    run_marcus = True if always_marcus else bool(run_debate or run_vision or run_sentiment)

    return {
        "run_debate": run_debate,
        "run_vision": run_vision,
        "run_sentiment": run_sentiment,
        "run_marcus": run_marcus,
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

    ctx["regime"] = regime
    ctx["market_state"] = regime

    ctx["conductor"] = {
        "run_debate": routing.get("run_debate"),
        "run_vision": routing.get("run_vision"),
        "run_sentiment": routing.get("run_sentiment"),
        "run_marcus": routing.get("run_marcus"),
        "reasons": routing.get("reasons", []),
        "engine_weights": routing.get("engine_weights", {}),
    }

    if db_path:
        pair = str(ctx.get("identity", {}).get("pair", "")).replace("/", "")
        perf = _get_recent_performance(db_path, pair, regime)
        ctx["recent_performance"] = perf

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

    routing = route_ai_functions(
        signal,
        regime,
        volume_divergence=volume_divergence,
        stop_run=stop_run,
        news_risk=news_risk,
        db_path=db_path,
    )

    context = build_conductor_context_packet(signal, regime, routing, db_path)

    result = {
        "routing": routing,
        "context": context,
        "execute": not routing.get("skip_signal", False),
    }

    global _LAST_CONDUCTOR_RESULT, _ALL_CONDUCTOR_RESULTS
    pair_key = signal.get("display") or signal.get("pair", "")
    with _conductor_state_lock:
        _LAST_CONDUCTOR_RESULT = result
        if pair_key:
            _ALL_CONDUCTOR_RESULTS[pair_key] = result

    return result
