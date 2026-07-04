"""Live FX factor scanner.

Builds actionable candidates from persisted factor decisions plus current
execution context. The scanner is signal-only: it never submits orders.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Callable, Optional

from config import CONFIG

from athena_fx import store as fx_store
from athena_fx.execution import sl_tp_from_atr
from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_NO_TRADE,
    ACTION_REDUCE,
    ACTION_SELL,
    G8_PAIRS,
    GATE_FAIL,
    FxTradeCandidate,
    pair_legs,
)
from athena_fx.pipeline import _atr_pips_from_bars, refresh_decisions


PriceProvider = Callable[[str], tuple[float | None, float | None] | None]
BarsProvider = Callable[[str], list[dict[str, Any]]]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _pip_size(symbol: str) -> float:
    return 0.01 if pair_legs(symbol)[1] == "JPY" else 0.0001


def _latest_decision_date(decisions: list[dict[str, Any]]) -> Optional[str]:
    dates = [str(d.get("as_of_date") or "") for d in decisions if d.get("as_of_date")]
    return max(dates) if dates else None


def _default_price_provider(symbol: str) -> tuple[float | None, float | None] | None:
    try:
        from mt5_executor import mt5_get_symbol_info

        sym = symbol.replace("/", "").upper()
        display = f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 else sym
        info = mt5_get_symbol_info(display)
        if not info or info.get("error"):
            return None
        bid = float(info.get("bid") or 0)
        ask = float(info.get("ask") or 0)
        return (bid or None, ask or None)
    except Exception:
        return None


def _default_bars_provider(symbol: str, as_of_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from datetime import date, timedelta

        from athena_fx.backtest_data import load_daily_bars

        end = as_of_date or _dt.date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=90)).isoformat()
        bars_map, _diags = load_daily_bars([symbol], start, end)
        return list(bars_map.get(symbol.replace("/", "").upper(), []) or [])
    except Exception:
        return []


def _gate_map(decision: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for gate in decision.get("gate_results") or []:
        if isinstance(gate, dict) and gate.get("gate_name"):
            mapped[str(gate["gate_name"])] = dict(gate)
    return mapped


def _failed_gates(gates: dict[str, dict[str, Any]]) -> list[str]:
    return [
        f"{name}:{gate.get('reason_code')}"
        for name, gate in gates.items()
        if gate.get("result") == GATE_FAIL
    ]


def build_candidate_from_decision(
    decision: dict[str, Any],
    *,
    scan_id: str,
    bid: float | None,
    ask: float | None,
    bars: list[dict[str, Any]],
    execution_allowed: bool,
    execution_block_reason: str | None,
    existing_position: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> FxTradeCandidate:
    sym = str(decision.get("symbol") or "").replace("/", "").upper()
    direction = decision.get("direction")
    gates = _gate_map(decision)
    failed = _failed_gates(gates)
    raw_action = str(decision.get("action") or ACTION_NO_TRADE)
    factor_eligible = raw_action in {ACTION_BUY, ACTION_SELL, ACTION_REDUCE, ACTION_FLATTEN}
    action = raw_action
    if failed and action in {ACTION_BUY, ACTION_SELL}:
        action = ACTION_NO_TRADE

    mid = None
    if bid and ask:
        mid = (float(bid) + float(ask)) / 2.0
    elif bid or ask:
        mid = float(bid or ask or 0)

    spread_pips = None
    if bid and ask and ask >= bid:
        spread_pips = (float(ask) - float(bid)) / _pip_size(sym)
    atr_pips = _atr_pips_from_bars(bars, sym)

    entry = None
    if action == ACTION_BUY:
        entry = float(ask or mid or 0) or None
    elif action == ACTION_SELL:
        entry = float(bid or mid or 0) or None

    sl = tp1 = tp2 = rr1 = None
    if entry and atr_pips and direction in {"LONG", "SHORT"}:
        sl, tp1, tp2 = sl_tp_from_atr(float(entry), str(direction), float(atr_pips), sym)
        risk = abs(float(entry) - sl)
        reward = abs(tp1 - float(entry))
        rr1 = reward / risk if risk > 0 else None

    size_multiplier = float(decision.get("size_multiplier") or 0.0)
    target_weight = float(CONFIG.get("FX_FACTOR_PAIR_RISK_CAP", 0.02) or 0.02) * max(0.0, size_multiplier)
    execution_eligible = (
        action in {ACTION_BUY, ACTION_SELL, ACTION_REDUCE, ACTION_FLATTEN}
        and not failed
        and bool(entry or action in {ACTION_REDUCE, ACTION_FLATTEN})
    )
    tradable_now = action in {ACTION_BUY, ACTION_SELL} and execution_eligible and execution_allowed
    reason = str(decision.get("reason") or "")
    if failed:
        reason = f"Factor setup exists, but hard gate blocked execution: {', '.join(failed)}"
    elif action == ACTION_NO_TRADE and not reason:
        reason = "No factor-aligned trade candidate."
    if not execution_allowed and action in {ACTION_BUY, ACTION_SELL}:
        reason = f"{reason}; execution blocked: {execution_block_reason}"

    return FxTradeCandidate(
        scan_id=scan_id,
        symbol=sym,
        action=action,
        direction=direction,
        tradable_now=tradable_now,
        factor_eligible=factor_eligible,
        execution_eligible=execution_eligible,
        current_price=mid,
        bid=bid,
        ask=ask,
        spread_pips=spread_pips,
        atr_pips=atr_pips,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        rr1=rr1,
        target_weight=target_weight,
        size_multiplier=size_multiplier,
        aligned_families=list(decision.get("aligned_families") or []),
        blocked_families=list(decision.get("blocked_families") or []),
        family_scores=dict(decision.get("factor_scores") or {}),
        gate_results=gates,
        cot_overlay=decision.get("cot_overlay"),
        cost_diagnostics=dict(decision.get("cost_diagnostics") or {}),
        execution_preflight={
            "execution_allowed": execution_allowed,
            "execution_block_reason": execution_block_reason,
        },
        existing_position=existing_position,
        reason=reason,
        diagnostics=list(decision.get("diagnostics") or []),
        last_factor_decision_date=decision.get("as_of_date"),
        generated_at=generated_at or _now_iso(),
    )


def _rank_key(candidate: FxTradeCandidate) -> tuple[int, float, str]:
    if candidate.tradable_now and candidate.action in {ACTION_BUY, ACTION_SELL}:
        bucket = 0
    elif candidate.action in {ACTION_REDUCE, ACTION_FLATTEN}:
        bucket = 1
    elif candidate.factor_eligible and not candidate.tradable_now:
        bucket = 2
    else:
        bucket = 3
    score = 0.0
    for row in candidate.family_scores.values():
        if isinstance(row, dict):
            score += abs(float(row.get("points") or 0.0))
    return (bucket, -score, candidate.symbol)


def scan_forex(
    *,
    symbols: list[str] | None = None,
    refresh: bool = False,
    include_blocked: bool = True,
    strict_factor_data: bool = True,
    as_of_date: str | None = None,
    execution_allowed: bool = False,
    execution_block_reason: str | None = None,
    decisions_by_symbol: dict[str, dict[str, Any]] | None = None,
    price_provider: PriceProvider | None = None,
    bars_provider: BarsProvider | None = None,
) -> dict[str, Any]:
    universe = [s.replace("/", "").upper() for s in (symbols or list(G8_PAIRS))]
    if refresh:
        refresh_decisions(symbols=universe, as_of_date=as_of_date)

    diagnostics: list[dict[str, Any]] = []
    if decisions_by_symbol is None:
        all_decisions = fx_store.load_decisions(as_of_date=as_of_date)
        latest = as_of_date or _latest_decision_date(all_decisions)
        decisions_by_symbol = {
            str(d.get("symbol") or "").upper(): d
            for d in all_decisions
            if not latest or d.get("as_of_date") == latest
        }
        as_of_date = latest

    scan_id = f"fxscan-{uuid.uuid4().hex[:12]}"
    generated_at = _now_iso()
    price_provider = price_provider or _default_price_provider
    bars_provider = bars_provider or (lambda s: _default_bars_provider(s, as_of_date))
    candidates: list[FxTradeCandidate] = []

    for sym in universe:
        decision = decisions_by_symbol.get(sym)
        if decision is None:
            diagnostics.append({"code": "MISSING_DECISION", "symbol": sym})
            if strict_factor_data:
                decision = {
                    "symbol": sym,
                    "as_of_date": as_of_date,
                    "action": ACTION_NO_TRADE,
                    "direction": None,
                    "reason": "missing factor decision",
                    "diagnostics": [{"code": "MISSING_DECISION", "severity": "error"}],
                }
            else:
                continue

        price = price_provider(sym)
        bid = ask = None
        if price:
            bid, ask = price
        else:
            diagnostics.append({"code": "MISSING_PRICE", "symbol": sym})
        bars = bars_provider(sym)
        if not bars:
            diagnostics.append({"code": "MISSING_BARS", "symbol": sym})
        candidate = build_candidate_from_decision(
            decision,
            scan_id=scan_id,
            bid=bid,
            ask=ask,
            bars=bars,
            execution_allowed=execution_allowed,
            execution_block_reason=execution_block_reason,
            generated_at=generated_at,
        )
        if include_blocked or candidate.tradable_now:
            candidates.append(candidate)

    candidates.sort(key=_rank_key)
    rows = [c.to_dict() for c in candidates]
    summary = {
        "symbols_scanned": len(universe),
        "tradable_count": sum(1 for c in candidates if c.tradable_now),
        "blocked_count": sum(1 for c in candidates if c.factor_eligible and not c.tradable_now and c.action == ACTION_NO_TRADE),
        "no_trade_count": sum(1 for c in candidates if not c.factor_eligible),
        "buy_count": sum(1 for c in candidates if c.action == ACTION_BUY),
        "sell_count": sum(1 for c in candidates if c.action == ACTION_SELL),
        "execution_enabled": execution_allowed,
    }
    return {
        "scan_id": scan_id,
        "as_of_date": as_of_date,
        "generated_at": generated_at,
        "summary": summary,
        "candidates": rows,
        "diagnostics": diagnostics,
        "warnings": [],
    }
