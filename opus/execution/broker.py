"""Broker interface and the paper broker.

The paper broker is the default and is always available. It simulates a fill
against the live quote with a configured slippage, so paper results are
directly comparable to live ones instead of being optimistic by construction.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import opus.config as config
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

    def place(self, signal: Signal, *, units: float) -> OrderResult:
        raise NotImplementedError

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
    """Simulated execution against the signal's live quote."""

    name = "paper"
    is_live = False

    def place(self, signal: Signal, *, units: float) -> OrderResult:
        cfg = config.load()["EXECUTION"]
        order_id = new_order_id()

        if units <= 0:
            return OrderResult(
                ok=False, order_id=order_id, status="rejected", broker=self.name,
                mode="paper", symbol=signal.symbol, direction=signal.direction.value,
                message="size resolved to zero units", signal_id=signal.signal_id,
            )

        quote = signal.quote
        if quote is None:
            return OrderResult(
                ok=False, order_id=order_id, status="rejected", broker=self.name,
                mode="paper", symbol=signal.symbol, direction=signal.direction.value,
                message="no live quote to fill against", signal_id=signal.signal_id,
            )

        # Cross the spread, then add the configured slippage AGAINST the trade.
        # A paper fill at mid, or at the limit price, would make paper results
        # systematically better than live and destroy their comparability.
        base = quote.entry_price(signal.direction)
        slip_bps = float(cfg["paper_slippage_bps"]) / 10_000.0
        fill = base * (1.0 + slip_bps * signal.direction.sign)

        return OrderResult(
            ok=True, order_id=order_id, status="filled", broker=self.name,
            mode="paper", symbol=signal.symbol, direction=signal.direction.value,
            units=float(units), entry=float(fill),
            stop=float(signal.levels.stop),
            target=float(signal.levels.targets[0]) if signal.levels.targets else None,
            order_type=str(cfg.get("order_type", "limit")),
            broker_ref=None, message="simulated fill",
            signal_id=signal.signal_id,
            detail={
                "quoteBid": quote.bid, "quoteAsk": quote.ask,
                "quoteSource": quote.source,
                "referencePrice": base, "slippageBps": cfg["paper_slippage_bps"],
                "plannedEntry": signal.levels.entry,
                "fillVsPlanned": round(float(fill - signal.levels.entry), 10),
            },
        )

    def account(self) -> dict:
        risk = config.load()["RISK"]
        return {
            "broker": self.name, "available": True, "live": False,
            "equity": float(risk["default_equity"]),
            "currency": str(risk["account_currency"]),
            "note": "simulated account",
        }


__all__ = ["Broker", "OrderResult", "PaperBroker", "new_order_id"]
