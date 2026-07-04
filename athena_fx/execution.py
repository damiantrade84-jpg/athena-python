"""FX factor lane execution bridge → risk_engine → MT5 (demo/paper only)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from config import CONFIG

from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_NO_TRADE,
    ACTION_REDUCE,
    ACTION_SELL,
    pair_legs,
)
from athena_fx.pipeline import _atr_pips_from_bars

log = logging.getLogger("athena_fx.execution")


def _compact(text: Any) -> str:
    return str(text or "").replace("/", "").replace(" ", "").upper()


def _pip_size(symbol: str) -> float:
    quote = pair_legs(symbol)[1]
    return 0.01 if quote == "JPY" else 0.0001


def _display_symbol(symbol: str) -> str:
    sym = symbol.replace("/", "").upper()
    return f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 else sym


def sl_tp_from_atr(
    entry: float,
    direction: str,
    atr_pips: float,
    symbol: str,
    *,
    sl_mult: float | None = None,
    tp_mult: float | None = None,
) -> tuple[float, float, float]:
    """Wide swing stops for factor holds (config-gated ATR multiples)."""
    sl_m = float(sl_mult if sl_mult is not None else CONFIG.get("FX_FACTOR_EXEC_SL_ATR_MULT", 2.5) or 2.5)
    tp_m = float(tp_mult if tp_mult is not None else CONFIG.get("FX_FACTOR_EXEC_TP_ATR_MULT", 5.0) or 5.0)
    pip = _pip_size(symbol)
    atr_px = max(atr_pips * pip, pip * 5.0)
    if direction == "LONG":
        sl = entry - sl_m * atr_px
        tp1 = entry + tp_m * atr_px
        tp2 = tp1 + (tp1 - entry) * 0.5
    else:
        sl = entry + sl_m * atr_px
        tp1 = entry - tp_m * atr_px
        tp2 = tp1 - (entry - tp1) * 0.5
    return sl, tp1, tp2


def fx_decision_to_execution_dict(
    decision: dict[str, Any],
    *,
    entry_price: float,
    sl: float,
    tp1: float,
    tp2: float,
) -> dict[str, Any]:
    """Map an ``FxDecision`` dict to the legacy execution/risk signal contract."""
    sym = str(decision.get("symbol") or "").replace("/", "").upper()
    action = str(decision.get("action") or "")
    direction = decision.get("direction")
    if not direction:
        direction = "LONG" if action == ACTION_BUY else "SHORT" if action == ACTION_SELL else None
    risk_abs = abs(entry_price - sl)
    reward_abs = abs(tp1 - entry_price)
    rr1 = reward_abs / risk_abs if risk_abs > 0 else 0.0
    return {
        "pair": _display_symbol(sym),
        "symbol": sym,
        "direction": direction,
        "price": float(entry_price),
        "sl": float(sl),
        "tp": float(tp1),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "type": "forex",
        "style": str(CONFIG.get("FX_FACTOR_EXEC_STYLE", "swing") or "swing"),
        "horizon": "swing",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": float(decision.get("total_score") or 0),
        "scoreNorm": float(decision.get("total_score") or 0) / 100.0,
        "engine": "FX_FACTOR",
        "fxFactorExecution": True,
        "fxFactorAction": action,
        "as_of_date": decision.get("as_of_date"),
        "aligned_families": list(decision.get("aligned_families") or []),
        "size_multiplier": float(decision.get("size_multiplier") or 1.0),
        "rr1": rr1,
        "source": "mt5",
        "engineATradeEnabled": True,
    }


@dataclass
class FxExecutionDeps:
    """Injectable runtime dependencies (tests avoid live broker contact)."""

    get_executor_mode: Callable[[], str] = field(
        default_factory=lambda: (lambda: "paper")
    )
    get_mt5_trade_mode: Callable[[], int | None] = field(
        default_factory=lambda: (lambda: 0)
    )
    get_kill_switch: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    execution_enabled: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    fx_execution_enabled: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    mt5_execution_enabled: Callable[[], bool] = field(default_factory=lambda: (lambda: True))
    get_account: Callable[[], dict[str, Any]] = field(
        default_factory=lambda: (lambda: {"balance": 10000.0, "equity": 10000.0})
    )
    get_positions: Callable[[], tuple[list[dict[str, Any]], dict | None]] = field(
        default_factory=lambda: (lambda: ([], None))
    )
    get_symbol_info: Callable[[str, dict], dict | None] = field(
        default_factory=lambda: (lambda _s, _p: None)
    )
    get_live_price: Callable[[str], float | None] = field(
        default_factory=lambda: (lambda _s: None)
    )
    load_bars: Callable[[str, str], list[dict[str, Any]]] = field(
        default_factory=lambda: (lambda _s, _d: [])
    )
    guardian_check: Callable[[dict, list, dict, dict | None], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _s, _p, _a, _r: (True, "OK"))
    )
    risk_check: Callable[..., Any] = field(default_factory=lambda: (lambda **_kw: None))
    mt5_execute: Callable[[dict, Any], dict] = field(
        default_factory=lambda: (lambda _s, _a: {"success": False, "error": "not_configured"})
    )
    close_position: Callable[[dict], dict] = field(
        default_factory=lambda: (lambda _p: {"success": False, "error": "not_configured"})
    )


def assert_fx_execution_allowed(deps: FxExecutionDeps) -> tuple[bool, str]:
    """Fail-closed preflight before any FX factor order."""
    from athena_ase.gates.demo_only import assert_demo

    if not deps.fx_execution_enabled():
        return False, "FX_FACTOR_EXECUTION_DISABLED"
    if not deps.execution_enabled():
        return False, "EXECUTION_DISABLED"
    if not deps.mt5_execution_enabled():
        return False, "MT5_EXECUTION_DISABLED"
    if deps.get_kill_switch():
        return False, "KILL_SWITCH"
    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
    )
    if not gate.ok:
        return False, f"demo_gate:{gate.reason}"
    return True, "ok"


def _position_for_symbol(
    positions: list[dict[str, Any]], symbol: str
) -> dict[str, Any] | None:
    key = _compact(symbol)
    for pos in positions:
        if _compact(pos.get("pair")) == key or _compact(pos.get("symbol")) == key:
            return pos
    return None


def execute_fx_decision(
    decision: dict[str, Any],
    *,
    deps: FxExecutionDeps | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute one eligible FX factor decision on MT5 demo (BUY/SELL only)."""
    deps = deps or default_fx_execution_deps()
    sym = str(decision.get("symbol") or "").replace("/", "").upper()
    action = str(decision.get("action") or "")

    if action not in {ACTION_BUY, ACTION_SELL}:
        return {"executed": False, "reason": f"action_not_executable:{action}", "symbol": sym}

    ok, reason = assert_fx_execution_allowed(deps)
    if not ok:
        return {"executed": False, "reason": reason, "symbol": sym}

    direction = decision.get("direction") or (
        "LONG" if action == ACTION_BUY else "SHORT"
    )
    positions, positions_raw = deps.get_positions()
    existing = _position_for_symbol(positions, sym)
    if existing and str(existing.get("direction") or "").upper() == str(direction).upper():
        return {
            "executed": False,
            "reason": "already_positioned",
            "symbol": sym,
            "direction": direction,
        }
    if existing:
        if dry_run:
            return {
                "executed": False,
                "reason": "would_flip_close_first",
                "symbol": sym,
                "dry_run": True,
            }
        close_res = deps.close_position(existing)
        if not close_res.get("success"):
            return {
                "executed": False,
                "reason": "close_before_flip_failed",
                "symbol": sym,
                "close_result": close_res,
            }

    entry = deps.get_live_price(sym)
    if entry is None or entry <= 0:
        return {"executed": False, "reason": "missing_live_price", "symbol": sym}

    as_of = str(decision.get("as_of_date") or datetime.now(timezone.utc).date().isoformat())
    bars = deps.load_bars(sym, as_of)
    atr_pips = _atr_pips_from_bars(bars, sym)
    if atr_pips is None or atr_pips <= 0:
        return {"executed": False, "reason": "missing_atr", "symbol": sym}

    sl, tp1, tp2 = sl_tp_from_atr(float(entry), str(direction), float(atr_pips), sym)
    exec_dict = fx_decision_to_execution_dict(
        decision, entry_price=float(entry), sl=sl, tp1=tp1, tp2=tp2
    )

    if dry_run:
        return {
            "executed": False,
            "reason": "dry_run",
            "symbol": sym,
            "signal": exec_dict,
            "dry_run": True,
        }

    account = deps.get_account()
    ok_guard, guard_reason = deps.guardian_check(
        exec_dict, positions, account, positions_raw
    )
    if not ok_guard:
        return {"executed": False, "reason": f"guardian:{guard_reason}", "symbol": sym}

    size_mult = float(decision.get("size_multiplier") or 1.0)
    sizing_override = max(0.25, min(1.0, size_mult))

    approval = deps.risk_check(
        signal=exec_dict,
        account_balance=float(account.get("balance", 0)),
        account_equity=float(account.get("equity", 0)),
        open_positions=positions,
        symbol_info=deps.get_symbol_info(sym, exec_dict),
        kill_switch=deps.get_kill_switch(),
        sizing_override=sizing_override,
        execution_context="fx_factor_bridge",
    )
    if approval is None or not getattr(approval, "approved", False):
        rej = getattr(approval, "reason", "risk_rejected") if approval else "risk_rejected"
        return {"executed": False, "reason": f"risk:{rej}", "symbol": sym}

    result = deps.mt5_execute(exec_dict, approval)
    success = bool(result.get("success"))
    return {
        "executed": success,
        "reason": "ok" if success else str(result.get("error", "executor_failed")),
        "symbol": sym,
        "direction": direction,
        "result": result,
        "approval_volume": getattr(approval, "volume", None),
    }


