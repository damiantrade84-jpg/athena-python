"""Broker interface and the paper broker.

The paper broker is the default and is always available. It mirrors live
routing rather than flattering it: a pending plan RESTS at the planned entry
and reports `working`, exactly as a broker-side limit or stop order does, and
only a market plan fills immediately - crossing the spread and paying the
configured slippage. A paper broker that instantly fills every order at the
market is not a simulation of this engine, because this engine's geometry is
mostly resting orders.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import opus.config as config
from opus.execution.orders import MARKET, STOP, OrderPlan
from opus.types import Signal


@dataclass
class OrderResult:
    ok: bool
    order_id: str
    status: str                    # filled | working | rejected | error
    broker: str
    mode: str
    symbol: str
    direction: str
    units: float = 0.0
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    order_type: str = "limit"
    broker_ref: str | None = None
    message: str = ""
    signal_id: str | None = None
    submitted_ts: float = field(default_factory=time.time)
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": bool(self.ok),
            "orderId": self.order_id,
            "status": self.status,
            "broker": self.broker,
            "mode": self.mode,
            "symbol": self.symbol,
            "direction": self.direction,
            "units": round(float(self.units), 6),
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "orderType": self.order_type,
            "brokerRef": self.broker_ref,
            "message": self.message,
            "signalId": self.signal_id,
            "submittedTs": self.submitted_ts,
            "detail": self.detail,
        }

    def as_store_row(self) -> dict:
        return {
            "order_id": self.order_id, "signal_id": self.signal_id,
            "submitted_ts": self.submitted_ts, "mode": self.mode,
            "broker": self.broker, "symbol": self.symbol,
            "direction": self.direction, "order_type": self.order_type,
            "units": self.units, "entry": self.entry, "stop": self.stop,
            "target": self.target, "status": self.status,
            "broker_ref": self.broker_ref, "detail": self.detail,
        }


class Broker:
    """Base broker adapter."""

    name = "base"
    is_live = False

    def place(self, signal: Signal, *, units: float, plan: OrderPlan) -> OrderResult:
        """Place `plan` for `signal`.

        `plan` is not optional and is not re-derived here. The order type and
        price are decided once, in `opus.execution.orders`, so a venue adapter
        cannot quietly turn a resting order into a market one.
        """
        raise NotImplementedError

    def realised_pnl_today(self, now: float | None = None) -> float | None:
        """The account's realised P&L for the current day, or None.

        None means "this venue cannot tell us", which the daily loss guard
        treats as a refusal rather than as a flat day.
        """
        return None

    def order_state(self, order_row: dict) -> dict:
        """What the VENUE says about a working order.

        `{"status": "working" | "filled" | "cancelled" | "unknown", "entry": ...}`
        An adapter that cannot answer returns `unknown`, and the reconciler
        leaves the order alone: silence is not evidence that an order is gone.
        """
        return {"status": "unknown"}

    def cancel(self, order_row: dict) -> OrderResult:
        """Cancel a working order. Adapters that can rest orders MUST override.

        The default refuses rather than reporting success, so a reconciler can
        never record an order as cancelled at a venue that never heard of it.
        """
        return OrderResult(
            ok=False, order_id=str(order_row.get("order_id") or ""),
            status=str(order_row.get("status") or "working"), broker=self.name,
            mode=str(order_row.get("mode") or ""),
            symbol=str(order_row.get("symbol") or ""),
            direction=str(order_row.get("direction") or ""),
            message=f"{self.name} cannot cancel orders",
            signal_id=order_row.get("signal_id"),
        )

    def account(self) -> dict:
        return {"broker": self.name, "available": False}

    def account_kind(self) -> str:
        """demo | contest | real | unknown.

        Adapters that can reach a real-money account MUST override this. The
        router refuses to route live when it cannot positively confirm the
        account is not real.
        """
        return "unknown"

    def positions(self) -> list[dict]:
        return []


def new_order_id() -> str:
    return "opus-" + uuid.uuid4().hex[:14]


class PaperBroker(Broker):
    """Simulated execution that rests orders the way a broker does."""

    name = "paper"
    is_live = False

    def place(self, signal: Signal, *, units: float, plan: OrderPlan) -> OrderResult:
        cfg = config.load()["EXECUTION"]
        order_id = new_order_id()

        def _reject(message: str) -> OrderResult:
            return OrderResult(
                ok=False, order_id=order_id, status="rejected", broker=self.name,
                mode="paper", symbol=signal.symbol,
                direction=signal.direction.value, units=float(units),
                message=message, signal_id=signal.signal_id,
            )

        if plan.rejected:
            return _reject(plan.reason or "no executable order plan")
        if units <= 0:
            return _reject("size resolved to zero units")

        quote = signal.quote
        if quote is None:
            return _reject("no live quote to price against")

        stop = float(signal.levels.stop)
        target = float(signal.levels.targets[0]) if signal.levels.targets else None
        common = {
            "quoteBid": quote.bid, "quoteAsk": quote.ask,
            "quoteSource": quote.source,
            "plannedEntry": signal.levels.entry,
            "planKind": plan.kind,
        }

        if plan.is_pending:
            # A resting order does not fill now. Reporting a fill here would
            # reintroduce exactly the defect this plan exists to remove.
            return OrderResult(
                ok=True, order_id=order_id, status="working", broker=self.name,
                mode="paper", symbol=signal.symbol,
                direction=signal.direction.value, units=float(units),
                entry=float(plan.price), stop=stop, target=target,
                order_type=plan.kind, broker_ref=None,
                message=f"resting {plan.kind} order at {plan.price:.8g}",
                signal_id=signal.signal_id,
                detail={**common, "expiresTs": plan.expires_ts},
            )

        # Market plan: cross the spread, then add the configured slippage
        # AGAINST the trade. A paper fill at mid would make paper results
        # systematically better than live and destroy their comparability.
        slip_bps = float(cfg["paper_slippage_bps"]) / 10_000.0
        fill = float(plan.price) * (1.0 + slip_bps * signal.direction.sign)

        return OrderResult(
            ok=True, order_id=order_id, status="filled", broker=self.name,
            mode="paper", symbol=signal.symbol, direction=signal.direction.value,
            units=float(units), entry=float(fill), stop=stop, target=target,
            order_type=MARKET, broker_ref=None, message="simulated fill",
            signal_id=signal.signal_id,
            detail={
                **common,
                "referencePrice": float(plan.price),
                "slippageBps": cfg["paper_slippage_bps"],
                "fillVsPlanned": round(float(fill - signal.levels.entry), 10),
            },
        )

    def cancel(self, order_row: dict) -> OrderResult:
        return OrderResult(
            ok=True, order_id=str(order_row.get("order_id") or new_order_id()),
            status="cancelled", broker=self.name,
            mode=str(order_row.get("mode") or "paper"),
            symbol=str(order_row.get("symbol") or ""),
            direction=str(order_row.get("direction") or ""),
            units=float(order_row.get("units") or 0.0),
            entry=order_row.get("entry"), stop=order_row.get("stop"),
            target=order_row.get("target"),
            order_type=str(order_row.get("order_type") or ""),
            message="simulated cancel", signal_id=order_row.get("signal_id"),
            submitted_ts=float(order_row.get("submitted_ts") or time.time()),
        )

    def fill_price(self, order_row: dict, plan_kind: str, direction_sign: int) -> float:
        """Price a resting paper order fills at once its level trades.

        A limit fills AT its price - that is what a limit is. A stop is a
        market order once triggered, so it pays the configured slippage; a
        stop that filled exactly at its trigger would make every breakout
        setup look better on paper than it can be live.
        """
        price = float(order_row.get("entry") or 0.0)
        if str(plan_kind).lower() != STOP:
            return price
        slip_bps = float(config.load()["EXECUTION"]["paper_slippage_bps"]) / 10_000.0
        return price * (1.0 + slip_bps * int(direction_sign))

    def account(self) -> dict:
        risk = config.load()["RISK"]
        return {
            "broker": self.name, "available": True, "live": False,
            "equity": float(risk["default_equity"]),
            "currency": str(risk["account_currency"]),
            "note": "simulated account",
        }


__all__ = ["Broker", "OrderResult", "PaperBroker", "new_order_id"]
