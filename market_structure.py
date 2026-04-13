import pandas as pd
import numpy as np
import math
from scipy.signal import find_peaks
import logging
import threading
import config
from zone_registry import get_zone_registry

log = logging.getLogger(__name__)


ENGINE_B_REGIME_GATE_DEFAULTS = {
    "TRENDING": 0.85,
    "RANGING": 1.15,
    "HIGH_VOLATILITY": 1.20,
    "LOW_VOLATILITY": 1.0,
}

# Observability only — stable codes for logs/diagnostics (no scoring side effects).
ENGINE_B_REASON_RESISTANCE_TOO_CLOSE = "resistance_too_close"
ENGINE_B_REASON_SUPPORT_TOO_CLOSE = "support_too_close"
ENGINE_B_REASON_ADVERSE_DXY = "adverse_dxy_correlation"
ENGINE_B_REASON_STRUCTURAL_SL_HARD_CAP = "structural_sl_rejected_hard_cap"
ENGINE_B_REASON_FOREX_ADX_LOW = "forex_adx_below_min"


def _adx_from_indicator_snap(snap: dict | None) -> float | None:
    """Read ADX from calc_indicators / calc_indicators_with_normalized snap (Engine A parity)."""
    if not isinstance(snap, dict):
        return None
    v = snap.get("adx")
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _engine_b_regime_gate(regime_label: str | None, asset_type: str = "") -> float:
    regime_key = str(regime_label or "").upper()
    cfg_gate = config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS", {}) or {}
    # Try per-asset-class first (nested dict)
    asset_cfg = cfg_gate.get(asset_type.lower(), None) if asset_type else None
    if isinstance(asset_cfg, dict):
        try:
            return float(
                asset_cfg.get(
                    regime_key, ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0)
                )
            )
        except (TypeError, ValueError):
            return float(ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0))
    # Fallback to flat config (backward compatible)
    try:
        return float(
            cfg_gate.get(regime_key, ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0))
        )
    except (TypeError, ValueError):
        return float(ENGINE_B_REGIME_GATE_DEFAULTS.get(regime_key, 1.0))


def engine_b_min_score_threshold(
    style_profile: dict | None, regime_label: str | None, asset_type: str = ""
) -> float:
    """Return the scaled Engine B score floor for a style/regime combination."""
    profile = style_profile if isinstance(style_profile, dict) else {}
    base_min = float(profile.get("min_score", 0.0) or 0.0)
    scaled = base_min * _engine_b_regime_gate(regime_label, asset_type)
    if scaled <= 0:
        return 0.0
    # Engine B scores in whole checklist points, so make the discrete gate explicit.
    return float(math.ceil(scaled - 1e-12))


def engine_b_confidence_passes(
    conf_data: dict | None,
    style_profile: dict | None,
    regime_label: str | None,
    asset_type: str = "",
) -> tuple[bool, float]:
    """Require both the score floor and the checklist verdict to pass."""
    min_score_scaled = engine_b_min_score_threshold(
        style_profile, regime_label, asset_type
    )
    conf = conf_data if isinstance(conf_data, dict) else {}
    score = float(conf.get("score", 0.0) or 0.0)
    passed = bool(conf.get("passed", False))
    return passed and score >= min_score_scaled, min_score_scaled


