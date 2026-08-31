"""Brokers.

PaperBroker  — deterministic fills (market + slippage + taker fee), TP1 partial,
               TP2/SL exits, equity curve, JSON persistence.
BybitBroker  — optional real adapter (demo / testnet / live). Active ONLY when
               KIMI_LIVE_ENABLED=1 and API keys are present in the environment.
               Otherwise .available() is False and nothing routes there.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field

from .config import Config
from .signals import Signal


@dataclass
class Position:
    id: str
    symbol: str
    direction: int
    qty: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    opened_at: float
    broker: str = "paper"
    tp1_done: bool = False
    realized: float = 0.0     # realized pnl from partials
    fees: float = 0.0
    status: str = "OPEN"
    signal_id: str = ""
    score: float = 0.0
    ccy_factor: float = 1.0   # quote→account conversion for pnl/fees

    def unrealized(self, price: float) -> float:
        return ((price - self.entry) * self.qty * self.direction
                * self.ccy_factor)

    def to_dict(self, price: float | None = None) -> dict:
        d = {
            "id": self.id, "symbol": self.symbol,
            "direction": "LONG" if self.direction > 0 else "SHORT",
            "qty": self.qty, "entry": self.entry, "sl": self.sl,
            "tp1": self.tp1, "tp2": self.tp2, "openedAt": self.opened_at,
            "broker": self.broker, "status": self.status,
            "realized": round(self.realized, 2), "fees": round(self.fees, 2),
            "signalId": self.signal_id, "score": self.score,
        }
        if price is not None:
            d["mark"] = price
            d["unrealized"] = round(self.unrealized(price), 2)
        return d


class PaperBroker:
    name = "paper"

    def __init__(self, state_path: str) -> None:
        self.state_path = state_path
        self.equity = Config.START_EQUITY
        self.positions: dict[str, Position] = {}
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self._load()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        return True

    def execute(self, sig: Signal, qty: float,
                ccy_factor: float = 1.0) -> Position:
        slip = Config.SLIPPAGE_BPS / 1e4
        fill = sig.entry * (1 + slip * sig.direction)
        fee = fill * qty * Config.TAKER_FEE_BPS / 1e4 * ccy_factor
        pos = Position(
            id=f"P{uuid.uuid4().hex[:8]}", symbol=sig.symbol,
            direction=sig.direction, qty=qty, entry=fill, sl=sig.sl,
            tp1=sig.tp1, tp2=sig.tp2, opened_at=time.time(),
            fees=fee, signal_id=sig.id, score=sig.card.total,
            ccy_factor=ccy_factor,
        )
        self.equity -= fee
        self.positions[pos.id] = pos
        self._save()
        return pos

    def close(self, pos_id: str, price: float, reason: str = "manual") -> dict | None:
        pos = self.positions.get(pos_id)
        if not pos:
            return None
        rec = self._close_leg(pos, pos.qty, price, reason)
        pos.status = "CLOSED"
        del self.positions[pos_id]
        self.trades.append(rec)
        self.trades = self.trades[-300:]
        self._save()
        return rec

    def _close_leg(self, pos: Position, qty: float, price: float, reason: str) -> dict:
        slip = Config.SLIPPAGE_BPS / 1e4
        fill = price * (1 - slip * pos.direction)
        fee = fill * qty * Config.TAKER_FEE_BPS / 1e4 * pos.ccy_factor
        pnl = (fill - pos.entry) * qty * pos.direction * pos.ccy_factor - fee
        self.equity += pnl
        pos.fees += fee
        pos.realized += pnl
        return {"id": pos.id, "symbol": pos.symbol,
                "direction": "LONG" if pos.direction > 0 else "SHORT",
                "qty": qty, "entry": pos.entry, "exit": fill,
                "pnl": round(pnl, 2), "reason": reason,
                "closedAt": time.time(), "score": pos.score,
                "signalId": pos.signal_id}

    def mark(self, prices: dict[str, float]) -> list[dict]:
        """Mark-to-market; check TP1/TP2/SL on latest prices. Returns events."""
        events: list[dict] = []
        for pos in list(self.positions.values()):
            px = prices.get(pos.symbol)
            if px is None:
                continue
            # TP1 partial
            if not pos.tp1_done and (px - pos.tp1) * pos.direction >= 0:
                leg_qty = pos.qty * Config.TP1_CLOSE_FRAC
                rec = self._close_leg(pos, leg_qty, pos.tp1, "TP1")
                pos.qty -= leg_qty
                pos.tp1_done = True
                events.append({"type": "TP1", **rec})
                # move SL to breakeven after TP1
                pos.sl = pos.entry
            # TP2
            elif (px - pos.tp2) * pos.direction >= 0:
                rec = self.close(pos.id, pos.tp2, "TP2")
                if rec:
                    events.append({"type": "TP2", **rec})
                continue
            # SL
            elif (pos.sl - px) * pos.direction >= 0:
                rec = self.close(pos.id, pos.sl, "SL" if not pos.tp1_done else "BE")
                if rec:
                    events.append({"type": "SL", **rec})
                continue
        if events:
            self._save()
        return events

    def snapshot(self, prices: dict[str, float]) -> dict:
        upnl = sum(p.unrealized(prices[p.symbol]) for p in self.positions.values()
                   if p.symbol in prices)
        eq = self.equity + upnl
        self.equity_curve.append({"t": time.time(), "equity": round(eq, 2)})
        self.equity_curve = self.equity_curve[-2000:]
        return {
            "equity": round(eq, 2), "cash": round(self.equity, 2),
            "unrealized": round(upnl, 2),
            "positions": [p.to_dict(prices.get(p.symbol)) for p in self.positions.values()],
            "trades": list(reversed(self.trades[-60:])),
            "equityCurve": self.equity_curve[-600:],
        }

    # ------------------------------------------------------------------
    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "equity": self.equity,
                    "positions": [p.__dict__ for p in self.positions.values()],
                    "trades": self.trades[-300:],
                    "equityCurve": self.equity_curve[-2000:],
                }, f)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f)
            self.equity = float(d.get("equity", Config.START_EQUITY))
            for pd in d.get("positions", []):
                self.positions[pd["id"]] = Position(**pd)
            self.trades = d.get("trades", [])
            self.equity_curve = d.get("equityCurve", [])
        except (OSError, ValueError, TypeError, KeyError):
            pass


class BybitBroker:
    """Bybit linear USDT-perp execution via ccxt — same account contract as the
    host platform: BYBIT_API_KEY / BYBIT_API_SECRET in the environment and
    BYBIT_DEMO=true for demo trading (api-demo.bybit.com).

    Armed only when ALL hold:
      * ccxt importable
      * KIMI_BYBIT_KEY / KIMI_BYBIT_SECRET set (BYBIT_API_KEY/BYBIT_API_SECRET
        accepted as aliases), non-placeholder
      * KIMI_BYBIT_ENV = demo | testnet | live   (default demo;
        BYBIT_DEMO=true also forces demo)
      * KIMI_LIVE_ENABLED=1        (engine-level arming switch)

    Entry = market order; SL/TP are attached on-exchange (trading-stop) so the
    exchange enforces exits even if this process dies. TP is set at TP2; TP1
    partials stay a paper-broker feature for now.
    """

    name = "bybit"

    def __init__(self) -> None:
        self._ex = None
        self._env = Config.BYBIT_ENV
        self._err: str | None = None
        self._status_cache: dict | None = None
        self._status_ts: float = 0.0

    # ------------------------------------------------------------------
    def _client(self):
        if self._ex is not None:
            return self._ex
        if not Config.LIVE_ENABLED:
            self._err = "KIMI_LIVE_ENABLED != 1"
            return None
        import os
        env = Config.BYBIT_ENV
        if os.environ.get("BYBIT_DEMO", "").lower() in ("true", "1", "yes"):
            env = "demo"
        if env not in ("demo", "testnet", "live"):
            self._err = f"unknown KIMI_BYBIT_ENV {env!r}"
            return None
        key = Config.BYBIT_KEY or os.environ.get("BYBIT_API_KEY", "")
        secret = Config.BYBIT_SECRET or os.environ.get("BYBIT_API_SECRET", "")
        if not key or not secret or key == "REVOKE_AND_REPLACE":
            self._err = "KIMI_BYBIT_KEY/KIMI_BYBIT_SECRET missing"
            return None
        try:
            import ccxt  # noqa: PLC0415
            self._ex = ccxt.bybit({
                "apiKey": key, "secret": secret, "enableRateLimit": True,
                "options": {"defaultType": "linear", "defaultSettle": "USDT",
                            "recvWindow": 30000},
            })
            if env == "demo":
                self._ex.enable_demo_trading(True)
            elif env == "testnet":
                self._ex.set_sandbox_mode(True)
            # "live" is an explicit user choice; KIMI_LIVE_ENABLED=1 already required
            self._env = env
            self._ex.fetch_balance()           # verify signed call works
            self._err = None
            return self._ex
        except ImportError:
            self._err = "ccxt not installed in this Python env"
        except Exception as exc:  # noqa: BLE001
            self._err = f"connect failed: {exc}"
            self._ex = None
        return None

    def available(self) -> bool:
        return self._client() is not None

    @staticmethod
    def _sym(symbol: str) -> str:
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        return f"{base}/USDT:USDT"

    def status(self) -> dict:
        if self._status_cache is not None and time.time() - self._status_ts < 30:
            return self._status_cache
        ex = self._client()
        out = {"venue": "bybit", "env": self._env, "armed": ex is not None,
               "error": self._err}
        if ex is not None:
            try:
                bal = ex.fetch_balance()
                usdt = bal.get("USDT", {})
                out["equity"] = float(usdt.get("total") or 0)
                out["free"] = float(usdt.get("free") or 0)
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"balance: {exc}"
        self._status_cache = out
        self._status_ts = time.time()
        return out

    def get_equity(self) -> float | None:
        st = self.status()
        return st.get("equity")

    # ------------------------------------------------------------------
    def market_order(self, symbol: str, side: str, qty: float,
                     sl: float | None = None, tp: float | None = None) -> dict:
        ex = self._client()
        if ex is None:
            raise RuntimeError(f"bybit demo not armed: {self._err}")
        ccxt_sym = self._sym(symbol)
        qty = float(ex.amount_to_precision(ccxt_sym, qty))
        if qty <= 0:
            raise RuntimeError("qty rounds to zero at instrument precision")
        order = ex.create_order(ccxt_sym, "market", side.lower(), qty, None,
                                {"positionIdx": 0})
        warn = None
        if sl or tp:
            try:
                args = {"category": "linear", "symbol": symbol, "positionIdx": 0,
                        "tpslMode": "Full", "slOrderType": "Market",
                        "tpOrderType": "Market"}
                if sl:
                    args["stopLoss"] = f"{sl:.10g}"
                    args["slTriggerBy"] = "MarkPrice"
                if tp:
                    args["takeProfit"] = f"{tp:.10g}"
                    args["tpTriggerBy"] = "MarkPrice"
                ex.private_post_v5_position_trading_stop(args)
            except Exception as exc:  # noqa: BLE001
                warn = f"trading-stop failed (manage exits manually): {exc}"
        return {"orderId": order.get("id"), "qty": qty, "warning": warn}

    def close_market(self, symbol: str, direction: int, qty: float) -> dict:
        ex = self._client()
        if ex is None:
            raise RuntimeError(f"bybit demo not armed: {self._err}")
        ccxt_sym = self._sym(symbol)
        side = "sell" if direction > 0 else "buy"
        qty = float(ex.amount_to_precision(ccxt_sym, qty))
        return ex.create_order(ccxt_sym, "market", side, qty, None,
                               {"positionIdx": 0, "reduceOnly": True})

    def position_open(self, symbol: str) -> bool | None:
        """True/False if determinable, None on error."""
        ex = self._client()
        if ex is None:
            return None
        try:
            poss = ex.fetch_positions([self._sym(symbol)])
            for p in poss:
                if abs(float(p.get("contracts") or 0)) > 0:
                    return True
            return False
        except Exception:  # noqa: BLE001
            return None
