"""ASE standalone execution bridge → risk_engine → broker primitives (demo only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from athena_ase.contracts import ASESignal
from athena_ase.execution.journal import append_execution_outcome, append_trade_signals
from athena_ase.gates.demo_only import assert_demo
from athena_ase.instruments import instrument_by_symbol

log = logging.getLogger("ase.execution.bridge")

_TYPE_BY_FAMILY = {
    "forex": "forex",
    "crypto": "crypto",
    "commodity": "commodity",
    "equity": "stock",
    "index_etf": "etf",
}


@dataclass
class ASEExecutionDeps:
    """Injectable runtime dependencies for tests (no live broker contact)."""

    get_executor_mode: Callable[[], str] = field(
        default_factory=lambda: (lambda: "paper")
    )
    get_mt5_trade_mode: Callable[[], int | None] = field(
        default_factory=lambda: (lambda: 0)
    )
    get_bybit_base_url: Callable[[], str | None] = field(
        default_factory=lambda: (lambda: "https://api-testnet.bybit.com")
    )
    get_kill_switch: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    get_account: Callable[[str], dict[str, Any]] = field(
        default_factory=lambda: (lambda _t: {"balance": 10000.0, "equity": 10000.0})
    )
    get_positions: Callable[[str], tuple[list, dict | None]] = field(
        default_factory=lambda: (lambda _t: ([], None))
    )
    get_symbol_info: Callable[[str, dict], dict | None] = field(
        default_factory=lambda: (lambda _s, _p: None)
    )
    guardian_check: Callable[[dict, list, dict, dict | None], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _s, _p, _a, _r: (True, "OK"))
    )
    risk_check: Callable[..., Any] = field(default_factory=lambda: (lambda **_kw: None))
    route_executor: Callable[[dict], str] = field(
        default_factory=lambda: (lambda sig: "mt5" if sig.get("type") != "crypto" else "bybit")
    )
    mt5_execute: Callable[[dict, Any], dict] = field(
        default_factory=lambda: (lambda _s, _a: {"success": False, "error": "not_configured"})
    )
    bybit_execute: Callable[[dict, Any], dict] = field(
        default_factory=lambda: (lambda _s, _a: {"success": False, "error": "not_configured"})
    )
    close_position: Callable[[dict], dict] = field(
        default_factory=lambda: (lambda _p: {"success": False, "error": "not_configured"})
    )
    candle_freshness_ok: Callable[[ASESignal, dict], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _s, _p: (True, "ok"))
    )


def ase_signal_to_execution_dict(
    signal: ASESignal,
    pair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inst = instrument_by_symbol(signal.instrument)
    pair_meta = dict(pair or {})
    display = pair_meta.get("display") or (inst.display if inst else signal.instrument)
    asset_type = pair_meta.get("type") or _TYPE_BY_FAMILY.get(signal.modelFamily, "forex")
    rr = abs(signal.tp1 - signal.entryReference) / max(
        abs(signal.entryReference - signal.sl), 1e-9
    )
    payload = signal.to_execution_dict(style=signal.horizon)
    payload.update(
        {
            "pair": display,
            "symbol": signal.instrument,
            "direction": signal.direction,
            "price": signal.entryReference,
            "sl": signal.sl,
            "tp": signal.tp1,
            "tp2": signal.tp2,
            "type": asset_type,
            "timestamp": signal.decisionTimeMs or int(time.time() * 1000),
            "confluenceScore": signal.signalStrength,
            "scoreNorm": signal.scoreNorm,
            "engineATradeEnabled": True,
            "aseExecution": True,
            "decisionStatus": signal.decisionStatus,
            "maxHoldBars": signal.maxHoldBars,
            "rr1": rr,
            "horizon": signal.horizon,
            "source": pair_meta.get("source", "mt5" if asset_type != "crypto" else "bybit"),
        }
    )
    return payload


def _rr_floor_ok(signal: ASESignal) -> bool:
    sl_dist = abs(signal.entryReference - signal.sl)
    tp_dist = abs(signal.tp1 - signal.entryReference)
    if sl_dist <= 0:
        return False
    return tp_dist >= 0.6 * sl_dist


def execute_trade_signal(
    signal: ASESignal,
    *,
    pair: dict[str, Any] | None = None,
    deps: ASEExecutionDeps | None = None,
    write_journal: bool = True,
) -> dict[str, Any]:
    """Demo-only TRADE execution: gate → guardian → risk_check → executor."""
    deps = deps or ASEExecutionDeps()
    if signal.decisionStatus != "TRADE":
        return {"executed": False, "reason": "not_trade_status"}

    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        return {"executed": False, "reason": f"demo_gate:{gate.reason}", "gate": gate.to_dict()}

    if not _rr_floor_ok(signal):
        return {"executed": False, "reason": "rr_floor"}

    exec_dict = ase_signal_to_execution_dict(signal, pair)
    fresh_ok, fresh_reason = deps.candle_freshness_ok(signal, exec_dict)
    if not fresh_ok:
        return {"executed": False, "reason": f"freshness:{fresh_reason}"}

    if write_journal:
        append_trade_signals([signal])

    venue = deps.route_executor(exec_dict)
    positions, positions_raw = deps.get_positions(venue)
    account = deps.get_account(venue)

    ok_guard, guard_reason = deps.guardian_check(exec_dict, positions, account, positions_raw)
    if not ok_guard:
        if write_journal:
            append_execution_outcome(
                instrument=signal.instrument,
                decision_time_ms=signal.decisionTimeMs,
                status="rejected",
                detail={"stage": "guardian", "reason": guard_reason},
            )
        return {"executed": False, "reason": f"guardian:{guard_reason}"}

    approval = deps.risk_check(
        signal=exec_dict,
        account_balance=float(account.get("balance", 0)),
        account_equity=float(account.get("equity", 0)),
        open_positions=positions,
        symbol_info=deps.get_symbol_info(exec_dict.get("symbol", ""), exec_dict),
        kill_switch=deps.get_kill_switch(),
        execution_context="ase_bridge",
    )
    if approval is None or not getattr(approval, "approved", False):
        reason = getattr(approval, "reason", "risk_rejected") if approval else "risk_rejected"
        if write_journal:
            append_execution_outcome(
                instrument=signal.instrument,
                decision_time_ms=signal.decisionTimeMs,
                status="rejected",
                detail={"stage": "risk_engine", "reason": reason},
            )
        return {"executed": False, "reason": f"risk:{reason}"}

    if venue == "bybit":
        result = deps.bybit_execute(exec_dict, approval)
    else:
        result = deps.mt5_execute(exec_dict, approval)

    success = bool(result.get("success"))
    status = "filled" if success else "failed"
    if write_journal:
        append_execution_outcome(
            instrument=signal.instrument,
            decision_time_ms=signal.decisionTimeMs,
            status=status,
            detail=result,
            order_id=str(result.get("orderId") or result.get("order_id") or ""),
        )
    return {
        "executed": success,
        "reason": "ok" if success else str(result.get("error", "executor_failed")),
        "venue": venue,
        "result": result,
        "tp2": signal.tp2,
        "approval_volume": getattr(approval, "volume", None),
    }


def enforce_time_stops(
    open_positions: list[dict[str, Any]],
    *,
    deps: ASEExecutionDeps | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Close ASE-owned positions older than maxHoldBars (bar-duration heuristic)."""
    deps = deps or ASEExecutionDeps()
    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        return []

    now = now_ms or int(time.time() * 1000)
    closed: list[dict[str, Any]] = []
    for pos in open_positions:
        if not pos.get("aseExecution") and pos.get("engine") != "ASE":
            continue
        opened_ms = int(pos.get("openedAtMs") or pos.get("timestamp") or 0)
        max_hold = int(pos.get("maxHoldBars") or 0)
        horizon = str(pos.get("horizon") or "intraday")
        if opened_ms <= 0 or max_hold <= 0:
            continue
        bar_ms = 3_600_000 if horizon == "intraday" else 86_400_000
        if now - opened_ms < max_hold * bar_ms:
            continue
        result = deps.close_position(pos)
        closed.append({"position": pos, "result": result})
    return closed


