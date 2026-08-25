"""OX Book evidence gates.

Every gate here maps to a measured finding in the surviving-strategy literature or
the repo's own 25-yr TSMOM validation:

  edge quality      exp(R)/std(R) >= floor        (tsmom screen bar: 0.20)
  trade count       N >= min_trades               (statistical power)
  SQN100 headline   >= floor                      (Van Tharp, capped at N=100)
  OOS stability     OOS expectancy > 0            (60/40 split by entry date)
  plateau           neighbourhood configs clear the floor fraction (no knife-edge)
  cost stress       stressed-cost SQN100 clears the floor (survive 2x costs)
  era positivity    positive expectancy in >= min_positive_eras of the eras
  correlation cap   pairwise daily-return corr < cap (breadth without dilution)
"""

from __future__ import annotations

import pandas as pd

from ox_book import settings
from ox_book.contracts import BookVerdict, MarketEvaluation, OxMetrics, OxParams
from ox_book.core import (
    daily_returns,
    era_expectancies,
    metrics,
    returns_correlation,
    simulate,
    split_is_oos,
    validate_candles,
)
from ox_book.significance import TrialRegistry


def _neighbourhood_params(canonical: OxParams) -> list[OxParams]:
    out = []
    for fast in settings.plateau_fast_set():
        slow = max(fast + 1, int(round(fast * settings.plateau_slow_ratio())))
        for atr_mult in settings.plateau_atr_mult_set():
            out.append(
                OxParams(
                    fast=fast,
                    slow=slow,
                    atr_n=canonical.atr_n,
                    atr_mult=atr_mult,
                    long_only=canonical.long_only,
                    cost_per_side=canonical.cost_per_side,
                )
            )
    return out


def _is_positive(value: float | None) -> bool:
    return value is not None and value > 0.0


def evaluate_market(
    symbol: str,
    df: pd.DataFrame,
    canonical: OxParams,
    trial_registry: TrialRegistry | None = None,
) -> MarketEvaluation:
    if not validate_candles(df, settings.min_bars()):
        return MarketEvaluation(symbol=symbol, qualifies=False, reasons=["insufficient_bars"])

    trades = simulate(df, canonical)
    full = metrics(trades)
    is_trades, oos_trades = split_is_oos(trades)
    oos = metrics(oos_trades)
    eras = era_expectancies(trades, settings.era_years())

    if trial_registry is not None:
        trial_registry.record(
            {
                "symbol": symbol,
                "params": canonical.key,
                "kind": "canonical",
                "n": full.n,
                "t_stat": full.t_stat,
                "sqn100": full.sqn100,
            }
        )

    reasons: list[str] = []

    if full.n < settings.min_trades() or full.exp_r is None:
        reasons.append("insufficient_trades")

    edge_quality = None
    if full.exp_r is not None and full.std_r:
        edge_quality = full.exp_r / full.std_r
        if edge_quality < settings.min_edge_quality():
            reasons.append("edge_quality_below_floor")

    if full.sqn100 is not None and full.sqn100 < settings.sqn_floor():
        reasons.append("sqn_below_floor")
    elif full.sqn100 is None:
        reasons.append("sqn_undefined")

    if len(is_trades) >= 5 and not _is_positive(oos.exp_r):
        reasons.append("oos_expectancy_not_positive")

    plateau_results: list[OxMetrics] = []
    for params in _neighbourhood_params(canonical):
        nb_trades = simulate(df, params)
        nb_metrics = metrics(nb_trades)
        plateau_results.append(nb_metrics)
        if trial_registry is not None:
            trial_registry.record(
                {
                    "symbol": symbol,
                    "params": params.key,
                    "kind": "plateau",
                    "n": nb_metrics.n,
                    "t_stat": nb_metrics.t_stat,
                    "sqn100": nb_metrics.sqn100,
                }
            )
    floor = settings.sqn_floor()
    passing = sum(1 for m in plateau_results if m.sqn100 is not None and m.sqn100 >= floor)
    plateau_frac = passing / float(len(plateau_results)) if plateau_results else 0.0
    if plateau_frac < settings.plateau_min_pass_frac():
        reasons.append("plateau_not_robust")

    stressed_params = OxParams(
        fast=canonical.fast,
        slow=canonical.slow,
        atr_n=canonical.atr_n,
        atr_mult=canonical.atr_mult,
        long_only=canonical.long_only,
        cost_per_side=canonical.cost_per_side * settings.cost_stress_mult(),
    )
    stressed = metrics(simulate(df, stressed_params))
    if trial_registry is not None:
        trial_registry.record(
            {
                "symbol": symbol,
                "params": stressed_params.key,
                "kind": "cost_stress",
                "n": stressed.n,
                "t_stat": stressed.t_stat,
                "sqn100": stressed.sqn100,
            }
        )
    if stressed.sqn100 is None or stressed.sqn100 < floor:
        reasons.append("fails_cost_stress")

    positive_eras = sum(1 for e in eras if e > 0.0)
    if eras and positive_eras < settings.min_positive_eras():
        reasons.append("era_consistency_failed")
    elif not eras:
        reasons.append("era_data_missing")

    return MarketEvaluation(
        symbol=symbol,
        qualifies=not reasons,
        reasons=reasons,
        edge_quality=edge_quality,
        metrics_full=full,
        metrics_oos=oos,
        plateau_pass_frac=plateau_frac,
        stressed_sqn100=stressed.sqn100,
        era_expectancies=eras,
    )


def build_book(
    evaluations: list[MarketEvaluation],
    candles_by_symbol: dict[str, pd.DataFrame],
    canonical: OxParams,
) -> BookVerdict:
    """Greedy max-diversification over qualifying markets only."""
    qualifying = [ev for ev in evaluations if ev.qualifies]
    qualifying.sort(key=lambda ev: (-(ev.edge_quality or 0.0), ev.symbol))

    members: list[MarketEvaluation] = []
    rejected: list[MarketEvaluation] = []
    member_returns: dict[str, pd.Series] = {}

    for ev in qualifying:
        rets = daily_returns(candles_by_symbol[ev.symbol])
        too_correlated_with = None
        for member_symbol, member_rets in member_returns.items():
            corr = returns_correlation(rets, member_rets)
            if corr is not None and abs(corr) >= settings.corr_max():
                too_correlated_with = f"{member_symbol}({corr:.2f})"
                break
        if too_correlated_with is not None:
            rejected = rejected + [
                MarketEvaluation(
                    symbol=ev.symbol,
                    qualifies=False,
                    reasons=[f"correlated_with_{too_correlated_with}"],
                    edge_quality=ev.edge_quality,
                    metrics_full=ev.metrics_full,
                )
            ]
            continue
        if len(members) >= settings.max_book_size():
            rejected = rejected + [
                MarketEvaluation(
                    symbol=ev.symbol,
                    qualifies=False,
                    reasons=["book_full"],
                    edge_quality=ev.edge_quality,
                    metrics_full=ev.metrics_full,
                )
            ]
            continue
        members.append(ev)
        member_returns[ev.symbol] = rets

    for ev in evaluations:
        if not ev.qualifies:
            rejected.append(ev)

    return BookVerdict(
        members=members,
        rejected=rejected,
        canonical_key=canonical.key,
    )
