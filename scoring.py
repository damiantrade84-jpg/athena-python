"""scoring.py — Confluence scoring engine, session detection, and divergence detection.

Depends on: config.CONFIG, indicators (calc_* functions).
"""
import logging
from datetime import datetime, timezone

from config import CONFIG, PAIR_PROFILE_VOTES, PAIR_PROFILE_FILTERS
from indicators import (
    calc_rsi, calc_fib, calc_fib_proximity, calc_rsi_divergence, calc_weinstein_stage,
    calc_bb_width_percentile, calc_obv_trend, calc_squeeze,
)
from regime import detect_regime

log = logging.getLogger("athena")


def get_pair_profile(pair: dict) -> dict:
    """Return pair-specific profile overrides keyed by display or symbol."""
    profiles = CONFIG.get("PAIR_PROFILES", {}) or {}
    return profiles.get(pair.get("display")) or profiles.get(pair.get("symbol")) or {}


def get_pair_vote_weights(pair: dict) -> dict:
    """Merge pair overrides on top of class-level vote weights."""
    ptype = pair.get("type", "stock")
    weights = dict(CONFIG.get("VOTE_WEIGHTS", {}).get(ptype, CONFIG.get("VOTE_WEIGHTS", {}).get("stock", {})))
    profile = get_pair_profile(pair)
    for vote_name in profile.get("disabled_votes", []) or []:
        weights[vote_name] = 0.0
    for vote_name, weight in (profile.get("weight_overrides", {}) or {}).items():
        try:
            weights[vote_name] = float(weight)
        except (TypeError, ValueError):
            log.warning(f"[CFG] Invalid weight override for {pair.get('display')}: {vote_name}={weight!r}")
    return weights


def pair_filter_enabled(pair: dict, filter_name: str) -> bool:
    disabled = set(get_pair_profile(pair).get("disable_filters", []) or [])
    return filter_name not in disabled


def classify_signal_setup(direction: str, entry_mode: str,
                          squeeze_bonus: bool = False, atr_breakout: bool = False,
                          votes: dict | None = None) -> str:
    """Classify the setup so pair-specific routing is easier to audit.

    Uses structured flags (squeeze_bonus, atr_breakout) set during scoring rather
    than re-parsing warning strings, so renaming warning text never silently breaks classification.
    """
    if entry_mode == "mean_revert":
        return "mean_reversion"
    if squeeze_bonus or atr_breakout:
        return "breakout"
    dir_vote = 1 if direction == "LONG" else -1
    votes = votes or {}
    if votes.get("H1 BB Pullback") == dir_vote and votes.get("D1 Trend Gate") == dir_vote:
        return "trend_pullback"
    return "trend_continuation"


def get_session(bar_time: str | None = None) -> dict:
    """Determine forex session from UTC hour.

    bar_time: ISO timestamp string of the bar being evaluated (for backtesting).
              If None, uses current wall-clock time (live mode).
    """
    if bar_time:
        try:
            dt = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
            h = dt.hour
        except Exception:
            h = datetime.now(timezone.utc).hour
    else:
        h = datetime.now(timezone.utc).hour
    if 7  <= h < 9:  return {"name": "London Open",         "quality": "high",   "color": "#22c55e"}
    if 13 <= h < 16: return {"name": "London/NY Overlap",   "quality": "high",   "color": "#22c55e"}
    if 9  <= h < 13: return {"name": "London",              "quality": "medium",  "color": "#3b82f6"}
    if 16 <= h < 22: return {"name": "New York",            "quality": "medium",  "color": "#3b82f6"}
    return {"name": "Asian / Off-Hours", "quality": "low", "color": "#f59e0b"}


