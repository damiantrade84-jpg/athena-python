"""Hard gates for the athena_fx factor lane (Phase 8).

Volatility and cost are never score components — no score can override a gate failure.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

from config import CONFIG

from athena_fx.models import (
    GATE_FAIL,
    GATE_PASS,
    GATE_REDUCED,
    GateResult,
)

logger = logging.getLogger("athena_fx.gates")

_TRADING_DAYS = 252
_MIN_OBS = _TRADING_DAYS + 20


def _rolling_vol_20d(returns: list[float], end_exclusive: int) -> float | None:
    """Annualized 20-day realized vol ending at index ``end_exclusive - 1``."""
    start = end_exclusive - 20
    if start < 0 or end_exclusive > len(returns):
        return None
    window = returns[start:end_exclusive]
    if len(window) < 20:
        return None
    return statistics.stdev(window) * (_TRADING_DAYS ** 0.5)


def realized_vol_metrics(daily_returns: list[float]) -> dict[str, float] | None:
    """Compute realized-vol metrics; ``None`` when history is insufficient."""
    n = len(daily_returns)
    if n < _MIN_OBS:
        return None

    vol_20d = _rolling_vol_20d(daily_returns, n)
    vol_60d = statistics.stdev(daily_returns[-60:]) * (_TRADING_DAYS ** 0.5)
    if vol_20d is None or vol_20d <= 0:
        return None

    rolling_vols: list[float] = []
    for end in range(n - _TRADING_DAYS + 1, n + 1):
        v = _rolling_vol_20d(daily_returns, end)
        if v is not None:
            rolling_vols.append(v)
    if len(rolling_vols) < 2:
        return None

    vol_252d_baseline = statistics.mean(rolling_vols)
    roll_stdev = statistics.stdev(rolling_vols)
    vol_z = (vol_20d - vol_252d_baseline) / roll_stdev if roll_stdev > 0 else 0.0

    vol_20d_5_ago = _rolling_vol_20d(daily_returns, n - 5)
    if vol_20d_5_ago is None or vol_20d_5_ago <= 0:
        return None
    vol_jump = vol_20d / vol_20d_5_ago

    return {
        "vol_20d": vol_20d,
        "vol_60d": vol_60d,
        "vol_252d_baseline": vol_252d_baseline,
        "vol_z": vol_z,
        "vol_jump": vol_jump,
    }


def _vol_thresholds() -> dict[str, float | int]:
    return {
        "reduce_z": float(CONFIG.get("FX_FACTOR_VOL_REDUCE_Z", 1.0) or 1.0),
        "block_z": float(CONFIG.get("FX_FACTOR_VOL_BLOCK_Z", 2.0) or 2.0),
        "flatten_z": float(CONFIG.get("FX_FACTOR_VOL_FLATTEN_Z", 3.0) or 3.0),
        "jump_flatten": float(CONFIG.get("FX_FACTOR_VOL_JUMP_FLATTEN", 2.0) or 2.0),
        "resume_z": float(CONFIG.get("FX_FACTOR_VOL_RESUME_Z", 0.5) or 0.5),
        "resume_closes": int(CONFIG.get("FX_FACTOR_VOL_RESUME_CLOSES", 3) or 3),
    }


def _empty_hysteresis() -> dict[str, Any]:
    return {"blocked": False, "calm_closes": 0}


def volatility_gate(
    daily_returns: list[float],
    *,
    has_existing_position: bool = False,
    hysteresis_state: dict | None = None,
) -> tuple[GateResult, dict]:
    """Evaluate volatility hard gate; returns ``(GateResult, new_hysteresis_state)``."""
    thresholds = _vol_thresholds()
    state = dict(hysteresis_state) if hysteresis_state else _empty_hysteresis()
    blocked = bool(state.get("blocked", False))
    calm_closes = int(state.get("calm_closes", 0))

    metrics = realized_vol_metrics(daily_returns)
    if metrics is None:
        new_state = _empty_hysteresis()
        return (
            GateResult(
                gate_name="volatility",
                result=GATE_FAIL,
                reason_code="insufficient_vol_history",
                numeric_inputs={"observations": len(daily_returns), **thresholds},
                recommended_action="block",
                size_multiplier=0.0,
            ),
            new_state,
        )

    numeric = {**metrics, **thresholds}

    if metrics["vol_z"] < thresholds["resume_z"]:
        calm_closes += 1
    else:
        calm_closes = 0

    if blocked and calm_closes < thresholds["resume_closes"]:
        new_state = {"blocked": True, "calm_closes": calm_closes}
        if has_existing_position:
            return (
                GateResult(
                    gate_name="volatility",
                    result=GATE_REDUCED,
                    reason_code="hysteresis_hold",
                    numeric_inputs=numeric,
                    recommended_action="reduce",
                    size_multiplier=0.5,
                ),
                new_state,
            )
        return (
            GateResult(
                gate_name="volatility",
                result=GATE_FAIL,
                reason_code="hysteresis_hold",
                numeric_inputs=numeric,
                recommended_action="block",
                size_multiplier=0.0,
            ),
            new_state,
        )

    vol_z = metrics["vol_z"]
    vol_jump = metrics["vol_jump"]
    reduce_z = thresholds["reduce_z"]
    block_z = thresholds["block_z"]
    flatten_z = thresholds["flatten_z"]
    jump_flatten = thresholds["jump_flatten"]

    if has_existing_position:
        if vol_z > flatten_z or vol_jump >= jump_flatten:
            new_state = {"blocked": True, "calm_closes": 0}
            return (
                GateResult(
                    gate_name="volatility",
                    result=GATE_FAIL,
                    reason_code="vol_flatten",
                    numeric_inputs=numeric,
                    recommended_action="flatten",
                    size_multiplier=0.0,
                ),
                new_state,
            )
        if vol_z > block_z:
            new_state = {"blocked": True, "calm_closes": 0}
            return (
                GateResult(
                    gate_name="volatility",
                    result=GATE_REDUCED,
                    reason_code="vol_reduce_existing",
                    numeric_inputs=numeric,
                    recommended_action="reduce",
                    size_multiplier=0.5,
                ),
                new_state,
            )
        new_state = {"blocked": False, "calm_closes": calm_closes}
        return (
            GateResult(
                gate_name="volatility",
                result=GATE_PASS,
                reason_code="vol_ok",
                numeric_inputs=numeric,
                recommended_action="allow",
                size_multiplier=1.0,
            ),
            new_state,
        )

    # New entries
    if vol_z > block_z:
        new_state = {"blocked": True, "calm_closes": 0}
        return (
            GateResult(
                gate_name="volatility",
                result=GATE_FAIL,
                reason_code="vol_block",
                numeric_inputs=numeric,
                recommended_action="block",
                size_multiplier=0.0,
            ),
            new_state,
        )
    if vol_z > reduce_z:
        new_state = {"blocked": True, "calm_closes": 0}
        return (
            GateResult(
                gate_name="volatility",
                result=GATE_REDUCED,
                reason_code="vol_reduce",
                numeric_inputs=numeric,
                recommended_action="reduce",
                size_multiplier=0.5,
            ),
            new_state,
        )

    new_state = {"blocked": False, "calm_closes": calm_closes}
    return (
        GateResult(
            gate_name="volatility",
            result=GATE_PASS,
            reason_code="vol_ok",
            numeric_inputs=numeric,
            recommended_action="allow",
            size_multiplier=1.0,
        ),
        new_state,
    )


def cost_gate(
    *,
    spread_pips: float | None,
    atr_pips: float | None,
    slippage_pips: float | None = None,
    commission_pips: float | None = None,
    directional_swap_annual: float | None = None,
    rollover_window: bool = False,
    news_window: bool = False,
) -> GateResult:
    """Evaluate execution-cost hard gate (fail-closed on missing inputs)."""
    slip = (
        float(slippage_pips)
        if slippage_pips is not None
        else float(CONFIG.get("FX_FACTOR_SLIPPAGE_PIPS", 0.3) or 0.3)
    )
    max_frac = float(CONFIG.get("FX_FACTOR_COST_MAX_ATR_FRACTION", 0.10) or 0.10)

    numeric: dict[str, Any] = {
        "slippage_pips": slip,
        "max_atr_fraction": max_frac,
    }
    if directional_swap_annual is not None:
        numeric["directional_swap_annual"] = directional_swap_annual
    if commission_pips is not None:
        numeric["commission_pips"] = commission_pips

    if rollover_window:
        return GateResult(
            gate_name="cost",
            result=GATE_FAIL,
            reason_code="rollover_window",
            numeric_inputs=numeric,
            recommended_action="block",
            size_multiplier=0.0,
        )
    if news_window:
        return GateResult(
            gate_name="cost",
            result=GATE_FAIL,
            reason_code="news_window",
            numeric_inputs=numeric,
            recommended_action="block",
            size_multiplier=0.0,
        )
    if spread_pips is None:
        return GateResult(
            gate_name="cost",
            result=GATE_FAIL,
            reason_code="missing_spread",
            numeric_inputs=numeric,
            recommended_action="block",
            size_multiplier=0.0,
        )
    if atr_pips is None:
        numeric["spread_pips"] = spread_pips
        return GateResult(
            gate_name="cost",
            result=GATE_FAIL,
            reason_code="missing_atr",
            numeric_inputs=numeric,
            recommended_action="block",
            size_multiplier=0.0,
        )

    half_spread = spread_pips / 2.0
    total_cost_pips = half_spread + slip + (commission_pips or 0.0)
    cost_limit = max_frac * atr_pips
    numeric.update(
        {
            "spread_pips": spread_pips,
            "half_spread_pips": half_spread,
            "atr_pips": atr_pips,
            "total_cost_pips": total_cost_pips,
            "cost_limit_pips": cost_limit,
        }
    )

    if total_cost_pips > cost_limit:
        return GateResult(
            gate_name="cost",
            result=GATE_FAIL,
            reason_code="cost_exceeds_atr_fraction",
            numeric_inputs=numeric,
            recommended_action="block",
            size_multiplier=0.0,
        )

    return GateResult(
        gate_name="cost",
        result=GATE_PASS,
        reason_code="cost_ok",
        numeric_inputs=numeric,
        recommended_action="allow",
        size_multiplier=1.0,
    )


def data_quality_gate(derivatives: dict | Any) -> GateResult:
    """Gate on ``FactorDerivatives`` status (invalid/degraded/ok)."""
    if hasattr(derivatives, "to_dict"):
        data = derivatives.to_dict()
    elif isinstance(derivatives, dict):
        data = derivatives
    else:
        data = {"status": "invalid", "diagnostics": []}

    status = str(data.get("status", "invalid"))
    diags = data.get("diagnostics") or []
    missing_codes = ",".join(
        d.get("code", "") for d in diags if isinstance(d, dict) and d.get("code")
    )

    if status == "invalid":
        return GateResult(
            gate_name="data_quality",
            result=GATE_FAIL,
            reason_code=missing_codes or "invalid_data",
            numeric_inputs={"status": status},
            recommended_action="block",
            size_multiplier=0.0,
        )
    if status == "degraded":
        return GateResult(
            gate_name="data_quality",
            result=GATE_REDUCED,
            reason_code=missing_codes or "degraded_data",
            numeric_inputs={"status": status},
            recommended_action="reduce",
            size_multiplier=0.5,
        )
    return GateResult(
        gate_name="data_quality",
        result=GATE_PASS,
        reason_code="data_ok",
        numeric_inputs={"status": status},
        recommended_action="allow",
        size_multiplier=1.0,
    )
