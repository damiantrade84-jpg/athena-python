"""Daily TSMOM runner: load closed D1 bars -> compute decision -> route through the
gated bridge -> persist the trail state. Run once per instrument after each daily close.

`route_decision` is pure routing (no IO) so it is unit-testable with injected deps;
`run_once` adds the feed + state persistence around it."""
from __future__ import annotations

import logging
from typing import Any

from tsmom_live import bridge, feed, state as state_store
from tsmom_live.signal import INSTRUMENTS, LiveDecision, PositionState, TsmomConfig, compute_decision

log = logging.getLogger("tsmom_live.runtime")


def _state_to_dict(entry: float, stop: float, extreme: float, entry_time_ms: int,
                   ticket: Any, volume: Any) -> dict:
    return {"direction": 1, "entry": float(entry), "stop": float(stop), "extreme": float(extreme),
            "entry_time_ms": int(entry_time_ms or 0), "ticket": ticket, "volume": volume}


def _dict_to_state(d: dict) -> PositionState:
    return PositionState(direction=int(d.get("direction", 1)), entry=float(d.get("entry", 0.0)),
                         stop=float(d.get("stop", 0.0)), extreme=float(d.get("extreme", 0.0)),
                         entry_time_ms=int(d.get("entry_time_ms", 0)),
                         ticket=d.get("ticket"), volume=d.get("volume"))


def route_decision(
    decision: LiveDecision,
    position: PositionState | None,
    cfg: TsmomConfig,
    deps: bridge.TsmomExecDeps,
) -> dict[str, Any]:
    """Map a decision + current position to a gated bridge action. Pure (no IO)."""
    if position is None:
        if decision.action == "OPEN_LONG":
            res = bridge.open_trade(decision, cfg, deps=deps)
            if not res.get("executed"):
                return {"status": "open_rejected", "result": res, "state": None}
            result = res.get("result") or {}
            ticket = result.get("orderId") or result.get("order_id") or result.get("ticket")
            new = _state_to_dict(decision.entry_ref, decision.sl, decision.extreme,
                                 decision.bar_time_ms, ticket, res.get("approval_volume"))
            return {"status": "opened", "result": res, "state": new}
        return {"status": "flat", "reason": decision.reason, "state": None}

    # already long
    if decision.action == "CLOSE":
        res = bridge.close_trade(position, cfg, decision, deps=deps)
        closed = bool(res.get("closed"))
        return {"status": "closed" if closed else "close_failed", "result": res,
                "clear": closed, "state": None}

    # HOLD: ratchet the broker stop up to the new trail (modify is only-improves on MT5 side)
    mod = deps.modify_sl(position.ticket, decision.trail_stop) if position.ticket is not None else {"skipped": True}
    new = _state_to_dict(position.entry, decision.trail_stop, decision.extreme,
                         position.entry_time_ms, position.ticket, position.volume)
    return {"status": "hold", "modify": mod, "trail_stop": decision.trail_stop, "state": new}


def status(cfg: TsmomConfig | None = None, *, time_now: float | None = None, df=None) -> dict[str, Any]:
    """Read-only snapshot for the UI panel — current decision, position, freshness.
    Computes the signal but executes NOTHING."""
    cfg = cfg or INSTRUMENTS["gold"]
    if df is None:
        df = feed.load_closed_daily_bars(cfg, time_now=time_now)
    base = {"instrument": cfg.instrument, "display": cfg.display, "brokerSymbol": cfg.broker_symbol,
            "config": {"fast": cfg.fast, "slow": cfg.slow, "atrMult": cfg.atr_mult}}
    if df is None or len(df) == 0:
        return {**base, "hasData": False, "status": "no_bars"}

    raw = state_store.load_state(cfg.instrument)
    position = _dict_to_state(raw) if raw else None
    dec = compute_decision(df, position, cfg, now_ms=int(time_now * 1000) if time_now else None)
    fresh_ok, fresh_reason = feed.freshness_ok(cfg, {}, time_now=time_now)
    return {
        **base,
        "hasData": True,
        "bars": int(len(df)),
        "lastBarTimeMs": dec.bar_time_ms,
        "inPosition": position is not None,
        "position": raw,
        "freshness": {"ok": bool(fresh_ok), "reason": fresh_reason},
        "decision": {
            "action": dec.action, "reason": dec.reason, "direction": dec.direction,
            "entryRef": dec.entry_ref, "sl": dec.sl, "tp1": dec.tp1, "trailStop": dec.trail_stop,
            "atr": dec.atr, "emaFast": dec.ema_fast, "emaSlow": dec.ema_slow,
        },
    }


def run_once(
    cfg: TsmomConfig | None = None,
    deps: bridge.TsmomExecDeps | None = None,
    *,
    time_now: float | None = None,
    df=None,
) -> dict[str, Any]:
    """One daily cycle for one instrument. `df`/`deps` injectable for tests."""
    cfg = cfg or INSTRUMENTS["gold"]
    deps = deps or bridge.default_deps()
    if df is None:
        df = feed.load_closed_daily_bars(cfg, time_now=time_now)
    if df is None or len(df) == 0:
        return {"status": "no_bars", "instrument": cfg.instrument}

    raw = state_store.load_state(cfg.instrument)
    position = _dict_to_state(raw) if raw else None
    decision = compute_decision(df, position, cfg, now_ms=int(time_now * 1000) if time_now else None)

    out = route_decision(decision, position, cfg, deps)

    if out.get("clear"):
        state_store.clear_state(cfg.instrument)
    elif out.get("state"):
        state_store.save_state(cfg.instrument, out["state"])

    out["instrument"] = cfg.instrument
    out["decision"] = {"action": decision.action, "reason": decision.reason,
                       "trail_stop": decision.trail_stop, "bar_time_ms": decision.bar_time_ms}
    return out
