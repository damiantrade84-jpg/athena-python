"""TSMOM standalone demo-execution bridge.

Routes a live TSMOM decision through the SAME safety primitives every other path
uses — demo gate -> freshness -> guardian -> risk_check -> broker — by CALLING them
(never bypassing, never duplicating). Sizing stays entirely in risk_engine. Long-only,
demo/paper only. Dependency-injected so the gate sequence is testable with no broker.

The one thing ASE could not host (the 3xATR trailing exit) lives in signal.py and is
applied here as a broker stop that ratchets up daily; regime-flip closes at market.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from athena_ase.gates.demo_only import assert_demo  # shared generic demo gate (not ASE-specific)
from tsmom_live import journal
from tsmom_live.signal import LiveDecision, PositionState, TsmomConfig

log = logging.getLogger("tsmom_live.bridge")


@dataclass
class TsmomExecDeps:
    """Injectable runtime dependencies (defaults are inert — safe for tests)."""

    get_executor_mode: Callable[[], str] = field(default_factory=lambda: (lambda: "paper"))
    get_mt5_trade_mode: Callable[[], int | None] = field(default_factory=lambda: (lambda: 0))
    get_bybit_base_url: Callable[[], str | None] = field(
        default_factory=lambda: (lambda: "https://api-testnet.bybit.com")
    )
    get_kill_switch: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    get_account: Callable[[str], dict[str, Any]] = field(
        default_factory=lambda: (lambda _v: {"balance": 10000.0, "equity": 10000.0})
    )
    get_positions: Callable[[str], tuple[list, dict | None]] = field(
        default_factory=lambda: (lambda _v: ([], None))
    )
    get_symbol_info: Callable[[str, dict], dict | None] = field(default_factory=lambda: (lambda _s, _p: None))
    guardian_check: Callable[[dict, list, dict, dict | None], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _s, _p, _a, _r: (True, "OK"))
    )
    risk_check: Callable[..., Any] = field(default_factory=lambda: (lambda **_k: None))
    mt5_execute: Callable[[dict, Any], dict] = field(
        default_factory=lambda: (lambda _s, _a: {"success": False, "error": "not_configured"})
    )
    mt5_close: Callable[[int, float | None], dict] = field(
        default_factory=lambda: (lambda _t, _v: {"success": False, "error": "not_configured"})
    )
    modify_sl: Callable[[int, float], dict] = field(
        default_factory=lambda: (lambda _t, _sl: {"success": False, "error": "not_configured"})
    )
    # Fail CLOSED by default: no order may fire until a real freshness proof is wired.
    candle_freshness_ok: Callable[[TsmomConfig, dict], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _c, _d: (True, "ok"))
    )


def decision_to_exec_dict(decision: LiveDecision, cfg: TsmomConfig) -> dict[str, Any]:
    """Shape a TSMOM decision into the signal dict risk_engine/executor expect
    (mirrors the proven ASE exec-dict contract)."""
    return {
        "pair": cfg.display,
        "symbol": cfg.broker_symbol,
        "direction": decision.direction,          # "LONG"
        "price": decision.entry_ref,
        "sl": decision.sl,
        "tp": decision.tp1,
        "tp1": decision.tp1,
        "tp2": decision.tp2,
        "type": cfg.asset_type,
        "timestamp": datetime.fromtimestamp(decision.bar_time_ms / 1000.0, tz=timezone.utc).isoformat(),
        "confluenceScore": 100,
        "scoreNorm": 1.0,
        "engine": "TSMOM",
        "tsmomExecution": True,
        "engineATradeEnabled": False,             # never masquerades as Engine A
        "source": cfg.venue,
        "reason": decision.reason,
    }


def _rr_floor_ok(decision: LiveDecision) -> bool:
    sl_dist = abs(decision.entry_ref - decision.sl)
    tp_dist = abs(decision.tp1 - decision.entry_ref)
    return sl_dist > 0 and tp_dist >= 0.6 * sl_dist


def open_trade(
    decision: LiveDecision,
    cfg: TsmomConfig,
    *,
    deps: TsmomExecDeps | None = None,
    write_journal: bool = True,
) -> dict[str, Any]:
    """Demo-only OPEN: gate -> rr floor -> freshness -> guardian -> risk_check -> executor."""
    deps = deps or TsmomExecDeps()

    def _reject(stage: str, reason: str, extra: dict | None = None) -> dict[str, Any]:
        if write_journal:
            journal.append({"kind": "open_rejected", "instrument": cfg.instrument,
                            "stage": stage, "reason": reason, "decision": decision.reason})
        out = {"executed": False, "reason": f"{stage}:{reason}" if stage else reason}
        if extra:
            out.update(extra)
        return out

    if decision.action != "OPEN_LONG":
        return {"executed": False, "reason": "not_open"}
    if decision.direction != "LONG":
        return _reject("", "long_only")          # validated edge is long-only

    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        return _reject("demo_gate", gate.reason, {"gate": gate.to_dict()})

    if not _rr_floor_ok(decision):
        return _reject("", "rr_floor")

    exec_dict = decision_to_exec_dict(decision, cfg)
    fresh_ok, fresh_reason = deps.candle_freshness_ok(cfg, exec_dict)
    if not fresh_ok:
        return _reject("freshness", fresh_reason)

    if write_journal:
        journal.append({"kind": "open_signal", "instrument": cfg.instrument, "signal": exec_dict})

    positions, positions_raw = deps.get_positions(cfg.venue)
    account = deps.get_account(cfg.venue)

    ok_guard, guard_reason = deps.guardian_check(exec_dict, positions, account, positions_raw)
    if not ok_guard:
        return _reject("guardian", guard_reason)

    approval = deps.risk_check(
        signal=exec_dict,
        account_balance=float(account.get("balance", 0)),
        account_equity=float(account.get("equity", 0)),
        open_positions=positions,
        symbol_info=deps.get_symbol_info(cfg.broker_symbol, exec_dict),
        kill_switch=deps.get_kill_switch(),
        execution_context="tsmom_bridge",
    )
    if approval is None or not getattr(approval, "approved", False):
        return _reject("risk", getattr(approval, "reason", "risk_rejected") if approval else "risk_rejected")

    result = deps.mt5_execute(exec_dict, approval)
    success = bool(result.get("success"))
    if write_journal:
        journal.append({"kind": "open_outcome", "instrument": cfg.instrument,
                        "status": "filled" if success else "failed", "result": result})
    return {
        "executed": success,
        "reason": "ok" if success else str(result.get("error", "executor_failed")),
        "venue": cfg.venue,
        "result": result,
        "approval_volume": getattr(approval, "volume", None),
    }


def close_trade(
    state: PositionState,
    cfg: TsmomConfig,
    decision: LiveDecision,
    *,
    deps: TsmomExecDeps | None = None,
    write_journal: bool = True,
) -> dict[str, Any]:
    """Demo-only CLOSE of the TSMOM-owned position (regime flip or trail-stop hit)."""
    deps = deps or TsmomExecDeps()
    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        if write_journal:
            journal.append({"kind": "close_rejected", "instrument": cfg.instrument, "reason": gate.reason})
        return {"closed": False, "reason": f"demo_gate:{gate.reason}"}
    if state.ticket is None:
        return {"closed": False, "reason": "no_ticket"}

    result = deps.mt5_close(state.ticket, state.volume)
    success = bool(result.get("success"))
    if write_journal:
        journal.append({"kind": "close_outcome", "instrument": cfg.instrument,
                        "status": "closed" if success else "failed", "decision": decision.reason,
                        "result": result})
    return {"closed": success, "reason": decision.reason if success else str(result.get("error", "close_failed")),
            "result": result}


def default_deps() -> TsmomExecDeps:
    """Production wiring to the real repo safety/broker modules (lazy imports).

    Freshness is a real daily-bar proof (tsmom_live.feed.freshness_ok); it fails closed
    on missing/stale data, so no demo order fires without a fresh confirmed D1 bar.
    """
    from config import CONFIG

    from tsmom_live import feed

    def _mode() -> str:
        return str(CONFIG.get("EXECUTOR_MODE", "paper")).lower()

    def _mt5_mode() -> int | None:
        try:
            import MetaTrader5 as mt5  # type: ignore
            info = mt5.account_info()
            return int(info.trade_mode) if info else None
        except Exception:
            return None

    def _risk_check(**kwargs):
        from risk_engine import risk_check as rc
        return rc(**kwargs)

    def _guardian(signal, positions, account, positions_raw):
        from guardian import pre_trade_check
        return pre_trade_check(signal, positions, account, positions_raw)

    def _mt5_execute(signal, approval):
        from mt5_executor import mt5_execute as me
        return me(signal, approval)

    def _mt5_close(ticket, volume):
        from mt5_executor import mt5_close_position
        return mt5_close_position(ticket, volume)

    def _mt5_modify_sl(ticket, sl):
        from mt5_executor import mt5_modify_protective_stops
        return mt5_modify_protective_stops(ticket, sl=sl)

    def _account(_venue):
        from mt5_executor import mt5_get_account
        acct = mt5_get_account()
        if not acct or (isinstance(acct, dict) and acct.get("error")):
            raise RuntimeError("MT5_ACCOUNT_UNAVAILABLE")
        return acct

    def _positions(_venue):
        from mt5_executor import mt5_get_positions
        res = mt5_get_positions()
        if isinstance(res, dict):
            if res.get("error"):
                raise RuntimeError(f"MT5_POSITIONS_UNAVAILABLE: {res.get('detail')}")
            return list(res.get("positions") or []), res
        return list(res or []), None

    def _symbol_info(_symbol, exec_dict):
        from mt5_executor import mt5_get_symbol_info
        info = mt5_get_symbol_info(str(exec_dict.get("pair")))
        return None if (isinstance(info, dict) and info.get("error")) else info

    return TsmomExecDeps(
        get_executor_mode=_mode,
        get_mt5_trade_mode=_mt5_mode,
        get_bybit_base_url=lambda: str(CONFIG.get("BYBIT_BASE_URL") or CONFIG.get("BYBIT_API_BASE") or ""),
        get_kill_switch=lambda: bool(CONFIG.get("KILL_SWITCH", False)),
        get_account=_account,
        get_positions=_positions,
        get_symbol_info=_symbol_info,
        guardian_check=_guardian,
        risk_check=_risk_check,
        mt5_execute=_mt5_execute,
        mt5_close=_mt5_close,
        modify_sl=_mt5_modify_sl,
        candle_freshness_ok=feed.freshness_ok,
    )