def detect_div(d1c: list, h4c: list, h1c: list) -> list:
    """Detect H4 RSI divergence and H1 volume divergence. Returns list of warning strings."""
    w = []
    try:
        h4 = h4c[-20:]
        cl = [c["close"] for c in h4]
        rsi = calc_rsi(cl, 14)
        pr = [c["high"] for c in h4]
        n = len(pr)
        if n >= 10 and rsi[-1] is not None:
            t = n // 3
            pm = max(pr[t:2 * t])
            rm = [x for x in rsi[t:2 * t] if x is not None]
            if rm:
                if pr[-1] > pm and rsi[-1] < max(rm): w.append("H4 RSI Bearish Divergence")
                if pr[-1] < pm and rsi[-1] > max(rm): w.append("H4 RSI Bullish Divergence")
    except Exception as e:
        log.warning(f"detect_div H4: {e}")
    try:
        h1 = h1c[-20:]
        vols = [c["vol"] for c in h1]
        pr = [c["close"] for c in h1]
        n = len(pr)
        if n >= 10:
            m = n // 2
            if pr[-1] > pr[0] and vols[-1] < vols[m] and vols[-1] > 0:
                w.append("H1 Vol Div - rising price, falling vol")
            if pr[-1] < pr[0] and vols[-1] < vols[m] and vols[-1] > 0:
                w.append("H1 Vol Div - falling price, falling vol")
    except Exception as e:
        log.warning(f"detect_div H1: {e}")
    return w


# ── Correlation clusters ─────────────────────────────────────────────────────
CORR_CLUSTERS: dict = {
    "metals":    ["XAU/USD", "XAG/USD", "GLD"],
    "defi":      ["SOL/USDT", "AVAX/USDT", "LINK/USDT", "BNB/USDT", "ETH/USDT", "INJ/USDT", "NEAR/USDT"],
    "ai_crypto": ["FET/USDT", "RENDER/USDT"],
    "forex_usd": ["EUR/USD", "GBP/USD", "AUD/USD", "NZD/USD", "USD/CHF", "USD/CAD", "USD/ZAR", "USD/MXN", "USD/SGD"],
    "forex_jpy": ["EUR/JPY", "GBP/JPY", "AUD/JPY"],
    "jse":       ["Naspers", "Sasol", "Std Bank", "Anglo Am", "MTN Group", "Shoprite",
                  "Richemont", "FirstRand", "Absa", "Capitec", "Prosus", "Gold Fields",
                  "AngloGold", "Sibanye"],
    "us_tech":   ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG"],
    "us_sp500":  ["SPY", "QQQ", "S&P 500", "Nasdaq"],
}


def apply_correlation_cap(signals: list) -> list:
    """Tag signals with correlationWarning if cluster already has 2+ active signals."""
    cluster_counts: dict = {}
    for sig in signals:
        pair_name = sig["pair"]
        for cluster, members in CORR_CLUSTERS.items():
            if pair_name in members:
                cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
                if cluster_counts[cluster] >= 2:
                    sig.setdefault("warnings", [])
                    sig["warnings"].append(
                        f"CORR CAP: {cluster} cluster already has {cluster_counts[cluster] - 1}"
                        f" signal(s) — halve size to cap USD exposure"
                    )
                    sig["correlationWarning"] = cluster
    return signals


