"""Composition glue for the athena_fx factor lane.

Builds per-symbol factor state (families -> COT overlay -> hard gates ->
decision) from persisted swap-audit / total-return / REER data through the
shared derivatives adapter, so the live status surface and the backtest
consume the exact same resolution path (Phase 1 parity guarantee).
"""
from __future__ import annotations

import datetime as _dt
import logging
from types import SimpleNamespace
from typing import Any, Optional

from config import CONFIG

from athena_fx import store as fx_store
from athena_fx.carry import carry_family_score
from athena_fx.cot_overlay import cot_overlay
from athena_fx.decision import decide
from athena_fx.derivatives_adapter import resolve_factor_derivatives
from athena_fx.gates import cost_gate, data_quality_gate, volatility_gate
from athena_fx.models import (
    FAMILY_CARRY,
    FAMILY_MOMENTUM,
    FAMILY_VALUE,
    G8_CURRENCIES,
    G8_PAIRS,
    FxDecision,
    StrictFactorDataError,
    pair_legs,
)
from athena_fx.momentum import momentum_family_score
from athena_fx.total_return import currency_total_return_indexes
from athena_fx.value import value_family_score

log = logging.getLogger("athena_fx.pipeline")

_CURRENCY_INDEX_CACHE: dict[str, dict[str, list[dict[str, Any]]]] = {}


def clear_currency_index_cache() -> None:
    """Test helper — reset cached currency total-return indexes."""
    _CURRENCY_INDEX_CACHE.clear()


def _usd_pairs_for_indexes() -> list[str]:
    return [
        pair for pair in G8_PAIRS
        if "USD" in (pair[:3], pair[3:])
    ]


def _load_currency_indexes(
    as_of_date: str, db_path: Optional[str] = None
) -> dict[str, list[dict[str, Any]]]:
    """Currency total-return indexes from persisted USD-pair total returns."""
    cache_key = f"{as_of_date}:{db_path or ''}"
    cached = _CURRENCY_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    rows_by_symbol: dict[str, list[Any]] = {}
    for pair in _usd_pairs_for_indexes():
        rows = fx_store.load_total_return(pair, end=as_of_date, db_path=db_path)
        if rows:
            # currency_total_return_indexes expects attribute access.
            rows_by_symbol[pair] = [SimpleNamespace(**row) for row in rows]
    if not rows_by_symbol:
        _CURRENCY_INDEX_CACHE[cache_key] = {}
        return {}
    try:
        indexes = currency_total_return_indexes(rows_by_symbol)
        _CURRENCY_INDEX_CACHE[cache_key] = indexes
        return indexes
    except Exception as exc:
        log.warning("currency index build failed: %s", exc)
        _CURRENCY_INDEX_CACHE[cache_key] = {}
        return {}


def _candidate_direction(families: dict[str, Any]) -> Optional[str]:
    """Direction implied by family agreement (>=2 same non-neutral direction)."""
    votes = {"bullish": 0, "bearish": 0}
    for family_score in families.values():
        d = family_score.direction if hasattr(family_score, "direction") else family_score.get("direction")
        if d in votes:
            votes[d] += 1
    if votes["bullish"] >= 2:
        return "LONG"
    if votes["bearish"] >= 2:
        return "SHORT"
    return None


def _atr_pips_from_bars(
    bars: Optional[list[dict[str, Any]]], symbol: str, period: int = 14
) -> Optional[float]:
    """ATR (simple mean of true ranges) in pips from daily OHLC bars."""
    if not bars or len(bars) < period + 1:
        return None
    quote = pair_legs(symbol)[1]
    pip = 0.01 if quote == "JPY" else 0.0001
    ranges: list[float] = []
    prev_close: Optional[float] = None
    for bar in bars[-(period + 1):]:
        try:
            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        except (KeyError, TypeError, ValueError):
            return None
        if prev_close is None:
            prev_close = close
            continue
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    if not ranges:
        return None
    return (sum(ranges) / len(ranges)) / pip


def _spread_pips_for(symbol: str, override: Optional[float]) -> Optional[float]:
    if override is not None:
        return float(override)
    spread_map = CONFIG.get("FX_FACTOR_SPREAD_PIPS_BY_SYMBOL") or {}
    if isinstance(spread_map, dict):
        value = spread_map.get(symbol) or spread_map.get(f"{symbol[:3]}/{symbol[3:]}")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _load_recent_daily_bars_for_cost_gate(
    symbol: str,
    as_of_date: str,
    *,
    lookback_days: int = 90,
) -> list[dict[str, Any]]:
    """Read cached D1 bars for ATR; absence keeps the cost gate fail-closed."""
    sym = symbol.replace("/", "").upper()
    try:
        end_date = _dt.date.fromisoformat(str(as_of_date)[:10])
    except ValueError:
        return []
    start = (end_date - _dt.timedelta(days=lookback_days)).isoformat()
    end = end_date.isoformat()
    try:
        from athena_fx.backtest_data import load_daily_bars

        bars_by_symbol, diagnostics = load_daily_bars([sym], start, end)
    except Exception as exc:
        log.debug("D1 bar load for cost gate failed (%s): %s", sym, exc)
        return []
    if diagnostics:
        log.debug(
            "D1 bar load for cost gate diagnostics (%s): %s",
            sym,
            [d.to_dict() if hasattr(d, "to_dict") else d for d in diagnostics],
        )
    return list(bars_by_symbol.get(sym) or [])


