"""OX Book settings — config-gated with safe defaults (mirrors athena_ase pattern)."""

from __future__ import annotations

import os
from typing import Any


def _cfg() -> dict[str, Any]:
    if os.environ.get("OX_BOOK_RESEARCH_CONFIG_DEFAULTS", "").strip() == "1":
        return {}
    try:
        from config import CONFIG

        return CONFIG
    except BaseException:
        return {}


def _block() -> dict[str, Any]:
    raw = _cfg().get("OX_BOOK")
    return raw if isinstance(raw, dict) else {}


def _f(name: str, default: float) -> float:
    try:
        return float(_block().get(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(_block().get(name, default))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return bool(_block().get("ENABLED", False))


def signal_timeframe() -> str:
    tf = str(_block().get("SIGNAL_TIMEFRAME", "D1")).strip().upper()
    return tf or "D1"


def ema_fast() -> int:
    return max(2, _i("EMA_FAST", 15))


def ema_slow() -> int:
    slow = max(3, _i("EMA_SLOW", 60))
    return max(slow, ema_fast() + 1)


def atr_n() -> int:
    return max(2, _i("ATR_N", 14))


def atr_mult() -> float:
    return max(0.5, _f("ATR_MULT", 3.0))


def long_only() -> bool:
    return bool(_block().get("LONG_ONLY", True))


def min_bars() -> int:
    return max(50, _i("MIN_BARS", 260))


def min_trades() -> int:
    return max(5, _i("MIN_TRADES", 30))


def min_edge_quality() -> float:
    return max(0.0, _f("MIN_EDGE_QUALITY", 0.20))


def sqn_floor() -> float:
    return max(0.0, _f("SQN_FLOOR", 2.0))


def plateau_fast_set() -> tuple[int, ...]:
    raw = _block().get("PLATEAU_FAST_SET")
    if not isinstance(raw, (list, tuple)) or not raw:
        return (12, 15, 18)
    vals = []
    for v in raw:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv >= 2:
            vals.append(iv)
    return tuple(vals) or (12, 15, 18)


def plateau_slow_ratio() -> float:
    return max(1.5, _f("PLATEAU_SLOW_RATIO", 4.0))


def plateau_atr_mult_set() -> tuple[float, ...]:
    raw = _block().get("PLATEAU_ATR_MULT_SET")
    if not isinstance(raw, (list, tuple)) or not raw:
        return (2.5, 3.0, 3.5)
    vals = []
    for v in raw:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv >= 0.5:
            vals.append(fv)
    return tuple(vals) or (2.5, 3.0, 3.5)


def plateau_min_pass_frac() -> float:
    return min(1.0, max(0.0, _f("PLATEAU_MIN_PASS_FRAC", 0.6)))


def cost_stress_mult() -> float:
    return max(1.0, _f("COST_STRESS_MULT", 2.0))


def base_cost_per_side(asset_class: str | None = None) -> float:
    costs = _block().get("BASE_COST_PER_SIDE_BY_CLASS")
    if isinstance(costs, dict):
        if asset_class is not None:
            try:
                return abs(float(costs.get(asset_class, costs.get("default", 0.0002))))
            except (TypeError, ValueError):
                pass
        try:
            return abs(float(costs.get("default", 0.0002)))
        except (TypeError, ValueError):
            pass
    return 0.0002


def era_years() -> int:
    return max(1, _i("ERA_YEARS", 5))


def min_positive_eras() -> int:
    return max(0, _i("MIN_POSITIVE_ERAS", 4))


def corr_max() -> float:
    return min(1.0, max(0.0, _f("CORR_MAX", 0.50)))


def max_book_size() -> int:
    return max(1, _i("MAX_BOOK_SIZE", 5))


def t_stat_hurdle() -> float:
    return max(0.0, _f("T_STAT_HURDLE", 3.0))


def decay_haircut() -> float:
    return min(0.95, max(0.0, _f("DECAY_HAIRCUT", 0.58)))


def trial_log_path() -> str:
    raw = str(_block().get("TRIAL_LOG_PATH", "data/ox_book_trials.jsonl")).strip()
    return raw or "data/ox_book_trials.jsonl"


def live_members() -> tuple[str, ...]:
    """Instruments authorized for gated demo execution. Fail-closed: unknown or
    empty means nothing is authorized. Membership must match markets that passed
    the ox_book evidence gates AND exist in tsmom_live INSTRUMENTS."""
    raw = _block().get("LIVE_MEMBERS")
    if not isinstance(raw, (list, tuple)):
        return ()
    out = []
    for v in raw:
        name = str(v).strip().lower()
        if name:
            out.append(name)
    return tuple(out)
