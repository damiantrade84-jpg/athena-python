"""KIMI Engine orchestration — scan loop, signal lifecycle, execution routing,
state snapshots for API/SSE.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import indicators as I
from . import structure as S
from .broker import BybitBroker, PaperBroker, Position
from .config import Config
from .datafeed import DataFeed, is_crypto
from .risk import RiskManager
from .signals import Signal, evaluate


class Engine:
    def __init__(self) -> None:
        self.feed = DataFeed()
        self.paper = PaperBroker(Config.STATE_FILE)
        self.bybit = BybitBroker()
        self.risk = RiskManager()
        self.lock = threading.RLock()
        self.running = False
        self.auto_trade = False
        self.min_score = Config.SIGNAL_MIN_SCORE
        self.symbols: list[str] = list(Config.SYMBOLS)   # user-editable universe
        self._scanning = False
        self.broker_positions: dict[str, Position] = {}  # bybit demo positions
        self.signals: dict[str, Signal] = {}        # active signals
        self.signal_history: list[dict] = []
        self.scores: dict[str, dict] = {}           # per-symbol live scorecards
        self.events: list[dict] = []                # execution/fill events feed
        self._cooldown: dict[tuple[str, int], float] = {}
        self._thread: threading.Thread | None = None
        self.last_scan: float = 0.0
        self.scan_count = 0
        self.errors: list[str] = []

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="kimi-engine")
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        while self.running:
            try:
                self.scan_once()
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{time.strftime('%H:%M:%S')} {exc}")
                self.errors = self.errors[-20:]
            time.sleep(Config.TICK_POLL_SEC)

    # ------------------------------------------------------------------
    def scan_once(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        try:
            self._scan_once_inner()
        finally:
            self._scanning = False

    def scan_now(self) -> dict:
        """Manual scan trigger from the UI — async so HTTP stays fast."""
        if self._scanning:
            return {"ok": False, "error": "scan already running"}
        threading.Thread(target=self.scan_once, daemon=True,
                         name="kimi-scan-now").start()
        return {"ok": True}

    def set_symbols(self, symbols: list[str]) -> dict:
        """Replace the scan universe. Validates format; unknown symbols simply
        fail data fetch and surface in the errors feed — fail-closed."""
        from .universe import SYMBOL_RE, canonicalize_kimi_symbol
        clean = []
        for s in symbols:
            hit = canonicalize_kimi_symbol(s)
            if hit and SYMBOL_RE.fullmatch(hit) and hit not in clean:
                clean.append(hit)
        if not clean:
            return {"ok": False, "error": "empty universe"}
        if len(clean) > Config.MAX_SYMBOLS:
            return {"ok": False,
                    "error": f"max {Config.MAX_SYMBOLS} symbols "
                             f"({len(clean)} requested)"}
        with self.lock:
            removed = set(self.symbols) - set(clean)
            self.symbols = clean
            for s in removed:
                self.scores.pop(s, None)
                for sig in list(self.signals.values()):
                    if sig.symbol == s:
                        sig.status = "CANCELLED"
                        del self.signals[sig.id]
        return {"ok": True, "symbols": clean}

    def _scan_once_inner(self) -> None:
        prices: dict[str, float] = {}
        tickers = self.feed.tickers(self.symbols)
        for sym, tk in tickers.items():
            prices[sym] = tk["price"]

        with self.lock:
            self.risk.roll_day(self.paper.equity)
            if not self.risk.kill_switch:
                fills = self.paper.mark(prices)
                self.events.extend(fills)
                self.events.extend(self._mark_broker(prices))
                self.events = self.events[-100:]

            now = time.time()
            # expire stale signals
            for sig in list(self.signals.values()):
                if sig.status == "ACTIVE" and now > sig.valid_until:
                    sig.status = "EXPIRED"
                    self.signal_history.append(sig.to_dict())
                    del self.signals[sig.id]

        # Per-symbol evaluation: three kline fetches plus the scoring maths.
        # Fetch and score in a pool (IO-bound, no shared state), then commit
        # each result under the lock in turn. The commit stays serial so signal
        # dedupe, cooldown and auto-trade see a consistent view - only the
        # network wait is parallel.
        universe = list(self.symbols)

        def _evaluate(sym: str):
            if sym not in self.symbols:
                return sym, None, None            # universe changed mid-scan
            try:
                cs_e = self.feed.klines(sym, Config.TF_ENTRY)
                cs_x = self.feed.klines(sym, Config.TF_CONTEXT)
                cs_b = self.feed.klines(sym, Config.TF_BIAS)
                sig, cards = evaluate(cs_e, cs_x, cs_b, self.min_score)
            except Exception as exc:  # noqa: BLE001
                return sym, None, exc
            return sym, (cs_e, sig, cards), None

        workers = max(1, min(int(Config.SCAN_WORKERS), len(universe) or 1))
        if workers > 1 and len(universe) > 1:
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="kimi-eval") as pool:
                evaluated = list(pool.map(_evaluate, universe))
        else:
            evaluated = [_evaluate(sym) for sym in universe]

        for sym, payload, exc in evaluated:
            # A pair-picker Save can replace the universe while network reads
            # from the previous scan are still in flight. Never let that old
            # scan repopulate a symbol that set_symbols() already removed.
            with self.lock:
                if sym not in self.symbols:
                    continue
            if exc is not None:
                with self.lock:
                    self.errors.append(f"{time.strftime('%H:%M:%S')} {sym}: {exc}")
                    self.errors = self.errors[-20:]
                continue
            if payload is None:
                continue
            cs_e, sig, cards = payload

            px = prices.get(sym, cs_e.last_price)
            if px:
                # keep a last-known price for symbols the ticker batch cannot
                # cover, so paper TP/SL marking never goes blind
                prices.setdefault(sym, px)
            tf_ms = Config.TF_MINUTES.get(Config.TF_ENTRY, 5) * 60_000
            # entry-TF freshness gate: forex/metals close on weekends and Yahoo
            # is delayed — stale candles may still score (UI) but never signal.
            # Lower bound too: unshifted broker-clock (UTC+3) bars would
            # otherwise read as "from the future" after the Friday close.
            age_ms = time.time() * 1000 - float(cs_e.t[-1]) if len(cs_e) else 9e15
            fresh = -300_000 <= age_ms < 2 * tf_ms
            # Price freshness is a SEPARATE question from candle freshness:
            # a symbol whose tick feed died still has fresh bars, and the old
            # payload only reported the bar age. A quote with no stamp at all
            # is treated as stale, not as fresh.
            tk = tickers.get(sym, {})
            quote_ts = float(tk.get("ts") or 0.0)
            price_age = (time.time() - quote_ts) if quote_ts else None
            price_fresh = price_age is not None and price_age <= Config.TICK_STALE_SEC

            with self.lock:
                self.scores[sym] = {
                    "price": px,
                    "ticker": tk,
                    "priceAgeSec": None if price_age is None else round(price_age, 1),
                    "priceFresh": price_fresh,
                    "priceSource": tk.get("source") or cs_e.source,
                    "fresh": fresh,
                    "source": cs_e.source,
                    "cards": {("long" if c.direction > 0 else "short"): {
                        "total": c.total, "grade": c.grade,
                        "components": c.components, "reasons": c.reasons,
                    } for c in cards},
                }
                if sig is not None and fresh:
                    key = (sig.symbol, sig.direction)
                    last = self._cooldown.get(key, 0.0)
                    dup = any(s.symbol == sig.symbol and s.direction == sig.direction
                              and s.status == "ACTIVE" for s in self.signals.values())
                    if now_ok := (time.time() - last > Config.SIGNAL_COOLDOWN_SEC):
                        if not dup:
                            self._cooldown[key] = time.time()
                            self.signals[sig.id] = sig
                            self.signal_history.append(sig.to_dict())
                            self.signal_history = self.signal_history[-120:]
                            if self.auto_trade and sig.card.grade == "A+":
                                self.execute(sig.id)

            self.last_scan = time.time()
        self.scan_count += 1

    # ------------------------------------------------------------------
    def execute(self, signal_id: str, venue: str = "auto") -> dict:
        # Host pipeline first: real broker orders through Athena's audited
        # path (freshness → demo gate → guardian → risk engine → managed
        # execution). Broker IO is slow, so it runs outside the engine lock.
        if venue == "host" or (venue == "auto" and self._host_available()):
            with self.lock:
                sig = self.signals.get(signal_id)
                if sig is None or sig.status != "ACTIVE":
                    return {"ok": False, "error": "signal not active"}
            from . import host_exec
            return host_exec.execute_signal(self, sig)
        with self.lock:
            sig = self.signals.get(signal_id)
            if sig is None or sig.status != "ACTIVE":
                return {"ok": False, "error": "signal not active"}

            use_bybit = venue == "bybit" or (venue == "auto" and Config.BROKER == "bybit")
            if use_bybit:
                if not is_crypto(sig.symbol):
                    return {"ok": False,
                            "error": "bybit venue trades USDT crypto pairs only "
                                     "- use paper for MT5 symbols"}
                if not self.bybit.available():
                    return {"ok": False,
                            "error": f"bybit demo not armed: {self.bybit._err}"}
                # size off the DEMO account equity, not the paper ledger
                acct_eq = self.bybit.get_equity()
                verdict = self.risk.approve(
                    entry=sig.entry, sl=sig.sl,
                    equity=acct_eq or Config.START_EQUITY,
                    open_positions=len(self.broker_positions))
                if not verdict.ok:
                    return {"ok": False, "error": f"risk: {verdict.reason}"}
                try:
                    side = "Buy" if sig.direction > 0 else "Sell"
                    res = self.bybit.market_order(sig.symbol, side, verdict.qty,
                                                  sl=sig.sl, tp=sig.tp2)
                    pos = Position(
                        id=f"B{sig.id[1:]}", symbol=sig.symbol,
                        direction=sig.direction, qty=res.get("qty", verdict.qty),
                        entry=sig.entry, sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2,
                        opened_at=time.time(), broker="bybit",
                        signal_id=sig.id, score=sig.card.total)
                    self.broker_positions[pos.id] = pos
                    sig.status = "EXECUTED"
                    del self.signals[sig.id]
                    ev = {"type": "DEMO", "symbol": sig.symbol, "side": side,
                          "qty": pos.qty, "orderId": res.get("orderId"),
                          "sl": sig.sl, "tp": sig.tp2,
                          "t": time.time(), "signalId": sig.id}
                    if res.get("warning"):
                        ev["warning"] = res["warning"]
                    self.events.append(ev)
                    out = {"ok": True, "venue": "bybit-demo", "order": res,
                           "position": pos.to_dict()}
                    if res.get("warning"):
                        out["warning"] = res["warning"]
                    return out
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": f"bybit: {exc}"}

            equity = self.paper.snapshot({}).get("equity", Config.START_EQUITY)
            verdict = self.risk.approve(entry=sig.entry, sl=sig.sl, equity=equity,
                                        open_positions=len(self.paper.positions))
            if not verdict.ok:
                return {"ok": False, "error": f"risk: {verdict.reason}"}
            pos = self.paper.execute(sig, verdict.qty)
            sig.status = "EXECUTED"
            del self.signals[sig.id]
            ev = {"type": "PAPER", "symbol": pos.symbol,
                  "direction": "LONG" if pos.direction > 0 else "SHORT",
                  "qty": pos.qty, "entry": pos.entry, "t": time.time(),
                  "signalId": sig.id}
            self.events.append(ev)
            return {"ok": True, "venue": "paper", "position": pos.to_dict()}

    def close_position(self, pos_id: str) -> dict:
        with self.lock:
            # broker (bybit demo) position?
            if pos_id in self.broker_positions:
                pos = self.broker_positions[pos_id]
                try:
                    self.bybit.close_market(pos.symbol, pos.direction, pos.qty)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": f"bybit close: {exc}"}
                price = self.scores.get(pos.symbol, {}).get("price", pos.entry)
                rec = {"id": pos.id, "symbol": pos.symbol,
                       "direction": "LONG" if pos.direction > 0 else "SHORT",
                       "qty": pos.qty, "entry": pos.entry, "exit": price,
                       "pnl": round((price - pos.entry) * pos.qty * pos.direction, 2),
                       "reason": "manual", "closedAt": time.time(),
                       "score": pos.score, "signalId": pos.signal_id,
                       "broker": "bybit"}
                del self.broker_positions[pos_id]
                self.events.append({"type": "CLOSE", **rec})
                return {"ok": True, "trade": rec}

            sym = None
            for p in self.paper.positions.values():
                if p.id == pos_id:
                    sym = p.symbol
            price = None
            if sym and sym in self.scores:
                price = self.scores[sym].get("price")
            if price is None and sym:
                try:
                    price = self.feed.klines(sym, Config.TF_ENTRY).last_price
                except Exception:  # noqa: BLE001
                    pass
            if price is None:
                return {"ok": False, "error": "no price for symbol"}
            rec = self.paper.close(pos_id, price, "manual")
            if rec is None:
                return {"ok": False, "error": "position not found"}
            self.events.append({"type": "CLOSE", **rec})
            return {"ok": True, "trade": rec}

    def _mark_broker(self, prices: dict[str, float]) -> list[dict]:
        """Broker positions carry on-exchange SL/TP. Detect the exit by polling
        the position when price crosses a level (plus a slow periodic check for
        external closes); record the fill locally only when confirmed flat."""
        events: list[dict] = []
        for pos in list(self.broker_positions.values()):
            px = prices.get(pos.symbol)
            if px is None:
                continue
            crossed_sl = (pos.sl - px) * pos.direction >= 0
            crossed_tp = (px - pos.tp2) * pos.direction >= 0
            checks = getattr(pos, "_checks", 0) + 1
            setattr(pos, "_checks", checks)
            periodic = checks % 12 == 0          # ~1/min at 5s scan cadence
            if not (crossed_sl or crossed_tp or periodic):
                continue
            state = self.bybit.position_open(pos.symbol)
            if state is not False:
                continue                          # still open, or API unsure
            reason = "SL" if crossed_sl else ("TP2" if crossed_tp else "external")
            exit_px = pos.sl if crossed_sl else (pos.tp2 if crossed_tp else px)
            pnl = (exit_px - pos.entry) * pos.qty * pos.direction
            rec = {"type": "CLOSE", "id": pos.id, "symbol": pos.symbol,
                   "direction": "LONG" if pos.direction > 0 else "SHORT",
                   "qty": pos.qty, "entry": pos.entry, "exit": exit_px,
                   "pnl": round(pnl, 2), "reason": reason,
                   "closedAt": time.time(), "score": pos.score,
                   "signalId": pos.signal_id, "broker": "bybit"}
            del self.broker_positions[pos.id]
            events.append(rec)
        return events

    def set_kill(self, on: bool) -> dict:
        with self.lock:
            self.risk.kill_switch = on
            return {"ok": True, "killSwitch": on}

    def set_min_score(self, value: float) -> dict:
        """Update the signal floor and drop any live signal now below it."""
        with self.lock:
            self.min_score = float(value)
            self._drop_subthreshold_signals()
            return {"ok": True, "minScore": self.min_score}

    def _drop_subthreshold_signals(self) -> None:
        for sig in list(self.signals.values()):
            if sig.card.total < self.min_score:
                sig.status = "CANCELLED"
                del self.signals[sig.id]

    def _host_available(self) -> bool:
        try:
            from . import host_exec
            return host_exec.host_available()
        except Exception:  # noqa: BLE001
            return False

    def _host_status(self) -> dict:
        try:
            from . import host_exec
            return host_exec.status()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc), "demoOnly": True}

    def _broker_accounts(self) -> dict:
        """Real MT5/Bybit equity for the header, alongside the paper ledger.

        Cached inside host_exec, so calling it on every snapshot (3s SSE tick)
        does not hammer the terminal or the exchange.
        """
        try:
            from . import host_exec
            return host_exec.accounts()
        except Exception as exc:  # noqa: BLE001
            return {"mt5": None, "bybit": None, "reason": str(exc)}

    # ------------------------------------------------------------------
    def chart(self, symbol: str, tf: str = Config.TF_CONTEXT) -> dict:
        cs = self.feed.klines(symbol, tf, limit=180, max_age_sec=10)
        vw, s1, s2 = I.vwap_session(cs.t, cs.h, cs.l, cs.c, cs.v)
        e21 = I.ema(cs.c, 21)
        evts = S.structure_events(cs.o, cs.h, cs.l, cs.c, max_age=48)
        candles = [{"t": int(cs.t[i] / 1000), "o": float(cs.o[i]), "h": float(cs.h[i]),
                    "l": float(cs.l[i]), "c": float(cs.c[i]), "v": float(cs.v[i])}
                   for i in range(len(cs))]
        return {
            "symbol": symbol, "tf": tf, "candles": candles,
            "vwap": [{"t": int(cs.t[i] / 1000), "v": float(vw[i])} for i in range(len(cs))],
            "ema21": [{"t": int(cs.t[i] / 1000), "v": float(e21[i])} for i in range(len(cs))],
            "events": [{"t": int(cs.t[e.idx] / 1000), "kind": e.kind,
                        "dir": e.direction, "level": e.level} for e in evts[-24:]],
            "signals": [s.to_dict() for s in
                        list(self.signals.values()) if s.symbol == symbol],
            "source": cs.source,
        }

    def snapshot(self) -> dict:
        with self.lock:
            prices = {s: d.get("price", 0.0) for s, d in self.scores.items()}
            account = self.paper.snapshot(prices)
            broker_status = self.bybit.status()
            broker_pos = [p.to_dict(prices.get(p.symbol))
                          for p in self.broker_positions.values()]
            account["positions"] = account["positions"] + broker_pos
            return {
                "engine": "KIMI", "version": "1.0.0",
                "time": time.time(),
                "running": self.running, "lastScan": self.last_scan,
                "scanCount": self.scan_count, "scanning": self._scanning,
                "killSwitch": self.risk.kill_switch,
                "autoTrade": self.auto_trade,
                "minScore": self.min_score,
                "universe": list(self.symbols),
                "brokerMode": Config.BROKER,
                "liveAvailable": broker_status["armed"],
                "broker": broker_status,
                "hostExec": self._host_status(),
                "accounts": self._broker_accounts(),
                "dailyLossPct": round(self.risk.daily_loss_pct(account["equity"]), 2),
                "symbols": self.scores,
                "signals": [s.to_dict() for s in self.signals.values()
                            if s.card.total >= self.min_score],
                "signalHistory": list(reversed(self.signal_history[-40:])),
                "account": account,
                "events": list(reversed(self.events[-25:])),
                "errors": list(reversed(self.errors[-8:])),
                "config": {"riskPerTradePct": Config.RISK_PER_TRADE_PCT,
                           "maxPositions": Config.MAX_POSITIONS,
                           "maxDailyLossPct": Config.MAX_DAILY_LOSS_PCT,
                           "takerFeeBps": Config.TAKER_FEE_BPS,
                           "tickStaleSec": Config.TICK_STALE_SEC,
                           "tfEntry": Config.TF_ENTRY, "tfContext": Config.TF_CONTEXT,
                           "tfBias": Config.TF_BIAS},
            }