def calc_confluence(d1: dict, h4: dict, h1: dict, vr: float, stoch: dict,
                    pair: dict, btc_bias: str,
                    d1_candles: list | None = None,
                    h4_candles: list | None = None,
                    h1_candles: list | None = None,
                    funding_rate: float | None = None,
                    volume_threshold: float | None = None,
                    bar_time: str | None = None) -> dict:
    """Factor-based confluence using normalized indicators, regime-aware weights, and correlation filtering.
    Preserves legacy API and raw-threshold warnings for human readability.
    """
    from factor_scoring import compute_factor_scores
    from confidence_engine import compute_confidence
    # Compute factor scores
    factor_result = compute_factor_scores(d1["snap"], h4["snap"], h1["snap"], pair,
                                        d1_candles or [], h4_candles or [], h1_candles or [],
                                        vr, funding_rate, bar_time)
    # Preserve warnings for readability (raw thresholds)
    w = []
    s = d1["snap"]; s4 = h4["snap"]; s1 = h1["snap"]
    _ptype = pair["type"]
    _rsi_b = CONFIG["RSI_BOUNDS"].get(_ptype, {"ob": 70, "os": 30})
    r4 = s4.get("rsi")
    if r4 is not None:
        if r4 >= _rsi_b["ob"]:
            w.append(f"H4 RSI overbought ({r4:.0f} >= {_rsi_b['ob']}) — wait for pullback")
        elif r4 <= _rsi_b["os"]:
            w.append(f"H4 RSI oversold ({r4:.0f} <= {_rsi_b['os']}) — wait for bounce")
    # Map final_score to legacy 'score' field
    score = factor_result["final_score"]
    direction = factor_result["direction"]
    log.debug(f"[FACTOR] {pair.get('display')} score={score:.3f} dir={direction} "
             f"dir_score={factor_result.get('directional_score', 0):.3f} "
             f"nondir_score={factor_result.get('nondirectional_score', 0):.3f} "
             f"factors={factor_result['factor_scores']} regime={factor_result['regime']}")
    # Legacy compatibility: construct votes dict from factor scores
    v = {}
    for factor, val in factor_result["factor_scores"].items():
        if val is not None:
            v[f"FACTOR_{factor.upper()}"] = 1 if val > 0 else -1
    
    # Map legacy vote names for UI compatibility
    legacy_votes = {
        "D1 Trend Gate": v.get("FACTOR_TREND", 0),
        "D1 ADX Trend": v.get("FACTOR_MOMENTUM", 0), 
        "D1 Weinstein Stage": v.get("FACTOR_WEINSTEIN", 0),
        "H4 MACD Momentum": v.get("FACTOR_MOMENTUM", 0),
        "H4 RSI Zone": v.get("FACTOR_RSI", 0),
        "H4 Stochastic": v.get("FACTOR_STOCH", 0),
        "H4 Fib Level": v.get("FACTOR_FIB", 0),
        "H1 EMA Entry": v.get("FACTOR_EMA", 0),
        "H1 BB Pullback": v.get("FACTOR_BB", 0),
        "H1 RSI Divergence": v.get("FACTOR_RSI_DIV", 0),
    }
    # Update votes dict with legacy names (remove FACTOR_ prefix)
    v.update(legacy_votes)

    # Use factor result; preserve legacy return structure
    score = round(score, 2)
    adx_val = s4.get("adx")
    _rng = CONFIG["RANGING"].get(pair["type"], CONFIG["RANGING"]["commodity"])
    trend_state = "UNKNOWN"
    if adx_val is not None:
        if adx_val >= 35:               trend_state = "TRENDING"
        elif adx_val >= 25:             trend_state = "DEVELOPING"
        elif adx_val >= _rng["dead"]:   trend_state = "RANGING"
        else:                           trend_state = "DEAD RANGING"
    # Legacy compatibility values
    bull = max(0.0, score)
    bear = max(0.0, score) if direction == "SHORT" else 0.0
    # Spread = abs directional score (0–1 z-score scale): drives conviction label in AI prompt
    spread = round(abs(factor_result.get("directional_score", 0.0)), 2)
    # Rebuild regime as dict so callers can do res['regime'].get('state')
    _regime_str = factor_result.get("regime", "UNKNOWN") or "UNKNOWN"
    _REGIME_STATE = {"TRENDING": 0, "RANGING": 1, "HIGH_VOLATILITY": 2, "LOW_VOLATILITY": 3}
    _regime = {"state": _REGIME_STATE.get(_regime_str.upper(), 1), "label": _regime_str}
    w.append(f"Regime: {_regime_str.upper()}")
    # Confidence engine — diagnostic field (graceful degradation)
    _signal_type = "trend"  # default; could be derived from entryMode
    _conf = compute_confidence(
        factor_result=factor_result,
        h4_factor_result=factor_result,  # primary timeframe
        signal_type=_signal_type,
        volume_ratio=vr,
    )
    confidence_val = _conf["confidence"]
    # Legacy return dict
    return {
        "score": score, "votes": v, "direction": direction,
        "bull": round(bull, 1), "bear": round(bear, 1), "spread": spread,
        "warnings": w, "trendState": trend_state,
        "weinsteinStage": None, "weinsteinLabel": None,
        "entryMode": "trend", "adxMomentum": None, "adxSlope": None,
        "signalClass": "trend_continuation",
        "regime": _regime,
        "fundingRate": funding_rate,
        "maxScoreOverride": 3.0,  # Z-score clamp cap — final_score is a weighted average of clamped z-scores
        # New fields for factor diagnostics
        "factorScores": factor_result["factor_scores"],
        "factorWeights": factor_result["weights"],
        "regimeName": factor_result["regime"],
        # Confidence diagnostics
        "confidence": confidence_val,
        "confidenceDetail": _conf,
        # Factor diagnostics
        "correlationAdjustments": factor_result.get("correlation_adjustments", {}),
        "disabledFactors": factor_result.get("disabled_factors", []),
    }


