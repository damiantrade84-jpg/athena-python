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
    """Spec-aligned confluence system with per-class vote routing.

    Categories (balanced to ~25/25/25/17/8 split):
      TREND:     D1 Trend Gate (2.0) + H1 EMA Entry (1.0)
      MOMENTUM:  D1 ADX Strength (1.0) + H4 MACD (1.0) + H4 Oscillator (1.0)
      FLOW:      Volume (1.0) + Funding/Session per class (1.0)
      STRUCTURE: H4 Fib (1.0) + H1 BB Pullback (1.0)
      CYCLE:     Weinstein (1.0 stocks, 0 crypto/forex — AI context only)
    BONUS:       H1 RSI Divergence (+1.0 when detected)
    Per-class max varies: crypto ~9.25, forex ~9.0, stock ~10.0, commodity/index ~9.5
    All indicators STILL COMPUTED and fed to Marcus Reid AI regardless of per-class weight.
    """
    v: dict = {}
    w: list = []
    bull = bear = 0.0
    s = d1["snap"]; s4 = h4["snap"]; s1 = h1["snap"]
    _ptype = pair["type"]
    _vw = get_pair_vote_weights(pair)

    # ── CAT 1 / TREND: D1 Trend Gate — per-class weight ─────────────────────
    W_TREND = _vw.get("d1_trend", 2.0)
    d1_trend = 0
    if s["ema21"] and s["ema50"] and s["ema200"]:
        if s["ema21"] > s["ema50"] > s["ema200"]:
            v["D1 Trend Gate"] = 1;  bull += W_TREND; d1_trend = 1
        elif s["ema21"] < s["ema50"] < s["ema200"]:
            v["D1 Trend Gate"] = -1; bear += W_TREND; d1_trend = -1
        elif s["ema21"] > s["ema50"]:
            v["D1 Trend Gate"] = 1; bull += W_TREND * 0.5; d1_trend = 1
            w.append(f"D1 EMA partial — ema21>ema50 but ema200 not aligned (partial credit {W_TREND * 0.5:.1f})")
        elif s["ema21"] < s["ema50"]:
            v["D1 Trend Gate"] = -1; bear += W_TREND * 0.5; d1_trend = -1
            w.append(f"D1 EMA partial — ema21<ema50 but ema200 not aligned (partial credit {W_TREND * 0.5:.1f})")
        else:
            v["D1 Trend Gate"] = 0; w.append("D1 EMA stack mixed — no clear trend")
    else:
        v["D1 Trend Gate"] = 0

    # ── CAT 2 / MOMENTUM: D1 ADX Strength — STRENGTH ONLY, not direction ────
    W_ADX = _vw.get("d1_adx", 1.0)
    d1_adx = s.get("adx"); d1_pdi = s.get("plusDI"); d1_mdi = s.get("minusDI")
    _adx_min = CONFIG["ADX_TREND_MIN_CLASS"].get(_ptype, 25)
    _d1_adx_pct = s.get("adxPct")
    _adx_strength = 0
    if d1_adx is not None:
        if _d1_adx_pct is not None and _d1_adx_pct >= 75:    _adx_strength = W_ADX
        elif _d1_adx_pct is not None and _d1_adx_pct >= 50:  _adx_strength = W_ADX * 0.67
        elif d1_adx >= _adx_min:                               _adx_strength = W_ADX * 0.33
        if _adx_strength > 0:
            v["D1 ADX Strength"] = 1
            if d1_trend == 1:    bull += _adx_strength
            elif d1_trend == -1: bear += _adx_strength
            else:
                if d1_pdi is not None and d1_mdi is not None:
                    if d1_pdi > d1_mdi: bull += _adx_strength
                    else:               bear += _adx_strength
                else:
                    bull += _adx_strength * 0.5; bear += _adx_strength * 0.5
        else:
            v["D1 ADX Strength"] = 0
            w.append(f"D1 ADX weak ({d1_adx:.1f}, pct:{_d1_adx_pct}) — below {_ptype} threshold ({_adx_min})")
    else:
        v["D1 ADX Strength"] = 0

    # ── CAT 5 / CYCLE: Weinstein Stage — per-class weight ───────────────────
    W_WEIN = _vw.get("weinstein", 0.0)
    weinstein_stage = weinstein_label = None
    _wein_lb = CONFIG["WEINSTEIN_LOOKBACK"].get(_ptype, 150)
    if d1_candles:
        weinstein_stage, weinstein_label = calc_weinstein_stage(d1_candles, lookback=_wein_lb)
        if W_WEIN > 0:
            if weinstein_stage == 2:   v["D1 Weinstein Stage"] = 1;  bull += W_WEIN
            elif weinstein_stage == 4: v["D1 Weinstein Stage"] = -1; bear += W_WEIN
            elif weinstein_stage == 3 and pair_filter_enabled(pair, "weinstein"):
                v["D1 Weinstein Stage"] = 0
                w.append(f"Weinstein {weinstein_label} — potential distribution, avoid new longs")
            elif weinstein_stage == 1 and pair_filter_enabled(pair, "weinstein"):
                v["D1 Weinstein Stage"] = 0
                w.append(f"Weinstein {weinstein_label} — basing, wait for Stage 2 breakout")
            else:
                v["D1 Weinstein Stage"] = 0
        else:
            v["D1 Weinstein Stage"] = 0
            if weinstein_stage in (3, 1) and pair_filter_enabled(pair, "weinstein"):
                w.append(f"Weinstein {weinstein_label} — AI context only for {_ptype} (weight=0)")
    else:
        v["D1 Weinstein Stage"] = 0

    hard_long_block  = (v["D1 Trend Gate"] == -1)
    hard_short_block = (v["D1 Trend Gate"] == 1)

    # ── CAT 3 / FLOW: Session Quality — scored for forex, warning for others ─
    W_SESS = _vw.get("session", 0.0)
    # bar_time: use historical bar timestamp in backtest so session vote reflects
    # when the trade would actually have been taken, not the current wall-clock time.
    _bar_ts = bar_time
    if not _bar_ts and h4_candles:
        _bar_ts = (h4_candles[-1] or {}).get("time")
    _sess = get_session(_bar_ts)
    _is_asia_active = any(x in pair["display"] for x in ["AUD", "NZD"])
    if W_SESS > 0 and pair_filter_enabled(pair, "session"):
        if _sess["quality"] == "high":
            v["Session Quality"] = 1
            bull += W_SESS * 0.5; bear += W_SESS * 0.5
        elif _sess["quality"] == "medium":
            v["Session Quality"] = 0
        elif _is_asia_active:
            v["Session Quality"] = 0
            w.append(f"FOREX SESSION: {_sess['name']} — {pair['display']} active during Asian hours")
        else:
            v["Session Quality"] = -1
            w.append(f"FOREX SESSION: {_sess['name']} — off-hours, low-expansion window")
    else:
        v["Session Quality"] = 0
        if _ptype == "forex" and _sess["quality"] == "low" and not _is_asia_active:
            w.append(f"FOREX SESSION: {_sess['name']} — off-hours (warning only for {_ptype})")

    # ── RANGING SUPPRESSION — via detect_regime ──────────────────────────────
    adx_val = s4["adx"]; adx_prev = s4.get("adxPrev")
    _adx_mom = s4.get("adxMomentum", "stable"); _adx_slope = s4.get("adxSlope", 0)
    _rng = CONFIG["RANGING"].get(pair["type"], CONFIG["RANGING"]["commodity"])
    _bbw_pct = None
    if h4_candles and len(h4_candles) >= 50:
        _bbw_pct, _bbw_lbl = calc_bb_width_percentile(h4_candles)
    _regime = detect_regime(s4, _ptype, bb_width_pct=_bbw_pct)
    ranging_penalty = _regime["ranging_penalty"]
    if adx_val is not None and adx_val < _rng["dead"]:
        w.append(f"DEAD RANGING: H4 ADX={adx_val:.1f} (<{_rng['dead']}) — score penalised -{_rng['dead_pen']}, avoid entirely")
    elif adx_val is not None and adx_val < _rng["choppy"]:
        w.append(f"CHOPPY MARKET: H4 ADX={adx_val:.1f} (<{_rng['choppy']}) — score penalised -{_rng['choppy_pen']}")

    if _ptype == "crypto" and _adx_mom in ("collapsing", "exhausting") and pair_filter_enabled(pair, "regime_transition"):
        w.append(f"REGIME TRANSITION: H4 ADX {_adx_mom} (slope={_adx_slope}) — trend fading (warning only)")

    # ── CAT 2 / MOMENTUM: H4 MACD — per-class weight ────────────────────────
    W_MACD = _vw.get("h4_macd", 1.0)
    if s4["macdLine"] is not None and s4["macdSignal"] is not None and s4["macdHist"] is not None:
        hist_now = s4["macdHist"]; hist_prev = s4.get("macdHistPrev")
        if s4["macdLine"] > s4["macdSignal"] and hist_now > 0:
            v["H4 MACD Momentum"] = 1; bull += W_MACD
            if hist_prev is not None and hist_now < hist_prev:
                w.append("H4 MACD histogram decelerating — momentum fading")
        elif s4["macdLine"] < s4["macdSignal"] and hist_now < 0:
            v["H4 MACD Momentum"] = -1; bear += W_MACD
            if hist_prev is not None and hist_now > hist_prev:
                w.append("H4 MACD histogram decelerating — momentum fading")
        else:
            v["H4 MACD Momentum"] = 0
    else:
        v["H4 MACD Momentum"] = 0

    # ── CAT 2 / MOMENTUM: H4 Oscillator (merged RSI + Stoch) — per-class ────
    W_OSC = _vw.get("h4_oscillator", 1.0)
    r4 = s4["rsi"]
    _rsi_b = CONFIG["RSI_BOUNDS"].get(_ptype, {"ob": 78, "os": 22})
    lK = stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None
    lD = stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None
    _rsi_dir = 0; _stoch_dir = 0
    if r4 is not None:
        # RSI zones are intentionally asymmetric: LONG fires in (45, ob) — biased toward
        # overbought momentum; SHORT fires in (os, 55) — biased toward oversold reversal.
        # Both zones span the same width but are offset so ambiguous mid-range (55-ob) only
        # allows LONG signals, reinforcing the long-bias of trending momentum markets.
        if 45 < r4 < _rsi_b["ob"]:      _rsi_dir = 1
        elif _rsi_b["os"] < r4 < 55:    _rsi_dir = -1
        elif r4 >= _rsi_b["ob"]:
            w.append(f"H4 RSI overbought ({r4:.0f} >= {_rsi_b['ob']}) — wait for pullback")
        elif r4 <= _rsi_b["os"]:
            w.append(f"H4 RSI oversold ({r4:.0f} <= {_rsi_b['os']}) — wait for bounce")
    if lK is not None and lD is not None:
        if   lK > lD and lK < 35:        _stoch_dir = 1
        elif lK < lD and lK > 65:        _stoch_dir = -1
        elif lK > lD and 35 <= lK <= 55: _stoch_dir = 1
        elif lK < lD and 45 <= lK <= 65: _stoch_dir = -1
    if _rsi_dir != 0 and _stoch_dir != 0 and _rsi_dir == _stoch_dir:
        v["H4 Oscillator"] = _rsi_dir
        if _rsi_dir == 1: bull += W_OSC
        else:              bear += W_OSC
    elif _rsi_dir != 0:
        v["H4 Oscillator"] = _rsi_dir
        if _rsi_dir == 1: bull += W_OSC * 0.5
        else:              bear += W_OSC * 0.5
        w.append(f"H4 Oscillator split — RSI={_rsi_dir}, Stoch={'neutral' if _stoch_dir == 0 else 'opposite'} (half credit)")
    elif _stoch_dir != 0:
        v["H4 Oscillator"] = _stoch_dir
        if _stoch_dir == 1: bull += W_OSC * 0.5
        else:                bear += W_OSC * 0.5
        w.append(f"H4 Oscillator split — Stoch={_stoch_dir}, RSI neutral (half credit)")
    else:
        v["H4 Oscillator"] = 0

    # ── CAT 1 / TREND: H1 EMA Entry — per-class weight ──────────────────────
    W_H1EMA = _vw.get("h1_ema", 1.0)
    if s1["ema21"] and s1["ema50"]:
        if s1["ema21"] > s1["ema50"]: v["H1 EMA Entry"] = 1;  bull += W_H1EMA
        else:                          v["H1 EMA Entry"] = -1; bear += W_H1EMA
    else:
        v["H1 EMA Entry"] = 0

    # ── CAT 4 / STRUCTURE: H1 BB Pullback — per-class weight (upgraded) ─────
    W_BB = _vw.get("h1_bb", 1.0)
    if s1["bbUpper"] is not None and s1["bbLower"] is not None:
        bbr = s1["bbUpper"] - s1["bbLower"]; cl1_p = s1.get("close")
        if bbr > 0 and cl1_p is not None:
            bbp = (cl1_p - s1["bbLower"]) / bbr
            if bbp < 0.25:   v["H1 BB Pullback"] = 1;  bull += W_BB
            elif bbp > 0.75: v["H1 BB Pullback"] = -1; bear += W_BB
            else:            v["H1 BB Pullback"] = 0
        else:
            v["H1 BB Pullback"] = 0
    else:
        v["H1 BB Pullback"] = 0

    # ── BONUS: H1 RSI Divergence — per-class weight (not in base max) ───────
    W_DIV = _vw.get("divergence", 1.0)
    if h1_candles:
        h1_div = calc_rsi_divergence(h1_candles)
        if h1_div == "bullish":
            v["H1 RSI Divergence"] = 1;  bull += W_DIV
            w.append("H1 RSI Bullish Divergence — reversal signal (Wilder)")
        elif h1_div == "bearish":
            v["H1 RSI Divergence"] = -1; bear += W_DIV
            w.append("H1 RSI Bearish Divergence — reversal signal (Wilder)")
        else:
            v["H1 RSI Divergence"] = 0
    else:
        v["H1 RSI Divergence"] = 0

    # ── CAT 4 / STRUCTURE: H4 Fib Confluence — per-class weight ─────────────
    W_FIB = _vw.get("h4_fib", 1.0)
    if h4_candles:
        h4_fib = calc_fib(h4_candles)
        fib_vote = calc_fib_proximity(s4.get("close", 0) or 0, h4_fib)
        v["H4 Fib Level"] = fib_vote
        if fib_vote == 1:    bull += W_FIB
        elif fib_vote == -1: bear += W_FIB
    else:
        v["H4 Fib Level"] = 0

    # ── CAT 3 / FLOW: Volume Ratio — promoted to scored vote (non-forex) ────
    W_VOL = _vw.get("volume", 0.0)
    _vol_thr = CONFIG["VOLUME_THRESHOLD"] if volume_threshold is None else float(volume_threshold)
    if W_VOL > 0:
        if vr >= _vol_thr:
            v["Volume"] = 1
            if d1_trend == 1 or bull > bear: bull += W_VOL
            else:                             bear += W_VOL
            w.append(f"High volume ({vr:.1f}x avg) confirms move (+{W_VOL})")
        else:
            v["Volume"] = 0
            if max(bull, bear) >= 4:
                w.append(f"Low volume ({vr:.1f}x avg) — confirm before entry")
    else:
        v["Volume"] = 0
        if _ptype != "forex" and vr >= _vol_thr:
            w.append(f"High volume ({vr:.1f}x) confirms move (warning only for {_ptype})")
        elif _ptype != "forex" and max(bull, bear) >= 4:
            w.append(f"Low volume ({vr:.1f}x avg) — confirm before entry")

    # ── OBV trend confirmation (warning-only, not a scored vote) ─────────────
    if h4_candles and _ptype != "forex" and pair_filter_enabled(pair, "obv"):
        _obv = calc_obv_trend(h4_candles, lookback=20)
        if _obv == "diverging_bearish":
            w.append("OBV BEARISH DIVERGENCE: price rising but volume accumulation falling — rally may be exhausting")
        elif _obv == "diverging_bullish":
            w.append("OBV BULLISH DIVERGENCE: price falling but volume accumulation rising — selling pressure weakening")

    # ── CAT 3 / FLOW: Funding Rate — per-class weight (crypto only) ─────────
    W_FUND = _vw.get("funding", 0.0)
    _funding_vote_active = False
    if _ptype == "crypto" and funding_rate is not None:
        _funding_vote_active = True
        if funding_rate > 0.001 and pair_filter_enabled(pair, "funding"):
            w.append(f"EXTREME FUNDING: {funding_rate*100:.4f}% — overleveraged, reversal risk")
        if W_FUND > 0:
            if funding_rate < 0.0001:
                v["Funding Rate"] = 1;  bull += W_FUND
            elif funding_rate > 0.0005:
                v["Funding Rate"] = -1; bear += W_FUND
            else:
                v["Funding Rate"] = 0
        else:
            v["Funding Rate"] = 0
    else:
        v["Funding Rate"] = 0

    # ── DIRECTION decided after ALL votes are tallied ─────────────────────────
    # Tie-break: bull >= bear → LONG (intentional long bias; ties are rare with weighted votes).
    direction = "LONG" if bull >= bear else "SHORT"

    # ATR compression bonus for crypto
    _atr_pct = s4.get("atrPct"); _atr_lbl = s4.get("atrLabel", "")
    _atr_breakout = False
    if _ptype == "crypto" and _atr_pct is not None:
        if _atr_pct <= 25 and _atr_lbl == "expanding":
            if direction == "LONG":  bull += 0.5
            else:                    bear += 0.5
            _atr_breakout = True
            w.append(f"ATR COMPRESSION BREAKOUT: ATR pct={_atr_pct} (25th) expanding (+0.5)")
        elif _atr_pct >= 75:
            w.append(f"ATR EXTENDED: ATR pct={_atr_pct} (75th+) — already extended, late entry risk")

    # Volatility squeeze detection (BB inside Keltner) — all asset classes
    _squeeze_bonus = False
    if h4_candles and len(h4_candles) >= 25 and pair_filter_enabled(pair, "squeeze"):
        _squeeze = calc_squeeze(h4_candles)
        if _squeeze and _squeeze["active"]:
            _sq_bars = _squeeze["bars"]
            _sq_mom = _squeeze["momentum"]
            if _sq_bars >= 6:
                # Sustained squeeze — breakout imminent, add bonus
                if _sq_mom == "bullish" and (d1_trend == 1 or bull > bear):
                    bull += 0.5; _squeeze_bonus = True
                elif _sq_mom == "bearish" and (d1_trend == -1 or bear > bull):
                    bear += 0.5; _squeeze_bonus = True
                w.append(f"VOLATILITY SQUEEZE: BB inside Keltner for {_sq_bars} bars — {_sq_mom} breakout likely (+0.5)")
            else:
                w.append(f"VOLATILITY SQUEEZE: BB inside Keltner for {_sq_bars} bars — watch for breakout")

    # Range mean-reversion mode
    _entry_mode = "trend"
    if pair_filter_enabled(pair, "mean_revert") and ((_ptype == "forex" and ranging_penalty > 0) or (_ptype == "crypto" and _adx_mom in ("collapsing", "exhausting"))):
        _h4_bbp = None
        if s4["bbUpper"] is not None and s4["bbLower"] is not None:
            _bbr4 = s4["bbUpper"] - s4["bbLower"]
            if _bbr4 > 0:
                _h4_bbp = (s4.get("close", 0) - s4["bbLower"]) / _bbr4
        if _h4_bbp is not None and r4 is not None:
            if (_h4_bbp < 0.20 and r4 < 40) or (_h4_bbp > 0.80 and r4 > 60):
                _entry_mode = "mean_revert"
                ranging_penalty = max(0, ranging_penalty - 1.0)
                if _h4_bbp < 0.20: direction = "LONG"
                elif _h4_bbp > 0.80: direction = "SHORT"
                w.append(f"MEAN-REVERT: BB%={_h4_bbp:.2f}, ranging penalty reduced — fade to BB mid")

    # Intermarket: BTC bias for alts
    if pair["type"] == "crypto" and pair["symbol"] != "BTCUSDT" and pair_filter_enabled(pair, "btc_bias"):
        if direction == "LONG"  and btc_bias == "bearish":
            w.append("BTC bearish — alt LONG is counter-trend risk")
        elif direction == "SHORT" and btc_bias == "bullish":
            w.append("BTC bullish — alt SHORT is counter-trend risk")

    # Final score — one penalty per condition (no stacking)
    raw_score = max(bull, bear)
    score = max(0.0, raw_score - ranging_penalty)

    _ct_pen = abs(CONFIG["COUNTER_TREND_PEN"].get(_ptype, -1.0))
    if direction == "LONG" and hard_long_block:
        w.append(f"COUNTER-TREND: D1 bearish — Elder Triple Screen violation, -{_ct_pen} score")
        score = max(0.0, score - _ct_pen)
    if direction == "SHORT" and hard_short_block:
        w.append(f"COUNTER-TREND: D1 bullish — Elder Triple Screen violation, -{_ct_pen} score")
        score = max(0.0, score - _ct_pen)

    score = round(score, 1)

    # Trend state label
    if adx_val is not None:
        if adx_val >= 35:               trend_state = "TRENDING"
        elif adx_val >= 25:             trend_state = "DEVELOPING"
        elif adx_val >= _rng["dead"]:   trend_state = "RANGING"
        else:                           trend_state = "DEAD RANGING"
    else:
        trend_state = "UNKNOWN"

    atr_mults = CONFIG["ATR_CLASS"].get(pair["type"], {"sl": CONFIG["SL_ATR_MULT"]})
    if s1["atr"] and s1.get("close") and s1["atr"] * atr_mults["sl"] > s1["close"] * 0.03:
        w.append("Wide SL > 3% of price — size down")

    spread = round(abs(bull - bear), 1)
    # Per-class base max from VOTE_WEIGHTS (sum all non-divergence weights).
    # Session quality adds W_SESS*0.5 to BOTH bull and bear simultaneously — net directional
    # contribution is 0.5 max, not the full W_SESS. Subtract the unused half so confluencePct
    # is not overstated (e.g. forex: 8.5 reported → 8.0 effective when W_SESS=1.0).
    _base_max = sum(v for k, v in _vw.items() if k != "divergence")
    if W_SESS > 0:
        _base_max -= W_SESS * 0.5
    signal_class = classify_signal_setup(direction, _entry_mode,
                                         squeeze_bonus=_squeeze_bonus, atr_breakout=_atr_breakout,
                                         votes=v)
    return {
        "score": score, "votes": v, "direction": direction,
        "bull": round(bull, 1), "bear": round(bear, 1), "spread": spread,
        "warnings": w, "trendState": trend_state,
        "weinsteinStage": weinstein_stage, "weinsteinLabel": weinstein_label,
        "entryMode": _entry_mode, "adxMomentum": _adx_mom, "adxSlope": _adx_slope,
        "signalClass": signal_class,
        "regime": _regime,
        "fundingRate": funding_rate,
        "maxScoreOverride": round(_base_max, 2),
    }
