import pandas as pd
import numpy as np
from scipy.signal import find_peaks
import logging

log = logging.getLogger(__name__)

class NakedEngine:
    def __init__(self):
        pass

    def _find_zones(self, highs: np.ndarray, lows: np.ndarray, atr: float):
        # Find resistance peaks
        # Prominence ensures we only get significant peaks, relative to ATR
        prominence_threshold = atr * 1.5
        
        peak_idx, _ = find_peaks(highs, prominence=prominence_threshold, distance=5)
        trough_idx, _ = find_peaks(-lows, prominence=prominence_threshold, distance=5)
        
        res_zones = []
        for idx in peak_idx:
            peak_price = highs[idx]
            # Zone expands below the peak (ceiling)
            res_zones.append({
                "upper": peak_price + (atr * 0.2), # slight overshoot tolerance
                "lower": peak_price - (atr * 0.8), # buffer zone thickness
                "center": peak_price,
            })
            
        sup_zones = []
        for idx in trough_idx:
            trough_price = lows[idx]
            # Zone expands above the trough (floor)
            sup_zones.append({
                "lower": trough_price - (atr * 0.2),
                "upper": trough_price + (atr * 0.8),
                "center": trough_price,
            })
            
        return res_zones, sup_zones

    def _determine_sequence(self, highs: np.ndarray, lows: np.ndarray, atr: float) -> dict:
        """Finds the most recent swings to determine HH/HL or LH/LL sequence."""
        prominence = atr * 0.8
        peak_idx, _ = find_peaks(highs, prominence=prominence, distance=3)
        trough_idx, _ = find_peaks(-lows, prominence=prominence, distance=3)
        
        last_peaks = [highs[i] for i in peak_idx[-3:]] if len(peak_idx) > 0 else []
        last_troughs = [lows[i] for i in trough_idx[-3:]] if len(trough_idx) > 0 else []
        
        sequence = "RANGING"
        if len(last_peaks) >= 2 and len(last_troughs) >= 2:
            if last_peaks[-1] > last_peaks[-2] and last_troughs[-1] > last_troughs[-2]:
                sequence = "HH_HL" # Uptrend structure
            elif last_peaks[-1] < last_peaks[-2] and last_troughs[-1] < last_troughs[-2]:
                sequence = "LH_LL" # Downtrend structure
            elif last_peaks[-1] < last_peaks[-2] and last_troughs[-1] > last_troughs[-2]:
                sequence = "CONTRACTION"
            elif last_peaks[-1] > last_peaks[-2] and last_troughs[-1] < last_troughs[-2]:
                sequence = "EXPANSION"
                
        # Most recent extrema for standard tight SL
        recent_swing_high = last_peaks[-1] if last_peaks else np.max(highs)
        recent_swing_low = last_troughs[-1] if last_troughs else np.min(lows)
        
        return {
            "state": sequence,
            "recent_high": recent_swing_high,
            "recent_low": recent_swing_low
        }

    def analyze_structure(self, d1_candles: list, h4_candles: list, h1_candles: list, current_price: float, direction: str, atr: float) -> dict:
        """
        Analyzes raw candle data to find Support/Resistance zones and trend sequence.
        Returns structural verdict used by the Comparator in athena.py.
        """
        if not d1_candles or not h4_candles or not h1_candles or atr <= 0:
            return {"structural_verdict": "ERROR", "reason": "Missing data or valid ATR"}
            
        # Extract numpy arrays for scipy
        h4_highs = np.array([float(c["high"]) for c in h4_candles])
        h4_lows = np.array([float(c["low"]) for c in h4_candles])
        
        h1_highs = np.array([float(c["high"]) for c in h1_candles])
        h1_lows = np.array([float(c["low"]) for c in h1_candles])
        
        # 1. Macro Zones (D1/H4)
        # Using H4 to find thick zones gives standard resolution.
        res_zones, sup_zones = self._find_zones(h4_highs, h4_lows, atr)
        
        # 2. Immediate M30/H1 Sequence
        sequence_data = self._determine_sequence(h1_highs, h1_lows, atr)
        
        # 3. Find Nearest Zones Relative to Current Price
        # Nearest resistance above price
        valid_res = [z for z in res_zones if z["lower"] > current_price]
        nearest_res = min(valid_res, key=lambda x: x["lower"] - current_price) if valid_res else None
        
        # Nearest support below price
        valid_sup = [z for z in sup_zones if z["upper"] < current_price]
        nearest_sup = max(valid_sup, key=lambda x: current_price - x["upper"]) if valid_sup else None

        return {
            "structural_verdict": "CLEAR", 
            "nearest_resistance_zone": nearest_res,
            "nearest_support_zone": nearest_sup,
            "current_swing_sequence": sequence_data["state"],
            "recommended_stop_loss": sequence_data["recent_low"] - (atr * 0.1) if direction == "LONG" else sequence_data["recent_high"] + (atr * 0.1),
            "recommended_take_profit": nearest_res["lower"] - (atr * 0.1) if nearest_res and direction == "LONG" else (nearest_sup["upper"] + (atr * 0.1) if nearest_sup and direction == "SHORT" else None),
            "distance_to_res": (nearest_res["lower"] - current_price) if nearest_res else None,
            "distance_to_sup": (current_price - nearest_sup["upper"]) if nearest_sup else None,
            "atr": atr
        }

    def calculate_confidence(self, res: dict, current_price: float, direction: str) -> dict:
        """
        Creates a dynamic score for Engine B naked structure trades out of 3 points 
        (to match UI confluence). Returns score and percentage.
        Base points: 1.0 (for structural validity)
        RR points: up to 1.0
        Zone Room points: up to 1.0
        """
        base_score = 1.0
        atr = res.get("atr", 1.0)
        atr_val = atr if atr > 0 else 0.0001
        
        sl = res.get("recommended_stop_loss")
        tp = res.get("recommended_take_profit")
        
        # Room to Move logic (points out of 1.0)
        room_score = 0.5
        if direction == "LONG" and res.get("distance_to_res"):
            dist = res["distance_to_res"]
            # optimal space is 2x ATR before hitting resistance
            room_score = min(1.0, (dist / atr_val) / 2.0)
        elif direction == "SHORT" and res.get("distance_to_sup"):
            dist = res["distance_to_sup"]
            room_score = min(1.0, (dist / atr_val) / 2.0)
            
        # Risk Reward logic (points out of 1.0)
        rr_score = 0.5
        if sl and tp:
            sl_dist = abs(current_price - sl)
            tp_dist = abs(tp - current_price)
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                # 1:2 RR = 1.0 point
                rr_score = min(1.0, rr / 2.0)
                
        total_score = round(base_score + room_score + rr_score, 2)
        pct = min(100, int((total_score / 3.0) * 100))
        
        return {
            "score": total_score,
            "pct": pct,
            "rr_points": round(rr_score, 2),
            "room_points": round(room_score, 2)
        }

    def check_macro_correlation(self, asset_close_series: list, dxy_close_series: list, direction: str) -> bool:
        """
        Calculates 30-period rolling Pearson correlation.
        Returns False (Block) if dynamically inversely correlated AND DXY moving against the trade.
        """
        if len(asset_close_series) < 30 or len(dxy_close_series) < 30:
            return True # Not enough data to block
            
        # Align lengths
        min_len = min(len(asset_close_series), len(dxy_close_series), 30)
        a_series = pd.Series(asset_close_series[-min_len:])
        d_series = pd.Series(dxy_close_series[-min_len:])
        
        correlation = a_series.corr(d_series)
        
        # Determine DXY short-term trend (last 5 periods vs 15 periods ago)
        dxy_recent = d_series.iloc[-5:].mean()
        dxy_past = d_series.iloc[-15:-5].mean()
        dxy_rising = dxy_recent > dxy_past
        
        # If mathematically highly inversely correlated (< -0.6)
        if correlation and not pd.isna(correlation) and correlation < -0.6:
            if direction == "LONG" and dxy_rising:
                return False # DXY is surging, blocking LONG
            if direction == "SHORT" and not dxy_rising:
                return False # DXY is falling, blocking SHORT
                
        return True # Clear

# Singleton instance
engine = NakedEngine()
