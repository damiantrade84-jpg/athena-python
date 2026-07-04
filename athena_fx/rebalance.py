"""Weekly FX factor rebalance — refresh decisions and sync MT5 demo positions."""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from config import CONFIG

from athena_fx import store as fx_store
from athena_fx.backtest import apply_exposure_caps, realized_vol_annualized, vol_scaled_weight
from athena_fx.execution import (
    FxExecutionDeps,
    close_fx_symbol_position,
    default_fx_execution_deps,
    execute_fx_decision,
)
from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_NO_TRADE,
    ACTION_REDUCE,
    ACTION_SELL,
    G8_PAIRS,
)
from athena_fx.pipeline import refresh_decisions

log = logging.getLogger("athena_fx.rebalance")


def _eligible_entry(decision: dict[str, Any]) -> bool:
    action = str(decision.get("action") or "")
    if action not in {ACTION_BUY, ACTION_SELL}:
        return False
    if not decision.get("direction"):
        return False
    for gate in decision.get("gate_results") or []:
        if isinstance(gate, dict) and gate.get("result") == "fail":
            return False
    return True


def _target_weights(
    decisions: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    as_of: str,
) -> dict[str, tuple[float, str, dict[str, Any]]]:
    """Mirror backtest vol-scaled weights for eligible BUY/SELL decisions."""
    target_vol = float(CONFIG.get("FX_FACTOR_TARGET_VOL", 0.10) or 0.10)
    pair_cap = float(CONFIG.get("FX_FACTOR_PAIR_RISK_CAP", 0.02) or 0.02)
    raw: dict[str, tuple[float, str]] = {}

    for decision in decisions:
        sym = str(decision.get("symbol") or "").replace("/", "").upper()
        action = str(decision.get("action") or "")
        if action == ACTION_FLATTEN:
            continue
        if action == ACTION_NO_TRADE:
            continue
        if action == ACTION_REDUCE:
            continue
        if action not in {ACTION_BUY, ACTION_SELL}:
            continue
        if not _eligible_entry(decision):
            continue

        bars = bars_by_symbol.get(sym, [])
        if not bars:
            continue
        bar_index = {b["date"]: b for b in bars}
        rv = realized_vol_annualized(bar_index, as_of)
        base_w = vol_scaled_weight(rv, target_vol, pair_cap)
        mult = float(decision.get("size_multiplier") or 1.0)
        w = base_w * mult
        if w <= 0:
            continue
        direction = "LONG" if action == ACTION_BUY else "SHORT"
        signed = w if direction == "LONG" else -w
        raw[sym] = (signed, direction)

    ccy_cap = float(CONFIG.get("FX_FACTOR_CCY_RISK_CAP", 0.04) or 0.04)
    total_cap = float(CONFIG.get("FX_FACTOR_TOTAL_RISK_CAP", 0.10) or 0.10)
    capped = apply_exposure_caps(raw, pair_cap, ccy_cap, total_cap)

    out: dict[str, tuple[float, str, dict[str, Any]]] = {}
    decision_by_sym = {
        str(d.get("symbol") or "").replace("/", "").upper(): d for d in decisions
    }
    for sym, (signed, direction) in capped.items():
        out[sym] = (abs(signed), direction, decision_by_sym.get(sym, {}))
    return out


def run_fx_factor_rebalance(
    *,
    symbols: Optional[list[str]] = None,
    as_of_date: Optional[str] = None,
    execute_trades: bool = False,
    dry_run: bool = False,
    deps: FxExecutionDeps | None = None,
) -> dict[str, Any]:
    """Refresh G8 factor decisions and optionally execute MT5 demo rebalance."""
    deps = deps or default_fx_execution_deps()
    universe = [s.replace("/", "").upper() for s in (symbols or list(G8_PAIRS))]
    as_of = as_of_date or _dt.date.today().isoformat()

    refresh_summary = refresh_decisions(symbols=universe, as_of_date=as_of)
    all_decisions = fx_store.load_decisions()
    latest_dates = sorted(
        {str(d.get("as_of_date") or "") for d in all_decisions if d.get("as_of_date")}
    )
    decision_date = as_of if as_of in latest_dates else (latest_dates[-1] if latest_dates else as_of)
    decisions = [d for d in all_decisions if d.get("as_of_date") == decision_date]

    bars_by_symbol: dict[str, list] = {}
    try:
        from datetime import timedelta

        from athena_fx.backtest_data import load_daily_bars

        start = (_dt.date.fromisoformat(decision_date) - timedelta(days=400)).isoformat()
        bars_by_symbol, _ = load_daily_bars(universe, start, decision_date)
    except Exception as exc:
        log.warning("rebalance bar load failed: %s", exc)

    targets = _target_weights(decisions, bars_by_symbol, decision_date)
    flatten_syms = {
        str(d.get("symbol") or "").replace("/", "").upper()
        for d in decisions
        if str(d.get("action") or "") == ACTION_FLATTEN
    }

    plan: list[dict[str, Any]] = []
    for sym, (weight, direction, decision) in sorted(targets.items()):
        plan.append({
            "symbol": sym,
            "action": "open",
            "direction": direction,
            "target_weight": weight,
            "decision": decision,
        })
    for sym in sorted(flatten_syms):
        plan.append({"symbol": sym, "action": "flatten", "direction": None})

    results: list[dict[str, Any]] = []
    if not execute_trades:
        return {
            "ok": True,
            "as_of_date": decision_date,
            "refresh": refresh_summary,
            "plan": plan,
            "executed": results,
            "dry_run": dry_run,
        }

    for item in plan:
        sym = item["symbol"]
        if item["action"] == "flatten":
            results.append(close_fx_symbol_position(sym, deps=deps, dry_run=dry_run))
            continue
        decision = item.get("decision") or {}
        if not decision:
            continue
        results.append(
            execute_fx_decision(decision, deps=deps, dry_run=dry_run)
        )

    filled = sum(1 for r in results if r.get("executed"))
    return {
        "ok": True,
        "as_of_date": decision_date,
        "refresh": refresh_summary,
        "plan": plan,
        "executed": results,
        "filled_count": filled,
        "dry_run": dry_run,
    }


__all__ = ["run_fx_factor_rebalance"]
