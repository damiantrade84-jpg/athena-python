import pandas as pd
import numpy as np
from scipy.signal import find_peaks
import logging
import config

log = logging.getLogger(__name__)

class NakedEngine:
    def __init__(self):
        pass

    def _find_zones(self, highs: np.ndarray, lows: np.ndarray, atr: float, regime: str, candles: list):
        # Find resistance peaks
        # Prominence ensures we only get significant peaks, relative to ATR
        prominence_threshold = atr * 1.5
        
        peak_idx, _ = find_peaks(highs, prominence=prominence_threshold, distance=5)
        trough_idx, _ = find_peaks(-lows, prominence=prominence_threshold, distance=5)
        
        multipliers = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_multipliers", {})
        buf = multipliers.get(regime.upper(), multipliers.get("RANGING", {"upper": 0.5, "lower": 1.2, "sl": 1.0}))
        
        # Calculate recent average volume for normalisation
        vols = [float(c.get("vol", 0)) for c in candles]
        avg_volume_20 = np.mean(vols[-20:]) if len(vols) >= 20 else (np.mean(vols) if vols else 1.0)
        if avg_volume_20 <= 0: avg_volume_20 = 1.0
        
        res_zones = []
        for idx in peak_idx:
            peak_price = highs[idx]
            
            # Average vol of 3 bars around the peak
            start_i = max(0, idx - 1)
            end_i = min(len(candles), idx + 2)
            zone_vol = np.mean([float(candles[i].get("vol", 0)) for i in range(start_i, end_i)])
            vol_strength = min(1.0, zone_vol / avg_volume_20)
            
            # Zone expands below the peak (ceiling)
            res_zones.append({
                "upper": peak_price + (atr * buf.get("upper", 0.5)), # slight overshoot tolerance
                "lower": peak_price - (atr * buf.get("lower", 1.2)), # buffer zone thickness
                "center": peak_price,
                "volume_strength": vol_strength
            })
            
        sup_zones = []
        for idx in trough_idx:
            trough_price = lows[idx]
            
            # Average vol of 3 bars around the trough
            start_i = max(0, idx - 1)
            end_i = min(len(candles), idx + 2)
            zone_vol = np.mean([float(candles[i].get("vol", 0)) for i in range(start_i, end_i)])
            vol_strength = min(1.0, zone_vol / avg_volume_20)
            
            # Zone expands above the trough (floor)
            sup_zones.append({
                "lower": trough_price - (atr * buf.get("upper", 0.5)),
                "upper": trough_price + (atr * buf.get("lower", 1.2)),
                "center": trough_price,
                "volume_strength": vol_strength
            })
            
        return res_zones, sup_zones

    def _determine_sequence(self, highs: np.ndarray, lows: np.ndarray, atr: float, direction: str) -> dict:
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
        
        # Check for Double Tops / Bottoms indicating a liquidity sweep
        equal_highs = len(last_peaks) >= 2 and abs(last_peaks[-1] - last_peaks[-2]) < atr * 0.3
        equal_lows  = len(last_troughs) >= 2 and abs(last_troughs[-1] - last_troughs[-2]) < atr * 0.3
        
        liquidity_sweep = False
        if equal_highs and direction == "SHORT":
            liquidity_sweep = True
        elif equal_lows and direction == "LONG":
            liquidity_sweep = True
        
        return {
            "state": sequence,
            "recent_high": recent_swing_high,
            "recent_low": recent_swing_low,
            "liquidity_sweep": liquidity_sweep
        }

    def _detect_bos(self, highs: np.ndarray, lows: np.ndarray, atr: float) -> dict:
        """
        Detect Break of Structure (BOS) patterns using peak/trough analysis.
        Returns dict with bullish/bearish BOS signals and broken levels.
        """
        try:
            from scipy.signal import find_peaks
            
            # Find peaks and troughs with ATR-based prominence
            peak_idx, _ = find_peaks(highs, prominence=atr*0.8, distance=3)
            trough_idx, _ = find_peaks(-lows, prominence=atr*0.8, distance=3)
            
            # Get last 3 peaks and troughs
            last_peaks = [highs[i] for i in peak_idx[-3:]]
            last_troughs = [lows[i] for i in trough_idx[-3:]]
            
            # Insufficient data for BOS detection
            if len(last_peaks) < 2 or len(last_troughs) < 2:
                return {"bos_bull": False, "bos_bear": False, "last_broken_high": None, "last_broken_low": None}
            
            # BOS Bull: recent peak > previous peak AND current close > previous peak
            bos_bull = False
            last_broken_high = None
            if last_peaks[-1] > last_peaks[-2] and highs[-1] > last_peaks[-2]:
                bos_bull = True
                last_broken_high = last_peaks[-2]
            
            # BOS Bear: recent trough < previous trough AND current close < previous trough  
            bos_bear = False
            last_broken_low = None
            if last_troughs[-1] < last_troughs[-2] and lows[-1] < last_troughs[-2]:
                bos_bear = True
                last_broken_low = last_troughs[-2]
            
            return {
                "bos_bull": bos_bull,
                "bos_bear": bos_bear, 
                "last_broken_high": last_broken_high,
                "last_broken_low": last_broken_low
            }
            
        except Exception as e:
            # Fallback on any error
            return {"bos_bull": False, "bos_bear": False, "last_broken_high": None, "last_broken_low": None}

    def _detect_sweep(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, atr: float) -> dict:
        """
        Detect liquidity sweep patterns in the last 5 candles.
        Looks for wick-based liquidity grabs below/above a reference level.
        """
        # Need at least 8 candles: 6th-to-last reference + 5 analysis candles
        if len(closes) < 8:
            return {"bull_sweep": False, "bear_sweep": False, "sweep_low": None, "sweep_high": None}
        
        try:
            # Reference level: 6th-to-last close
            ref_close = closes[-6]
            
            # Analyze last 5 candles
            last_5_highs = highs[-5:]
            last_5_lows = lows[-5:]
            last_5_closes = closes[-5:]
            
            bull_sweep = False
            bear_sweep = False
            sweep_low = None
            sweep_high = None
            
            # Check each of the last 5 candles for sweep patterns
            for i in range(5):
                high = last_5_highs[i]
                low = last_5_lows[i]
                close = last_5_closes[i]
                
                # Bearish sweep: wick below reference, close above reference
                if (low < ref_close - 0.3 * atr and  # Low dips below by >0.3*ATR
                    close > ref_close):             # But closes above reference
                    bear_sweep = True
                    sweep_low = low  # Actual wick low
                
                # Bullish sweep: wick above reference, close below reference  
                if (high > ref_close + 0.3 * atr and  # High extends above by >0.3*ATR
                    close < ref_close):              # But closes below reference
                    bull_sweep = True
                    sweep_high = high  # Actual wick high
            
            return {
                "bull_sweep": bull_sweep,
                "bear_sweep": bear_sweep,
                "sweep_low": sweep_low,
                "sweep_high": sweep_high
            }
            
        except Exception as e:
            # Fallback on any error
            return {"bull_sweep": False, "bear_sweep": False, "sweep_low": None, "sweep_high": None}

    def _detect_fvg(self, candles: list) -> list:
        # Bullish + Bearish FVGs
        fvgs = []
        for i in range(2, len(candles)-1):
            prev_high = float(candles[i-1]["high"])
            prev_low  = float(candles[i-1]["low"])
            curr_high = float(candles[i]["high"])
            curr_low  = float(candles[i]["low"])
            next_high = float(candles[i+1]["high"])
            next_low  = float(candles[i+1]["low"])
            
            # Bullish FVG
            if prev_low > next_high:
                fvgs.append({"type": "bullish", "top": prev_low, "bottom": next_high})
            # Bearish FVG
            if prev_high < next_low:
                fvgs.append({"type": "bearish", "top": next_low, "bottom": prev_high})
        return fvgs

    def analyze_structure(self, d1_candles: list, h4_candles: list, h1_candles: list, current_price: float, direction: str, atr: float, regime: str = "RANGING") -> dict:
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
        h1_closes = np.array([float(c["close"]) for c in h1_candles])
        
        # 1. Macro Zones (D1/H4)
        # Using H4 to find thick zones gives standard resolution.
        res_zones, sup_zones = self._find_zones(h4_highs, h4_lows, atr, regime, h4_candles)
        
        # Determine FVG overlap with zones
        fvgs = self._detect_fvg(h4_candles)
        for zone in res_zones + sup_zones:
            zone["fvg_overlap"] = any(
                not (zone["upper"] < fvg["bottom"] or zone["lower"] > fvg["top"])
                for fvg in fvgs
            )
        
        # 2. Immediate H1 Sequence and Macro H4 Sequence
        sequence_data = self._determine_sequence(h1_highs, h1_lows, atr, direction)
        macro_seq_data = self._determine_sequence(h4_highs, h4_lows, atr, direction)
        
        # 3. BOS and Sweep Detection
        bos_data = self._detect_bos(h1_highs, h1_lows, atr)
        sweep_data = self._detect_sweep(h1_highs, h1_lows, h1_closes, atr)
        
        # 4. Find Nearest Zones Relative to Current Price
        # Nearest resistance above price
        valid_res = [z for z in res_zones if z["lower"] > current_price]
        nearest_res = min(valid_res, key=lambda x: x["lower"] - current_price) if valid_res else None
        
        # Nearest support below price
        valid_sup = [z for z in sup_zones if z["upper"] < current_price]
        nearest_sup = min(valid_sup, key=lambda x: current_price - x["upper"]) if valid_sup else None

        # Enforce that SL cannot be on the wrong side of the entry price (live break of structure)
        anchored_low = min(current_price, sequence_data["recent_low"])
        anchored_high = max(current_price, sequence_data["recent_high"])

        multipliers = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_multipliers", {})
        buf = multipliers.get(regime.upper(), multipliers.get("RANGING", {"upper": 0.5, "lower": 1.2, "sl": 1.0}))
        sl_mult = buf.get("sl", 1.0)

        # 5. Improved SL logic with sweep detection
        sl = anchored_low - (atr * sl_mult) if direction == "LONG" else anchored_high + (atr * sl_mult)
        
        # Override SL if sweep occurred
        if direction == "LONG" and sweep_data["bear_sweep"] and sweep_data["sweep_low"] is not None:
            sl = sweep_data["sweep_low"] - (atr * sl_mult)
        elif direction == "SHORT" and sweep_data["bull_sweep"] and sweep_data["sweep_high"] is not None:
            sl = sweep_data["sweep_high"] + (atr * sl_mult)
        
        # TP tied to nearest opposing zone, but fallback to RR if it's uncomfortably close or inverted
        tp = None
        if direction == "LONG" and nearest_res:
            tp = nearest_res["lower"] - (atr * sl_mult)
            # If TP is below or within 0.5 ATR of entry, discard and use RR fallback
            if tp <= current_price + (atr * 0.5):
                tp = None
        elif direction == "SHORT" and nearest_sup:
            tp = nearest_sup["upper"] + (atr * sl_mult)
            # If TP is above or within 0.5 ATR of entry, discard and use RR fallback
            if tp >= current_price - (atr * 0.5):
                tp = None
                
        # Generate TP from 2.0 RR fallback if no valid opposing structural zone exists
        if tp is None:
            sl_dist = abs(current_price - sl) if (sl is not None) else (atr * sl_mult)
            if sl_dist == 0: sl_dist = atr * sl_mult
            if direction == "LONG":
                tp = current_price + (sl_dist * 2.0)
            else:
                tp = current_price - (sl_dist * 2.0)

        # 6. BOS validation
        bos_confirmed = (direction == "LONG" and bos_data["bos_bull"]) or (direction == "SHORT" and bos_data["bos_bear"])

        fvg_overlap = False
        if direction == "LONG" and nearest_sup:
            fvg_overlap = nearest_sup.get("fvg_overlap", False)
        elif direction == "SHORT" and nearest_res:
            fvg_overlap = nearest_res.get("fvg_overlap", False)

        return {
            "structural_verdict": "CLEAR", 
            "nearest_resistance_zone": nearest_res,
            "nearest_support_zone": nearest_sup,
            "current_swing_sequence": sequence_data["state"],
            "macro_swing_sequence": macro_seq_data["state"],
            "recommended_stop_loss": sl,
            "recommended_take_profit": tp,
            "distance_to_res": (nearest_res["lower"] - current_price) if nearest_res else None,
            "distance_to_sup": (current_price - nearest_sup["upper"]) if nearest_sup else None,
            "atr": atr,
            "bos_confirmed": bos_confirmed,
            "bos_data": bos_data,
            "sweep_data": sweep_data,
            "fvg_overlap": fvg_overlap,
            "liquidity_sweep": sequence_data.get("liquidity_sweep", False)
        }

    def calculate_confidence(self, res: dict, current_price: float, direction: str, learning_ctx: dict = None) -> dict:
        """
        Engine B confidence score out of 3.0 + catalyst bonus + ai adjustment.
        Structure points: up to 1.0 (swing sequence alignment) — MANDATORY gatekeeper
        RR points: up to 1.0 (based on actual risk:reward)
        Room points: up to 1.0 (space before hitting opposing zone)
        Catalyst bonus: up to 0.8 (liquidity sweep + FVG overlap)
        AI Adjustment: up to +0.2 or -0.3 based on Pillar 5 empirical stats.

        If structure is not aligned (no HH_HL or LH_LL), Room and RR are
        multiplied by 0.3 so the max possible score without structure is
        ~0.6 + catalyst — well below the 1.8 threshold unless a real
        SMC catalyst is present.
        """
        atr = res.get("atr", 1.0)
        atr_val = atr if atr > 0 else 0.0001

        # ── Structure points — swing sequence must align with direction ──
        # Both H4 macro-trend and H1 micro-trend must align for full points.
        h1_seq = res.get("current_swing_sequence", "")
        h4_seq = res.get("macro_swing_sequence", "")
        bos = res.get("bos_confirmed", False)
        
        if direction == "LONG" and h1_seq == "HH_HL":
            if h4_seq == "HH_HL":
                struct_score = 1.0 if bos else 0.8
            else:
                struct_score = 0.5  # H1 is bullish but H4 does not agree
        elif direction == "SHORT" and h1_seq == "LH_LL":
            if h4_seq == "LH_LL":
                struct_score = 1.0 if bos else 0.8
            else:
                struct_score = 0.5  # H1 is bearish but H4 does not agree
        else:
            struct_score = 0.0  # No directional alignment = zero structure

        # Penalty multiplier: if structure is weak, slash Room & RR contribution
        multiplier = 1.0 if struct_score >= 0.7 else 0.3

        # ── Room to Move (points out of 1.0) ──
        room_score = 0.3  # default: no zone data
        if direction == "LONG" and res.get("distance_to_res"):
            dist = res["distance_to_res"]
            room_score = min(1.0, (dist / atr_val) / 2.0)
        elif direction == "SHORT" and res.get("distance_to_sup"):
            dist = res["distance_to_sup"]
            room_score = min(1.0, (dist / atr_val) / 2.0)
        room_score *= multiplier

        # ── Risk Reward (points out of 1.0) ──
        sl = res.get("recommended_stop_loss")
        tp = res.get("recommended_take_profit")
        rr_score = 0.2  # default: no levels
        if sl and tp:
            sl_dist = abs(current_price - sl)
            tp_dist = abs(tp - current_price)
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                rr_score = min(1.0, rr / 2.0)  # 1:2 RR = full point
        rr_score *= multiplier

        # ── Catalyst bonus (SMC edge — sweeps and FVGs can rescue a trade) ──
        catalyst_bonus = 0.0
        if res.get("liquidity_sweep"):
            catalyst_bonus += 0.5
        if res.get("fvg_overlap"):
            catalyst_bonus += 0.3

        # ── Pillar 5: AI Statistical Feedback Loop ──
        ai_adjustment = 0.0
        if learning_ctx and isinstance(learning_ctx, dict):
            p_stats = learning_ctx.get("pair_stats")
            if p_stats and p_stats.get("total_trades", 0) >= 4:
                wr = p_stats.get("win_rate", 0.0)
                if wr < 0.40:
                    ai_adjustment = -0.3  # Penalise low probability pairs
                elif wr >= 0.65:
                    ai_adjustment = 0.2   # Boost high probability pairs

        total_score = round(max(0.0, struct_score + room_score + rr_score + catalyst_bonus + ai_adjustment), 2)
        max_possible = 3.0 + 0.8 + 0.2  # 3.0 base + 0.8 catalyst + 0.2 ai
        pct = min(100, int((total_score / max_possible) * 100))

        return {
            "score": total_score,
            "pct": pct,
            "max_possible": round(max_possible, 2),
            "struct_points": round(struct_score, 2),
            "rr_points": round(rr_score, 2),
            "room_points": round(room_score, 2),
            "catalyst_bonus": round(catalyst_bonus, 2),
            "ai_adjustment": round(ai_adjustment, 2)
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

    def simulate_trade(self, d1_candles, h4_candles, h1_candles, current_price, 
                       direction, atr, regime_label="RANGING", confidence_threshold=1.8, learning_ctx=None):
        """Backtest-friendly wrapper that returns entry/exit signals.
        Returns dict compatible with existing backtest reporting."""
        result = self.analyze_structure(d1_candles, h4_candles, h1_candles,
                                        current_price, direction, atr, regime_label)
        confidence = self.calculate_confidence(result, current_price, direction, learning_ctx)
        if confidence["score"] < confidence_threshold:
            return None  # No trade signal
        
        return {
            "entry_price": current_price,
            "sl": result["recommended_stop_loss"],
            "tp1": result["recommended_take_profit"],
            "direction": direction,
            "score": confidence["score"],
            "confidence_pct": confidence["pct"],
            "fvg_overlap": result.get("fvg_overlap", False),
            "liquidity_sweep": result.get("liquidity_sweep", False),
            "structural_verdict": result["structural_verdict"],
            "ai_adjustment": confidence.get("ai_adjustment", 0.0)
        }

 # Singleton instance
engine = NakedEngine()