class NakedEngine:
    def __init__(self):
        self._registry_context = threading.local()

    def set_registry_context(self, symbol: str | None):
        self._registry_context.symbol = (
            str(symbol).upper() if symbol is not None and str(symbol).strip() else None
        )
        return self

    def _consume_registry_symbol(self) -> str | None:
        symbol = getattr(self._registry_context, "symbol", None)
        self._registry_context.symbol = None
        return symbol

    def _registry_order_blocks(self, entries: list[dict]) -> list[dict]:
        obs = []
        for entry in entries:
            if entry.get("type") != "OB":
                continue
            obs.append({
                "type": entry.get("direction"),
                "top": float(entry.get("top", 0.0)),
                "bottom": float(entry.get("bottom", 0.0)),
                "strength": int(round(float(entry.get("strength", 0.0)))),
                "mitigated": bool(entry.get("mitigated", False)),
                "created_at": entry.get("created_at"),
                "mitigated_at": entry.get("mitigated_at"),
                "scan_count": int(entry.get("scan_count", 1)),
            })
        return obs

    def _registry_fvgs(self, entries: list[dict]) -> list[dict]:
        fvgs = []
        for entry in entries:
            if entry.get("type") != "FVG":
                continue
            top = float(entry.get("top", 0.0))
            bottom = float(entry.get("bottom", 0.0))
            fvgs.append({
                "type": entry.get("direction"),
                "top": top,
                "bottom": bottom,
                "size": round(abs(top - bottom), 6),
                "strength": float(entry.get("strength", abs(top - bottom))),
                "mitigated": bool(entry.get("mitigated", False)),
                "created_at": entry.get("created_at"),
                "mitigated_at": entry.get("mitigated_at"),
                "scan_count": int(entry.get("scan_count", 1)),
            })
        return fvgs

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
        vols = np.array([float(c.get("vol", 0)) for c in candles], dtype=float)
        avg_volume_20 = (
            np.mean(vols[-20:]) if len(vols) >= 20 else (np.mean(vols) if vols else 1.0)
        )
        if avg_volume_20 <= 0:
            avg_volume_20 = 1.0
        zone_vol_means = (
            pd.Series(vols).rolling(window=3, center=True, min_periods=1).mean().to_numpy()
            if len(vols) > 0
            else np.array([], dtype=float)
        )

        res_zones = []
        for idx in peak_idx:
            peak_price = highs[idx]

            # Average vol of 3 bars around the peak
            zone_vol = float(zone_vol_means[idx]) if idx < len(zone_vol_means) else 0.0
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
            zone_vol = float(zone_vol_means[idx]) if idx < len(zone_vol_means) else 0.0
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

        has_equal_extrema = False
        if equal_highs and direction == "SHORT":
            has_equal_extrema = True
        elif equal_lows and direction == "LONG":
            has_equal_extrema = True
 
        return {
            "state": sequence,
            "recent_high": recent_swing_high,
            "recent_low": recent_swing_low,
            "has_equal_extrema": has_equal_extrema,
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

    def _detect_order_blocks(self, candles: list, bos_data: dict, atr: float) -> list:
        """
        Detect Order Blocks — the last opposing candle before a Break of Structure.

        Bullish OB: last bearish candle before a bullish BOS break.
        Bearish OB: last bullish candle before a bearish BOS break.

        Returns list of OB dicts with top, bottom, type, volume, displacement score.
        """
        obs = []
        if not candles or len(candles) < 10:
            return obs

        try:
            # Bullish OB: find last bearish candle before the bullish BOS
            if bos_data.get("bos_bull") and bos_data.get("last_broken_high") is not None:
                broken_high = float(bos_data["last_broken_high"])
                # Find the actual bar that initiated the break (the earliest bar in the current breakout run)
                bos_index = len(candles) - 1
                for j in range(len(candles) - 1, max(0, len(candles) - 20), -1):
                    if float(candles[j]["close"]) > broken_high:
                        bos_index = j
                    else:
                        break  # Found the point before the breakout began
                
                # Scan backward from bos_index - 1 for the last opposite candle
                for i in range(bos_index - 1, max(0, bos_index - 20), -1):
                    c = candles[i]
                    if float(c["close"]) < float(c["open"]):  # bearish candle
                        ob_top = float(c["open"])
                        ob_bottom = float(c["low"])
                        # Displacement: how far price moved after this candle (in ATRs)
                        if i + 1 < len(candles):
                            max_after = max(float(candles[j]["high"]) for j in range(i + 1, min(i + 6, len(candles))))
                            displacement = (max_after - ob_top) / atr if atr > 0 else 0
                        else:
                            displacement = 0
                        # Volume strength
                        vol = float(c.get("vol", 0))
                        avg_vol = np.mean([float(candles[k].get("vol", 0)) for k in range(max(0, i - 20), i)]) if i > 0 else 1
                        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0
                        # Strength score: 60% displacement, 40% volume (capped 0-100)
                        strength = min(100, int((min(displacement / 2.0, 1.0) * 60) + (min(vol_ratio / 2.0, 1.0) * 40)))
                        obs.append({
                            "type": "bullish",
                            "top": ob_top,
                            "bottom": ob_bottom,
                            "displacement": round(displacement, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "strength": strength,
                            "mitigated": False,
                        })
                        break

            # Bearish OB: find last bullish candle before the bearish BOS
            if bos_data.get("bos_bear") and bos_data.get("last_broken_low") is not None:
                broken_low = float(bos_data["last_broken_low"])
                # Find the actual bar that initiated the break (earliest bar in current breakdown run)
                bos_index = len(candles) - 1
                for j in range(len(candles) - 1, max(0, len(candles) - 20), -1):
                    if float(candles[j]["close"]) < broken_low:
                        bos_index = j
                    else:
                        break
                
                # Scan backward from bos_index - 1 for the last opposite candle
                for i in range(bos_index - 1, max(0, bos_index - 20), -1):
                    c = candles[i]
                    if float(c["close"]) > float(c["open"]):  # bullish candle
                        ob_top = float(c["high"])
                        ob_bottom = float(c["close"])
                        # Displacement
                        if i + 1 < len(candles):
                            min_after = min(float(candles[j]["low"]) for j in range(i + 1, min(i + 6, len(candles))))
                            displacement = (ob_bottom - min_after) / atr if atr > 0 else 0
                        else:
                            displacement = 0
                        # Volume strength
                        vol = float(c.get("vol", 0))
                        avg_vol = np.mean([float(candles[k].get("vol", 0)) for k in range(max(0, i - 20), i)]) if i > 0 else 1
                        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0
                        strength = min(100, int((min(displacement / 2.0, 1.0) * 60) + (min(vol_ratio / 2.0, 1.0) * 40)))
                        obs.append({
                            "type": "bearish",
                            "top": ob_top,
                            "bottom": ob_bottom,
                            "displacement": round(displacement, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "strength": strength,
                            "mitigated": False,
                        })
                        break
        except Exception:
            pass

        return obs

    def _detect_sweep(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, atr: float,
        swing_high: float = None, swing_low: float = None,
    ) -> dict:
        """
        Detect liquidity sweep patterns in the last 5 candles.
        Uses swing highs/lows (from find_peaks) as reference levels where
        stop losses cluster per SMC methodology.
        
        B4 FIX: Improved fallback logic - instead of using closes[-6] which can
        produce false positives in choppy markets, we now compute local min/max
        of the lookback window as a more robust reference level.
        """
        _empty = {
            "bull_sweep": False,
            "bear_sweep": False,
            "sweep_low": None,
            "sweep_high": None,
        }
        if len(closes) < 8:
            return _empty

        try:
            # B4 FIX: Improved fallback - use local extremes from lookback window
            # instead of arbitrary closes[-6] which can be misleading in choppy markets
            if swing_low is not None:
                ref_low = swing_low
            else:
                # Use the minimum low from bars 6-15 as reference (avoids recent noise)
                lookback_lows = lows[-15:-5] if len(lows) >= 15 else lows[:-5]
                ref_low = float(np.min(lookback_lows)) if len(lookback_lows) > 0 else float(lows[-6])
            
            if swing_high is not None:
                ref_high = swing_high
            else:
                # Use the maximum high from bars 6-15 as reference
                lookback_highs = highs[-15:-5] if len(highs) >= 15 else highs[:-5]
                ref_high = float(np.max(lookback_highs)) if len(lookback_highs) > 0 else float(highs[-6])

            last_5_highs = highs[-5:]
            last_5_lows = lows[-5:]
            last_5_closes = closes[-5:]

            bull_sweep = False
            bear_sweep = False
            sweep_low = None
            sweep_high = None

            for i in range(5):
                high = last_5_highs[i]
                low = last_5_lows[i]
                close = last_5_closes[i]

                # Bullish sweep: wick below swing low, close above it (stop hunt below → reversal up)
                if (
                    low < ref_low - 0.3 * atr
                    and close > ref_low
                ):
                    bull_sweep = True
                    sweep_low = low

                # Bearish sweep: wick above swing high, close below it (stop hunt above → reversal down)
                if (
                    high > ref_high + 0.3 * atr
                    and close < ref_high
                ):
                    bear_sweep = True
                    sweep_high = high

            return {
                "bull_sweep": bull_sweep,
                "bear_sweep": bear_sweep,
                "sweep_low": sweep_low,
                "sweep_high": sweep_high,
            }

        except Exception:
            return _empty

    def _detect_fvg(self, candles: list) -> list:
        """
        Detect Fair Value Gaps with mitigation tracking and consecutive merging.

        A bullish FVG: candle[i-1].low > candle[i+1].high (gap up)
        A bearish FVG: candle[i-1].high < candle[i+1].low (gap down)

        Mitigation: FVG is considered mitigated when price has retraced
        through 50%+ of the gap (consequent encroachment).

        Consecutive merging: adjacent FVGs of same type merge into one
        using the widest boundaries.
        """
        raw_fvgs = []
        for i in range(2, len(candles) - 1):
            prev_high = float(candles[i - 1]["high"])
            prev_low = float(candles[i - 1]["low"])
            next_high = float(candles[i + 1]["high"])
            next_low = float(candles[i + 1]["low"])

            # Bearish FVG — gap down: prev candle low above next candle high
            if prev_low > next_high:
                gap_top = prev_low
                gap_bottom = next_high
                gap_size = gap_top - gap_bottom
                # Mitigation: price rallies back UP into the gap (high >= midpoint)
                midpoint = gap_bottom + (gap_size * 0.5)
                mitigated = False
                for j in range(i + 2, len(candles)):
                    if float(candles[j]["high"]) >= midpoint:
                        mitigated = True
                        break
                raw_fvgs.append({
                    "type": "bearish", "top": gap_top, "bottom": gap_bottom,
                    "size": round(gap_size, 6), "mitigated": mitigated, "bar_index": i,
                })

            # Bullish FVG — gap up: prev candle high below next candle low
            if prev_high < next_low:
                gap_top = next_low
                gap_bottom = prev_high
                gap_size = gap_top - gap_bottom
                # Mitigation: price retraces DOWN into the gap (low <= midpoint)
                midpoint = gap_top - (gap_size * 0.5)
                mitigated = False
                for j in range(i + 2, len(candles)):
                    if float(candles[j]["low"]) <= midpoint:
                        mitigated = True
                        break
                raw_fvgs.append({
                    "type": "bullish", "top": gap_top, "bottom": gap_bottom,
                    "size": round(gap_size, 6), "mitigated": mitigated, "bar_index": i,
                })

        # Merge consecutive FVGs of same type
        if len(raw_fvgs) < 2:
            return raw_fvgs

        merged = [raw_fvgs[0]]
        for fvg in raw_fvgs[1:]:
            prev = merged[-1]
            # Same type and adjacent (within 2 bars)
            if fvg["type"] == prev["type"] and abs(fvg["bar_index"] - prev["bar_index"]) <= 2:
                # Merge: use widest boundaries
                prev["top"] = max(prev["top"], fvg["top"])
                prev["bottom"] = min(prev["bottom"], fvg["bottom"])
                prev["size"] = round(prev["top"] - prev["bottom"], 6)
                prev["mitigated"] = prev["mitigated"] and fvg["mitigated"]
            else:
                merged.append(fvg)

        return merged

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

    def _determine_independent_direction(
        self,
        h1_sequence: str,
        h4_sequence: str,
        bos_data: dict,
        d1_bos: dict,
        choch_data: dict,
        sweep_data: dict,
    ) -> dict:
        """
        Determine Engine B's own directional opinion from pure price-action evidence.

        This is advisory only — it does NOT affect scoring, checklist pass/fail,
        or the direction parameter passed into analyze_structure from the caller.
        It lets Engine C distinguish genuine direction conflicts from inherited ones.

        Returns:
            dict with:
              direction: 'LONG' | 'SHORT' | None  (None = no structural opinion)
              confidence: 'HIGH' | 'MEDIUM' | 'LOW'
              reason: str  human-readable rationale
              votes: dict  evidence map used to derive the opinion
        """
        votes: dict[str, int] = {}  # +1 = bullish, -1 = bearish, 0 = neutral

        # --- H1 micro swing sequence ---
        if h1_sequence == "HH_HL":
            votes["h1_swing"] = 1
        elif h1_sequence == "LH_LL":
            votes["h1_swing"] = -1
        else:
            votes["h1_swing"] = 0

        # --- H4 macro swing sequence ---
        if h4_sequence == "HH_HL":
            votes["h4_swing"] = 1
        elif h4_sequence == "LH_LL":
            votes["h4_swing"] = -1
        else:
            votes["h4_swing"] = 0

        # --- BOS direction (structural momentum) ---
        bos_bull = bool(bos_data.get("bos_bull"))
        bos_bear = bool(bos_data.get("bos_bear"))
        if bos_bull and not bos_bear:
            votes["bos"] = 1
        elif bos_bear and not bos_bull:
            votes["bos"] = -1
        elif bos_bull and bos_bear:
            votes["bos"] = 0  # both — conflicted
        else:
            votes["bos"] = 0  # no BOS

        # --- D1 BOS (macro confirmation, higher weight) ---
        d1_bull = bool(d1_bos.get("bos_bull"))
        d1_bear = bool(d1_bos.get("bos_bear"))
        if d1_bull and not d1_bear:
            votes["d1_bos"] = 1
        elif d1_bear and not d1_bull:
            votes["d1_bos"] = -1
        else:
            votes["d1_bos"] = 0

        # --- CHoCH (early reversal signal, lower weight) ---
        if choch_data.get("choch_bull") and not choch_data.get("choch_bear"):
            votes["choch"] = 1
        elif choch_data.get("choch_bear") and not choch_data.get("choch_bull"):
            votes["choch"] = -1
        else:
            votes["choch"] = 0

        # --- Liquidity sweep direction (sweep of lows → bullish, highs → bearish) ---
        if sweep_data.get("bull_sweep"):
            votes["sweep"] = 1   # sweep of lows = bullish reversal context
        elif sweep_data.get("bear_sweep"):
            votes["sweep"] = -1  # sweep of highs = bearish reversal context
        else:
            votes["sweep"] = 0

        # --- Weight the votes ---
        # D1 BOS and H4 swing carry more weight than H1 or CHoCH
        weights = {
            "d1_bos":   3,
            "h4_swing": 2,
            "bos":      2,
            "h1_swing": 1,
            "choch":    1,
            "sweep":    1,
        }
        total_weight = sum(weights.values())  # 10
        weighted_score = sum(
            votes.get(k, 0) * w for k, w in weights.items()
        )
        score_ratio = weighted_score / total_weight  # -1.0 to +1.0

        # Confidence: how strongly the evidence agrees
        active_votes = [v for v in votes.values() if v != 0]
        if not active_votes:
            return {
                "direction": None,
                "confidence": "NONE",
                "reason": "No structural evidence available",
                "votes": votes,
                "weighted_score": 0.0,
            }

        positive = sum(1 for v in active_votes if v > 0)
        negative = sum(1 for v in active_votes if v < 0)
        agreement_ratio = max(positive, negative) / len(active_votes)

        if agreement_ratio >= 0.80:
            confidence = "HIGH"
        elif agreement_ratio >= 0.60:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Direction from weighted score
        if score_ratio > 0.15:
            direction = "LONG"
            reason = (
                f"Structural evidence bullish: {positive}/{len(active_votes)} indicators agree "
                f"(h4={h4_sequence}, bos={'YES' if bos_bull else 'NO'}, d1_bos={'YES' if d1_bull else 'NO'})"
            )
        elif score_ratio < -0.15:
            direction = "SHORT"
            reason = (
                f"Structural evidence bearish: {negative}/{len(active_votes)} indicators agree "
                f"(h4={h4_sequence}, bos={'YES' if bos_bear else 'NO'}, d1_bos={'YES' if d1_bear else 'NO'})"
            )
        else:
            direction = None
            confidence = "LOW"
            reason = (
                f"Structural evidence mixed or ranging: score={score_ratio:.2f} "
                f"(pos={positive}, neg={negative}, h4={h4_sequence})"
            )

        return {
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "votes": votes,
            "weighted_score": round(score_ratio, 4),
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
        enable_zone_registry: bool = True,
        enable_profile_context: bool = True,
        d1_snap: dict | None = None,
        h4_snap: dict | None = None,
    ) -> dict:
        """
        Analyzes raw candle data to find Support/Resistance zones and trend sequence.
        Returns structural verdict used by the Comparator in athena.py.

        Optional ``d1_snap`` / ``h4_snap``: indicator snaps (e.g. from ``calc_indicators_with_normalized``).
        ``h4_snap`` should match the **second** candle series (zone TF, often H4). When provided,
        ``d1_adx`` / ``h4_adx`` on the result prefer ``snap["adx"]`` over recalculating from OHLC.
        """
        if not d1_candles or not h4_candles or not h1_candles or atr <= 0:
            return {
                "structural_verdict": "ERROR",
                "reason": "Missing data or valid ATR",
                "asset_type": asset_type,
                "d1_adx": None,
                "h4_adx": None,
            }

        registry_symbol = self._consume_registry_symbol() if enable_zone_registry else None

        # Forex structure timeframe selection
        _forex_struct_tf = config.CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
        _use_d1_structure = (asset_type == "forex" and _forex_struct_tf == "D1")
        structure_tf = "D1" if _use_d1_structure else "H1"
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
        active_fvgs = [f for f in fvgs if not f.get("mitigated", False)]

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

        # Compute swing highs/lows for sweep detection (structural reference levels)
        _sweep_swing_high = None
        _sweep_swing_low = None
        try:
            from scipy.signal import find_peaks as _fp
            _pk_idx, _ = _fp(struct_highs, prominence=atr * 0.8, distance=3)
            _tr_idx, _ = _fp(-struct_lows, prominence=atr * 0.8, distance=3)
            if len(_pk_idx) >= 1:
                _sweep_swing_high = float(struct_highs[_pk_idx[-1]])
            if len(_tr_idx) >= 1:
                _sweep_swing_low = float(struct_lows[_tr_idx[-1]])
        except Exception:
            pass
        sweep_data = self._detect_sweep(
            struct_highs, struct_lows, struct_closes, atr,
            swing_high=_sweep_swing_high, swing_low=_sweep_swing_low,
        )

        # 3e. Multi-TF BOS Chaining — H4/struct BOS confirmed by D1 BOS = high conviction
        d1_highs = np.array([float(c["high"]) for c in d1_candles])
        d1_lows = np.array([float(c["low"]) for c in d1_candles])
        d1_closes = np.array([float(c["close"]) for c in d1_candles])
        d1_bos = self._detect_bos(d1_highs, d1_lows, atr, closes=d1_closes)

        bos_mtf_confirmed = (
            (bos_data.get("bos_bull") and d1_bos.get("bos_bull")) or
            (bos_data.get("bos_bear") and d1_bos.get("bos_bear"))
        )

        order_blocks = self._detect_order_blocks(struct_candles, bos_data, atr)
        if registry_symbol:
            zone_registry = get_zone_registry()
            zone_registry.upsert_zones(registry_symbol, structure_tf, order_blocks, [], atr=atr)
            zone_registry.upsert_zones(registry_symbol, "H4", [], fvgs, atr=atr)
            zone_registry.mark_mitigated(registry_symbol, structure_tf, current_price, atr)
            zone_registry.mark_mitigated(registry_symbol, "H4", current_price, atr)
            zone_registry.prune_old_zones()

            if zone_registry.has_zones(registry_symbol, structure_tf):
                order_blocks = self._registry_order_blocks(
                    zone_registry.get_active_zones(registry_symbol, structure_tf)
                )
            if zone_registry.has_zones(registry_symbol, "H4"):
                active_fvgs = self._registry_fvgs(
                    zone_registry.get_active_zones(registry_symbol, "H4")
                )

        for zone in res_zones + sup_zones:
            overlapping_fvgs = [
                fvg for fvg in active_fvgs
                if not (zone["upper"] < fvg["bottom"] or zone["lower"] > fvg["top"])
            ]
            zone["fvg_overlap"] = len(overlapping_fvgs) > 0
            if overlapping_fvgs and atr > 0:
                largest_fvg = max(overlapping_fvgs, key=lambda f: abs(f["top"] - f["bottom"]))
                zone["fvg_size_atr"] = round(abs(largest_fvg["top"] - largest_fvg["bottom"]) / atr, 2)
            else:
                zone["fvg_size_atr"] = 0.0

        # 3b. CHoCH Detection (structural reversal)
        choch_data = self._detect_choch(struct_highs, struct_lows, atr)

        # 3d. Breaker Blocks — after CHoCH, the broken swing level becomes new S/R
        # A bullish CHoCH breaks above a Lower High → that LH level becomes support (breaker)
        # A bearish CHoCH breaks below a Higher Low → that HL level becomes resistance (breaker)
        breaker_block = None
        if choch_data.get("choch_bull") and choch_data.get("choch_level") is not None:
            breaker_block = {
                "type": "bullish_breaker",
                "level": choch_data["choch_level"],
                "upper": choch_data["choch_level"] + (atr * 0.3),
                "lower": choch_data["choch_level"] - (atr * 0.3),
            }
            # Add as a support zone
            sup_zones.append({
                "upper": breaker_block["upper"],
                "lower": breaker_block["lower"],
                "center": breaker_block["level"],
                "volume_strength": 0.8,  # high default — breakers are institutional
                "fvg_overlap": False,
                "fvg_size_atr": 0.0,
                "is_breaker": True,
            })
        elif choch_data.get("choch_bear") and choch_data.get("choch_level") is not None:
            breaker_block = {
                "type": "bearish_breaker",
                "level": choch_data["choch_level"],
                "upper": choch_data["choch_level"] + (atr * 0.3),
                "lower": choch_data["choch_level"] - (atr * 0.3),
            }
            # Add as a resistance zone
            res_zones.append({
                "upper": breaker_block["upper"],
                "lower": breaker_block["lower"],
                "center": breaker_block["level"],
                "volume_strength": 0.8,
                "fvg_overlap": False,
                "fvg_size_atr": 0.0,
                "is_breaker": True,
            })

        # ── Zone Merging: cluster zones within 0.5 ATR into single high-confluence pools ──
        def _merge_zones(zones, merge_dist):
            if not zones:
                return zones
            sorted_z = sorted(zones, key=lambda z: z["center"])
            merged = [sorted_z[0]]
            for z in sorted_z[1:]:
                prev = merged[-1]
                if abs(z["center"] - prev["center"]) <= merge_dist:
                    # Merge: widen boundaries, keep strongest volume
                    prev["upper"] = max(prev["upper"], z["upper"])
                    prev["lower"] = min(prev["lower"], z["lower"])
                    prev["center"] = (prev["upper"] + prev["lower"]) / 2
                    prev["volume_strength"] = max(
                        prev.get("volume_strength", 0), z.get("volume_strength", 0)
                    )
                    # Merge FVG overlap — any overlap = True
                    if z.get("fvg_overlap"):
                        prev["fvg_overlap"] = True
                        prev["fvg_size_atr"] = max(
                            prev.get("fvg_size_atr", 0), z.get("fvg_size_atr", 0)
                        )
                else:
                    merged.append(z)
            return merged

        _merge_dist = atr * 0.5
        res_zones = _merge_zones(res_zones, _merge_dist)
        sup_zones = _merge_zones(sup_zones, _merge_dist)

        # ── Zone Mitigation Purging: remove zones that price has pierced through ──
        current_close = float(struct_candles[-1]["close"]) if struct_candles else current_price
        res_zones = [z for z in res_zones if z["lower"] > current_close - (atr * 0.1)]
        sup_zones = [z for z in sup_zones if z["upper"] < current_close + (atr * 0.1)]

        # 3c. Order Block Detection — last opposing candle before BOS
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
            and sweep_data["bull_sweep"]
            and sweep_data["sweep_low"] is not None
        ):
            sl = sweep_data["sweep_low"] - (atr * sl_mult)
        elif (
            direction == "SHORT"
            and sweep_data["bear_sweep"]
            and sweep_data["sweep_high"] is not None
        ):
            sl = sweep_data["sweep_high"] + (atr * sl_mult)

        # Prefer the opposing structural zone, but fall back to RR projection when that
        # zone would place TP inverted or untradeably close to the current entry.
        tp = None
        tp_source = "fallback_rr"
        tp_structural_limited = False
        if direction == "LONG" and nearest_res:
            structural_tp = nearest_res["lower"] - (atr * sl_mult)
            if structural_tp <= current_price + (atr * 0.5):
                tp_structural_limited = True
            else:
                tp = structural_tp
                tp_source = "structural_zone"
        elif direction == "SHORT" and nearest_sup:
            structural_tp = nearest_sup["upper"] + (atr * sl_mult)
            if structural_tp >= current_price - (atr * 0.5):
                tp_structural_limited = True
            else:
                tp = structural_tp
                tp_source = "structural_zone"

        # Generate TP from fallback_rr when no usable opposing structural zone exists.
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

        # Check if an Order Block overlaps with the active zone
        _ob_min_strength = config.CONFIG.get("NAKED_ENGINE", {}).get("ob_min_strength", 50)
        _ob_at_zone = False
        if active_zone and order_blocks:
            az_lower = active_zone.get("lower", 0)
            az_upper = active_zone.get("upper", 0)
            for ob in order_blocks:
                if not (ob["top"] < az_lower or ob["bottom"] > az_upper):
                    if ob.get("strength", 0) >= _ob_min_strength:
                        _ob_at_zone = True
                        break
        trigger_ctx = self._price_action_trigger(
            trigger_candles,
            direction,
            atr,
            zone_ctx["zone_touched"] or zone_ctx["near_zone"],
            bos_confirmed,
        )

        # Previous-session fixed-range profile from the latest completed UTC session.
        _profile_result = {
            "prev_session_profile_valid": False,
            "prev_session_profile_source_tf": None,
            "prev_session_poc": None,
            "prev_session_vah": None,
            "prev_session_val": None,
            "prev_session_profile_high": None,
            "prev_session_profile_low": None,
            "prev_session_total_volume": None,
            "prev_session_start": None,
            "prev_session_end": None,
            "profile_in_play": False,
            "profile_level_in_play": None,
            "inside_prev_value_area": False,
            "above_prev_value_area": False,
            "below_prev_value_area": False,
            "touched_poc": False,
            "touched_vah": False,
            "touched_val": False,
            "rejected_from_poc": False,
            "rejected_from_vah": False,
            "rejected_from_val": False,
            "accepted_at_poc": False,
            "accepted_inside_value": False,
            "returned_to_value": False,
            "failed_return_to_value": False,
            "profile_bias": "neutral",
            "profile_reaction_strength": 0.0,
            "profile_notes": "",
        }
        if enable_profile_context:
            try:
                from volume_profile import (
                    classify_profile_interaction,
                    compute_fixed_range_volume_profile,
                    split_completed_sessions,
                )

                _profile_candles = h1_candles if len(h1_candles or []) >= 24 else h4_candles
                _profile_source_tf = "H1" if len(h1_candles or []) >= 24 else "H4"
                _sessions = split_completed_sessions(_profile_candles or [], asset_type)
                _prev_session = _sessions.get("prev_session_candles", [])
                if _prev_session:
                    _profile = compute_fixed_range_volume_profile(_prev_session)
                    if _profile.get("profile_valid"):
                        _interaction = classify_profile_interaction(
                            current_price=current_price,
                            recent_candles=(trigger_candles or struct_candles or [])[-10:],
                            direction=direction,
                            poc=_profile["poc"],
                            vah=_profile["vah"],
                            val=_profile["val"],
                            atr=atr,
                        )
                        _profile_result.update({
                            "prev_session_profile_valid": True,
                            "prev_session_profile_source_tf": _profile_source_tf,
                            "prev_session_poc": _profile["poc"],
                            "prev_session_vah": _profile["vah"],
                            "prev_session_val": _profile["val"],
                            "prev_session_profile_high": _profile["session_high"],
                            "prev_session_profile_low": _profile["session_low"],
                            "prev_session_total_volume": _profile["total_volume"],
                            "prev_session_start": _profile["session_start"],
                            "prev_session_end": _profile["session_end"],
                            **_interaction,
                        })
                        log.debug(
                            "[PROFILE] %s %s profile computed (%s POC=%s VAH=%s VAL=%s)",
                            registry_symbol or asset_type or "unknown",
                            direction,
                            _profile_source_tf,
                            _profile["poc"],
                            _profile["vah"],
                            _profile["val"],
                        )
                    else:
                        log.debug(
                            "[PROFILE] %s profile skipped: unusable prior-session volume",
                            registry_symbol or asset_type or "unknown",
                        )
                else:
                    log.debug(
                        "[PROFILE] %s profile skipped: no completed prior session",
                        registry_symbol or asset_type or "unknown",
                    )
            except Exception as _pe:
                log.debug(f"[PROFILE] {registry_symbol or asset_type or 'unknown'} profile skipped: {_pe}")

        is_sweep_event = (direction == "LONG" and sweep_data["bull_sweep"]) or \
                         (direction == "SHORT" and sweep_data["bear_sweep"])

        d1_adx_val = _adx_from_indicator_snap(d1_snap)
        h4_adx_val = _adx_from_indicator_snap(h4_snap)
        try:
            from indicators import calc_adx

            if d1_adx_val is None:
                _d1_hi = [float(c["high"]) for c in d1_candles]
                _d1_lo = [float(c["low"]) for c in d1_candles]
                _d1_cl = [float(c["close"]) for c in d1_candles]
                for _v in reversed((calc_adx(_d1_hi, _d1_lo, _d1_cl, 14).get("adx") or [])):
                    if _v is not None:
                        d1_adx_val = float(_v)
                        break
            if h4_adx_val is None:
                _h4_hi = [float(c["high"]) for c in h4_candles]
                _h4_lo = [float(c["low"]) for c in h4_candles]
                _h4_cl = [float(c["close"]) for c in h4_candles]
                for _v in reversed((calc_adx(_h4_hi, _h4_lo, _h4_cl, 14).get("adx") or [])):
                    if _v is not None:
                        h4_adx_val = float(_v)
                        break
        except Exception:
            pass

        return {
            "structural_verdict": "CLEAR",
            "nearest_resistance_zone": nearest_res,
            "nearest_support_zone": nearest_sup,
            "current_swing_sequence": sequence_data["state"],
            "macro_swing_sequence": macro_seq_data["state"],
            "recommended_stop_loss": sl,
            "recommended_take_profit": tp,
            "tp_source": tp_source,
            "tp_structural_limited": tp_structural_limited,
            "distance_to_res": (nearest_res["lower"] - current_price)
            if nearest_res
            else None,
            "distance_to_sup": (current_price - nearest_sup["upper"])
            if nearest_sup
            else None,
            "atr": atr,
            "bos_confirmed": bos_confirmed,
            "bos_mtf_confirmed": bos_mtf_confirmed,
            "bos_volume_confirmed": bos_data.get("bos_volume_confirmed", True),
            "bos_data": bos_data,
            "d1_bos_data": d1_bos,
            "sweep_data": sweep_data,
            "choch_data": choch_data,
            "choch_confirmed": choch_confirmed,
            "breaker_block": breaker_block,
            "order_blocks": order_blocks,
            "ob_at_zone": _ob_at_zone,
            "fvg_overlap": fvg_overlap,
            "active_fvgs": active_fvgs,
            "liquidity_sweep": is_sweep_event,
            "has_equal_extrema": sequence_data.get("has_equal_extrema", False),
            "active_zone_distance": zone_ctx["distance"],
            "near_active_zone": zone_ctx["near_zone"],
            "zone_touched": zone_ctx["zone_touched"],
            "trigger_pattern": trigger_ctx["pattern"],
            "trigger_ok": trigger_ctx["trigger_ok"],
            "rejection_candle": trigger_ctx["rejection"],
            "engulfing_candle": trigger_ctx["engulfing"],
            "inside_break_candle": trigger_ctx["inside_break"],
            "strong_close": trigger_ctx["strong_close"],
            "structure_tf": structure_tf,
            "asset_type": asset_type,
            "d1_adx": d1_adx_val,
            "h4_adx": h4_adx_val,
            # Independent directional assessment from Engine B's own price-action evidence.
            # Advisory only — does not affect scoring, checklist, or execution gates.
            # Allows Engine C to detect genuine direction conflicts vs inherited ones.
            "engine_b_independent_direction": self._determine_independent_direction(
                h1_sequence=sequence_data["state"],
                h4_sequence=macro_seq_data["state"],
                bos_data=bos_data,
                d1_bos=d1_bos,
                choch_data=choch_data,
                sweep_data=sweep_data,
            ),
            **_profile_result,
        }

    def _determine_lifecycle_state(self, res: dict, current_price: float, direction: str, trigger_ok: bool) -> tuple[str, str]:
        bos_confirmed = res.get("bos_confirmed", False)
        choch_confirmed = res.get("choch_confirmed", False)
        sweep = res.get("liquidity_sweep", False)

        zone_touched = res.get("zone_touched", False)
        near_zone = res.get("near_active_zone", False)

        sl = res.get("recommended_stop_loss")
        tp = res.get("recommended_take_profit")

        # 1. Invalidated checking
        if sl is not None:
            if direction == "LONG" and current_price < sl:
                return "invalidated", "Price breached Stop Loss level"
            elif direction == "SHORT" and current_price > sl:
                return "invalidated", "Price breached Stop Loss level"

        # 2. Expired checking
        if tp is not None:
            if direction == "LONG" and current_price >= tp:
                return "expired", "Target level already reached prior to entry"
            elif direction == "SHORT" and current_price <= tp:
                return "expired", "Target level already reached prior to entry"

        structural_setup = bos_confirmed or choch_confirmed or sweep

        # 3. Candidate
        if not structural_setup:
            return "candidate", "Awaiting structural confirmation (BOS/CHoCH/Sweep)"

        # 4. Triggered
        if trigger_ok and (zone_touched or near_zone):
            return "triggered", "Valid trigger pattern printed at the key entry zone"

        # 5. Armed
        if zone_touched or near_zone:
            return "armed", "Price is actively testing the key structural entry zone"

        # 6. Retracing (Pullback)
        nearest_sup = res.get("nearest_support_zone")
        nearest_res = res.get("nearest_resistance_zone")
        
        if direction == "LONG" and nearest_sup:
            center = nearest_sup.get("center", nearest_sup.get("upper", current_price))
            if current_price > center:
                return "retracing", "Price pulling back towards support zone"
        elif direction == "SHORT" and nearest_res:
            center = nearest_res.get("center", nearest_res.get("lower", current_price))
            if current_price < center:
                return "retracing", "Price pulling back towards resistance zone"

        # 7. Confirmed default
        return "confirmed", "Structural break confirmed, awaiting retracement to zone"


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
        _rma_cfg = profile.get("require_macro_align", False)
        if isinstance(_rma_cfg, dict):
            asset_type_lower = str(res.get("asset_type") or "").lower()
            require_macro_align = bool(_rma_cfg.get(asset_type_lower, False))
        else:
            require_macro_align = bool(_rma_cfg)
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
        bos_mtf = bool(res.get("bos_mtf_confirmed", False))
        structure_ok = not hard_counter and (
            micro_aligned
            or macro_aligned
            or res.get("bos_confirmed", False)
            or res.get("liquidity_sweep", False)
        )

        # Forex ADX gate — structural signals treated as noise below min ADX (trend strength).
        _forex_adx_gate = True
        if str(res.get("asset_type") or "").lower() == "forex":
            _forex_adx_min = float(config.CONFIG.get("ENGINE_B_FOREX_ADX_MIN", 25.0))
            if _forex_adx_min <= 0:
                _forex_adx_gate = True
            else:
                _adx_val = res.get("d1_adx")
                if _adx_val is None:
                    _adx_val = res.get("h4_adx")
                if _adx_val is not None:
                    try:
                        _adx_val = float(_adx_val)
                    except (TypeError, ValueError):
                        _adx_val = 0.0
                    _forex_adx_gate = _adx_val >= _forex_adx_min
                else:
                    _forex_adx_gate = False

        _diag_codes: list[str] = []
        if not _forex_adx_gate:
            structure_ok = False
            if str(res.get("asset_type") or "").lower() == "forex":
                _diag_codes.append(ENGINE_B_REASON_FOREX_ADX_LOW)

        macro_ok = macro_aligned or not require_macro_align
        zone_ok = bool(res.get("zone_touched") or res.get("near_active_zone"))
        trigger_ok = bool(res.get("trigger_ok"))
        breakout_ok = bool(res.get("bos_confirmed")) and bool(
            res.get("strong_close")
            or res.get("inside_break_candle")
            or res.get("engulfing_candle")
        )
        ob_at_zone = bool(res.get("ob_at_zone"))
        location_ok = zone_ok or ob_at_zone or (allow_breakout_entry and breakout_ok)
        room_dist = res.get("distance_to_res") if direction == "LONG" else res.get("distance_to_sup")
        room_ok = room_dist is None or room_dist >= atr_val * min_room_atr

        sl = res.get("recommended_stop_loss")
        tp = res.get("recommended_take_profit")
        rr = 0.0
        rr_ok = False
        tp_side_ok = False
        if sl and tp:
            sl_dist = abs(current_price - sl)
            if direction == "LONG":
                tp_side_ok = tp > current_price
            else:
                tp_side_ok = tp < current_price
            tp_dist = abs(tp - current_price) if tp_side_ok else 0.0
            if sl_dist > 0 and tp_side_ok:
                rr = tp_dist / sl_dist
                rr_ok = rr >= min_rr

        # Entry requires a candle pattern trigger OR a confirmed structural breakout
        # OR a professional technical catalyst (Sweep/CHoCH) at a key zone.
        entry_ok = (
            trigger_ok
            or (breakout_ok and bool(res.get("bos_volume_confirmed", False)))
            or (bool(res.get("liquidity_sweep")) and zone_ok)
            or (bool(res.get("choch_confirmed")) and zone_ok)
        )
        space_ok = room_ok or rr_ok

        confirmations = [structure_ok, location_ok, entry_ok, room_ok, rr_ok]
        if require_macro_align:
            confirmations.append(macro_ok)
        # Bonus confirmations — these add to the score but don't block if missing
        if bos_mtf:
            confirmations.append(True)  # MTF BOS alignment = extra point
        if ob_at_zone:
            confirmations.append(True)  # OB at zone = extra point

        total_score = float(sum(1 for passed in confirmations if passed))
        max_possible = float(len(confirmations)) if confirmations else 1.0
        _profile_points = 0.0
        _profile_ok = False
        _profile_alignment = "none"
        _profile_context = str(res.get("profile_notes") or "")
        if config.CONFIG.get("ENGINE_B_PROFILE_SCORING_ENABLED", False):
            _profile_valid = bool(res.get("prev_session_profile_valid", False))
            _profile_in_play = bool(res.get("profile_in_play", False))
            try:
                _profile_react = float(res.get("profile_reaction_strength", 0.0) or 0.0)
            except (TypeError, ValueError):
                _profile_react = 0.0
            _profile_bias = str(res.get("profile_bias") or "neutral").lower()
            _profile_bias_aligned = (
                (_profile_bias == "bullish" and direction == "LONG")
                or (_profile_bias == "bearish" and direction == "SHORT")
            )
            if _profile_valid and _profile_in_play and _profile_react > 0:
                max_possible += 1.0
                if _profile_bias_aligned and _profile_react >= 0.6:
                    _profile_points = min(1.0, _profile_react)
                    _profile_ok = True
                    _profile_alignment = "strong"
                elif _profile_bias_aligned and _profile_react >= 0.3:
                    _profile_points = round(_profile_react * 0.5, 2)
                    _profile_alignment = "moderate"
                else:
                    _profile_alignment = "weak"
                total_score += _profile_points
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
                and (trigger_ok or (breakout_ok and bool(res.get("bos_volume_confirmed", False))) or (bool(res.get("choch_confirmed")) and zone_ok))
                and rr_ok
                and macro_ok
            )

        lifecycle_state, lifecycle_reason = self._determine_lifecycle_state(
            res, current_price, direction, trigger_ok
        )

        if not room_ok:
            if direction == "LONG":
                _diag_codes.append(ENGINE_B_REASON_RESISTANCE_TOO_CLOSE)
            elif direction == "SHORT":
                _diag_codes.append(ENGINE_B_REASON_SUPPORT_TOO_CLOSE)

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
            "tp_side_ok": tp_side_ok,
            "space_ok": space_ok,
            "trigger_pattern": res.get("trigger_pattern", "NONE"),
            "ob_at_zone": ob_at_zone,
            "bos_mtf_confirmed": bos_mtf,
            "breaker_active": bool(res.get("breaker_block")),
            "profile_points": round(_profile_points, 2),
            "profile_ok": _profile_ok,
            "profile_alignment": _profile_alignment,
            "profile_context": _profile_context,
            "lifecycle_state": lifecycle_state,
            "lifecycle_reason": lifecycle_reason,
            "engine_b_diagnostics": {"reason_codes": _diag_codes},
        }

    def check_macro_correlation_detail(
        self, asset_close_series: list, dxy_close_series: list, direction: str
    ) -> tuple[bool, str | None]:
        """Same gate as check_macro_correlation; returns optional block reason code when False."""
        if len(asset_close_series) < 30 or len(dxy_close_series) < 30:
            return True, None

        min_len = min(len(asset_close_series), len(dxy_close_series), 30)
        a_series = pd.Series(asset_close_series[-min_len:])
        d_series = pd.Series(dxy_close_series[-min_len:])

        correlation = a_series.corr(d_series)

        dxy_recent = d_series.iloc[-5:].mean()
        dxy_past = d_series.iloc[-15:-5].mean()
        dxy_rising = dxy_recent > dxy_past

        if correlation and not pd.isna(correlation) and correlation < -0.6:
            if direction == "LONG" and dxy_rising:
                return False, ENGINE_B_REASON_ADVERSE_DXY
            if direction == "SHORT" and not dxy_rising:
                return False, ENGINE_B_REASON_ADVERSE_DXY

        return True, None

    def check_macro_correlation(
        self, asset_close_series: list, dxy_close_series: list, direction: str
    ) -> bool:
        """
        Calculates 30-period rolling Pearson correlation.
        Returns False (Block) if dynamically inversely correlated AND DXY moving against the trade.
        """
        ok, _reason = self.check_macro_correlation_detail(
            asset_close_series, dxy_close_series, direction
        )
        return ok

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
        d1_snap=None,
        h4_snap=None,
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
            d1_snap=d1_snap,
            h4_snap=h4_snap,
        )
        # Use entry_tf from style_profile to select entry candles (H4 for swing, H1 for intraday/scalp)
        _entry_tf = str((style_profile or {}).get("entry_tf", "H1")).upper() if style_profile else "H1"
        _entry_candles = h4_candles if _entry_tf == "H4" else h1_candles
        confidence = self.calculate_confidence(
            result,
            current_price,
            direction,
            learning_ctx,
            entry_candles=_entry_candles,
            style_profile=style_profile,
        )
        if isinstance(style_profile, dict):
            gate_ok, _ = engine_b_confidence_passes(
                confidence,
                style_profile,
                regime_label,
                asset_type,
            )
            if not gate_ok:
                return None  # No trade signal
        elif confidence["score"] < confidence_threshold or not confidence.get("passed"):
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
