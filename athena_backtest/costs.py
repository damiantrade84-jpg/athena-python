"""Single cost resolver for backtest trading costs (spread/commission/slippage/swap).

Both engines call `resolve_costs` + `cost_r`. Precedence:
    BY_SYMBOL override > BY_ASSET override > flat ENGINE_A_V3_BACKTEST base

`BY_ASSET.crypto` (commission 5.5bps / slippage 2.0bps per leg) already exists
in config.yaml precisely because the flat base is forex-grade and understates
crypto costs ~10x when not applied -- this resolver enforces that override for
BOTH engines, closing the historical gap where Engine B backtests skipped it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_BASE_KEYS = ("SPREAD_BPS", "COMMISSION_BPS", "SLIPPAGE_BPS", "SWAP_BPS_PER_DAY")


@dataclass(frozen=True)
class CostSpec:
    spread_bps: float
    commission_bps: float
    slippage_bps: float
    swap_bps_per_day: float
    round_trip: bool
    source_keys: tuple[str, ...]


def resolve_costs(pair: dict, *, engine: str, config: dict | None = None) -> CostSpec:
    """Resolve the effective cost spec for one pair.

    `engine` is accepted for interface symmetry / auditability (`source_keys`
    records which engine asked); the cost block itself is engine-agnostic --
    Engine B backtests previously never applied the crypto override, which is
    exactly the bug this shared resolver closes.
    """
    if config is None:
        from config import CONFIG as config  # type: ignore[assignment]

    bt_cfg = dict(config.get("ENGINE_A_V3_BACKTEST") or {})
    round_trip = bool(bt_cfg.get("ROUND_TRIP_COSTS", True))
    by_asset = dict(bt_cfg.get("BY_ASSET") or {})

    values = {k: float(bt_cfg.get(k, 0.0) or 0.0) for k in _BASE_KEYS}
    source_keys = ["ENGINE_A_V3_BACKTEST"]

    asset_type = str(pair.get("type") or pair.get("asset_type") or "").strip().lower()
    asset_override = by_asset.get(asset_type)
    if isinstance(asset_override, dict):
        for k in _BASE_KEYS:
            if k in asset_override:
                values[k] = float(asset_override[k])
        source_keys.append(f"BY_ASSET.{asset_type}")

    symbol = str(pair.get("symbol") or pair.get("display") or "").strip().upper()
    by_symbol = dict(bt_cfg.get("BY_SYMBOL") or {})
    symbol_override = by_symbol.get(symbol)
    if isinstance(symbol_override, dict):
        for k in _BASE_KEYS:
            if k in symbol_override:
                values[k] = float(symbol_override[k])
        source_keys.append(f"BY_SYMBOL.{symbol}")

    return CostSpec(
        spread_bps=values["SPREAD_BPS"],
        commission_bps=values["COMMISSION_BPS"],
        slippage_bps=values["SLIPPAGE_BPS"],
        swap_bps_per_day=values["SWAP_BPS_PER_DAY"],
        round_trip=round_trip,
        source_keys=tuple(source_keys),
    )


def cost_r(
    entry: float,
    sl: float,
    spec: CostSpec,
    *,
    entry_time: datetime,
    exit_time: datetime,
) -> float:
    """Round-trip friction in R, ported verbatim from engine_a_v3/backtest.py::_cost_r.

    Spread crossed once; commission and slippage paid on both legs when
    round_trip; swap accrues per calendar day held.
    """
    risk = abs(entry - sl)
    if entry <= 0 or risk <= 0:
        return 0.0
    days = max(0.0, (exit_time - entry_time).total_seconds() / 86_400.0)
    sided = 2.0 if spec.round_trip else 1.0
    total_bps = (
        spec.spread_bps
        + sided * spec.commission_bps
        + sided * spec.slippage_bps
        + spec.swap_bps_per_day * days
    )
    return (entry * total_bps / 10_000.0) / risk
