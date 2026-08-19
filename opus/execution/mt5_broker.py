"""Live MT5 order placement for OPUS.

Two things are pushed onto the broker deliberately.

The stop and target are attached to the order itself rather than managed in
process: if this application dies between entry and exit, a broker-side stop
still protects the position.

The ENTRY is a resting order, at the price the geometry chose, with a
broker-side expiry. OPUS entries are limits and stops - a reclaim level, a void
midpoint, a break beyond a coil - and filling those at the market instead
changes the stop distance, the reward multiple and the size that every gate
was evaluated against.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import opus.config as config
from opus.data.mt5_feed import ensure_connected, resolve_symbol, server_time_offset
from opus.execution.broker import Broker, OrderResult, new_order_id
from opus.execution.orders import LIMIT, MARKET, STOP, OrderPlan
from opus.types import Direction, Signal

SOURCE = "MT5"

# MT5 expiration_mode is a bitmask; 4 is SYMBOL_EXPIRATION_SPECIFIED.
_EXPIRATION_SPECIFIED = 4


def _mt5():
    import MetaTrader5 as mt5  # noqa: N813
    return mt5


class MT5Broker(Broker):
    name = "mt5"
    is_live = True

    def _equity(self) -> float:
        """Live account equity, in the ACCOUNT currency."""
        try:
            ensure_connected()
            info = _mt5().account_info()
        except Exception:  # noqa: BLE001
            return 0.0
        return float(getattr(info, "equity", 0.0) or 0.0) if info else 0.0

    def account_kind(self) -> str:
        """demo | contest | real | unknown, from the terminal's own report.

        Server NAME is not evidence: this broker's demo server is called
        "ATFXGM16-LIVE". Only account_info().trade_mode is authoritative.
        """
        try:
            ensure_connected()
            info = _mt5().account_info()
        except Exception:  # noqa: BLE001
            return "unknown"
        if info is None:
            return "unknown"
        return {0: "demo", 1: "contest", 2: "real"}.get(
            int(getattr(info, "trade_mode", -1)), "unknown"
        )

    def _filling_mode(self, mt5, symbol_info):
        """Pick a filling mode the symbol actually supports.

        MT5 rejects an order outright when the filling mode is unsupported, and
        the supported set varies per broker and per symbol.
        """
        allowed = int(getattr(symbol_info, "filling_mode", 0) or 0)
        if allowed & 2:
            return mt5.ORDER_FILLING_IOC
        if allowed & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _order_type(self, mt5, plan: OrderPlan, is_long: bool):
        """The MT5 order type this plan means. No plan maps to two types."""
        if plan.kind == MARKET:
            return mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL
        if plan.kind == LIMIT:
            return mt5.ORDER_TYPE_BUY_LIMIT if is_long else mt5.ORDER_TYPE_SELL_LIMIT
        return mt5.ORDER_TYPE_BUY_STOP if is_long else mt5.ORDER_TYPE_SELL_STOP

    def place(self, signal: Signal, *, units: float, plan: OrderPlan) -> OrderResult:
        order_id = new_order_id()
        cfg = config.load()["EXECUTION"]

        def _reject(message: str, detail: dict | None = None) -> OrderResult:
            return OrderResult(
                ok=False, order_id=order_id, status="rejected", broker=self.name,
                mode="live", symbol=signal.symbol, direction=signal.direction.value,
                units=float(units), message=message, signal_id=signal.signal_id,
                detail=detail or {},
            )

        if plan.rejected:
            return _reject(plan.reason or "no executable order plan")

        try:
            ensure_connected()
            mt5 = _mt5()
            broker_symbol = resolve_symbol(signal.symbol)
        except Exception as exc:  # noqa: BLE001
            return _reject(f"MT5 unavailable: {exc}")

        info = mt5.symbol_info(broker_symbol)
        if info is None:
            return _reject(f"MT5 has no symbol info for {broker_symbol}")
        if getattr(info, "trade_mode", None) == getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", -1):
            return _reject(f"{broker_symbol} is not tradable on this account")

        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None or tick.bid <= 0 or tick.ask <= 0:
            return _reject("no live MT5 tick at submit time")

        is_long = signal.direction is Direction.LONG
        price = float(tick.ask if is_long else tick.bid)

        # Round to the symbol's own tick size; an unrounded price is a common
        # cause of "Invalid price" rejections.
        digits = int(getattr(info, "digits", 5) or 5)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        vol_min = float(getattr(info, "volume_min", 0.01) or 0.01)
        vol_max = float(getattr(info, "volume_max", 100.0) or 100.0)

        # Size from TICK VALUE, which MT5 reports in the ACCOUNT currency.
        #
        # The engine's generic sizing produces base-currency units by dividing
        # a risk amount by the stop distance. That is only correct when the
        # account currency equals the quote currency. On a ZAR account trading
        # USD-quoted pairs it overstates every position by the USDZAR rate -
        # measured at 16.27x, i.e. 3.82 lots where 0.235 was intended.
        # trade_tick_value already carries the conversion, so deriving lots
        # from it is correct for any account/quote currency combination.
        contract = float(getattr(info, "trade_contract_size", 0) or 0) or 100_000.0
        tick_size = float(getattr(info, "trade_tick_size", 0) or 0)
        tick_value = float(getattr(info, "trade_tick_value", 0) or 0)

        # The order price is the PLAN's price - the resting level for a pending
        # order, the crossed spread for a market one - and the risk is measured
        # against that same price, so the size expresses the risk actually
        # taken rather than the risk of a fill that never happens.
        order_price = float(plan.price if plan.kind != MARKET else price)
        stop_distance = abs(order_price - float(signal.levels.stop))
        equity = self._equity()
        risk_fraction = float(signal.risk_pct) / 100.0

        # No fallback. Sizing from base-currency units is only correct when the
        # account currency equals the quote currency; on this ZAR account it
        # oversized every USD-quoted position by the USDZAR rate - measured at
        # 16.27x. trade_tick_value carries the conversion, so when the symbol
        # or the account will not tell us the tick economics, the honest answer
        # is that this order cannot be sized, not that it can be guessed.
        if tick_size <= 0 or tick_value <= 0:
            return _reject(
                f"{broker_symbol} publishes no tick economics "
                f"(tick_size={tick_size}, tick_value={tick_value}); "
                f"refusing to size an order without them"
            )
        if equity <= 0:
            return _reject(
                "MT5 account equity could not be read; refusing to size an "
                "order against an unknown account"
            )
        if stop_distance <= 0 or risk_fraction <= 0:
            return _reject(
                f"no usable risk geometry (stop distance {stop_distance:.8g}, "
                f"risk {risk_fraction * 100:.3f}%)"
            )

        risk_amount = equity * risk_fraction
        ticks = stop_distance / tick_size
        lots_raw = risk_amount / (ticks * tick_value)

        volume = round(round(lots_raw / step) * step, 8)
        volume = max(vol_min, min(vol_max, volume))
        if lots_raw < vol_min:
            return _reject(
                f"size {lots_raw:.4f} lots is below the broker minimum {vol_min} "
                f"(risk {risk_fraction * 100:.2f}% of {equity:.2f})"
            )

        is_pending = plan.is_pending
        request = {
            "action": mt5.TRADE_ACTION_PENDING if is_pending else mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": volume,
            "type": self._order_type(mt5, plan, is_long),
            "price": round(order_price, digits),
            "magic": 20260818,
            "comment": f"OPUS {signal.archetype.value[:12]}",
            "type_filling": self._filling_mode(mt5, info),
        }
        if is_pending:
            # Expiry is read by the terminal in the BROKER's timezone. OPUS
            # normalises broker timestamps to UTC on the way in, so the offset
            # has to go back on here or the order expires hours early - or is
            # rejected outright as a time already past.
            allowed = int(getattr(info, "expiration_mode", 0) or 0)
            if plan.expires_ts and (allowed & _EXPIRATION_SPECIFIED):
                request["type_time"] = mt5.ORDER_TIME_SPECIFIED
                request["expiration"] = int(plan.expires_ts + server_time_offset())
            else:
                # The reconciler cancels it instead; see execution/reconcile.py.
                request["type_time"] = mt5.ORDER_TIME_GTC
        else:
            request["type_time"] = mt5.ORDER_TIME_GTC
            request["deviation"] = int(cfg.get("deviation_points", 20))
        if bool(cfg.get("attach_stop", True)):
            request["sl"] = round(float(signal.levels.stop), digits)
        if bool(cfg.get("attach_target", True)) and signal.levels.targets:
            request["tp"] = round(float(signal.levels.targets[0]), digits)

        check = mt5.order_check(request)
        if check is not None and int(getattr(check, "retcode", 0)) not in (0, 10009):
            return _reject(
                f"MT5 pre-trade check failed ({check.retcode}): {check.comment}",
                {"request": {k: v for k, v in request.items() if k != "comment"}},
            )

        result = mt5.order_send(request)
        if result is None:
            code, message = mt5.last_error()
            return _reject(f"MT5 order_send returned nothing ({code}: {message})")

        retcode = int(getattr(result, "retcode", -1))
        if retcode != mt5.TRADE_RETCODE_DONE:
            return _reject(
                f"MT5 rejected the order ({retcode}): {getattr(result, 'comment', '')}",
                {"retcode": retcode},
            )

        return OrderResult(
            ok=True, order_id=order_id,
            status="working" if is_pending else "filled", broker=self.name,
            mode="live", symbol=signal.symbol, direction=signal.direction.value,
            units=float(volume),
            entry=float(order_price if is_pending else getattr(result, "price", price)),
            stop=request.get("sl"), target=request.get("tp"),
            order_type=plan.kind, broker_ref=str(getattr(result, "order", "")),
            message=(
                f"resting {plan.kind} order at {order_price:.8g}"
                if is_pending else "filled"
            ),
            signal_id=signal.signal_id,
            detail={
                "brokerSymbol": broker_symbol,
                "deal": str(getattr(result, "deal", "")),
                "requestedUnits": float(units),
                "sizingBasis": "tick_value",
                "planKind": plan.kind,
                "expiresTs": plan.expires_ts,
                "accountKind": self.account_kind(),
                "accountEquity": float(equity),
                "riskPct": float(signal.risk_pct),
                "contractSize": float(contract),
                "tickSize": float(tick_size),
                "tickValue": float(tick_value),
                "requestedLots": float(lots_raw),
                "submittedLots": float(volume),
                "filledLots": float(getattr(result, "volume", volume)),
                "plannedEntry": float(signal.levels.entry),
                "marketAtSubmit": float(price),
            },
        )

    def realised_pnl_today(self, now: float | None = None) -> float | None:
        """Account-wide realised P&L since the broker's own midnight.

        Deal times, and the datetimes `history_deals_get` filters on, are in
        the SERVER's timezone. Asking for UTC midnight on a UTC+3 server drops
        the first three hours of the trading day - and those hours contain the
        Asia session that a European-morning loss would have started in.

        Commission and swap are included. A day that looks flat on gross
        profit can be well down once costs are paid, and it is the net figure
        the loss cap is about.
        """
        now = float(now if now is not None else time.time())
        try:
            ensure_connected()
            mt5 = _mt5()
        except Exception:  # noqa: BLE001
            return None

        utc_midnight = datetime.fromtimestamp(now, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        offset = server_time_offset()
        date_from = datetime.fromtimestamp(utc_midnight + offset, tz=timezone.utc)
        date_to = datetime.fromtimestamp(now + offset + 60.0, tz=timezone.utc)

        try:
            deals = mt5.history_deals_get(
                date_from=date_from.replace(tzinfo=None),
                date_to=date_to.replace(tzinfo=None),
            )
        except Exception:  # noqa: BLE001 - unavailable history is not a flat day
            return None
        if deals is None:
            return None

        total = 0.0
        for deal in deals:
            total += float(getattr(deal, "profit", 0.0) or 0.0)
            total += float(getattr(deal, "commission", 0.0) or 0.0)
            total += float(getattr(deal, "swap", 0.0) or 0.0)
        return float(total)

    def order_state(self, order_row: dict) -> dict:
        """What the terminal says became of a pending order.

        Two lookups, in order: the live pending list, then order history. An
        order in neither is NOT evidence that it is gone - a terminal that has
        just reconnected, or a history window that does not reach back far
        enough, both look identical to "cancelled" - so that case reports
        `unknown` and the reconciler leaves the row alone.
        """
        ticket = str(order_row.get("broker_ref") or "").strip()
        if not ticket.isdigit():
            return {"status": "unknown", "detail": "no ticket recorded"}

        try:
            ensure_connected()
            mt5 = _mt5()
        except Exception as exc:  # noqa: BLE001
            return {"status": "unknown", "detail": f"MT5 unavailable: {exc}"}

        try:
            if mt5.orders_get(ticket=int(ticket)):
                return {"status": "working"}
            history = mt5.history_orders_get(ticket=int(ticket)) or ()
        except Exception as exc:  # noqa: BLE001
            return {"status": "unknown", "detail": str(exc)}

        if not history:
            return {"status": "unknown", "detail": "no terminal record"}

        record = history[0]
        state = int(getattr(record, "state", -1))
        if state == int(getattr(mt5, "ORDER_STATE_FILLED", 4)):
            price = float(getattr(record, "price_open", 0.0) or 0.0)
            return {"status": "filled", "entry": price or None}
        if state in (
            int(getattr(mt5, "ORDER_STATE_CANCELED", 2)),
            int(getattr(mt5, "ORDER_STATE_EXPIRED", 6)),
            int(getattr(mt5, "ORDER_STATE_REJECTED", 5)),
        ):
            return {"status": "cancelled", "detail": f"order state {state}"}
        return {"status": "working", "detail": f"order state {state}"}

    def cancel(self, order_row: dict) -> OrderResult:
        """Remove a working pending order from the broker."""
        order_id = str(order_row.get("order_id") or new_order_id())

        def _result(ok: bool, status: str, message: str) -> OrderResult:
            return OrderResult(
                ok=ok, order_id=order_id, status=status, broker=self.name,
                mode=str(order_row.get("mode") or "live"),
                symbol=str(order_row.get("symbol") or ""),
                direction=str(order_row.get("direction") or ""),
                units=float(order_row.get("units") or 0.0),
                entry=order_row.get("entry"), stop=order_row.get("stop"),
                target=order_row.get("target"),
                order_type=str(order_row.get("order_type") or ""),
                broker_ref=order_row.get("broker_ref"), message=message,
                signal_id=order_row.get("signal_id"),
                submitted_ts=float(order_row.get("submitted_ts") or time.time()),
            )

        ticket = str(order_row.get("broker_ref") or "").strip()
        if not ticket.isdigit():
            # Without a ticket there is nothing to remove, and reporting a
            # cancel would leave a live order running while the store says it
            # is gone - the one state this must never produce.
            return _result(False, str(order_row.get("status") or "working"),
                           "no MT5 ticket recorded for this order")

        try:
            ensure_connected()
            mt5 = _mt5()
        except Exception as exc:  # noqa: BLE001
            return _result(False, str(order_row.get("status") or "working"),
                           f"MT5 unavailable: {exc}")

        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(ticket),
        })
        if result is None:
            code, message = mt5.last_error()
            return _result(False, str(order_row.get("status") or "working"),
                           f"MT5 cancel returned nothing ({code}: {message})")

        retcode = int(getattr(result, "retcode", -1))
        if retcode != mt5.TRADE_RETCODE_DONE:
            return _result(False, str(order_row.get("status") or "working"),
                           f"MT5 refused the cancel ({retcode}): "
                           f"{getattr(result, 'comment', '')}")
        return _result(True, "cancelled", "cancelled at the broker")

    def account(self) -> dict:
        try:
            ensure_connected()
            info = _mt5().account_info()
        except Exception as exc:  # noqa: BLE001
            return {"broker": self.name, "available": False, "error": str(exc)}
        if info is None:
            return {"broker": self.name, "available": False}
        return {
            "broker": self.name, "available": True, "live": True,
            "equity": float(info.equity), "balance": float(info.balance),
            "currency": str(info.currency), "leverage": int(info.leverage),
            "marginFree": float(info.margin_free), "login": int(info.login),
            "server": str(getattr(info, "server", "")),
        }

    def positions(self) -> list[dict]:
        try:
            ensure_connected()
            rows = _mt5().positions_get() or []
        except Exception:  # noqa: BLE001
            return []
        return [
            {
                "symbol": p.symbol, "volume": float(p.volume),
                "direction": "LONG" if int(p.type) == 0 else "SHORT",
                "entry": float(p.price_open), "current": float(p.price_current),
                "profit": float(p.profit), "sl": float(p.sl), "tp": float(p.tp),
                "ticket": int(p.ticket), "openedTs": float(p.time),
            }
            for p in rows
        ]


__all__ = ["MT5Broker"]
