"""Execution routing and the safety boundary around it.

Everything that could put real money at risk passes through `submit`. The rules
are deliberately blunt:

  * Paper is the default and always available. Live requires the config flag
    AND the OPUS_LIVE_CONFIRM environment token - two independent switches, so
    neither a stray config edit nor a stray environment variable arms live
    execution alone.

  * A signal is RE-VALIDATED at submit time. Gate state at scan time is not
    evidence about now: quotes go stale in seconds, and the entire point of
    separating `decision` from `readiness` is lost if the router trusts a
    decision made thirty seconds ago.

  * Synthetic data can never reach a live broker. The demo feed exists to make
    the system explorable without a broker; if that data could route an order,
    it would be a liability rather than a convenience.

  * Portfolio limits (concurrent positions, per-symbol, correlation group,
    daily loss, cooldown) are checked here rather than per-signal, because
    they are properties of the book, not of any one idea.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import opus.config as config
from opus.execution.broker import Broker, OrderResult, PaperBroker, new_order_id
from opus.risk.sizing import correlation_group_of
from opus.types import Decision, Readiness, Signal

_SYNTHETIC_SOURCES = {"SYNTHETIC"}


@dataclass
class RouteDecision:
    allowed: bool
    reason: str
    broker_name: str
    mode: str

    def as_dict(self) -> dict:
        return {
            "allowed": bool(self.allowed), "reason": self.reason,
            "broker": self.broker_name, "mode": self.mode,
        }


def live_enabled() -> tuple[bool, str]:
    """Both switches, reported separately so the UI can say which is missing."""
    mode_live = str(config.load().get("MODE", "paper")).strip().lower() == "live"
    token = bool(os.environ.get("OPUS_LIVE_CONFIRM", "").strip())
    if mode_live and token:
        return True, "live armed (config MODE=live + OPUS_LIVE_CONFIRM)"
    if mode_live and not token:
        return False, "config MODE=live but OPUS_LIVE_CONFIRM is not set"
    if token and not mode_live:
        return False, "OPUS_LIVE_CONFIRM is set but config MODE is not 'live'"
    return False, "paper mode"


def _broker_for(venue: str, want_live: bool) -> Broker:
    if not want_live:
        return PaperBroker()
    mapping = config.load()["EXECUTION"].get("broker_by_venue") or {}
    target = str(mapping.get(venue, "paper")).lower()
    try:
        if target == "mt5":
            from opus.execution.mt5_broker import MT5Broker
            return MT5Broker()
        if target == "bybit":
            from opus.execution.bybit_broker import BybitBroker
            return BybitBroker()
    except Exception:  # noqa: BLE001 - an unavailable live broker falls back to paper
        return PaperBroker()
    return PaperBroker()


def revalidate(signal: Signal, *, now: float | None = None) -> tuple[bool, str]:
    """Re-check the signal at submit time, not at scan time."""
    now = float(now if now is not None else time.time())
    gates_cfg = config.load()["GATES"]

    if signal.decision is not Decision.TRADE:
        return False, f"decision is {signal.decision.value}, not TRADE"
    if signal.readiness is not Readiness.READY:
        return False, f"readiness is {signal.readiness.value}, not READY"

    blocking = signal.blocking_gates
    if blocking:
        return False, "blocking gates: " + ", ".join(g.name for g in blocking)

    quote = signal.quote
    if quote is None:
        return False, "no live quote at submit time"

    age = quote.age_sec(now)
    max_age = float(gates_cfg["max_quote_age_sec"])
    if age > max_age:
        return False, f"quote is {age:.1f}s old at submit (limit {max_age:.0f}s)"

    if quote.spread_pct > float(gates_cfg["max_spread_pct"]):
        return False, (
            f"spread widened to {quote.spread_pct:.4f}% "
            f"(limit {gates_cfg['max_spread_pct']}%)"
        )

    if signal.levels.risk_per_unit <= 0:
        return False, "stop distance is zero"

    return True, "revalidated"


def portfolio_limits(signal: Signal, open_orders: list[dict]) -> tuple[bool, str]:
    """Book-level constraints that no single signal can evaluate for itself."""
    risk = config.load()["RISK"]
    now = time.time()

    # An order row has no "closed" state - the store records submissions, not
    # exits. Counting every historical fill as an open position would mean that
    # after one EURUSD trade the router considers EURUSD permanently held and
    # never trades it again. OPUS is intraday and its trades resolve inside the
    # triple-barrier horizon, so an order older than that horizon is treated as
    # closed. A live broker's own `positions()` is authoritative where
    # available and should be preferred over this approximation.
    horizon = float(risk.get("position_horizon_sec", 6 * 3600))
    active = [
        o for o in open_orders
        if str(o.get("status", "")).lower() in {"filled", "working"}
        and (now - float(o.get("submitted_ts") or 0)) < horizon
    ]

    if len(active) >= int(risk["max_concurrent_positions"]):
        return False, (
            f"{len(active)} open positions at the "
            f"{risk['max_concurrent_positions']} limit"
        )

    same_symbol = [o for o in active if str(o.get("symbol")) == signal.symbol]
    if len(same_symbol) >= int(risk["max_positions_per_symbol"]):
        return False, f"already holding {signal.symbol}"

    group = correlation_group_of(signal.symbol)
    if group:
        in_group = [
            o for o in active
            if correlation_group_of(str(o.get("symbol", ""))) == group
        ]
        if len(in_group) >= int(risk["correlation_group_cap"]):
            return False, (
                f"correlation group {group} already has {len(in_group)} positions"
            )

    cooldown = float(risk["symbol_cooldown_sec"])
    recent = [
        o for o in open_orders
        if str(o.get("symbol")) == signal.symbol
        and str(o.get("status", "")).lower() != "rejected"
        and (now - float(o.get("submitted_ts") or 0)) < cooldown
    ]
    if recent:
        wait = cooldown - (now - float(recent[0].get("submitted_ts") or 0))
        return False, f"{signal.symbol} in cooldown for another {wait:.0f}s"

    today = [
        o for o in open_orders
        if str(o.get("status", "")).lower() != "rejected"
        and (now - float(o.get("submitted_ts") or 0)) < 86400
    ]
    if len(today) >= int(risk["max_daily_signals_executed"]):
        return False, f"daily execution cap of {risk['max_daily_signals_executed']} reached"

    return True, "within limits"


def route(signal: Signal, *, request_live: bool = False) -> RouteDecision:
    """Decide which broker, if any, this signal may reach."""
    armed, why = live_enabled()
    want_live = bool(request_live and armed)

    if request_live and not armed:
        return RouteDecision(False, f"live execution refused: {why}", "none", "blocked")

    if want_live:
        source = (signal.quote.source if signal.quote else "") or ""
        if source.upper() in _SYNTHETIC_SOURCES:
            return RouteDecision(
                False,
                "refusing to route synthetic demo data to a live broker",
                "none", "blocked",
            )
        venue = str(signal.provenance.get("venue", ""))
        broker = _broker_for(venue, True)
        if not broker.is_live:
            return RouteDecision(
                False,
                f"no live broker adapter available for venue '{venue}'",
                broker.name, "blocked",
            )

        # Demo-only guard. A broker's SERVER NAME is not evidence of account
        # type - this deployment's demo server is literally called
        # "ATFXGM16-LIVE" - so the check reads the terminal's own trade_mode.
        # `unknown` is refused too: if the account type cannot be confirmed,
        # the safe reading is that it might be real money.
        if bool(config.load()["EXECUTION"].get("require_demo_account", True)):
            kind = broker.account_kind()
            if kind != "demo":
                return RouteDecision(
                    False,
                    f"require_demo_account is on and the connected {broker.name} "
                    f"account reports '{kind}', not 'demo'",
                    broker.name, "blocked",
                )

        return RouteDecision(
            True, f"live routing armed ({broker.account_kind()} account)",
            broker.name, "live",
        )

    return RouteDecision(True, why, "paper", "paper")


def submit(
    signal: Signal,
    *,
    request_live: bool = False,
    units: float | None = None,
    store=None,
) -> OrderResult:
    """The single entry point that can place an order."""
    now = time.time()

    decision = route(signal, request_live=request_live)
    if not decision.allowed:
        return OrderResult(
            ok=False, order_id=new_order_id(), status="rejected",
            broker=decision.broker_name, mode=decision.mode,
            symbol=signal.symbol, direction=signal.direction.value,
            message=decision.reason, signal_id=signal.signal_id,
        )

    ok, reason = revalidate(signal, now=now)
    if not ok:
        return OrderResult(
            ok=False, order_id=new_order_id(), status="rejected",
            broker=decision.broker_name, mode=decision.mode,
            symbol=signal.symbol, direction=signal.direction.value,
            message=f"revalidation failed: {reason}", signal_id=signal.signal_id,
        )

    open_orders = []
    if store is not None:
        try:
            open_orders = store.orders(limit=200)
        except Exception:  # noqa: BLE001
            open_orders = []

    # Deduplicate on signal id: the same trigger bar must not be executed twice
    # because two scans or two clicks landed on it.
    window = float(config.load()["EXECUTION"]["dedupe_window_sec"])
    for prior in open_orders:
        if (
            prior.get("signal_id") == signal.signal_id
            and (now - float(prior.get("submitted_ts") or 0)) < window
            and str(prior.get("status", "")).lower() != "rejected"
        ):
            return OrderResult(
                ok=False, order_id=new_order_id(), status="rejected",
                broker=decision.broker_name, mode=decision.mode,
                symbol=signal.symbol, direction=signal.direction.value,
                message=f"duplicate of order {prior.get('order_id')}",
                signal_id=signal.signal_id,
            )

    allowed, why = portfolio_limits(signal, open_orders)
    if not allowed:
        return OrderResult(
            ok=False, order_id=new_order_id(), status="rejected",
            broker=decision.broker_name, mode=decision.mode,
            symbol=signal.symbol, direction=signal.direction.value,
            message=f"portfolio limit: {why}", signal_id=signal.signal_id,
        )

    size = float(units if units is not None else signal.size_units)
    if size <= 0:
        return OrderResult(
            ok=False, order_id=new_order_id(), status="rejected",
            broker=decision.broker_name, mode=decision.mode,
            symbol=signal.symbol, direction=signal.direction.value,
            message="no size authorised for this signal", signal_id=signal.signal_id,
        )

    broker = _broker_for(str(signal.provenance.get("venue", "")), decision.mode == "live")
    result = broker.place(signal, units=size)
    result.mode = decision.mode

    if store is not None:
        try:
            store.record_order(result.as_store_row())
        except Exception:  # noqa: BLE001
            pass
    return result


__all__ = [
    "RouteDecision", "live_enabled", "revalidate", "portfolio_limits",
    "route", "submit",
]
