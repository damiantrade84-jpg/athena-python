"""Engine A backtest adapter.

Zero scoring logic here: wires stored historical candles + the shared cost
resolver into `engine_a_v3.backtest.run_v3_backtest`, which itself calls the
same `evaluate_engine_a_v3` the live scan uses. This module's only job is
data/cost plumbing and authoritative timeframe-policy injection.
"""

from __future__ import annotations

from athena_backtest.costs import resolve_costs
from athena_backtest.data.store import ParquetCandleStore
from athena_backtest.policy import (
    attach_policy_provenance,
    policy_timeframes_for_scorer,
    resolve_backtest_policy_roles,
)

_PROVIDER_BY_SOURCE = {
    "mt5": "mt5",
    "binance": "binance_futures",
    "eodhd": "eodhd",
}


def load_candles(
    pair: dict,
    *,
    store: ParquetCandleStore | None = None,
    timeframes: tuple[str, ...] | list[str] | None = None,
) -> dict[str, list[dict]]:
    store = store or ParquetCandleStore()
    source = str(pair.get("source") or "").strip().lower()
    provider = _PROVIDER_BY_SOURCE.get(source)
    if provider is None:
        return {}
    tfs = tuple(timeframes) if timeframes else ("D1", "H4", "H1", "M15")
    # Fail-closed: a missing timeframe stays [] and is never substituted.
    return {tf: store.read(pair, tf, provider=provider) for tf in tfs}


def run_engine_a_backtest(
    pair: dict,
    *,
    horizon: str,
    store: ParquetCandleStore | None = None,
    candles: dict[str, list[dict]] | None = None,
    max_hold_bars: int = 24,
    collect_funnel: bool = True,
) -> dict:
    """Run the Engine A backtest for one pair using stored history and resolved costs."""
    from engine_a_v3.backtest import run_v3_backtest

    resolved = resolve_backtest_policy_roles(pair, engine="A", style=horizon)
    policy_timeframes = policy_timeframes_for_scorer(resolved)
    if candles is None:
        candles = load_candles(
            pair,
            store=store,
            timeframes=tuple(resolved["requiredTfs"]),
        )
    spec = resolve_costs(pair, engine="A")
    result = run_v3_backtest(
        pair,
        candles,
        horizon=horizon,
        spread_bps=spec.spread_bps,
        commission_bps=spec.commission_bps,
        slippage_bps=spec.slippage_bps,
        swap_bps_per_day=spec.swap_bps_per_day,
        max_hold_bars=max_hold_bars,
        collect_funnel=collect_funnel,
        policy_timeframes=policy_timeframes,
    )
    return attach_policy_provenance(result, resolved)
