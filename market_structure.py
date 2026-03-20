import pandas as pd
import numpy as np
from scipy.signal import find_peaks
import logging
import config

log = logging.getLogger(__name__)


class NakedEngine:
    def __init__(self):
        pass

    def _find_zones(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        atr: float,
        regime: str,
        candles: list,
    ):
        # Find resistance peaks
        # Prominence ensures we only get significant peaks, relative to ATR
        prominence_threshold = atr * 1.5

        peak_idx, _ = find_peaks(highs, prominence=prominence_threshold, distance=5)
        trough_idx, _ = find_peaks(-lows, prominence=prominence_threshold, distance=5)

        multipliers = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_multipliers", {})
        buf = multipliers.get(
            regime.upper(),
            multipliers.get("RANGING", {"upper": 0.5, "lower": 1.2, "sl": 1.0}),
        )

        # Calculate recent average volume for normalisation
        vols = [float(c.get("vol", 0)) for c in candles]
        avg_volume_20 = (
            np.mean(vols[-20:]) if len(vols) >= 20 else (np.mean(vols) if vols else 1.0)
        )
        if avg_volume_20 <= 0:
            avg_volume_20 = 1.0

        res_zones = []
        for idx in peak_idx:
            peak_price = highs[idx]

            # Average vol of 3 bars around the peak
            start_i = max(0, idx - 1)
            end_i = min(len(candles), idx + 2)
            zone_vol = np.mean(
                [float(candles[i].get("vol", 0)) for i in range(start_i, end_i)]
            )
            vol_strength = min(1.0, zone_vol / avg_volume_20)

            # Zone expands below the peak (ceiling)
            res_zones.append(
                {
                    "upper": peak_price
                    + (atr * buf.get("upper", 0.5)),  # slight overshoot tolerance
                    "lower": peak_price
                    - (atr * buf.get("lower", 1.2)),  # buffer zone thickness
                    "center": peak_price,
                    "volume_strength": vol_strength,
                }
            )

        sup_zones = []
        for idx in trough_idx:
            trough_price = lows[idx]

            # Average vol of 3 bars around the trough
            start_i = max(0, idx - 1)
            end_i = min(len(candles), idx + 2)
            zone_vol = np.mean(
                [float(candles[i].get("vol", 0)) for i in range(start_i, end_i)]
            )
            vol_strength = min(1.0, zone_vol / avg_volume_20)

            # Zone expands above the trough (floor)
            sup_zones.append(
                {
                    "lower": trough_price - (atr * buf.get("upper", 0.5)),
                    "upper": trough_price + (atr * buf.get("lower", 1.2)),
                    "center": trough_price,
                    "volume_strength": vol_strength,
                }
            )

        return res_zones, sup_zones

    def _determine_sequence(
        self, highs: np.ndarray, lows: np.ndarray, atr: float, direction: str
    ) -> dict:
        """Finds the most recent swings to determine HH/HL or LH/LL sequence."""
        prominence = atr * 0.8
        peak_idx, _ = find_peaks(highs, prominence=prominence, distance=3)
        trough_idx, _ = find_peaks(-lows, prominence=prominence, distance=3)

        last_peaks = [highs[i] for i in peak_idx[-3:]] if len(peak_idx) > 0 else []
        last_troughs = [lows[i] for i in trough_idx[-3:]] if len(trough_idx) > 0 else []

        sequence = "RANGING"
        if len(last_peaks) >= 2 and len(last_troughs) >= 2:
            if last_peaks[-1] > last_peaks[-2] and last_troughs[-1] > last_troughs[-2]:
                sequence = "HH_HL"  # Uptrend structure
            elif (
                last_peaks[-1] < last_peaks[-2] and last_troughs[-1] < last_troughs[-2]
            ):
                sequence = "LH_LL"  # Downtrend structure
            elif (
                last_peaks[-1] < last_peaks[-2] and last_troughs[-1] > last_troughs[-2]
            ):
                sequence = "CONTRACTION"
            elif (
                last_peaks[-1] > last_peaks[-2] and last_troughs[-1] < last_troughs[-2]
            ):
                sequence = "EXPANSION"

        # Most recent extrema for standard tight SL
        recent_swing_high = last_peaks[-1] if last_peaks else np.max(highs)
        recent_swing_low = last_troughs[-1] if last_troughs else np.min(lows)

        # Check for Double Tops / Bottoms indicating a liquidity sweep
        equal_highs = (
            len(last_peaks) >= 2 and abs(last_peaks[-1] - last_peaks[-2]) < atr * 0.3
        )
        equal_lows = (
            len(last_troughs) >= 2
            and abs(last_troughs[-1] - last_troughs[-2]) < atr * 0.3
        )

        liquidity_sweep = False
        if equal_highs and direction == "SHORT":
            liquidity_sweep = True
        elif equal_lows and direction == "LONG":
            liquidity_sweep = True

        return {
            "state": sequence,
            "recent_high": recent_swing_high,
            "recent_low": recent_swing_low,
            "liquidity_sweep": liquidity_sweep,
        }

    def _detect_bos(self, highs: np.ndarray, lows: np.ndarray, atr: float,
                    volumes: np.ndarray = None, closes: np.ndarray = None) -> dict:
        """
        Detect Break of Structure (BOS) patterns using peak/trough analysis.
        Returns dict with bullish/bearish BOS signals and broken levels.

        BOS confirmation is close-based: the bar's CLOSE must breach the prior
        swing level, not just the wick. Wick-only breaks are false positives.

        volumes: numpy array of bar volumes. None = skip volume check (forex).
        closes: numpy array of bar closes. None = fall back to highs/lows (legacy).
        """
        try:
            from scipy.signal import find_peaks

            # Find peaks and troughs with ATR-based prominence
            peak_idx, _ = find_peaks(highs, prominence=atr * 0.8, distance=3)
            trough_idx, _ = find_peaks(-lows, prominence=atr * 0.8, distance=3)

            # Get last 3 peaks and troughs
            last_peaks = [highs[i] for i in peak_idx[-3:]]
            last_troughs = [lows[i] for i in trough_idx[-3:]]

            # Insufficient data for BOS detection
            if len(last_peaks) < 2 or len(last_troughs) < 2:
                return {
                    "bos_bull": False,
                    "bos_bear": False,
                    "last_broken_high": None,
                    "last_broken_low": None,
                    "bos_volume_confirmed": False,
                }

            # Use close for BOS confirmation (more reliable than wick break)
            _last_close = closes[-1] if closes is not None and len(closes) > 0 else highs[-1]
            _last_close_bear = closes[-1] if closes is not None and len(closes) > 0 else lows[-1]

            # BOS Bull: recent peak > previous peak AND current CLOSE > previous peak
            bos_bull = False
            last_broken_high = None
            if last_peaks[-1] > last_peaks[-2] and _last_close > last_peaks[-2]:
                bos_bull = True
                last_broken_high = last_peaks[-2]

            # BOS Bear: recent trough < previous trough AND current CLOSE < previous trough
            bos_bear = False
            last_broken_low = None
            if last_troughs[-1] < last_troughs[-2] and _last_close_bear < last_troughs[-2]:
                bos_bear = True
                last_broken_low = last_troughs[-2]

            # Volume confirmation (only when volume data available)
            bos_volume_confirmed = True  # default True when no volume data
            if volumes is not None and len(volumes) >= 20 and (bos_bull or bos_bear):
                avg_vol_20 = float(np.mean(volumes[-20:]))
                last_vol = float(volumes[-1])
                if avg_vol_20 > 0:
                    bos_volume_confirmed = last_vol >= avg_vol_20 * 1.0  # at or above average
                # If volume data exists but is all zeros (forex with no real vol), skip
                if avg_vol_20 == 0:
                    bos_volume_confirmed = True

            return {
                "bos_bull": bos_bull,
                "bos_bear": bos_bear,
                "last_broken_high": last_broken_high,
                "last_broken_low": last_broken_low,
                "bos_volume_confirmed": bos_volume_confirmed,
            }

        except Exception:
            # Fallback on any error
            return {
                "bos_bull": False,
                "bos_bear": False,
                "last_broken_high": None,
                "last_broken_low": None,
                "bos_volume_confirmed": False,
            }

    def _detect_choch(self, highs: np.ndarray, lows: np.ndarray, atr: float) -> dict:
        """
        Detect Change of Character (CHoCH) — structural reversal signal.
        CHoCH occurs when price breaks the swing that produced the last BOS,
        indicating the trend structure has changed direction.

        This is a pure price-action detection — no indicators, no z-scores.

        Returns:
            choch_bull: True if bearish structure broke bullish (reversal up)
            choch_bear: True if bullish structure broke bearish (reversal down)
            choch_level: The price level that was broken
        """
        try:
            from scipy.signal import find_peaks

            peak_idx, _ = find_peaks(highs, prominence=atr * 0.8, distance=3)
            trough_idx, _ = find_peaks(-lows, prominence=atr * 0.8, distance=3)

            last_peaks = [highs[i] for i in peak_idx[-4:]]
            last_troughs = [lows[i] for i in trough_idx[-4:]]

            if len(last_peaks) < 3 or len(last_troughs) < 3:
                return {"choch_bull": False, "choch_bear": False, "choch_level": None}

            # Bullish CHoCH: price was making LH/LL (downtrend), then breaks
            # above the most recent Lower High — structure shifts bullish.
            was_bearish = (last_peaks[-2] < last_peaks[-3] and
                           last_troughs[-2] < last_troughs[-3])
            choch_bull = was_bearish and highs[-1] > last_peaks[-2]

            # Bearish CHoCH: price was making HH/HL (uptrend), then breaks
            # below the most recent Higher Low — structure shifts bearish.
            was_bullish = (last_peaks[-2] > last_peaks[-3] and
                           last_troughs[-2] > last_troughs[-3])
            choch_bear = was_bullish and lows[-1] < last_troughs[-2]

            choch_level = None
            if choch_bull:
                choch_level = float(last_peaks[-2])
            elif choch_bear:
                choch_level = float(last_troughs[-2])

            return {
                "choch_bull": choch_bull,
                "choch_bear": choch_bear,
                "choch_level": choch_level,
            }
        except Exception:
            return {"choch_bull": False, "choch_bear": False, "choch_level": None}

    def _detect_sweep(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, atr: float
    ) -> dict:
        """
        Detect liquidity sweep patterns in the last 5 candles.
        Looks for wick-based liquidity grabs below/above a reference level.
        """
        # Need at least 8 candles: 6th-to-last reference + 5 analysis candles
        if len(closes) < 8:
            return {
                "bull_sweep": False,
                "bear_sweep": False,
                "sweep_low": None,
                "sweep_high": None,
            }

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
                if (
                    low < ref_close - 0.3 * atr  # Low dips below by >0.3*ATR
                    and close > ref_close
                ):  # But closes above reference
                    bear_sweep = True
                    sweep_low = low  # Actual wick low

                # Bullish sweep: wick above reference, close below reference
                if (
                    high > ref_close + 0.3 * atr  # High extends above by >0.3*ATR
                    and close < ref_close
                ):  # But closes below reference
                    bull_sweep = True
                    sweep_high = high  # Actual wick high

            return {
                "bull_sweep": bull_sweep,
                "bear_sweep": bear_sweep,
                "sweep_low": sweep_low,
                "sweep_high": sweep_high,
            }

        except Exception:
            # Fallback on any error
            return {
                "bull_sweep": False,
                "bear_sweep": False,
                "sweep_low": None,
                "sweep_high": None,
            }

    def _detect_fvg(self, candles: list) -> list:
        # Bullish + Bearish FVGs
        fvgs = []
        for i in range(2, len(candles) - 1):
            prev_high = float(candles[i - 1]["high"])
            prev_low = float(candles[i - 1]["low"])
            next_high = float(candles[i + 1]["high"])
            next_low = float(candles[i + 1]["low"])

            # Bullish FVG
            if prev_low > next_high:
                fvgs.append({"type": "bullish", "top": prev_low, "bottom": next_high})
            # Bearish FVG
            if prev_high < next_low:
                fvgs.append({"type": "bearish", "top": next_low, "bottom": prev_high})
        return fvgs

    def _zone_context(
        self, zone: dict | None, current_price: float, atr: float, direction: str, candles: list
    ) -> dict:
        atr_val = atr if atr and atr > 0 else 0.0001
        if not zone:
            return {"distance": None, "near_zone": False, "zone_touched": False}

        lower = zone.get("lower")
        upper = zone.get("upper")
        center = zone.get("center")

        if lower is not None and upper is not None:
            if lower <= current_price <= upper:
                distance = 0.0
            elif current_price < lower:
                distance = lower - current_price
            else:
                distance = current_price - upper
        else:
            distance = abs(current_price - center) if center is not None else None

        near_zone = distance is not None and distance <= atr_val * 0.5
        zone_touched = False

        if candles:
            last = candles[-1]
            last_high = float(last["high"])
            last_low = float(last["low"])
            last_close = float(last["close"])
            if direction == "LONG":
                threshold = upper if upper is not None else center
                floor = lower if lower is not None else center
                if threshold is not None and last_low <= threshold + (atr_val * 0.1):
                    zone_touched = floor is None or last_close >= floor - (atr_val * 0.1)
            else:
                threshold = lower if lower is not None else center
                ceiling = upper if upper is not None else center
                if threshold is not None and last_high >= threshold - (atr_val * 0.1):
                    zone_touched = ceiling is None or last_close <= ceiling + (atr_val * 0.1)

        return {
            "distance": distance,
            "near_zone": near_zone,
            "zone_touched": zone_touched,
        }

    def _price_action_trigger(
        self,
        candles: list,
        direction: str,
        atr: float,
        zone_hit: bool,
        bos_confirmed: bool,
    ) -> dict:
        if len(candles) < 3:
            return {
                "pattern": "NONE",
                "trigger_ok": False,
                "rejection": False,
                "engulfing": False,
                "inside_break": False,
                "strong_close": False,
            }

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]

        open_ = float(last["open"])
        high = float(last["high"])
        low = float(last["low"])
        close = float(last["close"])

        prev_open = float(prev["open"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])
        prev_close = float(prev["close"])

        prev2_high = float(prev2["high"])
        prev2_low = float(prev2["low"])

        range_ = max(high - low, atr * 0.05, 1e-9)
        body = abs(close - open_)
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low

        bull_rejection = lower_wick >= max(body * 1.2, atr * 0.08) and close >= low + (range_ * 0.6)
        bear_rejection = upper_wick >= max(body * 1.2, atr * 0.08) and close <= high - (range_ * 0.6)

        bull_engulf = (
            close > open_
            and prev_close < prev_open
            and close >= prev_open
            and open_ <= prev_close
        )
        bear_engulf = (
            close < open_
            and prev_close > prev_open
            and close <= prev_open
            and open_ >= prev_close
        )

        inside_bar = prev_high < prev2_high and prev_low > prev2_low
        bull_inside_break = inside_bar and close > prev_high
        bear_inside_break = inside_bar and close < prev_low

        bull_strong_close = close > open_ and close >= low + (range_ * 0.7)
        bear_strong_close = close < open_ and close <= high - (range_ * 0.7)

        if direction == "LONG":
            trigger_ok = bull_rejection or bull_engulf or bull_inside_break or (
                bull_strong_close and (zone_hit or bos_confirmed)
            )
            pattern = (
                "REJECTION"
                if bull_rejection
                else "ENGULFING"
                if bull_engulf
                else "INSIDE_BREAK"
                if bull_inside_break
                else "STRONG_CLOSE"
                if bull_strong_close and (zone_hit or bos_confirmed)
                else "NONE"
            )
            rejection = bull_rejection
            engulfing = bull_engulf
            inside_break = bull_inside_break
            strong_close = bull_strong_close
        else:
            trigger_ok = bear_rejection or bear_engulf or bear_inside_break or (
                bear_strong_close and (zone_hit or bos_confirmed)
            )
            pattern = (
                "REJECTION"
                if bear_rejection
                else "ENGULFING"
                if bear_engulf
                else "INSIDE_BREAK"
                if bear_inside_break
                else "STRONG_CLOSE"
                if bear_strong_close and (zone_hit or bos_confirmed)
                else "NONE"
            )
            rejection = bear_rejection
            engulfing = bear_engulf
            inside_break = bear_inside_break
            strong_close = bear_strong_close

        return {
            "pattern": pattern,
            "trigger_ok": trigger_ok,
            "rejection": rejection,
            "engulfing": engulfing,
            "inside_break": inside_break,
            "strong_close": strong_close,
        }

    def analyze_structure(
        self,
        d1_candles: list,
        h4_candles: list,
        h1_candles: list,
        current_price: float,
        direction: str,
        atr: float,
        regime: str = "RANGING",
        fallback_rr: float = 2.0,
        asset_type: str = "",
    ) -> dict:
        """
        Analyzes raw candle data to find Support/Resistance zones and trend sequence.
        Returns structural verdict used by the Comparator in athena.py.
        """
        if not d1_candles or not h4_candles or not h1_candles or atr <= 0:
            return {
                "structural_verdict": "ERROR",
                "reason": "Missing data or valid ATR",
            }

        # Forex structure timeframe selection
        _forex_struct_tf = config.CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
        _use_d1_structure = (asset_type == "forex" and _forex_struct_tf == "D1")
        if _use_d1_structure:
            struct_candles = d1_candles
            trigger_candles = h4_candles
        else:
            struct_candles = h1_candles
            trigger_candles = h1_candles

        # Extract numpy arrays for scipy
        h4_highs = np.array([float(c["high"]) for c in h4_candles])
        h4_lows = np.array([float(c["low"]) for c in h4_candles])

        struct_highs = np.array([float(c["high"]) for c in struct_candles])
        struct_lows = np.array([float(c["low"]) for c in struct_candles])
        struct_closes = np.array([float(c["close"]) for c in struct_candles])

        # 1. Macro Zones (D1/H4)
        # Using H4 to find thick zones gives standard resolution.
        res_zones, sup_zones = self._find_zones(
            h4_highs, h4_lows, atr, regime, h4_candles
        )

        # Determine FVG overlap with zones — graded by quality
        fvgs = self._detect_fvg(h4_candles)
        for zone in res_zones + sup_zones:
            overlapping_fvgs = [
                fvg for fvg in fvgs
                if not (zone["upper"] < fvg["bottom"] or zone["lower"] > fvg["top"])
            ]
            zone["fvg_overlap"] = len(overlapping_fvgs) > 0
            # FVG quality: size relative to ATR (larger = more significant)
            if overlapping_fvgs and atr > 0:
                largest_fvg = max(overlapping_fvgs, key=lambda f: abs(f["top"] - f["bottom"]))
                zone["fvg_size_atr"] = round(abs(largest_fvg["top"] - largest_fvg["bottom"]) / atr, 2)
            else:
                zone["fvg_size_atr"] = 0.0

        # 2. Immediate Structure Sequence and Macro H4 Sequence
        sequence_data = self._determine_sequence(struct_highs, struct_lows, atr, direction)
        macro_seq_data = self._determine_sequence(h4_highs, h4_lows, atr, direction)

        # 3. BOS and Sweep Detection
        # Extract volumes for BOS volume confirmation (None for forex — no centralized volume)
        struct_volumes = None
        _has_vol = any(float(c.get("vol", 0)) > 0 for c in struct_candles[-5:])
        if _has_vol:
            struct_volumes = np.array([float(c.get("vol", 0)) for c in struct_candles])

        bos_data = self._detect_bos(struct_highs, struct_lows, atr, volumes=struct_volumes, closes=struct_closes)
        sweep_data = self._detect_sweep(struct_highs, struct_lows, struct_closes, atr)

        # 3b. CHoCH Detection (structural reversal)
        choch_data = self._detect_choch(struct_highs, struct_lows, atr)

        # 4. Find Nearest Zones Relative to Current Price
        # Nearest resistance above price
        valid_res = [z for z in res_zones if z["upper"] >= current_price]
        nearest_res = (
            min(
                valid_res,
                key=lambda x: 0.0
                if x["lower"] <= current_price <= x["upper"]
                else max(0.0, x["lower"] - current_price),
            )
            if valid_res
            else None
        )

        # Nearest support below price
        valid_sup = [z for z in sup_zones if z["lower"] <= current_price]
        nearest_sup = (
            min(
                valid_sup,
                key=lambda x: 0.0
                if x["lower"] <= current_price <= x["upper"]
                else max(0.0, current_price - x["upper"]),
            )
            if valid_sup
            else None
        )

        # Enforce that SL cannot be on the wrong side of the entry price (live break of structure)
        anchored_low = min(current_price, sequence_data["recent_low"])
        anchored_high = max(current_price, sequence_data["recent_high"])

        multipliers = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_multipliers", {})
        buf = multipliers.get(
            regime.upper(),
            multipliers.get("RANGING", {"upper": 0.5, "lower": 1.2, "sl": 1.0}),
        )
        sl_mult = buf.get("sl", 1.0)

        # 5. Improved SL logic with sweep detection
        sl = (
            anchored_low - (atr * sl_mult)
            if direction == "LONG"
            else anchored_high + (atr * sl_mult)
        )

        # Override SL if sweep occurred
        if (
            direction == "LONG"
            and sweep_data["bear_sweep"]
            and sweep_data["sweep_low"] is not None
        ):
            sl = sweep_data["sweep_low"] - (atr * sl_mult)
        elif (
            direction == "SHORT"
            and sweep_data["bull_sweep"]
            and sweep_data["sweep_high"] is not None
        ):
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

        # Generate TP from fallback_rr if no valid opposing structural zone exists
        if tp is None:
            sl_dist = abs(current_price - sl) if (sl is not None) else (atr * sl_mult)
            if sl_dist == 0:
                sl_dist = atr * sl_mult
            if direction == "LONG":
                tp = current_price + (sl_dist * fallback_rr)
            else:
                tp = current_price - (sl_dist * fallback_rr)

        # 6. BOS validation
        bos_confirmed = (direction == "LONG" and bos_data["bos_bull"]) or (
            direction == "SHORT" and bos_data["bos_bear"]
        )

        # CHoCH confirmation aligned with trade direction
        choch_confirmed = (direction == "LONG" and choch_data["choch_bull"]) or (
            direction == "SHORT" and choch_data["choch_bear"]
        )

        fvg_overlap = False
        if direction == "LONG" and nearest_sup:
            fvg_overlap = nearest_sup.get("fvg_overlap", False)
        elif direction == "SHORT" and nearest_res:
            fvg_overlap = nearest_res.get("fvg_overlap", False)

        active_zone = nearest_sup if direction == "LONG" else nearest_res
        zone_ctx = self._zone_context(active_zone, current_price, atr, direction, trigger_candles)
        trigger_ctx = self._price_action_trigger(
            trigger_candles,
            direction,
            atr,
            zone_ctx["zone_touched"] or zone_ctx["near_zone"],
            bos_confirmed,
        )

        return {
            "structural_verdict": "CLEAR",
            "nearest_resistance_zone": nearest_res,
            "nearest_support_zone": nearest_sup,
            "current_swing_sequence": sequence_data["state"],
            "macro_swing_sequence": macro_seq_data["state"],
            "recommended_stop_loss": sl,
            "recommended_take_profit": tp,
            "distance_to_res": (nearest_res["lower"] - current_price)
            if nearest_res
            else None,
            "distance_to_sup": (current_price - nearest_sup["upper"])
            if nearest_sup
            else None,
            "atr": atr,
            "bos_confirmed": bos_confirmed,
            "bos_volume_confirmed": bos_data.get("bos_volume_confirmed", True),
            "bos_data": bos_data,
            "sweep_data": sweep_data,
            "choch_data": choch_data,
            "choch_confirmed": choch_confirmed,
            "fvg_overlap": fvg_overlap,
            "liquidity_sweep": sequence_data.get("liquidity_sweep", False),
            "active_zone_distance": zone_ctx["distance"],
            "near_active_zone": zone_ctx["near_zone"],
            "zone_touched": zone_ctx["zone_touched"],
            "trigger_pattern": trigger_ctx["pattern"],
            "trigger_ok": trigger_ctx["trigger_ok"],
            "rejection_candle": trigger_ctx["rejection"],
            "engulfing_candle": trigger_ctx["engulfing"],
            "inside_break_candle": trigger_ctx["inside_break"],
            "strong_close": trigger_ctx["strong_close"],
            "structure_tf": "D1" if _use_d1_structure else "H1",
        }

    def calculate_confidence(
        self,
        res: dict,
        current_price: float,
        direction: str,
        learning_ctx: dict = None,
        entry_candles: list | None = None,
        style_profile: dict | None = None,
    ) -> dict:
        atr = res.get("atr", 1.0)
        atr_val = atr if atr > 0 else 0.0001
        h1_seq = res.get("current_swing_sequence", "")
        h4_seq = res.get("macro_swing_sequence", "")
        profile = style_profile if isinstance(style_profile, dict) else {}
        min_room_atr = float(profile.get("min_room_atr", 0.35))
        min_rr = float(profile.get("min_rr", 1.0))
        require_macro_align = bool(profile.get("require_macro_align", False))
        checklist_mode = str(profile.get("checklist_mode", "flexible")).lower()
        allow_breakout_entry = bool(
            profile.get("allow_breakout_entry", not require_macro_align)
        )

        micro_aligned = (direction == "LONG" and h1_seq == "HH_HL") or (
            direction == "SHORT" and h1_seq == "LH_LL"
        )
        macro_aligned = (direction == "LONG" and h4_seq == "HH_HL") or (
            direction == "SHORT" and h4_seq == "LH_LL"
        )
        hard_counter = (direction == "LONG" and h1_seq == "LH_LL" and h4_seq == "LH_LL") or (
            direction == "SHORT" and h1_seq == "HH_HL" and h4_seq == "HH_HL"
        )
        structure_ok = not hard_counter and (
            micro_aligned
            or macro_aligned
            or res.get("bos_confirmed", False)
            or res.get("liquidity_sweep", False)
        )
        macro_ok = macro_aligned or not require_macro_align
        zone_ok = bool(res.get("zone_touched") or res.get("near_active_zone"))
        trigger_ok = bool(res.get("trigger_ok"))
        breakout_ok = bool(res.get("bos_confirmed")) and bool(
            res.get("strong_close")
            or res.get("inside_break_candle")
            or res.get("engulfing_candle")
        )
        location_ok = zone_ok or (allow_breakout_entry and breakout_ok)
        room_dist = res.get("distance_to_res") if direction == "LONG" else res.get("distance_to_sup")
        room_ok = room_dist is None or room_dist >= atr_val * min_room_atr

        sl = res.get("recommended_stop_loss")
        tp = res.get("recommended_take_profit")
        rr = 0.0
        rr_ok = False
        if sl and tp:
            sl_dist = abs(current_price - sl)
            tp_dist = abs(tp - current_price)
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                rr_ok = rr >= min_rr

        # Entry requires a candle pattern trigger OR a confirmed structural event.
        # BOS/CHoCH alone without a trigger candle is not a valid entry.
        entry_ok = (
            trigger_ok
            or (breakout_ok and bool(res.get("bos_volume_confirmed", False)))
            or (bool(res.get("liquidity_sweep")) and trigger_ok)
            or (bool(res.get("choch_confirmed")) and (trigger_ok or zone_ok))
        )
        space_ok = room_ok or rr_ok

        confirmations = [structure_ok, location_ok, entry_ok, room_ok, rr_ok]
        if require_macro_align:
            confirmations.append(macro_ok)

        total_score = float(sum(1 for passed in confirmations if passed))
        max_possible = float(len(confirmations)) if confirmations else 1.0
        pct = min(100, int((total_score / max_possible) * 100))

        if checklist_mode == "strict":
            passed = structure_ok and zone_ok and trigger_ok and room_ok and rr_ok and macro_ok
        else:
            # Flexible but not free — require BOTH location AND a trigger/catalyst.
            # BOS alone is not enough. You need: structure + (zone OR breakout) + trigger + room/rr.
            # This prevents BOS from single-handedly passing 3 gates.
            passed = (
                structure_ok
                and location_ok
                and (trigger_ok or (breakout_ok and bool(res.get("bos_volume_confirmed", False))))
                and rr_ok
                and macro_ok
            )

        return {
            "score": total_score,
            "pct": pct,
            "max_possible": round(max_possible, 2),
            "struct_points": 1.0 if structure_ok else 0.0,
            "rr_points": 1.0 if rr_ok else 0.0,
            "room_points": 1.0 if room_ok else 0.0,
            "catalyst_bonus": 1.0 if entry_ok else 0.0,
            "ai_adjustment": 0.0,
            "zone_points": 1.0 if location_ok else 0.0,
            "macro_points": 1.0 if macro_ok and require_macro_align else 0.0,
            "rr": round(rr, 2),
            "passed": passed,
            "structure_ok": structure_ok,
            "macro_ok": macro_ok,
            "zone_ok": zone_ok,
            "breakout_ok": breakout_ok,
            "location_ok": location_ok,
            "trigger_ok": trigger_ok,
            "entry_ok": entry_ok,
            "room_ok": room_ok,
            "rr_ok": rr_ok,
            "space_ok": space_ok,
            "trigger_pattern": res.get("trigger_pattern", "NONE"),
        }

    def check_macro_correlation(
        self, asset_close_series: list, dxy_close_series: list, direction: str
    ) -> bool:
        """
        Calculates 30-period rolling Pearson correlation.
        Returns False (Block) if dynamically inversely correlated AND DXY moving against the trade.
        """
        if len(asset_close_series) < 30 or len(dxy_close_series) < 30:
            return True  # Not enough data to block

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
                return False  # DXY is surging, blocking LONG
            if direction == "SHORT" and not dxy_rising:
                return False  # DXY is falling, blocking SHORT

        return True  # Clear

    def simulate_trade(
        self,
        d1_candles,
        h4_candles,
        h1_candles,
        current_price,
        direction,
        atr,
        regime_label="RANGING",
        confidence_threshold=1.8,
        learning_ctx=None,
        style_profile=None,
        asset_type="",
    ):
        """Backtest-friendly wrapper that returns entry/exit signals.
        Returns dict compatible with existing backtest reporting."""
        result = self.analyze_structure(
            d1_candles,
            h4_candles,
            h1_candles,
            current_price,
            direction,
            atr,
            regime_label,
            asset_type=asset_type,
        )
        # For forex D1 structure, use H4 as entry candles instead of H1
        _forex_struct_tf = config.CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
        _entry_candles = h4_candles if (asset_type == "forex" and _forex_struct_tf == "D1") else h1_candles
        confidence = self.calculate_confidence(
            result,
            current_price,
            direction,
            learning_ctx,
            entry_candles=_entry_candles,
            style_profile=style_profile,
        )
        if confidence["score"] < confidence_threshold or not confidence.get("passed"):
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
            "ai_adjustment": confidence.get("ai_adjustment", 0.0),
            "trigger_pattern": confidence.get("trigger_pattern", "NONE"),
        }


# Singleton instance
engine = NakedEngine()