# ── Scan classification helpers — pure functions, no Flask/athena deps ────────
# Kept here so unit tests can import without touching athena.py (CLAUDE.md Rule 3).

def _pair_exchange_code(pair: dict) -> str | None:
    """Return the exchange code for a pair (JSE / US) or None."""
    sym = pair.get("symbol", "")
    if ".JO" in sym:
        return "JSE"
    if (".US" in sym or ".INDX" in sym) and pair.get("type") in ("stock", "index"):
        return "US"
    return None


def _pair_exchange_closed(pair: dict, closed_exchanges: set) -> bool:
    exch = _pair_exchange_code(pair)
    return exch in closed_exchanges if exch else False


def _build_event_risk(pair: dict, ds_ctx: dict, earnings_ctx: dict,
                      closed_exchanges: set) -> dict:
    """Build event-risk dict (hardBlock + reasons) for a pair."""
    risk: dict = {"hardBlock": False, "reasons": []}
    sym = pair.get("symbol", "")
    if _pair_exchange_closed(pair, closed_exchanges):
        risk["exchangeClosed"] = True
        risk["reasons"].append("Exchange closed")
    if sym in earnings_ctx:
        e = earnings_ctx[sym]
        risk["earnings"] = e
        risk["reasons"].append(f"Earnings in {e['daysTo']} day(s)")
        if e.get("daysTo", 99) <= 1:
            risk["hardBlock"] = True
    if sym in ds_ctx:
        ev = ds_ctx[sym]
        if ev.get("upcomingDiv"):
            d = ev["upcomingDiv"][0]
            risk["dividend"] = d
            risk["reasons"].append(f"Ex-div in {d['daysTo']} day(s)")
        if ev.get("upcomingSplit"):
            s = ev["upcomingSplit"][0]
            risk["split"] = s
            risk["reasons"].append(f"Split in {s['daysTo']} day(s)")
            if s.get("daysTo", 99) <= 3:
                risk["hardBlock"] = True
    return risk


def _classify_signal(signal: dict, pair: dict) -> tuple[str, str]:
    """Return (tier, reason) where tier is 'trade' | 'watchlist' | 'skip'."""
    threshold = signal.get(
        "scanThreshold",
        CONFIG["MIN_CONFLUENCE_CLASS"].get(pair.get("type", ""), CONFIG["MIN_CONFLUENCE"]),
    )
    score = signal.get("confluenceScore", 0)
    hard_event = signal.get("eventRisk", {}).get("hardBlock", False)
    exchange_closed = signal.get("exchangeClosed", False)
    if pair.get("enabled", True) and not exchange_closed and not hard_event and score >= threshold:
        return "trade", "Trade-ready"
    watch_floor = max(round(threshold - 0.3, 2), 0.2)
    reasons = [d["detail"] for d in signal.get("scanDiagnostics", [])]
    if score >= threshold and not pair.get("enabled", True):
        return "watchlist", "; ".join(reasons) or "Strong setup, but pair is disabled"
    if score >= threshold and (exchange_closed or hard_event):
        return "watchlist", "; ".join(reasons) or "Blocked by exchange/event risk"
    if signal.get("trendState") != "DEAD RANGING" and score >= watch_floor:
        return "watchlist", "; ".join(reasons) or f"Near miss ({score}/{threshold})"
    return "skip", "; ".join(reasons) or "Below discovery threshold"
