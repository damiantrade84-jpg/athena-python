"""ASE standalone execution bridge → risk_engine → broker primitives (demo only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
            # risk_engine expects an ISO-8601 string (legacy Engine A contract)
            "timestamp": datetime.fromtimestamp(
                (signal.decisionTimeMs or int(time.time() * 1000)) / 1000.0,
                tz=timezone.utc,
            ).isoformat(),
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


def _ptis_freshness_evidence(
    signal: ASESignal, exec_dict: dict[str, Any]
) -> tuple[bool, str]:
    """Attach real PTIS bar-freshness diagnostics so risk_engine can evaluate staleness.

    PTIS price rows stamp ``value_time`` at bar close; the canonical diagnostic
    buckets by bar open, so timestamps are shifted back one bar before
    classification. Fails closed: no evidence → no order.
    """
    try:
        from athena_app.services.market_state import candle_freshness_diagnostic
        from athena_ase.data.ptis import PTISStore, default_ptis_root
        from athena_ase.horizon import HORIZONS
        from athena_ase.signals.common import load_bar_series

        horizon = signal.horizon if signal.horizon in HORIZONS else "intraday"
        tf = HORIZONS[horizon].tf
        tf_s = {"H1": 3600, "D1": 86400}.get(tf, 3600)
        now_ms = int(time.time() * 1000)
        store = PTISStore(default_ptis_root())
        series = load_bar_series(
            store, signal.instrument, horizon, now_ms - 30 * 86_400_000, now_ms
        )
        if series is None or len(series.value_time) == 0:
            return False, "no_ptis_bars"
        pair = {
            "symbol": exec_dict.get("symbol") or signal.instrument,
            "display": exec_dict.get("pair") or signal.instrument,
            "type": exec_dict.get("type") or "forex",
            "source": exec_dict.get("source") or "mt5",
        }
        candles = [{"time": int(t) // 1000 - tf_s} for t in series.value_time[-5:]]
        diag = candle_freshness_diagnostic(pair, tf, candles)
        exec_dict["candleFreshness"] = {tf: dict(diag)}
        severity = str(diag.get("stalenessSeverity") or "")
        if severity == "fresh":
            exec_dict["candleConsistency"] = {tf: {"status": "OK"}}
            return True, severity
        if severity == "stale_1_bucket":
            # PTIS stores confirmed bars only; one-bucket lag is the intended policy.
            exec_dict["candleConsistency"] = {tf: {"status": "CONFIRMED_ONLY_OK"}}
            return True, severity
        # Multi-bucket / missing / error severities fail closed — no trade bridge.
        exec_dict["candleConsistency"] = {
            tf: {"status": "ERROR_STALE", "severity": severity or "unclassified"}
        }
        return False, f"stale_ptis:{severity or 'unclassified'}"
    except Exception as exc:
        log.warning("ASE freshness evidence failed for %s: %s", signal.instrument, exc)
        return False, f"evidence_error:{exc}"


def _result_order_id(result: dict[str, Any]) -> str:
    """Broker order/position id from an executor result.

    mt5_execute returns ``ticket`` (position ticket) and bybit_execute returns
    ``ticket`` (order id); the previous orderId/order_id lookup matched neither,
    leaving every journal fill without a broker reference.
    """
    for key in ("orderId", "order_id", "ticket"):
        val = result.get(key)
        if val:
            return str(val)
    legs = result.get("legs")
    if isinstance(legs, list) and legs:
        val = legs[0].get("ticket") if isinstance(legs[0], dict) else None
        if val:
            return str(val)
    return ""


def _rr_floor_ok(signal: ASESignal) -> bool:
    sl_dist = abs(signal.entryReference - signal.sl)
    tp_dist = abs(signal.tp1 - signal.entryReference)
    if sl_dist <= 0:
        return False
    return tp_dist >= 0.6 * sl_dist


def _notify_fill_opened(
    signal: ASESignal, exec_dict: dict[str, Any], venue: str
) -> None:
    """Fire-and-forget Telegram alert for a demo fill (manual-mirror aid).

    Must never affect execution results or journaling; notify_trade_opened is a
    no-op when Telegram is disabled and queues delivery asynchronously.
    """
    try:
        from telegram_notify import notify_trade_opened

        notify_trade_opened(
            pair=str(exec_dict.get("pair") or signal.instrument),
            direction=signal.direction,
            entry_price=float(signal.entryReference),
            stop_loss=float(signal.sl),
            take_profit=float(signal.tp1),
            style=signal.horizon,
            engine="ASE",
            exchange=venue,
        )
    except Exception as exc:
        log.warning("ASE fill notification failed for %s: %s", signal.instrument, exc)


def execute_trade_signal(
    signal: ASESignal,
    *,
    pair: dict[str, Any] | None = None,
    deps: ASEExecutionDeps | None = None,
    write_journal: bool = True,
    journal_outcomes: bool | None = None,
) -> dict[str, Any]:
    """Demo-only TRADE execution: gate → guardian → risk_check → executor.

    ``write_journal`` appends the signal row; ``journal_outcomes`` (defaults to
    ``write_journal``) records rejection/fill outcomes on the existing row, so
    callers that already journal signals (the scan) still see why orders died.
    """
    deps = deps or ASEExecutionDeps()
    log_outcomes = write_journal if journal_outcomes is None else journal_outcomes

    def _reject(stage: str, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if log_outcomes:
            try:
                append_execution_outcome(
                    instrument=signal.instrument,
                    decision_time_ms=signal.decisionTimeMs,
                    status="rejected",
                    detail={"stage": stage, "reason": reason},
                )
            except Exception as exc:
                log.warning("ASE outcome journal failed for %s: %s", signal.instrument, exc)
        out = {"executed": False, "reason": f"{stage}:{reason}" if stage else reason}
        if extra:
            out.update(extra)
        return out

    if signal.decisionStatus != "TRADE":
        return {"executed": False, "reason": "not_trade_status"}

    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        return _reject("demo_gate", gate.reason, {"gate": gate.to_dict()})

    if not _rr_floor_ok(signal):
        return _reject("", "rr_floor")

    exec_dict = ase_signal_to_execution_dict(signal, pair)
    fresh_ok, fresh_reason = deps.candle_freshness_ok(signal, exec_dict)
    if not fresh_ok:
        return _reject("freshness", fresh_reason)

    if write_journal:
        append_trade_signals([signal])

    venue = deps.route_executor(exec_dict)
    if venue == "bybit":
        # The demo gate skips the Bybit URL check when the URL is unset; an
        # order routed to Bybit must positively prove testnet before send.
        bybit_url = str(deps.get_bybit_base_url() or "")
        if not bybit_url.rstrip("/").endswith("api-testnet.bybit.com"):
            return _reject("venue", "bybit_url_not_testnet")
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
        symbol_info=deps.get_symbol_info(exec_dict.get("symbol", ""), exec_dict),
        kill_switch=deps.get_kill_switch(),
        execution_context="ase_bridge",
    )
    if approval is None or not getattr(approval, "approved", False):
        reason = getattr(approval, "reason", "risk_rejected") if approval else "risk_rejected"
        return _reject("risk", reason)

    if venue == "bybit":
        result = deps.bybit_execute(exec_dict, approval)
    else:
        result = deps.mt5_execute(exec_dict, approval)

    success = bool(result.get("success"))
    status = "filled" if success else "failed"
    if log_outcomes:
        append_execution_outcome(
            instrument=signal.instrument,
            decision_time_ms=signal.decisionTimeMs,
            status=status,
            detail=result,
            order_id=_result_order_id(result),
        )
    if success:
        _notify_fill_opened(signal, exec_dict, venue)
    return {
        "executed": success,
        "reason": "ok" if success else str(result.get("error", "executor_failed")),
        "venue": venue,
        "result": result,
        "tp2": signal.tp2,
        "approval_volume": getattr(approval, "volume", None),
    }


def _compact(text: Any) -> str:
    return str(text or "").replace("/", "").replace(" ", "").upper()


# Fill must occur shortly after its decision; used to match broker positions
# back to journal fill rows when the broker cannot carry ASE metadata.
_FILL_MATCH_WINDOW_MS = 2 * 3_600_000


def _ase_fill_index() -> list[dict[str, Any]]:
    """ASE-owned fills from the trade journal (broker positions carry no ASE tags).

    Returns rows with compacted display/symbol keys, decision time, horizon and
    maxHoldBars so open broker positions can be attributed to ASE.
    """
    try:
        from athena_ase.execution.journal import load_trade_journal

        df = load_trade_journal()
    except Exception as exc:  # pragma: no cover - journal read belt
        log.warning("ASE time-stop journal read failed: %s", exc)
        return []
    if df is None or df.empty or "executionStatus" not in df.columns:
        return []
    fills = df[df["executionStatus"] == "filled"]
    out: list[dict[str, Any]] = []
    for _, row in fills.iterrows():
        symbol = str(row.get("instrument") or "")
        inst = instrument_by_symbol(symbol)
        keys = {_compact(symbol)}
        if inst is not None:
            keys.add(_compact(inst.display))
        out.append(
            {
                "keys": keys,
                "direction": str(row.get("direction") or ""),
                "decisionTimeMs": int(row.get("decisionTimeMs") or 0),
                "maxHoldBars": int(row.get("maxHoldBars") or 0),
                "horizon": str(row.get("horizon") or "intraday"),
                "orderId": str(row.get("orderId") or ""),
            }
        )
    return out


def _match_fill(pos: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any] | None:
    ticket = str(pos.get("ticket") or "")
    pos_keys = {_compact(pos.get("pair")), _compact(pos.get("symbol"))} - {""}
    pos_dir = str(pos.get("direction") or "")
    open_ms = int(pos.get("open_time") or 0) * 1000
    best: dict[str, Any] | None = None
    for fill in fills:
        if ticket and fill["orderId"] and ticket == fill["orderId"]:
            return fill
        if not pos_keys & fill["keys"] or pos_dir != fill["direction"]:
            continue
        if open_ms > 0 and fill["decisionTimeMs"] > 0:
            delta = open_ms - fill["decisionTimeMs"]
            if -60_000 <= delta <= _FILL_MATCH_WINDOW_MS and (
                best is None or fill["decisionTimeMs"] > best["decisionTimeMs"]
            ):
                best = fill
    return best


def enforce_time_stops(
    open_positions: list[dict[str, Any]],
    *,
    deps: ASEExecutionDeps | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Close ASE-owned positions older than maxHoldBars (bar-duration heuristic).

    Broker positions carry no ASE metadata, so ownership and hold parameters
    are resolved from the ASE trade journal (ticket match first, then
    pair+direction+open-time proximity). Explicitly tagged positions (tests,
    future executors) keep the original direct path.
    """
    deps = deps or ASEExecutionDeps()
    gate = assert_demo(
        executor_mode=deps.get_executor_mode(),
        mt5_trade_mode=deps.get_mt5_trade_mode(),
        bybit_base_url=deps.get_bybit_base_url(),
    )
    if not gate.ok:
        return []

    now = now_ms or int(time.time() * 1000)
    fills: list[dict[str, Any]] | None = None
    closed: list[dict[str, Any]] = []
    for pos in open_positions:
        tagged = bool(pos.get("aseExecution")) or pos.get("engine") == "ASE"
        opened_ms = int(pos.get("openedAtMs") or pos.get("timestamp") or 0)
        max_hold = int(pos.get("maxHoldBars") or 0)
        horizon = str(pos.get("horizon") or "intraday")
        if not tagged:
            if fills is None:
                fills = _ase_fill_index()
            fill = _match_fill(pos, fills)
            if fill is None:
                continue
            opened_ms = opened_ms or int(pos.get("open_time") or 0) * 1000 or fill["decisionTimeMs"]
            max_hold = max_hold or fill["maxHoldBars"]
            horizon = fill["horizon"] or horizon
        if opened_ms <= 0 or max_hold <= 0:
            continue
        bar_ms = 3_600_000 if horizon == "intraday" else 86_400_000
        if now - opened_ms < max_hold * bar_ms:
            continue
        result = deps.close_position(pos)
        closed.append({"position": pos, "result": result})
        log.info(
            "ASE time stop closed %s %s (held > %d %s bars): %s",
            pos.get("pair") or pos.get("symbol"),
            pos.get("direction"),
            max_hold,
            horizon,
            result.get("success"),
        )
    return closed