def default_execution_deps() -> ASEExecutionDeps:
    """Production deps wired to repo risk/executor/guardian modules."""
    from config import CONFIG

    def _mode() -> str:
        return str(CONFIG.get("EXECUTOR_MODE", "paper")).lower()

    def _mt5_mode() -> int | None:
        try:
            import MetaTrader5 as mt5

            info = mt5.account_info()
            return int(info.trade_mode) if info else None
        except Exception:
            return None

    def _bybit_url() -> str | None:
        return str(CONFIG.get("BYBIT_BASE_URL") or CONFIG.get("BYBIT_API_BASE") or "")

    def _risk_check(**kwargs):
        from risk_engine import risk_check as _risk_check

        return _risk_check(**kwargs)

    def _guardian(signal, positions, account, positions_raw):
        from guardian import pre_trade_check

        return pre_trade_check(signal, positions, account, positions_raw)

    def _mt5_execute(signal, approval):
        from mt5_executor import mt5_execute as _mt5_execute

        return _mt5_execute(signal, approval)

    def _bybit_execute(signal, approval):
        from bybit_executor import bybit_execute as _bybit_execute

        return _bybit_execute(signal, approval)

    return ASEExecutionDeps(
        get_executor_mode=_mode,
        get_mt5_trade_mode=_mt5_mode,
        get_bybit_base_url=_bybit_url,
        get_kill_switch=lambda: bool(CONFIG.get("KILL_SWITCH", False)),
        risk_check=_risk_check,
        guardian_check=_guardian,
        mt5_execute=_mt5_execute,
        bybit_execute=_bybit_execute,
    )
