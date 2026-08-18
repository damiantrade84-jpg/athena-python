"""Live Bybit v5 order placement for OPUS.

Credentials come from the environment ONLY - OPUS_BYBIT_KEY and
OPUS_BYBIT_SECRET. They are never read from, written to, or logged into a
config file, and the secret never appears in a returned payload.

Signing follows Bybit v5: HMAC-SHA256 over
`timestamp + apiKey + recvWindow + body`, sent in the X-BAPI-* headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import requests

import opus.config as config
from opus.execution.broker import Broker, OrderResult, new_order_id
from opus.types import Direction, Signal

_BASE = "https://api.bybit.com"
_RECV_WINDOW = "5000"
_TIMEOUT = 12.0

_session = requests.Session()
_session.headers.update({"User-Agent": "OPUS/1.0"})


def credentials() -> tuple[str, str] | None:
    key = os.environ.get("OPUS_BYBIT_KEY", "").strip()
    secret = os.environ.get("OPUS_BYBIT_SECRET", "").strip()
    if not key or not secret:
        return None
    return key, secret


def _category(symbol: str) -> str:
    return "linear" if symbol.upper().endswith("USDT") else "spot"


def _signed_post(path: str, body: dict) -> dict:
    creds = credentials()
    if creds is None:
        raise RuntimeError(
            "Bybit credentials absent (set OPUS_BYBIT_KEY / OPUS_BYBIT_SECRET)"
        )
    key, secret = creds
    payload = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))

    signature = hmac.new(
        secret.encode("utf-8"),
        (timestamp + key + _RECV_WINDOW + payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json",
    }
    response = _session.post(
        f"{_BASE}{path}", data=payload, headers=headers, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def _signed_get(path: str, params: dict) -> dict:
    creds = credentials()
    if creds is None:
        raise RuntimeError("Bybit credentials absent")
    key, secret = creds
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    timestamp = str(int(time.time() * 1000))
    signature = hmac.new(
        secret.encode("utf-8"),
        (timestamp + key + _RECV_WINDOW + query).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
        "X-BAPI-SIGN": signature,
    }
    response = _session.get(
        f"{_BASE}{path}", params=params, headers=headers, timeout=_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return float(value)
    return float(int(value / step) * step)


class BybitBroker(Broker):
    name = "bybit"
    is_live = True

    def _instrument(self, symbol: str) -> dict:
        response = _session.get(
            f"{_BASE}/v5/market/instruments-info",
            params={"category": _category(symbol), "symbol": symbol},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        rows = (response.json().get("result") or {}).get("list") or []
        return rows[0] if rows else {}

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

        if credentials() is None:
            return _reject(
                "Bybit credentials absent (set OPUS_BYBIT_KEY / OPUS_BYBIT_SECRET)"
            )

        try:
            instrument = self._instrument(signal.symbol)
        except requests.RequestException as exc:
            return _reject(f"Bybit instrument lookup failed: {exc}")
        if not instrument:
            return _reject(f"Bybit has no instrument named {signal.symbol}")

        lot = instrument.get("lotSizeFilter") or {}
        price_filter = instrument.get("priceFilter") or {}
        qty_step = float(lot.get("qtyStep") or lot.get("basePrecision") or 0.001)
        min_qty = float(lot.get("minOrderQty") or 0.0)
        tick_size = float(price_filter.get("tickSize") or 0.01)

        qty = _round_step(float(units), qty_step)
        if qty <= 0 or (min_qty > 0 and qty < min_qty):
            return _reject(
                f"size {units:.8f} rounds below Bybit minimum {min_qty}"
            )

        body = {
            "category": _category(signal.symbol),
            "symbol": signal.symbol,
            "side": "Buy" if signal.direction is Direction.LONG else "Sell",
            "orderType": "Market",
            "qty": f"{qty:.10f}".rstrip("0").rstrip("."),
            "timeInForce": "IOC",
            "orderLinkId": order_id,
        }
        if bool(cfg.get("attach_stop", True)):
            body["stopLoss"] = f"{_round_step(float(signal.levels.stop), tick_size):.10f}".rstrip("0").rstrip(".")
        if bool(cfg.get("attach_target", True)) and signal.levels.targets:
            body["takeProfit"] = f"{_round_step(float(signal.levels.targets[0]), tick_size):.10f}".rstrip("0").rstrip(".")

        try:
            payload = _signed_post("/v5/order/create", body)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            return _reject(f"Bybit order request failed: {exc}")

        if int(payload.get("retCode", -1)) != 0:
            return _reject(
                f"Bybit rejected the order ({payload.get('retCode')}): "
                f"{payload.get('retMsg')}",
                {"retCode": payload.get("retCode")},
            )

        result = payload.get("result") or {}
        return OrderResult(
            ok=True, order_id=order_id, status="filled", broker=self.name,
            mode="live", symbol=signal.symbol, direction=signal.direction.value,
            units=float(qty), entry=float(signal.levels.entry),
            stop=float(signal.levels.stop),
            target=float(signal.levels.targets[0]) if signal.levels.targets else None,
            order_type="market", broker_ref=str(result.get("orderId") or ""),
            message="accepted", signal_id=signal.signal_id,
            detail={
                "orderLinkId": result.get("orderLinkId"),
                "category": body["category"],
                "requestedQty": float(units),
                "roundedQty": float(qty),
            },
        )

    def account(self) -> dict:
        if credentials() is None:
            return {
                "broker": self.name, "available": False,
                "error": "credentials absent",
            }
        try:
            payload = _signed_get(
                "/v5/account/wallet-balance", {"accountType": "UNIFIED"}
            )
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            return {"broker": self.name, "available": False, "error": str(exc)}
        if int(payload.get("retCode", -1)) != 0:
            return {
                "broker": self.name, "available": False,
                "error": str(payload.get("retMsg")),
            }
        rows = (payload.get("result") or {}).get("list") or []
        if not rows:
            return {"broker": self.name, "available": False}
        row = rows[0]
        return {
            "broker": self.name, "available": True, "live": True,
            "equity": float(row.get("totalEquity") or 0.0),
            "balance": float(row.get("totalWalletBalance") or 0.0),
            "currency": "USD",
            "marginFree": float(row.get("totalAvailableBalance") or 0.0),
        }

    def positions(self) -> list[dict]:
        if credentials() is None:
            return []
        try:
            payload = _signed_get(
                "/v5/position/list", {"category": "linear", "settleCoin": "USDT"}
            )
        except (requests.RequestException, RuntimeError, ValueError):
            return []
        rows = (payload.get("result") or {}).get("list") or []
        out = []
        for row in rows:
            size = float(row.get("size") or 0.0)
            if size <= 0:
                continue
            out.append({
                "symbol": row.get("symbol"),
                "volume": size,
                "direction": "LONG" if str(row.get("side")) == "Buy" else "SHORT",
                "entry": float(row.get("avgPrice") or 0.0),
                "current": float(row.get("markPrice") or 0.0),
                "profit": float(row.get("unrealisedPnl") or 0.0),
                "sl": float(row.get("stopLoss") or 0.0),
                "tp": float(row.get("takeProfit") or 0.0),
            })
        return out


__all__ = ["BybitBroker", "credentials"]
