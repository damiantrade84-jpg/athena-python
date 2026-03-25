"""scoring.py — Confluence scoring engine, session detection, and divergence detection.

Depends on: config.CONFIG, indicators (calc_* functions).
"""

import logging
from datetime import datetime, timezone

from config import CONFIG
from indicators import (
    calc_rsi,
)

log = logging.getLogger("athena")


def _tf_score_proxy(snap: dict) -> dict | None:
    """Build a lightweight per-timeframe score proxy for timeframe_alignment.

    Uses EMA alignment, RSI bias, and MACD direction to produce a simple
    directional score in the same scale as factor_result['final_score'].
    Returns dict with 'final_score' or None if snap lacks data.
    """
    if not snap:
        return None
    components = []
    # EMA alignment: close vs ema50 vs ema200
    close = snap.get("close")
    ema50 = snap.get("ema50")
    ema200 = snap.get("ema200")
    if close is not None and ema50 is not None and ema200 is not None and ema200 != 0:
        if close > ema50 > ema200:
            components.append(1.0)
        elif close < ema50 < ema200:
            components.append(-1.0)
        else:
            components.append(0.0)
    # RSI bias
    rsi = snap.get("rsi")
    if rsi is not None:
        components.append((rsi - 50) / 50)  # -1 to +1
    # MACD direction
    macd_hist = snap.get("macdHist")
    if macd_hist is not None:
        components.append(1.0 if macd_hist > 0 else -1.0)
    if not components:
        return None
    avg = sum(components) / len(components)
    return {"final_score": round(avg, 4)}

_MAJOR_FOREX = {
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"
}
_FOREX_CROSSES = {
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "GBP/AUD", "EUR/CHF", "USD/SGD"
}
_EXOTIC_FOREX = {"USD/ZAR", "USD/MXN"}
_PRECIOUS_TRACKERS = {"XAU/USD", "XAG/USD", "GLD", "SLV"}
_ENERGY_OIL = {"WTI Oil", "Brent Oil", "USO", "XLE"}
_US_INDICES_TRACKERS = {"NASDAQ-100", "S&P 500", "Dow Jones", "SPY", "QQQ"}
_EU_INDICES = {"DAX 40", "UK100"}
_ASIAN_INDICES = {"ASX 200", "Nikkei 225", "Hang Seng"}
_US_STOCK_CUSTOM = {
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG", "JPM", "V", "XOM",
    "NFLX", "AMD", "CRM", "DIS", "BA", "COIN", "PYPL", "INTC", "UBER", "PLTR"
}
_ALTCOIN_MAJORS = {
    "SOL/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "MATIC/USDT", "BNB/USDT",
    "DOT/USDT", "LTC/USDT", "SUI/USDT", "NEAR/USDT", "APT/USDT", "INJ/USDT",
    "RENDER/USDT"
}


def get_pair_profile(pair: dict) -> dict:
    """Return pair-specific profile overrides keyed by display or symbol."""
    profiles = CONFIG.get("PAIR_PROFILES", {}) or {}
    return profiles.get(pair.get("display")) or profiles.get(pair.get("symbol")) or {}


