"""scoring.py — Confluence scoring engine, session detection, and divergence detection.

Depends on: config.CONFIG, indicators (calc_* functions).
"""
import logging
from datetime import datetime, timezone

from config import CONFIG
from indicators import (
    calc_rsi, calc_fib, calc_fib_proximity, calc_rsi_divergence, calc_weinstein_stage,
)

log = logging.getLogger("athena")


def get_session() -> dict:
    """Determine current forex session (Asian/London/NY/Overlap) from UTC hour."""
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
    "ai_crypto": ["FET/USDT", "RENDER/USDT", "NEAR/USDT"],
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
                    e200s: float, pair: dict, btc_bias: str,
                    d1_candles: list | None = None,
                    h4_candles: list | None = None,
                    h1_candles: list | None = None) -> dict:
    """Weighted confluence system — higher timeframes score more than lower timeframes.

    Vote weights:
      D1 Trend Gate      = 2.0  (primary trend filter)
      D1 ADX Trend       = 1.5  (confirms D1 is trending)
      D1 Weinstein Stage = 1.5  (independent cycle stage)
      H4 MACD Momentum   = 1.5  (Elder Screen 2 momentum wave)
      H4 RSI Zone        = 1.0  (Cardwell regime health)
      H4 Stochastic      = 1.0  (entry timing)
      H1 EMA Entry       = 1.0  (Elder Screen 3 trigger)
      H1 BB Pullback     = 0.5  (noise-prone)
      H1 RSI Divergence  = 1.0  (strong reversal signal)
      H4 Fib Level       = 1.0  (Murphy price-at-fib)
    Max score = 13.0
    """
    v: dict = {}
    w: list = []
    bull = bear = 0.0
    s = d1["snap"]; s4 = h4["snap"]; s1 = h1["snap"]
    _ptype = pair["type"]

    # ── VOTE 1: D1 Trend Gate — weight 2.0 ──────────────────────────────────
    W1 = 1.0 if _ptype == "forex" else 2.0
    d1_trend = 0
    if s["ema21"] and s["ema50"] and s["ema200"]:
        if s["ema21"] > s["ema50"] > s["ema200"]:
            v["D1 Trend Gate"] = 1;  bull += W1; d1_trend = 1
        elif s["ema21"] < s["ema50"] < s["ema200"]:
            v["D1 Trend Gate"] = -1; bear += W1; d1_trend = -1
        elif _ptype == "crypto" and s["ema21"] > s["ema50"]:
            v["D1 Trend Gate"] = 1; bull += 1.0; d1_trend = 1
            w.append("D1 EMA partial — ema21>ema50 but ema200 not aligned (crypto partial credit 1.0)")
        elif _ptype == "crypto" and s["ema21"] < s["ema50"]:
            v["D1 Trend Gate"] = -1; bear += 1.0; d1_trend = -1
            w.append("D1 EMA partial — ema21<ema50 but ema200 not aligned (crypto partial credit 1.0)")
        else:
            v["D1 Trend Gate"] = 0; w.append("D1 EMA stack mixed — no clear trend")
    else:
        v["D1 Trend Gate"] = 0

    # ── VOTE 2: D1 ADX Trend Strength — weight 1.5 ──────────────────────────
    W2 = 1.5
    d1_adx = s.get("adx"); d1_pdi = s.get("plusDI"); d1_mdi = s.get("minusDI")
    _adx_min = CONFIG["ADX_TREND_MIN_CLASS"].get(_ptype, 25)
    _d1_adx_pct = s.get("adxPct")
    if d1_adx is not None and d1_pdi is not None and d1_mdi is not None:
        if _d1_adx_pct is not None and _d1_adx_pct >= 75:  _w2 = W2
        elif _d1_adx_pct is not None and _d1_adx_pct >= 50: _w2 = 1.0
        elif d1_adx >= _adx_min:                             _w2 = 0.5
        else:                                                 _w2 = 0
        if _w2 > 0:
            if d1_pdi > d1_mdi: v["D1 ADX Trend"] = 1;  bull += _w2
            else:               v["D1 ADX Trend"] = -1; bear += _w2
        else:
            v["D1 ADX Trend"] = 0
            w.append(f"D1 ADX weak ({d1_adx:.1f}, pct:{_d1_adx_pct}) — below {_ptype} threshold ({_adx_min})")
    else:
        v["D1 ADX Trend"] = 0

    # ── VOTE 3: D1 Weinstein Stage — weight 1.5 ─────────────────────────────
    W3 = 1.5
    weinstein_stage = weinstein_label = None
    _wein_lb = CONFIG["WEINSTEIN_LOOKBACK"].get(_ptype, 150)
    if d1_candles:
        weinstein_stage, weinstein_label = calc_weinstein_stage(d1_candles, lookback=_wein_lb)
        if weinstein_stage == 2:   v["D1 Weinstein Stage"] = 1;  bull += W3
        elif weinstein_stage == 4: v["D1 Weinstein Stage"] = -1; bear += W3
        elif weinstein_stage == 3:
            v["D1 Weinstein Stage"] = 0
            w.append(f"Weinstein {weinstein_label} — potential distribution, avoid new longs")
        elif weinstein_stage == 1:
            v["D1 Weinstein Stage"] = 0
            w.append(f"Weinstein {weinstein_label} — basing, wait for Stage 2 breakout")
        else:
            v["D1 Weinstein Stage"] = 0
    else:
        v["D1 Weinstein Stage"] = 0

    hard_long_block  = (v["D1 Trend Gate"] == -1)
    hard_short_block = (v["D1 Trend Gate"] == 1)

    # ── FOREX SESSION FILTER ─────────────────────────────────────────────────
    _forex_session_pen = 0.0
    if _ptype == "forex":
        _sess = get_session()
        _is_asia_active = any(x in pair["display"] for x in ["AUD", "NZD"])
        if _sess["quality"] == "low" and not _is_asia_active:
            _forex_session_pen = 1.5
            w.append(f"FOREX SESSION: {_sess['name']} — off-hours, low-expansion window, score penalised -1.5")
        elif _sess["quality"] == "low" and _is_asia_active:
            w.append(f"FOREX SESSION: {_sess['name']} — but {pair['display']} is active during Asian hours (no penalty)")

    # ── RANGING SUPPRESSION ──────────────────────────────────────────────────
    adx_val = s4["adx"]; adx_prev = s4.get("adxPrev")
    _adx_mom = s4.get("adxMomentum", "stable"); _adx_slope = s4.get("adxSlope", 0)
    ranging_penalty = 0.0
    _rng = CONFIG["RANGING"].get(pair["type"], CONFIG["RANGING"]["commodity"])
    if adx_val is not None and adx_val < _rng["dead"]:
        ranging_penalty = _rng["dead_pen"]
        w.append(f"DEAD RANGING: H4 ADX={adx_val:.1f} (<{_rng['dead']}) — score penalised -{_rng['dead_pen']}, avoid entirely")
    elif adx_val is not None and adx_val < _rng["choppy"]:
        ranging_penalty = _rng["choppy_pen"]
        w.append(f"CHOPPY MARKET: H4 ADX={adx_val:.1f} (<{_rng['choppy']}) — score penalised -{_rng['choppy_pen']}")

    if _ptype == "crypto" and _adx_mom in ("collapsing", "exhausting"):
        _trans_pen = 1.5 if _adx_mom == "collapsing" else 0.8
        ranging_penalty += _trans_pen
        w.append(f"REGIME TRANSITION: H4 ADX {_adx_mom} (slope={_adx_slope}) — trend fading, -{_trans_pen} penalty")

    # ── VOTE 4: H4 MACD Momentum — weight 1.5 ───────────────────────────────
    W4 = 1.5
    if s4["macdLine"] is not None and s4["macdSignal"] is not None and s4["macdHist"] is not None:
        hist_now = s4["macdHist"]; hist_prev = s4.get("macdHistPrev")
        if s4["macdLine"] > s4["macdSignal"] and hist_now > 0:
            v["H4 MACD Momentum"] = 1; bull += W4
            if hist_prev is not None and hist_now < hist_prev:
                w.append("H4 MACD histogram decelerating — momentum fading")
        elif s4["macdLine"] < s4["macdSignal"] and hist_now < 0:
            v["H4 MACD Momentum"] = -1; bear += W4
            if hist_prev is not None and hist_now > hist_prev:
                w.append("H4 MACD histogram decelerating — momentum fading")
        else:
            v["H4 MACD Momentum"] = 0
    else:
        v["H4 MACD Momentum"] = 0

    # ── VOTE 5: H4 RSI Zone — weight 1.0 ────────────────────────────────────
    W5 = 1.0; r4 = s4["rsi"]
    _rsi_b = CONFIG["RSI_BOUNDS"].get(_ptype, {"ob": 78, "os": 22})
    if r4 is not None:
        if 45 < r4 < _rsi_b["ob"]:
            v["H4 RSI Zone"] = 1;  bull += W5
        elif _rsi_b["os"] < r4 < 55:
            v["H4 RSI Zone"] = -1; bear += W5
        elif r4 >= _rsi_b["ob"]:
            v["H4 RSI Zone"] = 0; w.append(f"H4 RSI overbought ({r4:.0f} >= {_rsi_b['ob']}) — wait for pullback")
        elif r4 <= _rsi_b["os"]:
            v["H4 RSI Zone"] = 0; w.append(f"H4 RSI oversold ({r4:.0f} <= {_rsi_b['os']}) — wait for bounce")
        else:
            v["H4 RSI Zone"] = 0
    else:
        v["H4 RSI Zone"] = 0

    # ── VOTE 6: H4 Stochastic — weight 1.0 ──────────────────────────────────
    W6 = 1.0
    lK = stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None
    lD = stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None
    if lK is not None and lD is not None:
        if   lK > lD and lK < 35:       v["H4 Stochastic"] = 1;  bull += W6
        elif lK < lD and lK > 65:       v["H4 Stochastic"] = -1; bear += W6
        elif lK > lD and 35 <= lK <= 55: v["H4 Stochastic"] = 1;  bull += W6
        elif lK < lD and 45 <= lK <= 65: v["H4 Stochastic"] = -1; bear += W6
        else:                            v["H4 Stochastic"] = 0
    else:
        v["H4 Stochastic"] = 0

    # ── VOTE 7: H1 EMA Entry — weight 1.0 ───────────────────────────────────
    W7 = 1.0
    if s1["ema21"] and s1["ema50"]:
        if s1["ema21"] > s1["ema50"]: v["H1 EMA Entry"] = 1;  bull += W7
        else:                          v["H1 EMA Entry"] = -1; bear += W7
    else:
        v["H1 EMA Entry"] = 0

    # ── VOTE 8: H1 BB Pullback Zone — weight 0.5 ────────────────────────────
    W8 = 0.5
    if s1["bbUpper"] is not None and s1["bbLower"] is not None:
        bbr = s1["bbUpper"] - s1["bbLower"]; cl1_p = s1.get("close")
        if bbr > 0 and cl1_p is not None:
            bbp = (cl1_p - s1["bbLower"]) / bbr
            if bbp < 0.25:   v["H1 BB Pullback"] = 1;  bull += W8
            elif bbp > 0.75: v["H1 BB Pullback"] = -1; bear += W8
            else:            v["H1 BB Pullback"] = 0
        else:
            v["H1 BB Pullback"] = 0
    else:
        v["H1 BB Pullback"] = 0

    # ── VOTE 9: H1 RSI Divergence — weight 1.0 ──────────────────────────────
    W9 = 1.0
    if h1_candles:
        h1_div = calc_rsi_divergence(h1_candles)
        if h1_div == "bullish":
            v["H1 RSI Divergence"] = 1;  bull += W9
            w.append("H1 RSI Bullish Divergence — reversal signal (Wilder)")
        elif h1_div == "bearish":
            v["H1 RSI Divergence"] = -1; bear += W9
            w.append("H1 RSI Bearish Divergence — reversal signal (Wilder)")
        else:
            v["H1 RSI Divergence"] = 0
    else:
        v["H1 RSI Divergence"] = 0

    # ── VOTE 10: H4 Fib Confluence — weight 1.0 ─────────────────────────────
    W10 = 1.0
    if h4_candles:
        h4_fib = calc_fib(h4_candles)
        fib_vote = calc_fib_proximity(s4.get("close", 0) or 0, h4_fib)
        v["H4 Fib Level"] = fib_vote
        if fib_vote == 1:    bull += W10
        elif fib_vote == -1: bear += W10
    else:
        v["H4 Fib Level"] = 0

    # ── DIRECTION decided after ALL 10 votes are tallied ─────────────────────
    direction = "LONG" if bull >= bear else "SHORT"

    # ATR compression bonus for crypto
    _atr_pct = s4.get("atrPct"); _atr_lbl = s4.get("atrLabel", "")
    if _ptype == "crypto" and _atr_pct is not None:
        if _atr_pct <= 25 and _atr_lbl == "expanding":
            if direction == "LONG":  bull += 0.5
            else:                    bear += 0.5
            w.append(f"ATR COMPRESSION BREAKOUT: ATR pct={_atr_pct} (25th) expanding (+0.5)")
        elif _atr_pct >= 75:
            w.append(f"ATR EXTENDED: ATR pct={_atr_pct} (75th+) — already extended, late entry risk")

    # Range mean-reversion mode
    _entry_mode = "trend"
    if (_ptype == "forex" and ranging_penalty > 0) or (_ptype == "crypto" and _adx_mom in ("collapsing", "exhausting")):
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

    # Volume context (non-forex only)
    if _ptype != "forex":
        if vr >= CONFIG["VOLUME_THRESHOLD"]:
            w.append(f"High volume ({vr:.1f}x) confirms move")
        elif max(bull, bear) >= 5:
            w.append(f"Low volume ({vr:.1f}x avg) — confirm before entry")

    # Intermarket: BTC bias for alts
    if pair["type"] == "crypto" and pair["symbol"] != "BTCUSDT":
        if direction == "LONG"  and btc_bias == "bearish":
            w.append("BTC bearish — alt LONG is counter-trend risk")
        elif direction == "SHORT" and btc_bias == "bullish":
            w.append("BTC bullish — alt SHORT is counter-trend risk")

    # Final score
    raw_score = max(bull, bear)
    score = max(0.0, raw_score - ranging_penalty - _forex_session_pen)

    _ct_pen = abs(CONFIG["COUNTER_TREND_PEN"].get(_ptype, -3.0))
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
    return {
        "score": score, "votes": v, "direction": direction,
        "bull": round(bull, 1), "bear": round(bear, 1), "spread": spread,
        "warnings": w, "trendState": trend_state,
        "weinsteinStage": weinstein_stage, "weinsteinLabel": weinstein_label,
        "entryMode": _entry_mode, "adxMomentum": _adx_mom, "adxSlope": _adx_slope,
    }