def compute_symbol_state(
    symbol: str,
    as_of_date: str,
    *,
    strict: bool = False,
    has_existing_position: bool = False,
    spread_pips: Optional[float] = None,
    atr_pips: Optional[float] = None,
    bars: Optional[list[dict[str, Any]]] = None,
    hysteresis_state: Optional[dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Full factor state for one pair at one decision date.

    Returns {"decision", "families", "derivatives", "gates", "cot",
    "hysteresis_state"}. Missing data yields explicit diagnostics and
    fail-closed gates — never silent neutrality.
    """
    sym = symbol.replace("/", "").upper()

    # Shared adapter (Phase 1): identical semantics live and backtest.
    strict_violation: Optional[StrictFactorDataError] = None
    try:
        derivatives = resolve_factor_derivatives(
            sym, as_of_date, strict=strict, db_path=db_path
        )
    except StrictFactorDataError as exc:
        strict_violation = exc
        derivatives = resolve_factor_derivatives(
            sym, as_of_date, strict=False, db_path=db_path
        )

    audit_rows = fx_store.load_latest_swap_audit(symbol=sym, db_path=db_path)
    audit_row = audit_rows[0] if audit_rows else None

    total_return_rows = fx_store.load_total_return(sym, end=as_of_date, db_path=db_path)
    currency_indexes = _load_currency_indexes(as_of_date, db_path=db_path)

    families = {
        FAMILY_MOMENTUM: momentum_family_score(
            sym, total_return_rows, currency_indexes, as_of_date
        ),
        FAMILY_CARRY: carry_family_score(
            sym,
            as_of_date,
            swap_audit_row=audit_row,
            theoretical_carry=derivatives.carry_differential,
            db_path=db_path,
        ),
        FAMILY_VALUE: value_family_score(sym, as_of_date, db_path=db_path),
    }

    direction = _candidate_direction(families)
    cot = (
        cot_overlay(sym, direction, as_of_date, db_path=db_path)
        if direction
        else None
    )

    gate_results = [data_quality_gate(derivatives)]
    spot_returns = [
        float(row["spot_return"])
        for row in total_return_rows
        if row.get("spot_return") is not None
    ]
    vol_gate, new_hysteresis = volatility_gate(
        spot_returns,
        has_existing_position=has_existing_position,
        hysteresis_state=hysteresis_state,
    )
    gate_results.append(vol_gate)
    gate_results.append(cost_gate(
        spread_pips=_spread_pips_for(sym, spread_pips),
        atr_pips=atr_pips if atr_pips is not None else _atr_pips_from_bars(bars, sym),
    ))

    decision = decide(
        sym,
        as_of_date,
        family_scores=families,
        cot=cot,
        gate_results=gate_results,
        has_existing_position=has_existing_position,
    )
    if strict_violation is not None:
        decision.diagnostics.extend(strict_violation.diagnostics)

    return {
        "decision": decision,
        "families": families,
        "derivatives": derivatives,
        "gates": gate_results,
        "cot": cot,
        "hysteresis_state": new_hysteresis,
    }


def decide_for_backtest(
    symbol: str,
    as_of_date: str,
    has_existing_position: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Backtest decide_fn — same resolution path as the live status surface."""
    bars: Optional[list[dict[str, Any]]] = _kwargs.get("bars")
    if bars is None:
        try:
            from athena_fx.backtest_data import load_daily_bars

            start = (
                _dt.date.fromisoformat(as_of_date) - _dt.timedelta(days=60)
            ).isoformat()
            bars_map, _diags = load_daily_bars([symbol], start, as_of_date)
            bars = bars_map.get(symbol.replace("/", "").upper())
        except Exception as exc:
            log.debug("bar load for cost gate failed (%s): %s", symbol, exc)
    state = compute_symbol_state(
        symbol,
        as_of_date,
        strict=True,
        has_existing_position=has_existing_position,
        bars=bars,
    )
    decision: FxDecision = state["decision"]
    return decision.to_dict()


def refresh_decisions(
    symbols: Optional[list[str]] = None,
    as_of_date: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Compute and persist family scores + decisions for the universe."""
    universe = [s.replace("/", "").upper() for s in (symbols or list(G8_PAIRS))]
    as_of = as_of_date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    saved_scores = 0
    saved_decisions = 0
    errors: list[dict[str, str]] = []
    for sym in universe:
        try:
            bars = _load_recent_daily_bars_for_cost_gate(sym, as_of)
            state = compute_symbol_state(sym, as_of, bars=bars, db_path=db_path)
        except Exception as exc:
            log.warning("refresh failed for %s: %s", sym, exc)
            errors.append({"symbol": sym, "error": str(exc)})
            continue
        saved_scores += fx_store.save_family_scores(
            state["families"].values(), db_path=db_path
        )
        saved_decisions += fx_store.save_decisions([state["decision"]], db_path=db_path)
    return {
        "as_of_date": as_of,
        "symbols": universe,
        "family_scores_saved": saved_scores,
        "decisions_saved": saved_decisions,
        "errors": errors,
    }


__all__ = [
    "compute_symbol_state",
    "decide_for_backtest",
    "refresh_decisions",
    "G8_CURRENCIES",
    "pair_legs",
]