def get_pair_score_group(pair: dict) -> str:
    """Resolve subgroup used for scoring and confluence routing."""
    if pair.get("score_group"):
        return str(pair.get("score_group"))
    profile = get_pair_profile(pair)
    if profile.get("score_group"):
        return str(profile.get("score_group"))

    display = pair.get("display", "")
    ptype = pair.get("type", "")

    if ptype == "forex":
        if display in _MAJOR_FOREX:
            return "forex_majors"
        if display in _FOREX_CROSSES:
            return "forex_crosses"
        if display in _EXOTIC_FOREX:
            return "forex_exotics"
        return "forex_other"
    if ptype == "crypto":
        if display == "BTC/USDT":
            return "crypto_btc"
        if display == "ETH/USDT":
            return "crypto_eth"
        if display == "DOGE/USDT":
            return "crypto_doge"
        if display in _ALTCOIN_MAJORS:
            return "crypto_alt_majors"
        return "crypto_other"
    if ptype == "commodity":
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display == "Nat Gas":
            return "nat_gas"
        if display == "Copper":
            return "copper"
        if display in {"XPT/USD", "XPD/USD"}:
            return "pgm_metals"
        return "commodity_other"
    if ptype == "index":
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        if display in _EU_INDICES:
            return "eu_indices"
        if display in _ASIAN_INDICES:
            return "asian_indices"
        return "index_other"
    if ptype == "stock":
        if display in _US_STOCK_CUSTOM:
            return "us_stock_single"
        if display == "TLT":
            return "bond_tlt"
        if display in {"IWM", "EEM"}:
            return "smallcap_em_etf"
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        return "stock_other"
    return f"{ptype}_other" if ptype else "unknown"


def get_min_confluence_threshold(pair: dict) -> float:
    """Resolve scan threshold with pair profile, then score-group, then class defaults."""
    profile = get_pair_profile(pair)
    if profile.get("min_confluence") is not None:
        return float(profile.get("min_confluence"))

    ptype = pair.get("type", "")
    score_group = get_pair_score_group(pair)
    group_cfg = CONFIG.get("MIN_CONFLUENCE_GROUP", {}) or {}
    group_threshold = (group_cfg.get(ptype, {}) or {}).get(score_group)
    if group_threshold is not None:
        return float(group_threshold)

    return float(CONFIG["MIN_CONFLUENCE_CLASS"].get(ptype, CONFIG["MIN_CONFLUENCE"]))


def pair_filter_enabled(pair: dict, filter_name: str) -> bool:
    disabled = set(get_pair_profile(pair).get("disable_filters", []) or [])
    return filter_name not in disabled


def classify_signal_setup(
    direction: str,
    entry_mode: str,
    squeeze_bonus: bool = False,
    atr_breakout: bool = False,
    votes: dict | None = None,
) -> str:
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
    if (
        votes.get("H1 BB Pullback") == dir_vote
        and votes.get("D1 Trend Gate") == dir_vote
    ):
        return "trend_pullback"
    return "trend_continuation"


def get_session(bar_time: str | None = None) -> dict:
    """Determine forex session label from UTC hour (liquidity buckets for UI).

    bar_time: ISO timestamp of a specific bar (e.g. backtests). If omitted, uses
              current UTC — use this for live scans so the badge matches scan time.
    """
    if bar_time:
        try:
            dt = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
            h = dt.hour
        except Exception:
            h = datetime.now(timezone.utc).hour
    else:
        h = datetime.now(timezone.utc).hour
    if 7 <= h < 9:
        return {"name": "London Open", "quality": "high", "color": "#22c55e"}
    if 13 <= h < 16:
        return {"name": "London/NY Overlap", "quality": "high", "color": "#22c55e"}
    if 9 <= h < 13:
        return {"name": "London", "quality": "medium", "color": "#3b82f6"}
    if 16 <= h < 22:
        return {"name": "New York", "quality": "medium", "color": "#3b82f6"}
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
            # Find prior price peak/trough index and get RSI at that exact bar
            prior_highs = pr[t : 2 * t]
            prior_lows = cl[t : 2 * t]
            prior_rsi = rsi[t : 2 * t]
            if prior_highs and prior_rsi:
                peak_idx = prior_highs.index(max(prior_highs))
                trough_idx = prior_lows.index(min(prior_lows))
                rsi_at_peak = prior_rsi[peak_idx] if peak_idx < len(prior_rsi) else None
                rsi_at_trough = prior_rsi[trough_idx] if trough_idx < len(prior_rsi) else None
                # Bearish div: higher high in price, lower RSI at the peak
                if rsi_at_peak is not None and pr[-1] > max(prior_highs) and rsi[-1] < rsi_at_peak:
                    w.append("H4 RSI Bearish Divergence")
                # Bullish div: lower low in price, higher RSI at the trough
                if rsi_at_trough is not None and cl[-1] < min(prior_lows) and rsi[-1] > rsi_at_trough:
                    w.append("H4 RSI Bullish Divergence")
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
    "metals": ["XAU/USD", "XAG/USD", "GLD", "SLV"],
    "energy": ["XOM", "XLE", "USO", "WTI Oil", "Brent Oil"],
    "defi": [
        "SOL/USDT",
        "AVAX/USDT",
        "LINK/USDT",
        "BNB/USDT",
        "ETH/USDT",
        "INJ/USDT",
        "NEAR/USDT",
    ],
    "ai_crypto": ["RENDER/USDT"],
    "forex_usd": [
        "EUR/USD",
        "GBP/USD",
        "AUD/USD",
        "NZD/USD",
        "USD/CHF",
        "USD/CAD",
        "USD/ZAR",
        "USD/MXN",
        "USD/SGD",
    ],
    "forex_jpy": ["EUR/JPY", "GBP/JPY", "AUD/JPY"],
    "jse": [
        "Naspers",
        "Sasol",
        "Std Bank",
        "Anglo Am",
        "MTN Group",
        "Shoprite",
        "Richemont",
        "FirstRand",
        "Absa",
        "Capitec",
        "Prosus",
        "Gold Fields",
        "AngloGold",
        "Sibanye",
    ],
    "us_tech": ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG"],
    "us_sp500": ["SPY", "QQQ", "S&P 500", "Nasdaq"],
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


