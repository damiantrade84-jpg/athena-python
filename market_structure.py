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
ENGINE_B_REASON_TP_WRONG_SIDE = "tp_wrong_side"
ENGINE_B_REASON_SEQUENCE_COUNTER_TREND = "sequence_counter_trend"
ENGINE_B_REASON_NO_TRIGGER_PATTERN = "no_trigger_pattern"
ENGINE_B_REASON_BOS_WITHOUT_VOLUME = "bos_without_volume"
ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT = "d1_pd_array_conflict"
ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE = "structural_tp_too_close"


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
    # Engine B scores in checklist points; round regime scaling to avoid unreachable gates.
    return float(round(scaled))


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
    score = float(conf.get("gate_score", conf.get("score", 0.0)) or 0.0)
    passed = bool(conf.get("passed", False))
    return passed and score >= min_score_scaled, min_score_scaled


class NakedEngine:
    def __init__(self):
        self._registry_context = threading.local()

    @staticmethod
    def _compute_atr_from_candles(candles: list, period: int = 14, fallback: float | None = None) -> float:
        if not candles or len(candles) < 2:
            return float(fallback or 0.0)
        trs = []
        prev_close = None
        for c in candles:
            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])
            if prev_close is None:
                trs.append(high - low)
            else:
                trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
            prev_close = close
        window = trs[-period:] if len(trs) >= period else trs
        atr = float(np.mean(window)) if window else 0.0
        return atr if atr > 0 else float(fallback or 0.0)

    @staticmethod
    def _swing_cache(highs: np.ndarray, lows: np.ndarray, atr: float, distance: int = 3) -> dict:
        if atr <= 0:
            return {"peak_idx": np.array([], dtype=int), "trough_idx": np.array([], dtype=int)}
        prominence = atr * 0.8
        peak_idx, _ = find_peaks(highs, prominence=prominence, distance=distance)
        trough_idx, _ = find_peaks(-lows, prominence=prominence, distance=distance)
        return {"peak_idx": peak_idx, "trough_idx": trough_idx}

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
        self, highs: np.ndarray, lows: np.ndarray, atr: float, direction: str, swings: dict | None = None
    ) -> dict:
        """Finds the most recent swings to determine HH/HL or LH/LL sequence."""
        swings = swings if isinstance(swings, dict) else self._swing_cache(highs, lows, atr)
        peak_idx = swings.get("peak_idx", [])
        trough_idx = swings.get("trough_idx", [])

        last_peaks = [highs[i] for i in peak_idx[-3:]] if len(peak_idx) > 0 else []
        last_troughs = [lows[i] for i in trough_idx[-3:]] if len(trough_idx) > 0 else []

        sequence = "RANGING"
        if len(last_peaks) >= 2 and len(last_troughs) >= 2:
            if last_peaks[-1] > last_peaks[-2] and last_troughs[-1] > last_troughs[-2]:
                sequence = "HH_HL"  # Uptrend structure
            elif last_peaks[-1] < last_peaks[-2] and last_troughs[-1] < last_troughs[-2]:
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
                    volumes: np.ndarray = None, closes: np.ndarray = None,
                    swings: dict | None = None) -> dict:
        """
        Detect Break of Structure (BOS) patterns using peak/trough analysis.
        Returns dict with bullish/bearish BOS signals and broken levels.

        BOS confirmation is close-based: the bar's CLOSE must breach the prior
        swing level, not just the wick. Wick-only breaks are false positives.

        volumes: numpy array of bar volumes. None = skip volume check (forex).
        closes: numpy array of bar closes. None = fall back to highs/lows (legacy).
        """
        try:
            swings = swings if isinstance(swings, dict) else self._swing_cache(highs, lows, atr)
            peak_idx = swings.get("peak_idx", [])
            trough_idx = swings.get("trough_idx", [])

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

            # BOS Bull: current CLOSE breaks the prior structural swing high.
            bos_bull = False
            last_broken_high = None
            prior_high = float(last_peaks[-2])
            if _last_close > prior_high:
                bos_bull = True
                last_broken_high = prior_high

            # BOS Bear: current CLOSE breaks the prior structural swing low.
            bos_bear = False
            last_broken_low = None
            prior_low = float(last_troughs[-2])
            if _last_close_bear < prior_low:
                bos_bear = True
                last_broken_low = prior_low

            # Volume confirmation (only when volume data available)
            bos_volume_confirmed = True  # default True when no volume data
            if volumes is not None and len(volumes) >= 20 and (bos_bull or bos_bear):
                avg_vol_20 = float(np.mean(volumes[-20:]))
                last_vol = float(volumes[-1])
                if avg_vol_20 > 0:
                    multiplier = float(
                        config.CONFIG.get("NAKED_ENGINE", {}).get("bos_volume_multiplier", 1.3)
                    )
                    bos_volume_confirmed = last_vol >= avg_vol_20 * multiplier
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

    def _detect_choch(
        self, highs: np.ndarray, lows: np.ndarray, atr: float,
        closes: np.ndarray = None, swings: dict | None = None
    ) -> dict:
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
            swings = swings if isinstance(swings, dict) else self._swing_cache(highs, lows, atr)
            peak_idx = swings.get("peak_idx", [])
            trough_idx = swings.get("trough_idx", [])

            last_peaks = [highs[i] for i in peak_idx[-3:]]
            last_troughs = [lows[i] for i in trough_idx[-3:]]

            if len(last_peaks) < 3 or len(last_troughs) < 3:
                return {"choch_bull": False, "choch_bear": False, "choch_level": None, "choch_events": []}

            # Bullish CHoCH: price was making LH/LL (downtrend), then breaks
            # above the most recent Lower High — structure shifts bullish.
            was_bearish = (
                last_peaks[-1] < last_peaks[-2] < last_peaks[-3]
                and last_troughs[-1] < last_troughs[-2] < last_troughs[-3]
            )
            bull_break = closes[-1] if closes is not None and len(closes) > 0 else highs[-1]
            choch_bull = bool(was_bearish and bull_break > last_peaks[-1])

            # Bearish CHoCH: price was making HH/HL (uptrend), then breaks
            # below the most recent Higher Low — structure shifts bearish.
            was_bullish = (
                last_peaks[-1] > last_peaks[-2] > last_peaks[-3]
                and last_troughs[-1] > last_troughs[-2] > last_troughs[-3]
            )
            bear_break = closes[-1] if closes is not None and len(closes) > 0 else lows[-1]
            choch_bear = bool(was_bullish and bear_break < last_troughs[-1])

            choch_level = None
            choch_events = []
            if choch_bull:
                choch_level = float(last_peaks[-1])
                choch_events.append({"type": "bullish", "level": choch_level})
            elif choch_bear:
                choch_level = float(last_troughs[-1])
                choch_events.append({"type": "bearish", "level": choch_level})

            return {
                "choch_bull": choch_bull,
                "choch_bear": choch_bear,
                "choch_level": choch_level,
                "choch_events": choch_events,
            }
        except Exception:
            return {"choch_bull": False, "choch_bear": False, "choch_level": None, "choch_events": []}

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
                        ob_bottom = float(c["close"])  # body only — wick excluded per SMC
                        # Displacement: how far price moved after this candle (in ATRs)
                        if i + 1 < len(candles):
                            _ob_lookforward = int(
                                (config.CONFIG.get("NAKED_ENGINE", {}).get("ob_displacement_bars") or {})
                                .get("H4", 6)
                            )
                            max_after = max(float(candles[j]["high"]) for j in range(i + 1, min(i + _ob_lookforward, len(candles))))
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
                        ob_top = float(c["close"])  # body only — wick excluded per SMC
                        ob_bottom = float(c["open"])
                        # Displacement
                        if i + 1 < len(candles):
                            _ob_lookforward = int(
                                (config.CONFIG.get("NAKED_ENGINE", {}).get("ob_displacement_bars") or {})
                                .get("H4", 6)
                            )
                            min_after = min(float(candles[j]["low"]) for j in range(i + 1, min(i + _ob_lookforward, len(candles))))
                            displacement = (ob_top - min_after) / atr if atr > 0 else 0
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
        except Exception as _ob_err:
            import logging
            logging.getLogger(__name__).debug(
                f"[OB-DETECT] exception during detection: {_ob_err}"
            )

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
        for i in range(1, len(candles) - 1):
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

        _proximity_cfg = config.CONFIG.get("NAKED_ENGINE", {}).get("zone_proximity_atr_mult") or {}
        _proximity_mult = 0.5
        if isinstance(_proximity_cfg, dict):
            _proximity_mult = float(_proximity_cfg.get("default", 0.5))
        else:
            _proximity_mult = float(_proximity_cfg)
        near_zone = distance is not None and distance <= atr_val * _proximity_mult
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

        _current_price = float(candles[-1]["close"]) if candles else 0.0
        _min_range = max(atr * 0.05, _current_price * 1e-6 if _current_price > 0 else 1e-9, 1e-12)
        range_ = max(high - low, _min_range)
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

    @staticmethod
    def _float_or_none(value):
        try:
            if value is None:
                return None
            out = float(value)
            if out != out:
                return None
            return out
        except (TypeError, ValueError):
            return None

    @classmethod
    def _candle_value(cls, candle: dict, *keys: str, default: float = 0.0) -> float:
        if not isinstance(candle, dict):
            return default
        for key in keys:
            value = cls._float_or_none(candle.get(key))
            if value is not None:
                return value
        return default

    @classmethod
    def _crypto_kline_taker_pressure(cls, candle: dict) -> dict:
        total_volume = cls._candle_value(candle, "volume", "vol", default=0.0)
        taker_buy_volume = cls._float_or_none(
            (candle or {}).get("taker_buy_base_volume")
            if isinstance(candle, dict)
            else None
        )
        if taker_buy_volume is None and isinstance(candle, dict):
            taker_buy_volume = cls._float_or_none(candle.get("taker_buy_volume"))
        if taker_buy_volume is None or total_volume <= 0:
            return {
                "taker_data_missing": True,
                "taker_buy_volume": None,
                "taker_sell_volume": None,
                "taker_buy_ratio": None,
                "taker_sell_ratio": None,
            }
        taker_buy_volume = max(0.0, min(float(taker_buy_volume), float(total_volume)))
        taker_sell_volume = max(float(total_volume) - taker_buy_volume, 0.0)
        return {
            "taker_data_missing": False,
            "taker_buy_volume": taker_buy_volume,
            "taker_sell_volume": taker_sell_volume,
            "taker_buy_ratio": taker_buy_volume / float(total_volume),
            "taker_sell_ratio": taker_sell_volume / float(total_volume),
        }

    @classmethod
    def _crypto_volume_ratio(cls, candles: list, lookback: int = 20) -> float | None:
        if not candles or len(candles) < 2:
            return None
        last_volume = cls._candle_value(candles[-1], "volume", "vol", default=0.0)
        prior = candles[-(lookback + 1):-1] if len(candles) > lookback else candles[:-1]
        prior_volumes = [
            cls._candle_value(c, "volume", "vol", default=0.0)
            for c in prior
        ]
        prior_volumes = [v for v in prior_volumes if v > 0]
        if not prior_volumes:
            return None
        avg_volume = float(np.mean(prior_volumes))
        if avg_volume <= 0:
            return None
        return last_volume / avg_volume

    def _crypto_timeframe_trigger(
        self,
        candles: list,
        direction: str,
        atr: float,
        res: dict,
        timeframe: str,
    ) -> dict:
        state = {
            "trigger_passed": False,
            "trigger_timeframe": timeframe,
            "trigger_type": None,
            "trigger_volume_ratio": None,
            "trigger_taker_buy_ratio": None,
            "trigger_taker_sell_ratio": None,
            "trigger_displacement_atr": None,
            "taker_data_missing": True,
            "candle_pattern_state": {},
            "sweep_state": {},
            "bos_choch_trigger_state": {},
            "displacement_state": {},
            "retest_rejection_state": {},
            "trigger_reject_reason": "insufficient_candles",
        }
        if not candles or len(candles) < 6:
            return state

        last = candles[-1]
        prev_window = candles[-11:-1] if len(candles) > 11 else candles[:-1]
        if not prev_window:
            return state

        open_ = self._candle_value(last, "open")
        high = self._candle_value(last, "high")
        low = self._candle_value(last, "low")
        close = self._candle_value(last, "close")
        atr_val = float(atr) if atr and atr > 0 else self._compute_atr_from_candles(
            candles, fallback=max(abs(high - low), 1e-9)
        )
        atr_val = atr_val if atr_val > 0 else max(abs(high - low), 1e-9)
        range_ = max(high - low, 1e-9)
        body = abs(close - open_)
        upper_wick = max(high - max(open_, close), 0.0)
        lower_wick = max(min(open_, close) - low, 0.0)
        close_position = (close - low) / range_
        displacement_atr = body / atr_val

        prior_high = max(self._candle_value(c, "high") for c in prev_window)
        prior_low = min(self._candle_value(c, "low") for c in prev_window)
        volume_ratio = self._crypto_volume_ratio(candles)
        min_volume_ratio = float(config.CONFIG.get("ENGINE_B_CRYPTO_MIN_VOLUME_RATIO", 1.2) or 1.2)
        min_displacement_atr = float(
            config.CONFIG.get("ENGINE_B_CRYPTO_MIN_DISPLACEMENT_ATR", 0.35) or 0.35
        )
        min_taker_delta_ratio = float(
            config.CONFIG.get("ENGINE_B_CRYPTO_MIN_TAKER_DELTA_RATIO", 0.55) or 0.55
        )
        volume_ok = volume_ratio is not None and volume_ratio >= min_volume_ratio
        pressure = self._crypto_kline_taker_pressure(last)
        taker_missing = bool(pressure.get("taker_data_missing"))
        buy_ratio = pressure.get("taker_buy_ratio")
        sell_ratio = pressure.get("taker_sell_ratio")
        if direction == "LONG":
            taker_ok = taker_missing or (
                buy_ratio is not None and buy_ratio >= min_taker_delta_ratio
            )
            sweep_ok = low < prior_low and close > prior_low and volume_ok
            bos_ok = close > prior_high
            displacement_ok = (
                close > open_
                and displacement_atr >= min_displacement_atr
                and close_position >= 0.65
                and volume_ok
                and taker_ok
            )
            zone = res.get("nearest_support_zone") if isinstance(res, dict) else None
            level = (zone or {}).get("upper", (zone or {}).get("center"))
            retest_ok = (
                level is not None
                and low <= float(level)
                and close > float(level)
                and lower_wick >= max(body * 0.5, atr_val * 0.05)
                and (volume_ok or displacement_atr >= min_displacement_atr)
                and taker_ok
            )
            near_close_ok = close_position >= 0.65
        else:
            taker_ok = taker_missing or (
                sell_ratio is not None and sell_ratio >= min_taker_delta_ratio
            )
            sweep_ok = high > prior_high and close < prior_high and volume_ok
            bos_ok = close < prior_low
            displacement_ok = (
                close < open_
                and displacement_atr >= min_displacement_atr
                and close_position <= 0.35
                and volume_ok
                and taker_ok
            )
            zone = res.get("nearest_resistance_zone") if isinstance(res, dict) else None
            level = (zone or {}).get("lower", (zone or {}).get("center"))
            retest_ok = (
                level is not None
                and high >= float(level)
                and close < float(level)
                and upper_wick >= max(body * 0.5, atr_val * 0.05)
                and (volume_ok or displacement_atr >= min_displacement_atr)
                and taker_ok
            )
            near_close_ok = close_position <= 0.35

        trigger_type = None
        if sweep_ok:
            trigger_type = "SWEEP_RECLAIM"
        elif bos_ok:
            trigger_type = "BOS_CHOCH_CONFIRMATION"
        elif displacement_ok:
            trigger_type = "DISPLACEMENT"
        elif retest_ok:
            trigger_type = "RETEST_REJECTION"

        state.update(
            {
                "trigger_passed": trigger_type is not None,
                "trigger_type": trigger_type,
                "trigger_volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
                "trigger_taker_buy_ratio": round(buy_ratio, 4) if buy_ratio is not None else None,
                "trigger_taker_sell_ratio": round(sell_ratio, 4) if sell_ratio is not None else None,
                "trigger_displacement_atr": round(displacement_atr, 4),
                "taker_data_missing": taker_missing,
                "candle_pattern_state": {
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "body": round(body, 8),
                    "range": round(range_, 8),
                    "close_position": round(close_position, 4),
                    "near_required_close_area": near_close_ok,
                },
                "sweep_state": {
                    "prior_local_high": prior_high,
                    "prior_local_low": prior_low,
                    "sweep_reclaim": sweep_ok,
                },
                "bos_choch_trigger_state": {
                    "minor_structure_high": prior_high,
                    "minor_structure_low": prior_low,
                    "break_confirmed": bos_ok,
                },
                "displacement_state": {
                    "body_atr_multiple": round(displacement_atr, 4),
                    "volume_ok": volume_ok,
                    "taker_pressure_ok": taker_ok,
                },
                "retest_rejection_state": {
                    "level": float(level) if level is not None else None,
                    "retest_rejection": retest_ok,
                },
                "trigger_reject_reason": None if trigger_type else "no_crypto_m15_m5_trigger",
            }
        )
        return state

    def _crypto_trigger_profile(
        self,
        candles_by_tf: dict | None,
        direction: str,
        atr: float,
        res: dict,
    ) -> dict:
        configured_tfs = config.CONFIG.get(
            "ENGINE_B_CRYPTO_ENTRY_TIMEFRAMES", ["M15", "M5"]
        )
        if not isinstance(configured_tfs, (list, tuple)):
            configured_tfs = ["M15", "M5"]
        states = {}
        selected = None
        for tf in [str(tf).upper() for tf in configured_tfs]:
            candles = (candles_by_tf or {}).get(tf) or []
            tf_state = self._crypto_timeframe_trigger(candles, direction, atr, res, tf)
            states[tf] = tf_state
            if tf_state.get("trigger_passed") and selected is None:
                selected = tf_state

        selected = selected or {
            "trigger_passed": False,
            "trigger_timeframe": None,
            "trigger_type": None,
            "trigger_volume_ratio": None,
            "trigger_taker_buy_ratio": None,
            "trigger_taker_sell_ratio": None,
            "trigger_displacement_atr": None,
            "taker_data_missing": True,
            "trigger_reject_reason": "no_crypto_m15_m5_trigger",
        }
        reject_reasons = [
            f"{tf}:{state.get('trigger_reject_reason')}"
            for tf, state in states.items()
            if not state.get("trigger_passed")
        ]
        return {
            **selected,
            "trigger_reject_reason": None
            if selected.get("trigger_passed")
            else ";".join(reject_reasons) or "no_crypto_m15_m5_trigger",
            "m15_trigger_state": states.get("M15"),
            "m5_trigger_state": states.get("M5"),
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

        # --- BOS direction (diagnostic only; primary checklist already gates on BOS) ---
        bos_bull = bool(bos_data.get("bos_bull"))
        bos_bear = bool(bos_data.get("bos_bear"))
        diagnostic_bos = {"bos_bull": bos_bull, "bos_bear": bos_bear}
        # BOS stays diagnostic-only; it is already used by the primary checklist.

        # --- D1 BOS (diagnostic only; MTF BOS is tracked separately) ---
        d1_bull = bool(d1_bos.get("bos_bull"))
        d1_bear = bool(d1_bos.get("bos_bear"))
        diagnostic_bos.update({"d1_bull": d1_bull, "d1_bear": d1_bear})

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
        # H4 swing carries more weight than H1, CHoCH, or sweep.
        weights = {
            "h4_swing": 2,
            "h1_swing": 1,
            "choch":    1,
            "sweep":    1,
        }
        total_weight = sum(weights.values())
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
                "diagnostic_bos": diagnostic_bos,
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
                f"(h4={h4_sequence}, choch={votes.get('choch', 0)}, sweep={votes.get('sweep', 0)})"
            )
        elif score_ratio < -0.15:
            direction = "SHORT"
            reason = (
                f"Structural evidence bearish: {negative}/{len(active_votes)} indicators agree "
                f"(h4={h4_sequence}, choch={votes.get('choch', 0)}, sweep={votes.get('sweep', 0)})"
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
            "diagnostic_bos": diagnostic_bos,
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
        struct_swings = self._swing_cache(struct_highs, struct_lows, atr)

        # 1. Macro Zones (D1/H4)
        # Using H4 to find thick zones gives standard resolution.
        res_zones, sup_zones = self._find_zones(
            h4_highs, h4_lows, atr, regime, h4_candles
        )

        # Determine FVG overlap with zones — graded by quality
        fvgs = self._detect_fvg(h4_candles)
        active_fvgs = [f for f in fvgs if not f.get("mitigated", False)]

        # 2. Immediate Structure Sequence and Macro H4 Sequence
        h4_swings = self._swing_cache(h4_highs, h4_lows, atr)
        sequence_data = self._determine_sequence(struct_highs, struct_lows, atr, direction, swings=struct_swings)
        macro_seq_data = self._determine_sequence(h4_highs, h4_lows, atr, direction, swings=h4_swings)

        # 3. BOS and Sweep Detection
        # Extract volumes for BOS volume confirmation (None for forex — no centralized volume)
        struct_volumes = None
        _has_vol = any(float(c.get("vol", 0)) > 0 for c in struct_candles[-5:])
        if _has_vol:
            struct_volumes = np.array([float(c.get("vol", 0)) for c in struct_candles])

        bos_data = self._detect_bos(
            struct_highs, struct_lows, atr, volumes=struct_volumes, closes=struct_closes, swings=struct_swings
        )

        # Compute swing highs/lows for sweep detection (structural reference levels)
        _sweep_swing_high = None
        _sweep_swing_low = None
        try:
            _pk_idx = struct_swings.get("peak_idx", [])
            _tr_idx = struct_swings.get("trough_idx", [])
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
        d1_atr = self._compute_atr_from_candles(d1_candles, fallback=atr)
        d1_swings = self._swing_cache(d1_highs, d1_lows, d1_atr)
        d1_bos = self._detect_bos(d1_highs, d1_lows, d1_atr, closes=d1_closes, swings=d1_swings)

        # D1 PD-array detection (informational — no scoring gate change).
        # Detects D1 order blocks and FVGs that price is approaching and flags a
        # directional conflict when a D1 zone opposes the requested trade direction.
        # Consumers (Engine C, Marcus Reid) can use d1_pd_array_conflict as a soft
        # veto or reduce conviction — not enforced here.
        d1_order_blocks_raw: list = []
        d1_fvgs_raw: list = []
        d1_pd_array_conflict: bool = False
        d1_conflict_details: list = []
        d1_conflict_metric_details: list = []
        try:
            d1_order_blocks_raw = self._detect_order_blocks(d1_candles, d1_bos, d1_atr)
            d1_fvgs_raw = [f for f in self._detect_fvg(d1_candles) if not f.get("mitigated")]
            _conflict_window = d1_atr * 3.0  # within 3 D1 ATRs is "approaching"
            for ob in d1_order_blocks_raw:
                ob_mid = (ob["top"] + ob["bottom"]) / 2.0
                ob_dist = abs(current_price - ob_mid)
                if ob_dist > _conflict_window:
                    continue
                # Opposing D1 OB: bearish OB above price on a LONG; bullish OB below on SHORT
                if direction == "LONG" and ob["type"] == "bearish" and ob_mid > current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bearish_OB@{ob_mid:.5f} (str={ob['strength']})")
                    d1_conflict_metric_details.append({
                        "zone_type": "bearish_OB",
                        "zone_price_range": {"top": ob["top"], "bottom": ob["bottom"]},
                        "zone_mid": ob_mid,
                        "distance_to_conflict": ob_dist,
                        "distance_in_atr": (ob_dist / d1_atr) if d1_atr > 0 else None,
                        "conflict_side": "above_price",
                        "entry_side_or_tp_side": "tp_side",
                        "strength": ob.get("strength"),
                    })
                elif direction == "SHORT" and ob["type"] == "bullish" and ob_mid < current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bullish_OB@{ob_mid:.5f} (str={ob['strength']})")
                    d1_conflict_metric_details.append({
                        "zone_type": "bullish_OB",
                        "zone_price_range": {"top": ob["top"], "bottom": ob["bottom"]},
                        "zone_mid": ob_mid,
                        "distance_to_conflict": ob_dist,
                        "distance_in_atr": (ob_dist / d1_atr) if d1_atr > 0 else None,
                        "conflict_side": "below_price",
                        "entry_side_or_tp_side": "tp_side",
                        "strength": ob.get("strength"),
                    })
            for fvg in d1_fvgs_raw:
                fvg_mid = (fvg["top"] + fvg["bottom"]) / 2.0
                fvg_dist = abs(current_price - fvg_mid)
                if fvg_dist > _conflict_window:
                    continue
                if direction == "LONG" and fvg["type"] == "bearish" and fvg_mid > current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bearish_FVG@{fvg_mid:.5f}")
                    d1_conflict_metric_details.append({
                        "zone_type": "bearish_FVG",
                        "zone_price_range": {"top": fvg["top"], "bottom": fvg["bottom"]},
                        "zone_mid": fvg_mid,
                        "distance_to_conflict": fvg_dist,
                        "distance_in_atr": (fvg_dist / d1_atr) if d1_atr > 0 else None,
                        "conflict_side": "above_price",
                        "entry_side_or_tp_side": "tp_side",
                    })
                elif direction == "SHORT" and fvg["type"] == "bullish" and fvg_mid < current_price:
                    d1_pd_array_conflict = True
                    d1_conflict_details.append(f"D1_bullish_FVG@{fvg_mid:.5f}")
                    d1_conflict_metric_details.append({
                        "zone_type": "bullish_FVG",
                        "zone_price_range": {"top": fvg["top"], "bottom": fvg["bottom"]},
                        "zone_mid": fvg_mid,
                        "distance_to_conflict": fvg_dist,
                        "distance_in_atr": (fvg_dist / d1_atr) if d1_atr > 0 else None,
                        "conflict_side": "below_price",
                        "entry_side_or_tp_side": "tp_side",
                    })
        except Exception as _d1e:
            log.debug(f"[D1-PD] detection error: {_d1e}")

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
        choch_data = self._detect_choch(struct_highs, struct_lows, atr, closes=struct_closes, swings=struct_swings)

        # 3d. Breaker Blocks — after CHoCH, the broken swing level becomes new S/R
        # A bullish CHoCH breaks above a Lower High → that LH level becomes support (breaker)
        # A bearish CHoCH breaks below a Higher Low → that HL level becomes resistance (breaker)
        breaker_blocks = []
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
            breaker_blocks.append(breaker_block)
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
            breaker_blocks.append(breaker_block)
        for event in choch_data.get("choch_events") or []:
            level = event.get("level")
            if level is None or level == choch_data.get("choch_level"):
                continue
            level = float(level)
            if event.get("type") == "bullish":
                breaker = {
                    "type": "bullish_breaker",
                    "level": level,
                    "upper": level + (atr * 0.3),
                    "lower": level - (atr * 0.3),
                }
                sup_zones.append({
                    "upper": breaker["upper"],
                    "lower": breaker["lower"],
                    "center": breaker["level"],
                    "volume_strength": 0.8,
                    "fvg_overlap": False,
                    "fvg_size_atr": 0.0,
                    "is_breaker": True,
                })
                breaker_blocks.append(breaker)
            elif event.get("type") == "bearish":
                breaker = {
                    "type": "bearish_breaker",
                    "level": level,
                    "upper": level + (atr * 0.3),
                    "lower": level - (atr * 0.3),
                }
                res_zones.append({
                    "upper": breaker["upper"],
                    "lower": breaker["lower"],
                    "center": breaker["level"],
                    "volume_strength": 0.8,
                    "fvg_overlap": False,
                    "fvg_size_atr": 0.0,
                    "is_breaker": True,
                })
                breaker_blocks.append(breaker)
        breaker_block = breaker_blocks[-1] if breaker_blocks else None

        # ── Zone Merging: cluster zones within 0.5 ATR into single high-confluence pools ──
        def _merge_zones(zones, merge_dist):
            if not zones:
                return zones
            sorted_z = sorted(zones, key=lambda z: z["center"])
            merged = [sorted_z[0]]
            anchors = [float(sorted_z[0]["center"])]
            for z in sorted_z[1:]:
                prev = merged[-1]
                if abs(z["center"] - anchors[-1]) <= merge_dist:
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
                    anchors.append(float(z["center"]))
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
        structural_target_candidates = []
        
        # Crypto-specific target selection model
        crypto_target_v2_enabled_cfg = bool(
            config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_V2_ENABLED", False)
        )
        crypto_target_model_enabled = bool(
            config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_MODEL_ENABLED", False)
            or crypto_target_v2_enabled_cfg
        )
        is_crypto = asset_type.lower() == "crypto"
        
        # Debug fields for crypto target model
        old_target = None
        old_rr = None
        new_tp1 = None
        new_tp2 = None
        new_rr = None
        target_selection_mode = "standard"
        target_reject_reason = None
        path_clear_to_tp2 = None
        target_atr_multiple = None
        target_mode_used = target_selection_mode
        used_true_h4_liquidity_target = False
        used_fallback_projection = False
        fallback_reason = None
        tp2_reject_reason = None
        h4_liquidity_target_count = None
        selected_h4_liquidity_rank = None
        selected_h4_liquidity_price = None
        min_target_atr_multiple = None
        max_target_atr_multiple = None
        path_block_reason = None
        candidate_targets = []
        candidate_targets_total = 0
        candidate_targets_considered = 0
        candidate_targets_rejected = 0
        candidate_reject_reasons_count = {}
        selected_target_price = None
        selected_target_tf = None
        selected_target_type = None
        selected_target_rank = None
        selected_target_rr = None
        selected_target_atr_multiple = None
        selected_target_is_structural = False
        fallback_projection_price = None
        fallback_projection_rr = None
        fallback_used_for_diagnostics_only = False
        target_v2_reject_reason = None
        
        if is_crypto and crypto_target_model_enabled:
            crypto_target_v2_enabled = crypto_target_v2_enabled_cfg
            allow_fallback_target_for_pass = bool(config.CONFIG.get("ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS", False))
            target_selection_mode = "crypto_target_v2" if crypto_target_v2_enabled else "crypto_model"
            target_mode_used = target_selection_mode
            h4_targets = valid_res if direction == "LONG" else valid_sup
            h4_liquidity_target_count = len(h4_targets or [])
            tp2_candidate_source = None
            
            # First, calculate standard TP as old_target for comparison
            if direction == "LONG" and valid_res:
                for zone in sorted(valid_res, key=lambda z: z["lower"]):
                    structural_tp = zone["lower"] - (atr * sl_mult)
                    if structural_tp > current_price + (atr * 0.5):
                        old_target = structural_tp
                        break
            elif direction == "SHORT" and valid_sup:
                for zone in sorted(valid_sup, key=lambda z: z["upper"], reverse=True):
                    structural_tp = zone["upper"] + (atr * sl_mult)
                    if structural_tp < current_price - (atr * 0.5):
                        old_target = structural_tp
                        break
            
            if old_target is None:
                sl_dist = abs(current_price - sl) if (sl is not None) else (atr * sl_mult)
                if sl_dist == 0:
                    sl_dist = atr * sl_mult
                if direction == "LONG":
                    old_target = current_price + (sl_dist * fallback_rr)
                else:
                    old_target = current_price - (sl_dist * fallback_rr)
            
            # Calculate old RR
            if sl and old_target:
                sl_dist = abs(current_price - sl)
                tp_dist = abs(old_target - current_price)
                old_rr = tp_dist / sl_dist if sl_dist > 0 else 0.0
            
            # TP1: nearest minor level (same as standard logic)
            new_tp1 = old_target

            sl_dist_for_target = abs(current_price - sl) if sl is not None else 0.0
            if sl_dist_for_target > 0:
                if direction == "LONG":
                    fallback_projection_price = current_price + (sl_dist_for_target * (fallback_rr * 2))
                else:
                    fallback_projection_price = current_price - (sl_dist_for_target * (fallback_rr * 2))
                fallback_projection_rr = (
                    abs(fallback_projection_price - current_price) / sl_dist_for_target
                )

            if crypto_target_v2_enabled:
                max_rank = int(config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_SEARCH_MAX_RANK", 8) or 8)
                min_rr_required = float(config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_MIN_RR", 1.2) or 1.2)
                min_atr_multiple = float(config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_MIN_ATR_MULTIPLE", 1.0) or 1.0)
                max_atr_multiple = float(config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_MAX_ATR_MULTIPLE", 6.0) or 6.0)
                min_target_atr_multiple = min_atr_multiple
                max_target_atr_multiple = max_atr_multiple
                allow_d1_targets = bool(config.CONFIG.get("ENGINE_B_CRYPTO_ALLOW_D1_TARGETS", True))

                def _candidate_price(zone, _direction):
                    return float(zone["lower"] if _direction == "LONG" else zone["upper"])

                def _zone_rows(zones, tf_name, target_type):
                    rows = []
                    sorted_zones = (
                        sorted(zones, key=lambda z: z["lower"])
                        if direction == "LONG"
                        else sorted(zones, key=lambda z: z["upper"], reverse=True)
                    )
                    for idx, zone in enumerate(sorted_zones[:max_rank], 1):
                        rows.append({
                            "zone": zone,
                            "target_price": _candidate_price(zone, direction),
                            "target_tf": tf_name,
                            "target_type": target_type,
                            "rank": idx,
                        })
                    return rows

                h4_candidate_rows = _zone_rows(h4_targets or [], "H4", "liquidity_zone")
                d1_candidate_rows = []
                if allow_d1_targets:
                    try:
                        d1_res, d1_sup = self._find_zones(d1_highs, d1_lows, d1_atr, regime, d1_candles)
                        d1_res = [z for z in d1_res if z["upper"] >= current_price]
                        d1_sup = [z for z in d1_sup if z["lower"] <= current_price]
                        d1_targets = d1_res if direction == "LONG" else d1_sup
                        d1_candidate_rows = _zone_rows(d1_targets or [], "D1", "liquidity_zone")
                    except Exception:
                        d1_candidate_rows = []

                raw_candidate_rows = h4_candidate_rows + d1_candidate_rows
                candidate_targets_total = len(raw_candidate_rows)
                reject_counts = {}
                selected_candidate = None

                def _reject(candidate, reason):
                    candidate["valid"] = False
                    candidate["reject_reason"] = reason
                    reject_counts[reason] = reject_counts.get(reason, 0) + 1
                    candidate_targets.append(candidate)

                for row in raw_candidate_rows:
                    candidate_targets_considered += 1
                    price = float(row["target_price"])
                    candidate = {
                        "target_price": price,
                        "target_tf": row["target_tf"],
                        "target_type": row["target_type"],
                        "rank": row["rank"],
                        "is_structural": True,
                        "distance_from_entry": abs(price - current_price),
                        "atr_multiple": (abs(price - current_price) / atr) if atr > 0 else None,
                        "rr": None,
                        "path_clear": True,
                        "path_block_reason": None,
                        "valid": True,
                        "reject_reason": None,
                    }
                    if sl_dist_for_target <= 0:
                        _reject(candidate, "stop_invalid")
                        continue
                    if direction == "LONG" and price <= current_price:
                        _reject(candidate, "wrong_side_of_entry")
                        continue
                    if direction == "SHORT" and price >= current_price:
                        _reject(candidate, "wrong_side_of_entry")
                        continue
                    candidate["rr"] = abs(price - current_price) / sl_dist_for_target
                    if candidate["rr"] < min_rr_required:
                        _reject(candidate, "rr_below_crypto_target_min")
                        continue
                    if candidate["atr_multiple"] is not None and candidate["atr_multiple"] < min_atr_multiple:
                        _reject(candidate, "atr_multiple_below_min")
                        continue
                    if candidate["atr_multiple"] is not None and candidate["atr_multiple"] > max_atr_multiple:
                        _reject(candidate, "atr_multiple_above_max")
                        continue
                    blockers = [
                        other for other in raw_candidate_rows
                        if other is not row
                        and other["target_tf"] == row["target_tf"]
                        and (
                            (direction == "LONG" and current_price < float(other["target_price"]) < price)
                            or (direction == "SHORT" and price < float(other["target_price"]) < current_price)
                        )
                    ]
                    if blockers:
                        candidate["path_clear"] = False
                        candidate["path_block_reason"] = "major_structure_between_entry_and_target"
                        _reject(candidate, "blocked_by_major_opposing_structure")
                        continue
                    candidate_targets.append(candidate)
                    selected_candidate = candidate
                    break

                candidate_targets_rejected = sum(1 for c in candidate_targets if not c.get("valid"))
                candidate_reject_reasons_count = reject_counts

                if selected_candidate:
                    tp = float(selected_candidate["target_price"])
                    new_tp2 = tp
                    new_rr = selected_candidate["rr"]
                    tp_source = f"crypto_target_v2_{selected_candidate['target_tf'].lower()}_structural"
                    target_mode_used = "crypto_target_v2_structural"
                    used_true_h4_liquidity_target = selected_candidate["target_tf"] == "H4"
                    selected_h4_liquidity_rank = selected_candidate["rank"] if selected_candidate["target_tf"] == "H4" else None
                    selected_h4_liquidity_price = tp if selected_candidate["target_tf"] == "H4" else None
                    target_atr_multiple = selected_candidate["atr_multiple"]
                    path_clear_to_tp2 = selected_candidate["path_clear"]
                    path_block_reason = selected_candidate["path_block_reason"]
                    selected_target_price = tp
                    selected_target_tf = selected_candidate["target_tf"]
                    selected_target_type = selected_candidate["target_type"]
                    selected_target_rank = selected_candidate["rank"]
                    selected_target_rr = selected_candidate["rr"]
                    selected_target_atr_multiple = selected_candidate["atr_multiple"]
                    selected_target_is_structural = True
                else:
                    target_v2_reject_reason = "no_valid_structural_target"
                    target_reject_reason = target_v2_reject_reason
                    fallback_reason = target_v2_reject_reason
                    fallback_used_for_diagnostics_only = True
                    # Leave tp unset so a diagnostic fallback projection cannot pass Engine B.
                    tp = None
                    new_rr = None

            else:
            
                # TP2: legacy next major H4 liquidity zone (skip the first, use the second)
                if direction == "LONG" and len(valid_res) >= 2:
                    sorted_res = sorted(valid_res, key=lambda z: z["lower"])
                    major_zone = sorted_res[1]
                    selected_h4_liquidity_price = major_zone["lower"]
                    new_tp2 = major_zone["lower"] - (atr * sl_mult)
                    selected_h4_liquidity_rank = 2
                    tp2_candidate_source = "true_h4_liquidity"
                elif direction == "SHORT" and len(valid_sup) >= 2:
                    sorted_sup = sorted(valid_sup, key=lambda z: z["upper"], reverse=True)
                    major_zone = sorted_sup[1]
                    selected_h4_liquidity_price = major_zone["upper"]
                    new_tp2 = major_zone["upper"] + (atr * sl_mult)
                    selected_h4_liquidity_rank = 2
                    tp2_candidate_source = "true_h4_liquidity"
                
                if new_tp2 is None:
                    fallback_reason = "no_second_h4_liquidity_target"
                    tp2_candidate_source = "fallback_projection"
                    new_tp2 = fallback_projection_price
                
                max_atr_multiple = config.CONFIG.get("ENGINE_B_CRYPTO_MAX_TARGET_ATR_MULTIPLE", 6.0)
                min_atr_multiple = config.CONFIG.get("ENGINE_B_CRYPTO_MIN_TARGET_ATR_MULTIPLE", 1.0)
                require_clear_path = config.CONFIG.get("ENGINE_B_CRYPTO_REQUIRE_CLEAR_PATH_TO_TP2", True)
                min_target_atr_multiple = min_atr_multiple
                max_target_atr_multiple = max_atr_multiple
                
                tp2_valid = True
                if new_tp2 is not None:
                    tp2_dist = abs(new_tp2 - current_price)
                    tp2_atr_multiple = tp2_dist / atr if atr > 0 else 0.0
                    target_atr_multiple = tp2_atr_multiple
                    if direction == "LONG" and new_tp2 <= current_price:
                        tp2_valid = False
                        target_reject_reason = "tp2_wrong_direction"
                    elif direction == "SHORT" and new_tp2 >= current_price:
                        tp2_valid = False
                        target_reject_reason = "tp2_wrong_direction"
                    elif tp2_atr_multiple < min_atr_multiple:
                        tp2_valid = False
                        target_reject_reason = f"tp2_too_close_{tp2_atr_multiple:.2f}atr"
                    elif tp2_atr_multiple > max_atr_multiple:
                        tp2_valid = False
                        target_reject_reason = f"tp2_too_far_{tp2_atr_multiple:.2f}atr"
                    elif require_clear_path:
                        path_clear_to_tp2 = True
                        path_block_reason = None
                    if not tp2_valid:
                        tp2_reject_reason = target_reject_reason
                
                if tp2_valid and new_tp2 is not None:
                    tp = new_tp2
                    if tp2_candidate_source == "true_h4_liquidity":
                        tp_source = "crypto_tp2_major_h4"
                        target_mode_used = "true_h4_liquidity_tp2"
                        used_true_h4_liquidity_target = True
                    elif allow_fallback_target_for_pass:
                        tp_source = "crypto_fallback_2x_rr"
                        target_mode_used = "fallback_2x_rr_projection"
                        used_fallback_projection = True
                    else:
                        tp = None
                        target_mode_used = "fallback_2x_rr_projection_diagnostic_only"
                        used_fallback_projection = True
                        fallback_used_for_diagnostics_only = True
                        target_reject_reason = "fallback_target_not_allowed"
                else:
                    if allow_fallback_target_for_pass:
                        tp = fallback_projection_price
                        tp_source = "crypto_fallback_2x_rr"
                    else:
                        tp = None
                        fallback_used_for_diagnostics_only = True
                    target_mode_used = "fallback_2x_rr_projection" if allow_fallback_target_for_pass else "fallback_2x_rr_projection_diagnostic_only"
                    used_fallback_projection = True
                    if fallback_reason is None and target_reject_reason is not None:
                        if tp2_candidate_source == "true_h4_liquidity" and selected_h4_liquidity_rank:
                            fallback_reason = f"selected_h4_rank_{selected_h4_liquidity_rank}_{target_reject_reason}"
                        else:
                            fallback_reason = target_reject_reason
                    fallback_reason = fallback_reason or "tp2_invalid_used_2x_rr"
                    target_reject_reason = target_reject_reason or "tp2_invalid_used_2x_rr"
                
                if sl and tp:
                    sl_dist = abs(current_price - sl)
                    tp_dist = abs(tp - current_price)
                    new_rr = tp_dist / sl_dist if sl_dist > 0 else 0.0
        else:
            # Standard target selection (non-crypto or crypto model disabled)
            if direction == "LONG" and valid_res:
                for zone in sorted(valid_res, key=lambda z: z["lower"]):
                    structural_tp = zone["lower"] - (atr * sl_mult)
                    target_distance = structural_tp - current_price
                    structural_target_candidates.append({
                        "target_type": "resistance_zone",
                        "target_price": structural_tp,
                        "zone": dict(zone),
                        "correct_side": structural_tp > current_price,
                        "distance_to_target": target_distance,
                        "atr_multiple_to_target": (target_distance / atr) if atr > 0 else None,
                        "selected": False,
                        "rejected_reason": "too_close_or_wrong_side"
                        if structural_tp <= current_price + (atr * 0.5)
                        else None,
                    })
                    if structural_tp <= current_price + (atr * 0.5):
                        tp_structural_limited = True
                        continue
                    tp = structural_tp
                    tp_source = "structural_zone"
                    structural_target_candidates[-1]["selected"] = True
                    break
            elif direction == "SHORT" and valid_sup:
                for zone in sorted(valid_sup, key=lambda z: z["upper"], reverse=True):
                    structural_tp = zone["upper"] + (atr * sl_mult)
                    target_distance = current_price - structural_tp
                    structural_target_candidates.append({
                        "target_type": "support_zone",
                        "target_price": structural_tp,
                        "zone": dict(zone),
                        "correct_side": structural_tp < current_price,
                        "distance_to_target": target_distance,
                        "atr_multiple_to_target": (target_distance / atr) if atr > 0 else None,
                        "selected": False,
                        "rejected_reason": "too_close_or_wrong_side"
                        if structural_tp >= current_price - (atr * 0.5)
                        else None,
                    })
                    if structural_tp >= current_price - (atr * 0.5):
                        tp_structural_limited = True
                        continue
                    tp = structural_tp
                    tp_source = "structural_zone"
                    structural_target_candidates[-1]["selected"] = True
                    break

            # Generate TP from fallback_rr when no usable opposing structural zone exists.
            if tp is None:
                sl_dist = abs(current_price - sl) if (sl is not None) else (atr * sl_mult)
                if sl_dist == 0:
                    sl_dist = atr * sl_mult
                if direction == "LONG":
                    tp = current_price + (sl_dist * fallback_rr)
                else:
                    tp = current_price - (sl_dist * fallback_rr)
        
        selected_structural_target = next(
            (c for c in structural_target_candidates if c.get("selected")), None
        )

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
        latest_trigger_candle_time = None
        if trigger_candles:
            _last_trigger_candle = trigger_candles[-1]
            latest_trigger_candle_time = (
                _last_trigger_candle.get("time")
                or _last_trigger_candle.get("timestamp")
                or _last_trigger_candle.get("datetime")
                or _last_trigger_candle.get("date")
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
            "direction": direction,
            "nearest_resistance_zone": nearest_res,
            "nearest_support_zone": nearest_sup,
            "current_swing_sequence": sequence_data["state"],
            "macro_swing_sequence": macro_seq_data["state"],
            "recommended_stop_loss": sl,
            "recommended_take_profit": tp,
            "tp_source": tp_source,
            "tp_structural_limited": tp_structural_limited,
            "current_price": current_price,
            "structural_target_candidates": structural_target_candidates,
            "selected_structural_target": selected_structural_target,
            "nearest_target_type": (selected_structural_target or {}).get("target_type"),
            "nearest_target_price": (selected_structural_target or {}).get("target_price"),
            "distance_to_res": (nearest_res["lower"] - current_price)
            if nearest_res
            else None,
            "distance_to_sup": (current_price - nearest_sup["upper"])
            if nearest_sup
            else None,
            "atr": atr,
            "d1_atr": d1_atr,
            "bos_confirmed": bos_confirmed,
            "bos_mtf_confirmed": bos_mtf_confirmed,
            "bos_volume_confirmed": bos_data.get("bos_volume_confirmed", True),
            "bos_data": bos_data,
            "d1_bos_data": d1_bos,
            "sweep_data": sweep_data,
            "choch_data": choch_data,
            "choch_confirmed": choch_confirmed,
            "breaker_block": breaker_block,
            "breaker_blocks": breaker_blocks,
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
            "trigger_timeframe": "H4" if _use_d1_structure else "H1",
            "latest_confirmed_trigger_candle_time": latest_trigger_candle_time,
            "structure_tf": structure_tf,
            "asset_type": asset_type,
            "d1_adx": d1_adx_val,
            "h4_adx": h4_adx_val,
            # Crypto target model debug fields
            "crypto_target_old_target": old_target,
            "crypto_target_old_rr": old_rr,
            "crypto_target_tp1": new_tp1,
            "crypto_target_tp2": new_tp2,
            "crypto_target_new_rr": new_rr,
            "crypto_target_selection_mode": target_selection_mode,
            "crypto_target_reject_reason": target_reject_reason,
            "crypto_target_path_clear_to_tp2": path_clear_to_tp2,
            "crypto_target_atr_multiple": target_atr_multiple,
            "crypto_target_final_target": tp,
            "crypto_target_final_rr": new_rr,
            "crypto_target_mode_used": target_mode_used,
            "crypto_target_used_true_h4_liquidity_target": used_true_h4_liquidity_target,
            "crypto_target_used_fallback_projection": used_fallback_projection,
            "crypto_target_fallback_reason": fallback_reason,
            "crypto_target_tp2_reject_reason": tp2_reject_reason,
            "crypto_target_h4_liquidity_target_count": h4_liquidity_target_count,
            "crypto_target_selected_h4_liquidity_rank": selected_h4_liquidity_rank,
            "crypto_target_selected_h4_liquidity_price": selected_h4_liquidity_price,
            "crypto_target_min_target_atr_multiple": min_target_atr_multiple,
            "crypto_target_max_target_atr_multiple": max_target_atr_multiple,
            "crypto_target_path_block_reason": path_block_reason,
            "crypto_target_candidate_targets": candidate_targets,
            "crypto_target_candidate_targets_total": candidate_targets_total,
            "crypto_target_candidate_targets_considered": candidate_targets_considered,
            "crypto_target_candidate_targets_rejected": candidate_targets_rejected,
            "crypto_target_candidate_reject_reasons_count": candidate_reject_reasons_count,
            "crypto_target_selected_target_price": selected_target_price,
            "crypto_target_selected_target_tf": selected_target_tf,
            "crypto_target_selected_target_type": selected_target_type,
            "crypto_target_selected_target_rank": selected_target_rank,
            "crypto_target_selected_target_rr": selected_target_rr,
            "crypto_target_selected_target_atr_multiple": selected_target_atr_multiple,
            "crypto_target_selected_target_is_structural": selected_target_is_structural,
            "crypto_target_fallback_projection_price": fallback_projection_price,
            "crypto_target_fallback_projection_rr": fallback_projection_rr,
            "crypto_target_fallback_used_for_diagnostics_only": fallback_used_for_diagnostics_only,
            "crypto_target_v2_reject_reason": target_v2_reject_reason,
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
            # D1 PD-array context (informational — no scoring gate).
            # d1_pd_array_conflict=True when an opposing D1 OB or unmitigated FVG is within
            # 3×ATR in the direction of trade. Surfaces in Marcus Reid input and UI diagnostics.
            "d1_order_blocks": d1_order_blocks_raw,
            "d1_fvgs": d1_fvgs_raw,
            "d1_pd_array_conflict": d1_pd_array_conflict,
            "d1_conflict_details": d1_conflict_details,
            "d1_conflict_metric_details": d1_conflict_metric_details,
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
        crypto_entry_candles_by_tf: dict | None = None,
    ) -> dict:
        atr = res.get("atr", 1.0)
        atr_val = atr if atr > 0 else 0.0001
        h1_seq = res.get("current_swing_sequence", "")
        h4_seq = res.get("macro_swing_sequence", "")
        profile = style_profile if isinstance(style_profile, dict) else {}
        min_room_atr = float(profile.get("min_room_atr", 0.35))
        min_rr = float(profile.get("min_rr", 1.0))
        asset_type_lower = str(res.get("asset_type") or "").lower()
        crypto_profile_enabled = (
            asset_type_lower == "crypto"
            and bool(config.CONFIG.get("ENGINE_B_CRYPTO_PROFILE_ENABLED", False))
        )
        crypto_trigger_profile_enabled = (
            crypto_profile_enabled
            and bool(config.CONFIG.get("ENGINE_B_CRYPTO_TRIGGER_PROFILE_ENABLED", False))
        )
        crypto_target_v2_enabled = bool(
            config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_V2_ENABLED", False)
        )
        if crypto_profile_enabled:
            min_rr = float(
                config.CONFIG.get(
                    "ENGINE_B_CRYPTO_MIN_RR",
                    config.CONFIG.get("ENGINE_B_CRYPTO_TARGET_MIN_RR", min_rr),
                )
                or min_rr
            )
        _rma_cfg = profile.get("require_macro_align", False)
        if isinstance(_rma_cfg, dict):
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
                    _forex_adx_gate = True  # no ADX data available — don't block

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
        location_mode = "normal" if location_ok else "none"
        location_distance_atr = None
        if res.get("active_zone_distance") is not None:
            try:
                location_distance_atr = abs(float(res.get("active_zone_distance"))) / atr_val
            except (TypeError, ValueError):
                location_distance_atr = None
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

        stop_valid = False
        if sl:
            stop_valid = (direction == "LONG" and sl < current_price) or (
                direction == "SHORT" and sl > current_price
            )

        selected_target_price = res.get("crypto_target_selected_target_price")
        selected_target_tf = res.get("crypto_target_selected_target_tf")
        selected_target_type = res.get("crypto_target_selected_target_type")
        selected_target_rr = res.get("crypto_target_selected_target_rr")
        selected_target_is_structural = bool(
            res.get("crypto_target_selected_target_is_structural")
        )
        fallback_projection_price = res.get("crypto_target_fallback_projection_price")
        fallback_used_for_final_pass = bool(
            res.get("crypto_target_used_fallback_projection")
            and not res.get("crypto_target_fallback_used_for_diagnostics_only")
        )
        structural_target_required = bool(
            config.CONFIG.get("ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS", True)
        )
        selected_target_tf_ok = str(selected_target_tf or "").upper() in {"H4", "D1"}
        target_v2_valid = (
            crypto_target_v2_enabled
            and selected_target_price is not None
            and selected_target_tf_ok
            and selected_target_is_structural
            and not fallback_used_for_final_pass
        )
        path_block_reason = res.get("crypto_target_path_block_reason")
        path_clear_value = res.get("crypto_target_path_clear_to_tp2")
        path_clear_to_target = path_clear_value is not False and not path_block_reason

        crypto_trigger_state = {
            "trigger_passed": False,
            "trigger_timeframe": None,
            "trigger_type": None,
            "trigger_volume_ratio": None,
            "trigger_taker_buy_ratio": None,
            "trigger_taker_sell_ratio": None,
            "trigger_displacement_atr": None,
            "taker_data_missing": True,
            "trigger_reject_reason": "crypto_trigger_profile_disabled",
            "m15_trigger_state": None,
            "m5_trigger_state": None,
        }
        if crypto_profile_enabled and crypto_trigger_profile_enabled:
            crypto_trigger_state = self._crypto_trigger_profile(
                crypto_entry_candles_by_tf or {},
                direction,
                atr_val,
                res,
            )

        if crypto_profile_enabled:
            location_buffer = float(
                config.CONFIG.get("ENGINE_B_CRYPTO_LOCATION_ATR_BUFFER", 0.75) or 0.75
            )
            location_sources = []
            if location_distance_atr is not None:
                location_sources.append("zone_or_retest_area")
            if bool(res.get("breaker_block")):
                location_sources.append("breaker_block")
            if bool(res.get("ob_at_zone")):
                location_sources.append("order_block")
            if bool(res.get("fvg_overlap")):
                location_sources.append("fvg_edge")
            for distance_key, source_name in (
                ("vwap_distance", "vwap"),
                ("volume_level_distance", "volume_level"),
                ("retest_area_distance", "retest_area"),
            ):
                distance_value = res.get(distance_key)
                if distance_value is None:
                    continue
                try:
                    distance_atr = abs(float(distance_value)) / atr_val
                except (TypeError, ValueError):
                    continue
                if location_distance_atr is None or distance_atr < location_distance_atr:
                    location_distance_atr = distance_atr
                location_sources.append(source_name)
            trend_continuation_location_ok = (
                structure_ok
                and target_v2_valid
                and rr_ok
                and location_distance_atr is not None
                and location_distance_atr <= location_buffer
                and bool(location_sources)
            )
            if location_ok:
                location_mode = "normal"
            elif trend_continuation_location_ok:
                location_ok = True
                location_mode = "trend_continuation"
            else:
                location_mode = "none"

        # Entry requires a candle pattern trigger OR a confirmed structural breakout
        # OR a professional technical catalyst (Sweep/CHoCH) at a key zone.
        entry_ok = (
            trigger_ok
            or (breakout_ok and bool(res.get("bos_volume_confirmed", False)))
            or (bool(res.get("liquidity_sweep")) and zone_ok)
            or (bool(res.get("choch_confirmed")) and zone_ok)
        )
        if crypto_profile_enabled:
            trigger_ok = bool(crypto_trigger_state.get("trigger_passed"))
            entry_ok = crypto_trigger_profile_enabled and trigger_ok
        space_ok = room_ok or rr_ok

        gate_confirmations = [structure_ok, location_ok, entry_ok, room_ok, rr_ok]
        if require_macro_align:
            gate_confirmations.append(macro_ok)
        confirmations = list(gate_confirmations)
        bonus_points = 0.0
        # Bonus confirmations — these add to the score but don't block if missing
        if bos_mtf:
            bonus_points += 1.0  # MTF BOS alignment = extra point
        if ob_at_zone:
            bonus_points += 1.0  # OB at zone = extra point

        gate_score = float(sum(1 for passed in gate_confirmations if passed))
        gate_max_possible = float(len(gate_confirmations)) if gate_confirmations else 1.0
        total_score = gate_score + bonus_points
        max_possible = gate_max_possible + bonus_points
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
                bonus_points += _profile_points
                total_score += _profile_points

        # D1 PD-array conflict penalty (B-1).
        # When an unmitigated D1 OB or FVG opposes the trade direction within 3×ATR,
        # deduct from total_score. Default penalty reduced from 0.5 to 0.25 so that
        # signals at the exact min_score gate (e.g. 4/5 = 80%) drop to 3.75 and
        # may still pass if structural surplus exists. The penalty acts through the
        # numeric score gate, not the checklist flags.
        _d1_conflict = bool(res.get("d1_pd_array_conflict", False))
        _d1_penalty = float(
            config.CONFIG.get("NAKED_ENGINE", {}).get("d1_pd_array_penalty", 0.25)
        )
        _d1_penalty = _d1_penalty if _d1_conflict else 0.0
        total_score = max(0.0, total_score - _d1_penalty)
        gate_score = max(0.0, gate_score - _d1_penalty)

        pct = min(100, int((total_score / max_possible) * 100))
        if checklist_mode == "strict":
            passed = structure_ok and zone_ok and trigger_ok and room_ok and rr_ok and macro_ok
        else:
            # Flexible but not free — require BOTH location AND a trigger/catalyst.
            # BOS alone is not enough. You need: structure + (zone OR breakout) + trigger + room/rr.
            # This prevents BOS from single-handedly passing 3 gates.
            # macro_ok is only a hard gate when require_macro_align=True (mirrors confirmations list).
            passed = (
                structure_ok
                and location_ok
                and entry_ok
                and rr_ok
                and (macro_ok if require_macro_align else True)
            )

        failed_gate_names = []
        if not structure_ok:
            failed_gate_names.append("struct")
        if not location_ok:
            failed_gate_names.append("loc")
        if not entry_ok:
            failed_gate_names.append("trigger")
        if not rr_ok:
            failed_gate_names.append(f"rr={rr:.1f}")

        structure_verdict_clear = str(res.get("structural_verdict") or "CLEAR").upper() == "CLEAR"
        if crypto_profile_enabled:
            target_gate_ok = target_v2_valid
            fallback_gate_ok = not fallback_used_for_final_pass
            stop_gate_ok = stop_valid
            path_gate_ok = path_clear_to_target
            crypto_gates = {
                "crypto_profile": True,
                "struct": bool(structure_ok and structure_verdict_clear),
                "target_v2": bool(target_gate_ok),
                "fallback_not_used": bool(fallback_gate_ok),
                "rr": bool(rr_ok),
                "loc": bool(location_ok),
                "trigger": bool(entry_ok),
                "stop": bool(stop_gate_ok),
                "path": bool(path_gate_ok),
            }
            if not crypto_gates["target_v2"] and "target_v2" not in failed_gate_names:
                failed_gate_names.append("target_v2")
            if not crypto_gates["fallback_not_used"] and "fallback_target" not in failed_gate_names:
                failed_gate_names.append("fallback_target")
            if not crypto_gates["stop"] and "stop" not in failed_gate_names:
                failed_gate_names.append("stop")
            if not crypto_gates["path"] and "path" not in failed_gate_names:
                failed_gate_names.append("path")
            if not crypto_trigger_profile_enabled and "trigger_profile_disabled" not in failed_gate_names:
                failed_gate_names.append("trigger_profile_disabled")
            if not structure_verdict_clear and "structural_verdict" not in failed_gate_names:
                failed_gate_names.append("structural_verdict")
            passed = all(crypto_gates.values())

        lifecycle_state, lifecycle_reason = self._determine_lifecycle_state(
            res, current_price, direction, trigger_ok
        )

        if hard_counter:
            _diag_codes.append(ENGINE_B_REASON_SEQUENCE_COUNTER_TREND)
        if not trigger_ok and not breakout_ok:
            _diag_codes.append(ENGINE_B_REASON_NO_TRIGGER_PATTERN)
        elif breakout_ok and not res.get("bos_volume_confirmed", True):
            _diag_codes.append(ENGINE_B_REASON_BOS_WITHOUT_VOLUME)
        if _d1_conflict:
            _diag_codes.append(ENGINE_B_REASON_D1_PD_ARRAY_CONFLICT)
        if not room_ok:
            if direction == "LONG":
                _diag_codes.append(ENGINE_B_REASON_RESISTANCE_TOO_CLOSE)
            elif direction == "SHORT":
                _diag_codes.append(ENGINE_B_REASON_SUPPORT_TOO_CLOSE)
        if sl and tp and not tp_side_ok:
            _diag_codes.append(ENGINE_B_REASON_TP_WRONG_SIDE)
        if res.get("tp_structural_limited"):
            _diag_codes.append(ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE)

        return {
            "score": total_score,
            "gate_score": gate_score,
            "pct": pct,
            "max_possible": round(max_possible, 2),
            "gate_max_possible": round(gate_max_possible, 2),
            "bonus_points": round(bonus_points, 2),
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
            "trigger_pattern": crypto_trigger_state.get("trigger_type")
            if crypto_profile_enabled
            else res.get("trigger_pattern", "NONE"),
            "ob_at_zone": ob_at_zone,
            "bos_mtf_confirmed": bos_mtf,
            "breaker_active": bool(res.get("breaker_block")),
            "profile_points": round(_profile_points, 2),
            "profile_ok": _profile_ok,
            "profile_alignment": _profile_alignment,
            "profile_context": _profile_context,
            "lifecycle_state": lifecycle_state,
            "lifecycle_reason": lifecycle_reason,
            "d1_pd_conflict_penalty": round(_d1_penalty, 2),
            "d1_conflict_details": res.get("d1_conflict_details", []),
            "target_v2_enabled": crypto_target_v2_enabled,
            "selected_target_price": selected_target_price,
            "selected_target_tf": selected_target_tf,
            "selected_target_type": selected_target_type,
            "selected_target_rr": selected_target_rr,
            "selected_target_is_structural": selected_target_is_structural,
            "fallback_projection_price": fallback_projection_price,
            "fallback_used_for_final_pass": fallback_used_for_final_pass,
            "location_passed": location_ok,
            "location_mode": location_mode,
            "location_distance_atr": round(location_distance_atr, 4)
            if location_distance_atr is not None
            else None,
            "trigger_passed": trigger_ok,
            "trigger_timeframe": crypto_trigger_state.get("trigger_timeframe")
            if crypto_profile_enabled
            else res.get("trigger_timeframe"),
            "trigger_type": crypto_trigger_state.get("trigger_type")
            if crypto_profile_enabled
            else res.get("trigger_pattern", "NONE"),
            "trigger_volume_ratio": crypto_trigger_state.get("trigger_volume_ratio"),
            "trigger_taker_buy_ratio": crypto_trigger_state.get("trigger_taker_buy_ratio"),
            "trigger_taker_sell_ratio": crypto_trigger_state.get("trigger_taker_sell_ratio"),
            "trigger_displacement_atr": crypto_trigger_state.get("trigger_displacement_atr"),
            "taker_data_missing": crypto_trigger_state.get("taker_data_missing"),
            "m15_trigger_state": crypto_trigger_state.get("m15_trigger_state"),
            "m5_trigger_state": crypto_trigger_state.get("m5_trigger_state"),
            "exact_trigger_reject_reason": crypto_trigger_state.get("trigger_reject_reason")
            if crypto_profile_enabled
            else None,
            "failed_gate_names": failed_gate_names,
            "final_engine_b_passed": passed,
            "crypto_profile_enabled": crypto_profile_enabled,
            "crypto_trigger_profile_enabled": crypto_trigger_profile_enabled,
            "crypto_target_v2_valid": target_v2_valid,
            "crypto_stop_valid": stop_valid,
            "crypto_path_clear_to_target": path_clear_to_target,
            "engine_b_diagnostics": {"reason_codes": _diag_codes},
        }

    def check_macro_correlation_detail(
        self, asset_close_series: list, dxy_close_series: list, direction: str
    ) -> tuple[bool, str | None]:
        """Same gate as check_macro_correlation; returns optional block reason code when False."""
        if len(asset_close_series) < 60 or len(dxy_close_series) < 60:
            return True, None

        min_len = min(len(asset_close_series), len(dxy_close_series), 60)
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
