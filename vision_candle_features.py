"""vision_candle_features.py — Deterministic candle feature extraction for AI Vision."""

from typing import Dict, Any, List
import pandas as pd
import numpy as np

def extract_candle_features(candles: List[Dict[str, Any]], direction: str = "UNKNOWN", lookback: int = 20) -> Dict[str, Any]:
    """Extract quantitative candlestick patterns, anatomy metrics, and multi-TF sequencing."""
    if not candles:
        return {}
        
    df = pd.DataFrame(candles)
    col_map = {
        'open': 'Open', 'o': 'Open',
        'high': 'High', 'h': 'High',
        'low': 'Low', 'l': 'Low',
        'close': 'Close', 'c': 'Close',
        'vol': 'Volume', 'v': 'Volume', 'volume': 'Volume'
    }
    for k, v in col_map.items():
        if k in df.columns and v not in df.columns:
            df[v] = df[k]
            
    required = ['Open', 'High', 'Low', 'Close']
    for col in required:
        if col not in df.columns:
            return {}
            
    df['Open'] = pd.to_numeric(df['Open'], errors='coerce')
    df['High'] = pd.to_numeric(df['High'], errors='coerce')
    df['Low'] = pd.to_numeric(df['Low'], errors='coerce')
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    if 'Volume' in df.columns:
        df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce')
    else:
        df['Volume'] = np.nan
        
    df = df.dropna(subset=required).reset_index(drop=True)
    if df.empty:
        return {}
        
    lookback = max(3, int(lookback))
    df = df.tail(lookback).reset_index(drop=True)
    
    # 1. Anatomy (last bar)
    last = df.iloc[-1]
    o, h, l, c = float(last['Open']), float(last['High']), float(last['Low']), float(last['Close'])
    vol = float(last['Volume']) if not pd.isna(last['Volume']) else 0.0
    
    body_size = abs(c - o)
    range_size = max(h - l, 1e-9)
    body_pct_of_range = (body_size / range_size) * 100
    
    upper_wick_size = max(0.0, h - max(o, c))
    lower_wick_size = max(0.0, min(o, c) - l)
    upper_wick_pct = (upper_wick_size / range_size) * 100
    lower_wick_pct = (lower_wick_size / range_size) * 100
    
    close_location_pct = ((c - l) / range_size) * 100
    last_direction = "bull" if c > o else "bear" if c < o else "doji"
    
    anatomy = {
        "last_open": o, "last_high": h, "last_low": l, "last_close": c,
        "last_volume": vol, "last_direction": last_direction, "body_size": body_size,
        "range_size": range_size, "body_pct_of_range": body_pct_of_range,
        "upper_wick_size": upper_wick_size, "lower_wick_size": lower_wick_size,
        "upper_wick_pct": upper_wick_pct, "lower_wick_pct": lower_wick_pct,
        "close_location_pct": close_location_pct
    }
    
    # 2. Sequence
    df3 = df.tail(3)
    df5 = df.tail(5)
    def get_dir(row):
        return "bull" if row['Close'] > row['Open'] else "bear" if row['Close'] < row['Open'] else "doji"
        
    df3_dirs = df3.apply(get_dir, axis=1).tolist()
    df5_dirs = df5.apply(get_dir, axis=1).tolist()
    
    seq = {
        "last3_bull_count": df3_dirs.count("bull"),
        "last3_bear_count": df3_dirs.count("bear"),
        "last3_doji_count": df3_dirs.count("doji"),
        "last5_bull_count": df5_dirs.count("bull"),
        "last5_bear_count": df5_dirs.count("bear"),
        "last5_doji_count": df5_dirs.count("doji"),
    }
    
    bodies = (df5['Close'] - df5['Open']).abs().tolist() if len(df5) >= 3 else [body_size]
    ranges = (df5['High'] - df5['Low']).tolist() if len(df5) >= 3 else [range_size]
    
    def get_expansion(vals):
        if len(vals) < 3: return "mixed"
        if vals[-1] > vals[-2] > vals[-3]: return "expanding"
        if vals[-1] < vals[-2] < vals[-3]: return "contracting"
        return "mixed"
        
    seq["body_expansion_sequence"] = get_expansion(bodies)
    seq["range_expansion_sequence"] = get_expansion(ranges)
    
    closes = df5['Close'].tolist() if len(df5) >= 3 else [c]
    if len(closes) >= 3:
        if closes[-1] > closes[-2] > closes[-3]: seq["close_progression"] = "higher_closes"
        elif closes[-1] < closes[-2] < closes[-3]: seq["close_progression"] = "lower_closes"
        else: seq["close_progression"] = "mixed"
    else:
        seq["close_progression"] = "mixed"
        
    upper_wicks = df5.apply(lambda r: max(0, r['High'] - max(r['Open'], r['Close'])), axis=1).sum() if len(df5) >= 3 else upper_wick_size
    lower_wicks = df5.apply(lambda r: max(0, min(r['Open'], r['Close']) - r['Low']), axis=1).sum() if len(df5) >= 3 else lower_wick_size
    if upper_wicks > lower_wicks * 1.5: seq["wick_pressure"] = "upper_wick_dominant"
    elif lower_wicks > upper_wicks * 1.5: seq["wick_pressure"] = "lower_wick_dominant"
    else: seq["wick_pressure"] = "balanced"
    
    if seq["last5_bull_count"] >= 4: seq["momentum_sequence"] = "bullish_control"
    elif seq["last5_bear_count"] >= 4: seq["momentum_sequence"] = "bearish_control"
    elif seq["last3_doji_count"] >= 2: seq["momentum_sequence"] = "exhaustion_risk"
    else: seq["momentum_sequence"] = "neutral"
    
    # 3. Patterns
    patterns = []
    for i in range(len(df)):
        if i == 0: continue
        row = df.iloc[i]
        prev = df.iloc[i-1]
        o_cur, h_cur, l_cur, c_cur = float(row['Open']), float(row['High']), float(row['Low']), float(row['Close'])
        o_prev, h_prev, l_prev, c_prev = float(prev['Open']), float(prev['High']), float(prev['Low']), float(prev['Close'])
        
        b_cur = abs(c_cur - o_cur)
        r_cur = max(h_cur - l_cur, 1e-9)
        u_cur = max(0.0, h_cur - max(o_cur, c_cur))
        lw_cur = max(0.0, min(o_cur, c_cur) - l_cur)
        
        if b_cur <= (r_cur * 0.1):
            patterns.append({"name": "doji", "bias": "neutral", "index": i, "confidence": 0.8, "context_note": "Doji"})
        elif b_cur <= (r_cur * 0.3) and u_cur >= b_cur and lw_cur >= b_cur:
            patterns.append({"name": "spinning top", "bias": "neutral", "index": i, "confidence": 0.7, "context_note": "Spinning top"})
            
        if lw_cur >= b_cur * 2.0 and u_cur <= b_cur * 0.5:
            patterns.append({"name": "hammer" if c_cur > o_cur else "hanging man", "bias": "bull" if c_cur > o_cur else "bear", "index": i, "confidence": 0.8, "context_note": "Hammer/Hanging Man"})
        if u_cur >= b_cur * 2.0 and lw_cur <= b_cur * 0.5:
            patterns.append({"name": "inverted hammer" if c_cur > o_cur else "shooting star", "bias": "bull" if c_cur > o_cur else "bear", "index": i, "confidence": 0.7, "context_note": "Inverted Hammer/Shooting Star"})
            
        if c_prev < o_prev and c_cur > o_cur and o_cur <= c_prev and c_cur >= o_prev:
            patterns.append({"name": "bullish engulfing", "bias": "bull", "index": i, "confidence": 0.9, "context_note": "Bullish engulfing"})
        elif c_prev > o_prev and c_cur < o_cur and o_cur >= c_prev and c_cur <= o_prev:
            patterns.append({"name": "bearish engulfing", "bias": "bear", "index": i, "confidence": 0.9, "context_note": "Bearish engulfing"})
            
        if h_cur <= h_prev and l_cur >= l_prev:
            patterns.append({"name": "inside bar", "bias": "neutral", "index": i, "confidence": 0.7, "context_note": "Inside bar"})
        elif h_cur >= h_prev and l_cur <= l_prev:
            patterns.append({"name": "outside bar", "bias": "neutral", "index": i, "confidence": 0.7, "context_note": "Outside bar"})
            
        if b_cur >= r_cur * 0.85:
            patterns.append({"name": "marubozu", "bias": "bull" if c_cur > o_cur else "bear", "index": i, "confidence": 0.9, "context_note": "Marubozu"})
            
    # 4. Liquidity/Auction
    bullish_sweep_reclaim = False
    bearish_sweep_reject = False
    if len(df) >= 2:
        row = df.iloc[-1]
        prev = df.iloc[-2]
        if float(row['Low']) < float(prev['Low']) and float(row['Close']) > float(prev['Low']): bullish_sweep_reclaim = True
        if float(row['High']) > float(prev['High']) and float(row['Close']) < float(prev['High']): bearish_sweep_reject = True
        
    failed_breakout = bearish_sweep_reject
    failed_breakdown = bullish_sweep_reclaim
    absorption_like = False
    displacement_candle = False
    compression = (seq["body_expansion_sequence"] == "contracting")
    exhaustion = (seq["momentum_sequence"] == "exhaustion_risk")
    
    # 5. Volume
    volume_available = not pd.isna(df['Volume']).all()
    volume_ratio_vs_20 = 1.0
    volume_trend = "flat"
    breakout_volume_confirmed = "unknown"
    rejection_volume_confirmed = "unknown"
    
    if volume_available:
        vol_series = df['Volume'].dropna()
        if len(vol_series) >= 2:
            avg_vol = vol_series.mean()
            if avg_vol > 0: volume_ratio_vs_20 = float(vol_series.iloc[-1] / avg_vol)
            volume_trend = "rising" if vol_series.iloc[-1] > vol_series.iloc[-2] else "falling"
            breakout_volume_confirmed = volume_ratio_vs_20 > 1.2
            rejection_volume_confirmed = volume_ratio_vs_20 > 1.2
            if volume_ratio_vs_20 > 1.5 and (anatomy["upper_wick_pct"] > 40 or anatomy["lower_wick_pct"] > 40): absorption_like = True
            if volume_ratio_vs_20 > 1.5 and anatomy["body_pct_of_range"] > 60: displacement_candle = True
        else:
            volume_available = False
            
    # 6. Directional Read
    candle_confirms_direction = "unknown"
    candle_warning = ""
    candle_score_directional = 0.0
    candle_score_quality = 0.5
    
    if last_direction == "bull": candle_score_directional += 0.3
    elif last_direction == "bear": candle_score_directional -= 0.3
    if anatomy["close_location_pct"] >= 70: candle_score_directional += 0.3
    elif anatomy["close_location_pct"] <= 30: candle_score_directional -= 0.3
    if bullish_sweep_reclaim: candle_score_directional += 0.4
    if bearish_sweep_reject: candle_score_directional -= 0.4
    
    candle_score_directional = max(-1.0, min(1.0, candle_score_directional))
    if direction.upper() == "LONG":
        candle_confirms_direction = True if candle_score_directional > 0.2 else False if candle_score_directional < -0.2 else "unknown"
        if candle_confirms_direction is False: candle_warning = "Bearish price action contradicts LONG setup."
    elif direction.upper() == "SHORT":
        candle_confirms_direction = True if candle_score_directional < -0.2 else False if candle_score_directional > 0.2 else "unknown"
        if candle_confirms_direction is False: candle_warning = "Bullish price action contradicts SHORT setup."
        
    # 7. Style ratings
    style_candle_ratings = {
        "scalp": "STRONG" if candle_confirms_direction is True else "CONTRADICTS" if candle_confirms_direction is False else "WEAK",
        "intraday": "MODERATE" if seq["momentum_sequence"] != "exhaustion_risk" else "WEAK",
        "swing": "MODERATE" if seq["momentum_sequence"] != "exhaustion_risk" else "WEAK"
    }
    
    summary = f"Last 3 candles: {seq['last3_bull_count']} bull / {seq['last3_bear_count']} bear. Close location {anatomy['close_location_pct']:.1f}%."
    
    return {
        "anatomy": anatomy, "sequence": seq, "patterns": patterns,
        "auction": {
            "bullish_sweep_reclaim": bullish_sweep_reclaim, "bearish_sweep_reject": bearish_sweep_reject,
            "failed_breakout": failed_breakout, "failed_breakdown": failed_breakdown,
            "absorption_like": absorption_like, "displacement_candle": displacement_candle,
            "compression": compression, "exhaustion": exhaustion
        },
        "volume": {
            "volume_available": volume_available, "volume_ratio_vs_20": volume_ratio_vs_20,
            "volume_trend": volume_trend, "breakout_volume_confirmed": breakout_volume_confirmed,
            "rejection_volume_confirmed": rejection_volume_confirmed
        },
        "directional_read": {
            "candle_confirms_direction": candle_confirms_direction, "candle_warning": candle_warning,
            "candle_score_directional": candle_score_directional, "candle_score_quality": candle_score_quality
        },
        "style_candle_ratings": style_candle_ratings, "candle_summary": summary
    }