def default_execution_deps() -> ASEExecutionDeps:
    """Production deps wired to repo risk/executor/guardian modules (demo-only)."""
    from config import CONFIG

    gate = assert_demo(config=CONFIG)
    if not gate.ok:
        raise RuntimeError(f"ASE default_execution_deps blocked: {gate.reason}")

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

    def _get_account(venue: str) -> dict[str, Any]:
        if venue == "bybit":
            from bybit_executor import bybit_get_account

            acct = bybit_get_account()
        else:
            from mt5_executor import mt5_get_account

            acct = mt5_get_account()
        if not acct or (isinstance(acct, dict) and acct.get("error")):
            detail = acct.get("detail") if isinstance(acct, dict) else ""
            raise RuntimeError(f"{venue.upper()}_ACCOUNT_UNAVAILABLE: {detail}")
        return acct

    def _get_positions(venue: str) -> tuple[list, dict | None]:
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

    def _close_position(pos: dict) -> dict:
        venue = str(pos.get("venue") or ("bybit" if _compact(pos.get("symbol")).endswith("USDT") else "mt5"))
        if venue == "bybit":
            from bybit_executor import bybit_close_position

            return bybit_close_position(
                str(pos.get("pair") or pos.get("symbol") or ""),
                str(pos.get("direction") or ""),
                float(pos.get("volume") or pos.get("contracts") or 0.0),
            )
        from mt5_executor import mt5_close_position

        ticket = pos.get("ticket")
        if not ticket:
            return {"success": False, "error": "missing_ticket"}
        return mt5_close_position(int(ticket))

    def _get_symbol_info(symbol: str, exec_dict: dict) -> dict | None:
        display = str(exec_dict.get("pair") or symbol)
        if str(exec_dict.get("type") or "").lower() == "crypto":
            from bybit_executor import bybit_get_symbol_info

            info = bybit_get_symbol_info(display)
        else:
            from mt5_executor import mt5_get_symbol_info

            info = mt5_get_symbol_info(display)
        if isinstance(info, dict) and info.get("error"):
            return None
        return info

    return ASEExecutionDeps(
        get_executor_mode=_mode,
        get_mt5_trade_mode=_mt5_mode,
        get_bybit_base_url=_bybit_url,
        get_kill_switch=lambda: bool(CONFIG.get("KILL_SWITCH", False)),
        get_account=_get_account,
        get_positions=_get_positions,
        get_symbol_info=_get_symbol_info,
        risk_check=_risk_check,
        guardian_check=_guardian,
        mt5_execute=_mt5_execute,
        bybit_execute=_bybit_execute,
        close_position=_close_position,
        candle_freshness_ok=_ptis_freshness_evidence,
    )
