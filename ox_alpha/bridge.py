"""OX Alpha standalone demo-execution bridge.

Routes a live OX Alpha signal through the SAME safety primitives every other
path uses — demo gate -> rr floor -> freshness -> guardian -> risk_check ->
broker — by CALLING them (never bypassing, never duplicating). Venue routing
follows the platform convention: crypto executes on Bybit, everything else on
MT5. Dependency-injected so the gate sequence is testable with no broker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from athena_ase.gates.demo_only import assert_demo  # shared generic demo gate
from ox_alpha import journal

log = logging.getLogger("ox_alpha.bridge")


@dataclass
class OxExecDeps:
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
    get_symbol_info: Callable[[str, dict], dict | None] = field(
        default_factory=lambda: (lambda _s, _p: None)
    )
    guardian_check: Callable[[dict, list, dict, dict | None], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _s, _p, _a, _r: (True, "OK"))
    )
    risk_check: Callable[..., Any] = field(default_factory=lambda: (lambda **_k: None))
    mt5_execute: Callable[[dict, Any], dict] = field(
        default_factory=lambda: (lambda _s, _a: {"success": False, "error": "not_configured"})
    )
    bybit_execute: Callable[[dict, Any], dict] = field(
        default_factory=lambda: (lambda _s, _a: {"success": False, "error": "not_configured"})
    )
    # Fail CLOSED by default: no order may fire until a real freshness proof is wired.
    candle_freshness_ok: Callable[[dict, dict], tuple[bool, str]] = field(
        default_factory=lambda: (lambda _p, _d: (False, "freshness_not_wired"))
    )


def signal_to_exec_dict(signal: dict[str, Any]) -> dict[str, Any]:
    """Shape an OX Alpha signal into the dict risk_engine/executor expect."""
    direction = str(signal.get("direction") or "").upper()
    return {
        "pair": signal.get("display") or signal.get("symbol"),
        "symbol": signal.get("symbol") or signal.get("display"),
        "direction": direction,
        "price": signal.get("entryRef"),
        "sl": signal.get("sl"),
        "tp": signal.get("tp1"),
        "tp1": signal.get("tp1"),
        "tp2": signal.get("tp2"),
        "type": signal.get("type"),
        "timestamp": datetime.fromtimestamp(
            float(signal.get("timestamp") or datetime.now(timezone.utc).timestamp()),
            tz=timezone.utc,
        ).isoformat(),
        "confluenceScore": float(signal.get("score") or 0.0),
        "scoreNorm": round(min(1.0, max(0.0, float(signal.get("score") or 0.0) / 100.0)), 4),
        "engine": "OX_ALPHA",
        "oxAlphaExecution": True,
        "source": signal.get("source"),
        "reason": f"ox_alpha:{signal.get('playType') or 'setup'}",
        "rr1": signal.get("rr1"),
        "atr": signal.get("atr"),
        "atrTf": signal.get("atrTf"),
    }


def _venue_for(signal: dict[str, Any]) -> str:
    return "bybit" if str(signal.get("type") or "").lower() == "crypto" else "mt5"


def _rr_floor_ok(exec_dict: dict[str, Any], min_rr: float) -> bool:
    try:
        entry = float(exec_dict.get("price"))
        sl = float(exec_dict.get("sl"))
        tp = float(exec_dict.get("tp1"))
    except (TypeError, ValueError):
        return False
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    return sl_dist > 0 and tp_dist >= min_rr * sl_dist


def open_trade(
    signal: dict[str, Any],
    *,
    deps: OxExecDeps | None = None,
    min_rr: float = 1.4,
    write_journal: bool = True,
) -> dict[str, Any]:
    """Demo-only OPEN through the full shared gate sequence."""
    deps = deps or OxExecDeps()

    def _reject(stage: str, reason: str, extra: dict | None = None) -> dict[str, Any]:
        if write_journal:
            journal.append({
                "kind": "open_rejected",
                "engine": "OX_ALPHA",
                "pair": signal.get("display"),
                "stage": stage,
                "reason": reason,
            })
        out = {"executed": False, "reason": f"{stage}:{reason}" if stage else reason}
        if extra:
            out.update(extra)
        return out

    if str(signal.get("decision") or "").upper() != "TRADE":
        return _reject("", "not_a_trade_decision")
    if str(signal.get("direction") or "").upper() not in {"LONG", "SHORT"}:
        return _reject("", "direction_missing")
    readiness = str(signal.get("entryReadiness") or "").upper()
    if readiness != "READY":
        return _reject("readiness", readiness or "NOT_STAMPED")

    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        return _reject("demo_gate", gate.reason, {"gate": gate.to_dict()})

    exec_dict = signal_to_exec_dict(signal)
    if not _rr_floor_ok(exec_dict, min_rr):
        return _reject("", "rr_floor")

    fresh_ok, fresh_reason = deps.candle_freshness_ok(signal, exec_dict)
    if not fresh_ok:
        return _reject("freshness", fresh_reason)

    live = exec_dict.get("liveQuote") if isinstance(exec_dict.get("liveQuote"), dict) else {}
    try:
        bid = float(live.get("bid"))
        ask = float(live.get("ask"))
    except (TypeError, ValueError):
        bid = ask = 0.0
    direction = str(exec_dict.get("direction") or "").upper()
    side_px = ask if direction == "LONG" else bid if direction == "SHORT" else 0.0
    if side_px > 0:
        try:
            old_px = float(exec_dict.get("price"))
            sl = float(exec_dict.get("sl"))
            tp = float(exec_dict.get("tp1"))
            atr = float(signal.get("atr") or 0.0)
        except (TypeError, ValueError):
            return _reject("quote", "levels_unusable")
        delta = side_px - old_px
        if atr > 0 and abs(delta) > 0.5 * atr:
            return _reject("quote", "quote_drift")
        exec_dict["price"] = side_px
        exec_dict["sl"] = sl + delta
        exec_dict["tp"] = tp + delta
        exec_dict["tp1"] = tp + delta
        if exec_dict.get("tp2") is not None:
            try:
                exec_dict["tp2"] = float(exec_dict["tp2"]) + delta
            except (TypeError, ValueError):
                pass
        if not _rr_floor_ok(exec_dict, min_rr):
            return _reject("", "rr_floor")

    venue = _venue_for(signal)
    if write_journal:
        journal.append({"kind": "open_signal", "engine": "OX_ALPHA", "venue": venue, "signal": exec_dict})

    positions, positions_raw = deps.get_positions(venue)
    account = deps.get_account(venue)

    ok_guard, guard_reason = deps.guardian_check(exec_dict, positions, account, positions_raw)
    if not ok_guard:
        return _reject("guardian", guard_reason)

    approval = deps.risk_check(
        signal=exec_dict,
        account_balance=float(account.get("balance", 0)),
        account_equity=float(account.get("equity", 0)),
        open_positions=positions,
        symbol_info=deps.get_symbol_info(str(exec_dict.get("pair")), exec_dict),
        kill_switch=deps.get_kill_switch(),
        execution_context="ox_alpha_bridge",
    )
    if approval is None or not getattr(approval, "approved", False):
        return _reject(
            "risk",
            getattr(approval, "reason", "risk_rejected") if approval else "risk_rejected",
        )

    if venue == "bybit":
        result = deps.bybit_execute(exec_dict, approval)
    else:
        result = deps.mt5_execute(exec_dict, approval)
    success = bool(result.get("success"))
    if write_journal:
        journal.append({
            "kind": "open_outcome",
            "engine": "OX_ALPHA",
            "venue": venue,
            "status": "filled" if success else "failed",
            "result": result,
        })
    return {
        "executed": success,
        "reason": "ok" if success else str(result.get("error", "executor_failed")),
        "venue": venue,
        "result": result,
        "approval_volume": getattr(approval, "volume", None),
    }


def exec_data_gate(signal: dict[str, Any], exec_dict: dict[str, Any]) -> tuple[bool, str]:
    """Execute-time data proof: fresh intraday bars AND an open market."""
    from ox_alpha import feed

    fresh_ok, fresh_reason = feed.freshness_ok_for_exec(signal, exec_dict)
    if not fresh_ok:
        return False, fresh_reason
    from ox_alpha import runtime
    from ox_alpha.config import load_settings

    open_state = runtime.market_open_state(signal)
    if not open_state.get("open"):
        return False, f"market_closed:{open_state.get('reason')}"
    try:
        from config import CONFIG as _cfg

        settings = load_settings(_cfg)
    except Exception:
        settings = load_settings(None)
    quote = feed.load_quote(signal, max_age_sec=settings.quote_max_age_sec)
    if not quote:
        return False, "quote_unavailable"
    exec_dict["liveQuote"] = {
        k: quote.get(k) for k in ("bid", "ask", "ts", "source", "ageSec") if k in quote
    }
    exec_dict["quoteTimestamp"] = quote.get("ts")
    exec_dict["quoteAgeSec"] = quote.get("ageSec")
    return True, "ok"


def default_deps() -> OxExecDeps:
    """Production wiring to the real repo safety/broker modules (lazy imports).

    Freshness is a real intraday-bar proof plus a broker-truth market-open
    check (ox_alpha.bridge.exec_data_gate); it fails closed on missing/stale
    data or a closed market, so no demo order fires without both.
    """
    from config import CONFIG

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

    def _guardian(signal_exec, positions, account, positions_raw):
        from guardian import pre_trade_check

        return pre_trade_check(signal_exec, positions, account, positions_raw)

    def _mt5_execute(signal_exec, approval):
        from mt5_executor import mt5_execute as me

        return me(signal_exec, approval)

    def _bybit_execute(signal_exec, approval):
        from bybit_executor import bybit_execute as be

        return be(signal_exec, approval)

    def _account(venue):
        if venue == "bybit":
            from bybit_executor import bybit_get_account

            acct = bybit_get_account()
        else:
            from mt5_executor import mt5_get_account

            acct = mt5_get_account()
        if not acct or (isinstance(acct, dict) and acct.get("error")):
            raise RuntimeError(f"{venue.upper()}_ACCOUNT_UNAVAILABLE")
        return acct

    def _positions(venue):
        if venue == "bybit":
            from bybit_executor import bybit_get_positions

            res = bybit_get_positions()
        else:
            from mt5_executor import mt5_get_positions

            res = mt5_get_positions()
        if isinstance(res, dict):
            if res.get("error"):
                raise RuntimeError(f"{venue.upper()}_POSITIONS_UNAVAILABLE: {res.get('detail')}")
            return list(res.get("positions") or []), res
        return list(res or []), None

    def _symbol_info(display, exec_dict):
        venue = _venue_for(exec_dict)
        if venue == "bybit":
            from bybit_executor import bybit_get_symbol_info

            info = bybit_get_symbol_info(str(display))
        else:
            from mt5_executor import mt5_get_symbol_info

            info = mt5_get_symbol_info(str(display))
        return None if (isinstance(info, dict) and info.get("error")) else info

    return OxExecDeps(
        get_executor_mode=_mode,
        get_mt5_trade_mode=_mt5_mode,
        get_bybit_base_url=lambda: str(
            CONFIG.get("BYBIT_BASE_URL") or CONFIG.get("BYBIT_API_BASE") or ""
        ),
        get_kill_switch=lambda: bool(CONFIG.get("KILL_SWITCH", False)),
        get_account=_account,
        get_positions=_positions,
        get_symbol_info=_symbol_info,
        guardian_check=_guardian,
        risk_check=_risk_check,
        mt5_execute=_mt5_execute,
        bybit_execute=_bybit_execute,
        candle_freshness_ok=exec_data_gate,
    )
