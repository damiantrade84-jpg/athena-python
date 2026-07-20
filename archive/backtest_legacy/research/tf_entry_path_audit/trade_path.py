"""Extended trade-path diagnostics for TF/entry audit (Parts 4–6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class IntrabarExitClass(str, Enum):
    SL_ONLY = "SL_ONLY"
    TP_ONLY = "TP_ONLY"
    BOTH_TOUCHED_SAME_CANDLE = "BOTH_TOUCHED_SAME_CANDLE"
    NEITHER_TOUCHED = "NEITHER_TOUCHED"
    EXIT_BY_TIME = "EXIT_BY_TIME"
    EXIT_BY_SIGNAL = "EXIT_BY_SIGNAL"


class EntryQualityClass(str, Enum):
    CLEAN_ENTRY = "CLEAN_ENTRY"
    EARLY_ADVERSE = "EARLY_ADVERSE"
    LATE_ENTRY = "LATE_ENTRY"
    WRONG_DIRECTION = "WRONG_DIRECTION"
    TP_TOO_FAR = "TP_TOO_FAR"
    EXIT_FAILURE = "EXIT_FAILURE"
    STATIC_SL_FAILURE = "STATIC_SL_FAILURE"
    STRUCTURE_FAIL = "STRUCTURE_FAIL"
    AMBIGUOUS_INTRABAR = "AMBIGUOUS_INTRABAR"
    UNCLASSIFIED = "UNCLASSIFIED"


_R_THRESHOLDS = (0.25, 0.5, 1.0)
_NEGATIVE_R_THRESHOLDS = (-0.25, -0.5)


def _bar_touch_levels(
    bar: dict,
    *,
    direction: str,
    entry: float,
    sl: float,
    tp: float | None,
) -> tuple[bool, bool]:
    high = float(bar["high"])
    low = float(bar["low"])
    if direction == "LONG":
        sl_hit = low <= sl
        tp_hit = tp is not None and high >= tp
    else:
        sl_hit = high >= sl
        tp_hit = tp is not None and low <= tp
    return sl_hit, tp_hit


def classify_intrabar_exit(
    future_window: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    tp: float | None,
    outcome: str,
    same_bar_policy: str = "ADVERSE_SL_FIRST",
) -> tuple[IntrabarExitClass, bool]:
    """Classify how a trade exited relative to SL/TP touches."""
    outcome_u = str(outcome or "").upper()
    if outcome_u in ("TIMEOUT", "TP1_TIMEOUT", "TIMEOUT_PARTIAL"):
        return IntrabarExitClass.EXIT_BY_TIME, False
    if outcome_u in ("SIGNAL", "MANUAL", "FORCED"):
        return IntrabarExitClass.EXIT_BY_SIGNAL, False

    touched_sl = False
    touched_tp = False
    ambiguous = False
    for bar in future_window or []:
        sl_hit, tp_hit = _bar_touch_levels(
            bar, direction=direction, entry=entry, sl=sl, tp=tp
        )
        if sl_hit and tp_hit:
            ambiguous = True
            if same_bar_policy.upper() in ("ADVERSE_SL_FIRST", "PESSIMISTIC_SL"):
                return IntrabarExitClass.BOTH_TOUCHED_SAME_CANDLE, True
            return IntrabarExitClass.BOTH_TOUCHED_SAME_CANDLE, True
        if sl_hit:
            touched_sl = True
        if tp_hit:
            touched_tp = True

    if outcome_u in ("SL", "BE", "TP1_THEN_SL") or touched_sl:
        if touched_tp and not ambiguous:
            return IntrabarExitClass.BOTH_TOUCHED_SAME_CANDLE, True
        return IntrabarExitClass.SL_ONLY, ambiguous
    if outcome_u in ("TP1", "TP2", "TP1_TP2", "TP1_THEN_TP2") or touched_tp:
        return IntrabarExitClass.TP_ONLY, ambiguous
    return IntrabarExitClass.NEITHER_TOUCHED, ambiguous


def _first_bar_at_r(
    future_window: list[dict],
    *,
    direction: str,
    entry: float,
    risk: float,
    threshold_r: float,
    favorable: bool,
) -> int | None:
    if risk <= 0:
        return None
    for offset, bar in enumerate(future_window or [], start=1):
        high = float(bar["high"])
        low = float(bar["low"])
        if direction == "LONG":
            r_high = (high - entry) / risk
            r_low = (low - entry) / risk
        else:
            r_high = (entry - low) / risk
            r_low = (entry - high) / risk
        if favorable and r_high >= threshold_r:
            return offset
        if not favorable and r_low <= threshold_r:
            return offset
    return None


def compute_trade_path(
    future_window: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    tp: float | None,
    tp1: float | None = None,
    outcome: str = "",
    spread_pct_of_sl: float | None = None,
    spread_pct_of_tp: float | None = None,
    atr_at_entry: float | None = None,
    structure_distance: float | None = None,
) -> dict[str, Any]:
    """Compute full trade-path diagnostics on an execution-TF candle window."""
    risk = abs(float(entry) - float(sl))
    empty: dict[str, Any] = {
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "mfe_before_mae": None,
        "mae_before_mfe": None,
        "bars_to_first_plus_0_25r": None,
        "bars_to_first_plus_0_5r": None,
        "bars_to_first_plus_1_0r": None,
        "bars_to_first_minus_0_25r": None,
        "bars_to_first_minus_0_5r": None,
        "bars_to_sl": None,
        "bars_to_tp": None,
        "time_in_trade_bars": len(future_window or []),
        "max_unrealized_profit_before_loss": 0.0,
        "max_unrealized_loss_before_profit": 0.0,
        "touched_break_even": False,
        "touched_plus_0_25r": False,
        "touched_plus_0_5r": False,
        "touched_plus_1_0r": False,
        "touched_tp": False,
        "touched_sl": False,
        "touched_both_same_candle": False,
        "entry_to_stop_distance": risk,
        "entry_to_target_distance": abs(float(tp) - entry) if tp is not None else None,
        "entry_to_nearest_structure_distance": structure_distance,
        "atr_at_entry": atr_at_entry,
        "sl_distance_atr": (risk / atr_at_entry) if atr_at_entry and atr_at_entry > 0 else None,
        "tp_distance_atr": (
            abs(float(tp) - entry) / atr_at_entry if tp is not None and atr_at_entry and atr_at_entry > 0 else None
        ),
        "spread_pct_of_sl_distance": spread_pct_of_sl,
        "spread_pct_of_tp_distance": spread_pct_of_tp,
    }
    if risk <= 0 or not future_window:
        return empty

    direction = str(direction or "").upper()
    mfe_r = 0.0
    mae_r = 0.0
    mfe_bar: int | None = None
    mae_bar: int | None = None
    touched_be = False
    touched_025 = False
    touched_05 = False
    touched_1 = False
    touched_tp = False
    touched_sl = False
    touched_both = False
    bars_to_sl: int | None = None
    bars_to_tp: int | None = None
    running_mfe = 0.0
    max_profit_before_loss = 0.0
    max_loss_before_profit = 0.0
    final_r_sign: float | None = None

    tp_ref = tp if tp is not None else tp1

    for offset, bar in enumerate(future_window, start=1):
        high = float(bar["high"])
        low = float(bar["low"])
        if direction == "LONG":
            fav = (high - entry) / risk
            adv = (low - entry) / risk
            sl_hit = low <= sl
            tp_hit = tp_ref is not None and high >= tp_ref
            be_hit = low <= entry <= high or (offset == 1 and entry >= low and entry <= high)
        else:
            fav = (entry - low) / risk
            adv = (entry - high) / risk
            sl_hit = high >= sl
            tp_hit = tp_ref is not None and low <= tp_ref
            be_hit = low <= entry <= high

        if sl_hit and tp_hit:
            touched_both = True
        if sl_hit and bars_to_sl is None:
            bars_to_sl = offset
            touched_sl = True
        if tp_hit and bars_to_tp is None:
            bars_to_tp = offset
            touched_tp = True

        if fav > mfe_r:
            mfe_r = fav
            mfe_bar = offset
        if adv < mae_r:
            mae_r = adv
            mae_bar = offset

        touched_be = touched_be or be_hit or fav >= 0 or adv >= 0
        touched_025 = touched_025 or fav >= 0.25
        touched_05 = touched_05 or fav >= 0.5
        touched_1 = touched_1 or fav >= 1.0

        if final_r_sign is None:
            if sl_hit:
                final_r_sign = -1.0
                max_profit_before_loss = max(max_profit_before_loss, running_mfe)
            elif tp_hit:
                final_r_sign = 1.0
                max_loss_before_profit = min(max_loss_before_profit, mae_r)
        running_mfe = max(running_mfe, fav)

    mfe_before_mae: bool | None = None
    mae_before_mfe: bool | None = None
    if mfe_bar is not None and mae_bar is not None:
        mfe_before_mae = mfe_bar < mae_bar
        mae_before_mfe = mae_bar < mfe_bar

    return {
        "mfe_r": round(mfe_r, 4),
        "mae_r": round(mae_r, 4),
        "mfe_before_mae": mfe_before_mae,
        "mae_before_mfe": mae_before_mfe,
        "bars_to_first_plus_0_25r": _first_bar_at_r(
            future_window, direction=direction, entry=entry, risk=risk, threshold_r=0.25, favorable=True
        ),
        "bars_to_first_plus_0_5r": _first_bar_at_r(
            future_window, direction=direction, entry=entry, risk=risk, threshold_r=0.5, favorable=True
        ),
        "bars_to_first_plus_1_0r": _first_bar_at_r(
            future_window, direction=direction, entry=entry, risk=risk, threshold_r=1.0, favorable=True
        ),
        "bars_to_first_minus_0_25r": _first_bar_at_r(
            future_window, direction=direction, entry=entry, risk=risk, threshold_r=-0.25, favorable=False
        ),
        "bars_to_first_minus_0_5r": _first_bar_at_r(
            future_window, direction=direction, entry=entry, risk=risk, threshold_r=-0.5, favorable=False
        ),
        "bars_to_sl": bars_to_sl,
        "bars_to_tp": bars_to_tp,
        "time_in_trade_bars": len(future_window),
        "max_unrealized_profit_before_loss": round(max_profit_before_loss, 4),
        "max_unrealized_loss_before_profit": round(max_loss_before_profit, 4),
        "touched_break_even": touched_be,
        "touched_plus_0_25r": touched_025,
        "touched_plus_0_5r": touched_05,
        "touched_plus_1_0r": touched_1,
        "touched_tp": touched_tp,
        "touched_sl": touched_sl,
        "touched_both_same_candle": touched_both,
        "entry_to_stop_distance": round(risk, 8),
        "entry_to_target_distance": round(abs(float(tp_ref) - entry), 8) if tp_ref is not None else None,
        "entry_to_nearest_structure_distance": structure_distance,
        "atr_at_entry": atr_at_entry,
        "sl_distance_atr": round(risk / atr_at_entry, 4) if atr_at_entry and atr_at_entry > 0 else None,
        "tp_distance_atr": (
            round(abs(float(tp_ref) - entry) / atr_at_entry, 4)
            if tp_ref is not None and atr_at_entry and atr_at_entry > 0
            else None
        ),
        "spread_pct_of_sl_distance": spread_pct_of_sl,
        "spread_pct_of_tp_distance": spread_pct_of_tp,
    }


def classify_entry_quality(
    path: dict[str, Any],
    *,
    final_r: float,
    outcome: str,
    intrabar_class: IntrabarExitClass,
    sl_distance_atr: float | None = None,
) -> EntryQualityClass:
    """Classify trade entry quality from path metrics."""
    if intrabar_class == IntrabarExitClass.BOTH_TOUCHED_SAME_CANDLE:
        return EntryQualityClass.AMBIGUOUS_INTRABAR

    mfe = float(path.get("mfe_r") or 0)
    mae = abs(float(path.get("mae_r") or 0))
    bars_plus_05 = path.get("bars_to_first_plus_0_5r")
    bars_minus_025 = path.get("bars_to_first_minus_0_25r")
    bars_minus_05 = path.get("bars_to_first_minus_0_5r")
    touched_1 = bool(path.get("touched_plus_1_0r"))
    touched_tp = bool(path.get("touched_tp"))

    if bars_plus_05 is not None and bars_minus_025 is not None and bars_plus_05 < bars_minus_025:
        if final_r > 0:
            return EntryQualityClass.CLEAN_ENTRY
    if bars_minus_05 is not None:
        if bars_plus_05 is None or (bars_minus_05 is not None and bars_minus_05 < (bars_plus_05 or 999)):
            if final_r <= 0:
                return EntryQualityClass.EARLY_ADVERSE

    if touched_1 and final_r < 0:
        return EntryQualityClass.EXIT_FAILURE

    if mfe >= 0.5 and not touched_tp and final_r <= 0:
        return EntryQualityClass.TP_TOO_FAR

    if mfe >= 0.25 and mfe < 1.0 and final_r <= 0 and mae >= 0.5:
        return EntryQualityClass.LATE_ENTRY

    if mfe < 0.25 and mae >= 0.5:
        return EntryQualityClass.WRONG_DIRECTION

    if sl_distance_atr is not None and sl_distance_atr >= 2.0 and final_r <= 0 and mae >= 0.5:
        return EntryQualityClass.STATIC_SL_FAILURE

    outcome_u = str(outcome or "").upper()
    if outcome_u == "SL" and mfe < 0.15 and mae >= 0.5:
        return EntryQualityClass.STRUCTURE_FAIL

    if mfe >= 0.5 and (bars_minus_025 is None or (bars_plus_05 is not None and bars_plus_05 <= bars_minus_025)):
        return EntryQualityClass.CLEAN_ENTRY

    # Fallback when bar-timing unavailable but harness MFE/MAE present
    if bars_plus_05 is None and bars_minus_025 is None:
        if mfe >= 0.5 and mae < 0.25:
            return EntryQualityClass.CLEAN_ENTRY
        if mae >= 0.5 and mfe < 0.25:
            return EntryQualityClass.EARLY_ADVERSE
        if mfe >= 0.5 and not touched_tp and final_r <= 0:
            return EntryQualityClass.TP_TOO_FAR
        if mfe >= 1.0 and final_r < 0:
            return EntryQualityClass.EXIT_FAILURE
        if mfe < 0.25 and mae >= 0.5:
            return EntryQualityClass.WRONG_DIRECTION

    return EntryQualityClass.UNCLASSIFIED


def enrich_trade_record(
    trade: dict[str, Any],
    *,
    engine: str,
    symbol: str,
    signal_tf: str,
    entry_tf: str,
    exit_tf: str,
    execution_tf: str,
    indicator_profile: str,
    future_window: list[dict] | None = None,
    same_bar_policy: str = "ADVERSE_SL_FIRST",
    cost_adjusted_r: float | None = None,
) -> dict[str, Any]:
    """Merge base trade with extended diagnostic fields."""
    direction = str(trade.get("direction") or "")
    entry = float(trade.get("entry") or 0)
    sl = float(trade.get("sl") or 0)
    tp = trade.get("tp") or trade.get("tp2") or trade.get("tp1")
    tp_f = float(tp) if tp is not None else None
    tp1 = trade.get("tp1")
    tp1_f = float(tp1) if tp1 is not None else None
    outcome = str(trade.get("outcome") or "")
    final_r = float(trade.get("resultR") or trade.get("r_multiple") or 0)

    path = compute_trade_path(
        future_window or [],
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp_f,
        tp1=tp1_f,
        outcome=outcome,
        atr_at_entry=trade.get("atr_at_entry"),
        structure_distance=trade.get("structure_distance"),
    )
    # Merge harness-provided MFE/MAE when execution window unavailable
    if future_window is None:
        if trade.get("max_favorable_excursion_r") is not None:
            path["mfe_r"] = float(trade["max_favorable_excursion_r"])
        if trade.get("max_adverse_excursion_r") is not None:
            path["mae_r"] = float(trade["max_adverse_excursion_r"])

    intrabar_class, ambiguous = classify_intrabar_exit(
        future_window or [],
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp_f,
        outcome=outcome,
        same_bar_policy=same_bar_policy,
    )

    entry_class = classify_entry_quality(
        path,
        final_r=final_r,
        outcome=outcome,
        intrabar_class=intrabar_class,
        sl_distance_atr=path.get("sl_distance_atr"),
    )

    record = {
        "symbol": symbol,
        "engine": engine,
        "direction": direction,
        "signal_timeframe": signal_tf,
        "entry_timeframe": entry_tf,
        "exit_timeframe": exit_tf,
        "execution_timeframe": execution_tf,
        "indicator_profile": indicator_profile,
        "entry_timestamp": trade.get("entryTime") or trade.get("entry_time") or trade.get("date"),
        "exit_timestamp": trade.get("exitTime") or trade.get("exit_time"),
        "entry_price": entry,
        "exit_price": trade.get("exit_price") or trade.get("exit"),
        "sl_price": sl,
        "tp_price": tp_f,
        "final_r": final_r,
        "cost_adjusted_r": cost_adjusted_r if cost_adjusted_r is not None else final_r,
        "outcome": outcome,
        "intrabar_exit_class": intrabar_class.value,
        "intrabar_ambiguous": ambiguous,
        "entry_quality_class": entry_class.value,
        **{f"path_{k}": v for k, v in path.items()},
    }
    return record
