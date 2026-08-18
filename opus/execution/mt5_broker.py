"""Live MT5 order placement for OPUS.

Attaches the stop and target to the order itself rather than managing them in
process. If this application dies between entry and exit, a broker-side stop
still protects the position; an in-process stop does not.
"""

from __future__ import annotations

import opus.config as config
from opus.data.mt5_feed import ensure_connected, resolve_symbol
from opus.execution.broker import Broker, OrderResult, new_order_id
from opus.types import Direction, Signal

SOURCE = "MT5"


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

    def place(self, signal: Signal, *, units: float) -> OrderResult:
        order_id = new_order_id()
        cfg = config.load()["EXECUTION"]

        def _reject(message: str, detail: dict | None = None) -> OrderResult:
            return OrderResult(
                ok=False, order_id=order_id, status="rejected", broker=self.name,
                mode="live", symbol=signal.symbol, direction=signal.direction.value,
                units=float(units), message=message, signal_id=signal.signal_id,
                detail=detail or {},
            )

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

        stop_distance = abs(float(signal.levels.entry) - float(signal.levels.stop))
        equity = self._equity()
        risk_fraction = float(signal.risk_pct) / 100.0

        sizing_basis = "tick_value"
        if tick_size > 0 and tick_value > 0 and stop_distance > 0 and equity > 0 and risk_fraction > 0:
            risk_amount = equity * risk_fraction
            ticks = stop_distance / tick_size
            lots_raw = risk_amount / (ticks * tick_value)
        else:
            # Fall back only when the symbol does not publish tick economics.
            # Labelled so a mis-sized order is attributable rather than silent.
            sizing_basis = "units_per_contract_fallback"
            lots_raw = float(units) / contract

        volume = round(round(lots_raw / step) * step, 8)
        volume = max(vol_min, min(vol_max, volume))
        if lots_raw < vol_min:
            return _reject(
                f"size {lots_raw:.4f} lots is below the broker minimum {vol_min} "
                f"(basis={sizing_basis}, risk {risk_fraction * 100:.2f}% of "
                f"{equity:.2f})"
            )

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
            "price": round(price, digits),
            "deviation": int(cfg.get("deviation_points", 20)),
            "magic": 20260818,
            "comment": f"OPUS {signal.archetype.value[:12]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(mt5, info),
        }
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
            ok=True, order_id=order_id, status="filled", broker=self.name,
            mode="live", symbol=signal.symbol, direction=signal.direction.value,
            units=float(volume), entry=float(getattr(result, "price", price)),
            stop=request.get("sl"), target=request.get("tp"),
            order_type="market", broker_ref=str(getattr(result, "order", "")),
            message="filled", signal_id=signal.signal_id,
            detail={
                "brokerSymbol": broker_symbol,
                "deal": str(getattr(result, "deal", "")),
                "requestedUnits": float(units),
                "sizingBasis": sizing_basis,
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
            },
        )

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