def calc_confluence(
    d1: dict,
    h4: dict,
    h1: dict,
    vr: float,
    stoch: dict,
    pair: dict,
    btc_bias: str,
    d1_candles: list | None = None,
    h4_candles: list | None = None,
    h1_candles: list | None = None,
    funding_rate: float | None = None,
    volume_threshold: float | None = None,
    bar_time: str | None = None,
) -> dict:
    """Factor-based confluence using normalized indicators, regime-aware weights, and correlation filtering.
    Preserves legacy API and raw-threshold warnings for human readability.
    """
    from factor_scoring import compute_factor_scores
    from confidence_engine import compute_confidence

    # Compute factor scores
    factor_result = compute_factor_scores(
        d1["snap"],
        h4["snap"],
        h1["snap"],
        pair,
        d1_candles or [],
        h4_candles or [],
        h1_candles or [],
        vr,
        funding_rate,
        bar_time,
    )
    # Preserve warnings for readability (raw thresholds)
    w = []
    d1["snap"]
    s4 = h4["snap"]
    h1["snap"]
    _ptype = pair["type"]
    _rsi_b = CONFIG["RSI_BOUNDS"].get(_ptype, {"ob": 70, "os": 30})
    r4 = s4.get("rsi")
    if r4 is not None:
        if r4 >= _rsi_b["ob"]:
            w.append(
                f"H4 RSI overbought ({r4:.0f} >= {_rsi_b['ob']}) — wait for pullback"
            )
        elif r4 <= _rsi_b["os"]:
            w.append(f"H4 RSI oversold ({r4:.0f} <= {_rsi_b['os']}) — wait for bounce")
    # Map final_score to legacy 'score' field
    score = factor_result["final_score"]
    direction = factor_result["direction"]
    log.debug(
        f"[FACTOR] {pair.get('display')} score={score:.3f} dir={direction} "
        f"dir_score={factor_result.get('directional_score', 0):.3f} "
        f"nondir_score={factor_result.get('nondirectional_score', 0):.3f} "
        f"factors={factor_result['factor_scores']} regime={factor_result['regime']}"
    )
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
        if adx_val >= 35:
            trend_state = "TRENDING"
        elif adx_val >= 25:
            trend_state = "DEVELOPING"
        elif adx_val >= _rng["dead"]:
            trend_state = "RANGING"
        else:
            trend_state = "DEAD RANGING"
    # Legacy compatibility values
    bull = max(0.0, score)
    bear = max(0.0, score) if direction == "SHORT" else 0.0
    # Spread = abs directional score (0–1 z-score scale): drives conviction label in AI prompt
    spread = round(abs(factor_result.get("directional_score", 0.0)), 2)
    # Rebuild regime as dict so callers can do res['regime'].get('state')
    _regime_str = factor_result.get("regime", "UNKNOWN") or "UNKNOWN"
    _REGIME_STATE = {
        "TRENDING": 0,
        "RANGING": 1,
        "HIGH_VOLATILITY": 2,
        "LOW_VOLATILITY": 3,
    }
    _regime = {"state": _REGIME_STATE.get(_regime_str.upper(), 1), "label": _regime_str}
    w.append(f"Regime: {_regime_str.upper()}")
    # Confidence engine — diagnostic field (graceful degradation)
    # Derive signal classification from factor result flags
    _squeeze_bonus = factor_result.get("squeeze_bonus", False)
    _atr_breakout = factor_result.get("atr_breakout", False)
    _signal_class = classify_signal_setup(
        direction, "trend", squeeze_bonus=_squeeze_bonus,
        atr_breakout=_atr_breakout, votes=v,
    )
    _SIGNAL_TYPE_MAP = {
        "mean_reversion": "mean_reversion",
        "breakout": "breakout",
        "trend_pullback": "trend",
        "trend_continuation": "trend",
    }
    _signal_type = _SIGNAL_TYPE_MAP.get(_signal_class, "trend")
    _d1_proxy = _tf_score_proxy(d1["snap"])
    _h4_proxy = _tf_score_proxy(h4["snap"])
    _h1_proxy = _tf_score_proxy(h1["snap"])
    _conf = compute_confidence(
        factor_result=factor_result,
        d1_factor_result=_d1_proxy,
        h4_factor_result=_h4_proxy,
        h1_factor_result=_h1_proxy,
        signal_type=_signal_type,
        volume_ratio=vr,
    )
    confidence_val = _conf["confidence"]
    # Legacy return dict
    return {
        "score": score,
        "votes": v,
        "direction": direction,
        "bull": round(bull, 1),
        "bear": round(bear, 1),
        "spread": spread,
        "warnings": w,
        "trendState": trend_state,
        "weinsteinStage": None,
        "weinsteinLabel": None,
        "entryMode": _signal_type,
        "adxMomentum": None,
        "adxSlope": None,
        "signalClass": _signal_class,
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
        "factorDiagnostics": {
            "directionalScore": factor_result.get("directional_score"),
            "nondirectionalScore": factor_result.get("nondirectional_score"),
            "directionalConfidenceMultiplier": factor_result.get("directional_confidence_multiplier"),
            "minDirectionalThreshold": factor_result.get("min_directional_threshold"),
            "effectiveMinDirectional": factor_result.get("effective_min_directional"),
            "minDirectionalFailed": factor_result.get("min_directional_failed", False),
            "activeDirectionalFactors": factor_result.get("active_directional_factors", []),
            "activeNondirectionalFactors": factor_result.get("active_nondirectional_factors", []),
            "trendCoherence": factor_result.get("trend_coherence", {}),
            "missingDirectionalOptionalCount": factor_result.get("missing_directional_optional_count"),
            "optionalFactorCoverage": factor_result.get("optional_factor_coverage"),
            "insufficientFactors": factor_result.get("insufficient_factors", False),
        },
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


def _build_event_risk(
    pair: dict, ds_ctx: dict, earnings_ctx: dict, closed_exchanges: set
) -> dict:
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
    threshold = signal.get("scanThreshold", get_min_confluence_threshold(pair))
    score = signal.get("confluenceScore", 0)
    hard_event = signal.get("eventRisk", {}).get("hardBlock", False)
    exchange_closed = signal.get("exchangeClosed", False)
    if (
        pair.get("enabled", True)
        and not exchange_closed
        and not hard_event
        and score >= threshold
    ):
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