def close_fx_symbol_position(
    symbol: str,
    *,
    deps: FxExecutionDeps | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Close an open MT5 position for *symbol* (FLATTEN / rebalance)."""
    deps = deps or default_fx_execution_deps()
    sym = symbol.replace("/", "").upper()
    ok, reason = assert_fx_execution_allowed(deps)
    if not ok:
        return {"executed": False, "reason": reason, "symbol": sym}

    positions, _raw = deps.get_positions()
    pos = _position_for_symbol(positions, sym)
    if not pos:
        return {"executed": False, "reason": "no_position", "symbol": sym}

    if dry_run:
        return {"executed": False, "reason": "dry_run_close", "symbol": sym, "dry_run": True}

    result = deps.close_position(pos)
    return {
        "executed": bool(result.get("success")),
        "reason": "ok" if result.get("success") else str(result.get("error", "close_failed")),
        "symbol": sym,
        "result": result,
    }


def default_fx_execution_deps() -> FxExecutionDeps:
    """Production deps wired to repo risk/executor/guardian modules."""

    def _mode() -> str:
        return str(CONFIG.get("EXECUTOR_MODE", "paper")).lower()

    def _mt5_mode() -> int | None:
        try:
            import MetaTrader5 as mt5

            info = mt5.account_info()
            return int(info.trade_mode) if info else None
        except Exception:
            return None

    def _risk_check(**kwargs):
        from risk_engine import risk_check as _risk_check_fn

        return _risk_check_fn(**kwargs)

    def _guardian(signal, positions, account, positions_raw):
        from guardian import pre_trade_check

        return pre_trade_check(signal, positions, account, positions_raw)

    def _mt5_execute(signal, approval):
        from mt5_executor import mt5_execute as _mt5_execute_fn

        return _mt5_execute_fn(signal, approval)

    def _get_account() -> dict[str, Any]:
        from mt5_executor import mt5_get_account

        acct = mt5_get_account()
        if not acct or (isinstance(acct, dict) and acct.get("error")):
            detail = acct.get("detail") if isinstance(acct, dict) else ""
            raise RuntimeError(f"MT5_ACCOUNT_UNAVAILABLE: {detail}")
        return acct

    def _get_positions() -> tuple[list, dict | None]:
        from mt5_executor import mt5_get_positions

        res = mt5_get_positions()
        if isinstance(res, dict):
            if res.get("error"):
                raise RuntimeError(f"MT5_POSITIONS_UNAVAILABLE: {res.get('detail')}")
            return list(res.get("positions") or []), res
        return list(res or []), None

    def _close_position(pos: dict) -> dict:
        from mt5_executor import mt5_close_position

        ticket = pos.get("ticket")
        if not ticket:
            return {"success": False, "error": "missing_ticket"}
        return mt5_close_position(int(ticket))

    def _get_symbol_info(symbol: str, exec_dict: dict) -> dict | None:
        from mt5_executor import mt5_get_symbol_info

        display = str(exec_dict.get("pair") or _display_symbol(symbol))
        info = mt5_get_symbol_info(display)
        if isinstance(info, dict) and info.get("error"):
            return None
        return info

    def _get_live_price(symbol: str) -> float | None:
        from mt5_executor import mt5_get_symbol_info

        info = mt5_get_symbol_info(_display_symbol(symbol))
        if not info or info.get("error"):
            return None
        bid = float(info.get("bid") or 0)
        ask = float(info.get("ask") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        last = float(info.get("last") or info.get("close") or 0)
        return last if last > 0 else None

    def _load_bars(symbol: str, as_of: str) -> list[dict[str, Any]]:
        try:
            from datetime import date, timedelta

            from athena_fx.backtest_data import load_daily_bars

            start = (date.fromisoformat(as_of) - timedelta(days=90)).isoformat()
            bars_map, _ = load_daily_bars([symbol], start, as_of)
            return bars_map.get(symbol.replace("/", "").upper(), [])
        except Exception as exc:
            log.debug("bar load failed for %s: %s", symbol, exc)
            return []

    return FxExecutionDeps(
        get_executor_mode=_mode,
        get_mt5_trade_mode=_mt5_mode,
        get_kill_switch=lambda: bool(CONFIG.get("KILL_SWITCH", False)),
        execution_enabled=lambda: bool(CONFIG.get("EXECUTION_ENABLED", False)),
        fx_execution_enabled=lambda: bool(CONFIG.get("FX_FACTOR_EXECUTION_ENABLED", False)),
        mt5_execution_enabled=lambda: bool(CONFIG.get("MT5_EXECUTION_ENABLED", True)),
        get_account=_get_account,
        get_positions=_get_positions,
        get_symbol_info=_get_symbol_info,
        get_live_price=_get_live_price,
        load_bars=_load_bars,
        risk_check=_risk_check,
        guardian_check=_guardian,
        mt5_execute=_mt5_execute,
        close_position=_close_position,
    )


__all__ = [
    "FxExecutionDeps",
    "assert_fx_execution_allowed",
    "close_fx_symbol_position",
    "default_fx_execution_deps",
    "execute_fx_decision",
    "fx_decision_to_execution_dict",
    "sl_tp_from_atr",
]
