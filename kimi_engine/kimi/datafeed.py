"""Market data layer: multi-asset, multi-source.

  crypto quotes           → Bybit linear public ticker + Binance fallback
  crypto candles          → Binance public REST (primary) + Bybit fallback
  MT5 symbols (EURUSD, XAUUSD, US30…) → running MetaTrader 5 terminal
                                        (user's demo broker) via MetaTrader5 pkg
  anything else           → Yahoo Finance v8 chart API (fallback, delayed-ish)

Klines are normalized to a CandleSet of numpy arrays, newest last.
MT5 forex/CFD volume is tick volume; taker-flow is unavailable there and the
scorer treats it as neutral.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import requests

from .config import Config


def is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith("USDT") and len(symbol) > 4


# Recognised fiat triplets for the 6-letter forex test. Anything else that is
# 6 alpha chars (BTCUSD, ETHUSD — crypto CFDs some MT5 brokers carry) is NOT
# forex; it falls through to "other" and fail-closes at host execution.
_FIAT3 = {"USD", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD", "ZAR",
          "MXN", "SGD", "HKD", "NOK", "SEK", "DKK", "TRY", "CNH", "PLN",
          "HUF", "CZK", "ILS", "RON", "BRL"}


def classify(symbol: str) -> str:
    s = symbol.upper()
    if is_crypto(s):
        return "crypto"
    if s.startswith(("XAU", "XAG", "XPT", "XPD")):
        return "metals"
    if s.startswith(("US30", "USTEC", "US500", "SPX", "NAS", "UK100", "DE40",
                     "GER40", "JP225", "HK50", "AUS200", "FR40", "EU50", "STOXX")):
        return "indices"
    if s.startswith(("UKO", "USO", "WTI", "BRN", "NGAS", "NATGAS")):
        return "energy"
    if len(s) == 6 and s.isalpha() and s[:3] in _FIAT3 and s[3:] in _FIAT3:
        return "forex"
    # Share CFDs from the Athena book (INTC, AAPL, BA, SPY, …). Universe
    # canonicalization already drops 1-char tickers. Host execution maps
    # this class to signal type "stock"; "other" still fail-closes.
    if 2 <= len(s) <= 5 and s.isalnum():
        return "stock"
    return "other"


@dataclass
class CandleSet:
    symbol: str
    tf: str
    t: np.ndarray      # open time, ms epoch
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    taker: np.ndarray  # taker buy base volume
    closed: np.ndarray  # bool: candle closed
    source: str = "binance"
    fetched_at: float = field(default_factory=time.time)

    def __len__(self) -> int:
        return len(self.c)

    @property
    def last_price(self) -> float:
        return float(self.c[-1]) if len(self.c) else 0.0


class DataFeed:
    def __init__(self) -> None:
        self._sess = requests.Session()
        self._sess.headers.update({"User-Agent": "kimi-engine/1.0"})
        self._kl_cache: dict[tuple[str, str], CandleSet] = {}
        self._tick_cache: dict[str, dict] = {}
        self._tick_ts: float = 0.0
        self._avail: list[str] = []
        self._avail_ts: float = 0.0
        self._lock = threading.Lock()
        # The MetaTrader5 package is a terminal IPC bridge and is NOT
        # thread-safe; every mt5.* call serializes on this lock (re-entrant:
        # _fetch_mt5_klines → _mt5_resolve → _mt5 nest inside one another).
        self._mt5_ipc = threading.RLock()
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    def _get(self, base: str, path: str, params: dict) -> object:
        r = self._sess.get(base + path, params=params, timeout=Config.HTTP_TIMEOUT_SEC)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    def klines(self, symbol: str, tf: str, limit: int = Config.KLINE_LIMIT,
               max_age_sec: float = Config.KLINE_POLL_SEC) -> CandleSet:
        key = (symbol, tf)
        with self._lock:
            cached = self._kl_cache.get(key)
        if cached is not None and (time.time() - cached.fetched_at) < max_age_sec:
            return cached
        if is_crypto(symbol):
            cs = self._fetch_binance_klines(symbol, tf, limit)
            if cs is None:
                cs = self._fetch_bybit_klines(symbol, tf, limit)
        else:
            cs = self._fetch_mt5_klines(symbol, tf, limit)
            if cs is None:
                cs = self._fetch_yahoo_klines(symbol, tf, limit)
        if cs is None:
            # Failed refetch: serve the cache only while it is still roughly
            # of this timeframe's order. Beyond that the caller gets the error
            # and the engine's error path (drop live card, errors feed) or its
            # per-TF bar-age gate decides — an arbitrarily old cache must not
            # keep driving scores indefinitely.
            if cached is not None:
                tf_sec = Config.TF_MINUTES.get(tf, 15) * 60
                if time.time() - cached.fetched_at <= max(max_age_sec, 10 * tf_sec):
                    return cached
            raise RuntimeError(f"no kline source for {symbol} {tf}: {self.last_error}")
        with self._lock:
            self._kl_cache[key] = cs
        return cs

    # ------------------------------------------------------------------
    # MT5 (user's demo broker terminal — read-only attach)
    # ------------------------------------------------------------------
    _MT5_TF = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5",
               "15m": "TIMEFRAME_M15",
               "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1"}

    def _mt5_resolve(self, symbol: str) -> str | None:
        """Map a canonical name (EURUSD) to the broker's actual symbol.
        Brokers rename feeds — ATFX uses a ".s" suffix (EURUSD.s), so a bare
        name lookup fails even though the pair exists. Returns None when the
        broker offers no match."""
        with self._mt5_ipc:
            mt5 = self._mt5()
            if mt5 is None:
                return None
            names = getattr(self, "_mt5_names", None)
            if names is None:
                try:
                    names = {s.name for s in mt5.symbols_get()}
                except Exception:  # noqa: BLE001
                    names = set()
                self._mt5_names = names
            s = symbol.upper()
            resolved = None
            if s in names:
                resolved = s
            elif s + ".s" in names:
                resolved = s + ".s"
            elif "#" + s in names:
                resolved = "#" + s
            else:
                hash_hit = next((n for n in sorted(names)
                                 if n[:1] == "#" and n[1:].upper() == s), None)
                resolved = hash_hit or next(
                    (n for n in sorted(names) if n.upper().startswith(s)), None)
            if resolved is None:
                self._mt5_names = None          # refresh name cache next attempt
                self.last_error = f"mt5: no symbol matching {symbol}"
                return None
            try:
                mt5.symbol_select(resolved, True)   # ensure MarketWatch subscription
            except Exception:  # noqa: BLE001
                pass
            return resolved

    def _mt5(self):
        with self._mt5_ipc:
            if getattr(self, "_mt5_mod", None) is not None:
                return self._mt5_mod if self._mt5_ok else None
            try:
                import MetaTrader5 as mt5  # noqa: PLC0415
                self._mt5_ok = bool(mt5.initialize())
                self._mt5_mod = mt5
            except Exception as exc:  # noqa: BLE001
                self._mt5_mod = None
                self._mt5_ok = False
                self.last_error = f"mt5 init: {exc}"
            return self._mt5_mod if self._mt5_ok else None

    def _mt5_time_shift_sec(self, mt5, resolved: str, newest_epoch_s: float) -> int:
        """Seconds to subtract from raw MT5 bar stamps to get UTC.

        MT5 bars and ticks share the broker server clock (ATFX = UTC+3), so
        raw stamps are hours ahead of UTC and would defeat every freshness
        gate. Infer the offset from a live tick vs wall clock (same idea as
        the host's mt5_time_alignment); when the tick is frozen (weekend),
        return 0 — unshifted bars stay visibly stale instead of being
        relabeled fresh.
        """
        with self._mt5_ipc:
            now = time.time()
            tick_t = None
            try:
                tk = mt5.symbol_info_tick(resolved)
                if tk and getattr(tk, "time", 0):
                    tick_t = float(tk.time)
            except Exception:  # noqa: BLE001
                tick_t = None
            if tick_t:
                off_h = round((tick_t - now) / 3600.0)
                if 0 <= off_h <= 14:
                    return off_h * 3600
                return 0
            cfg = Config.MT5_BROKER_UTC_OFFSET * 3600
            if cfg and 0 <= now - (newest_epoch_s - cfg) <= 7200:
                return cfg
            return 0

    def _fetch_mt5_klines(self, symbol: str, tf: str, limit: int) -> CandleSet | None:
        with self._mt5_ipc:
            tf_name = self._MT5_TF.get(tf)
            if tf_name is None:
                # Never relabel another timeframe as the one requested.
                self.last_error = f"mt5: unsupported tf {tf}"
                return None
            mt5 = self._mt5()
            if mt5 is None:
                return None
            resolved = self._mt5_resolve(symbol)
            if resolved is None:
                return None
            try:
                tf_const = getattr(mt5, tf_name)
                rates = mt5.copy_rates_from_pos(resolved, tf_const, 0, limit)
                if rates is None or len(rates) < 30:
                    self.last_error = f"mt5 no rates {symbol}"
                    return None
                shift = self._mt5_time_shift_sec(mt5, resolved, float(rates[-1][0]))
                t = np.array([(int(r[0]) - shift) * 1000 for r in rates])
                o = np.array([float(r[1]) for r in rates])
                h = np.array([float(r[2]) for r in rates])
                l = np.array([float(r[3]) for r in rates])
                c = np.array([float(r[4]) for r in rates])
                v = np.array([float(r[5]) for r in rates])          # tick volume
                tk = np.zeros_like(v)                                # no taker split on MT5
                tf_ms = Config.TF_MINUTES.get(tf, 15) * 60_000
                closed = (t + tf_ms) <= int(time.time() * 1000)
                return CandleSet(symbol, tf, t, o, h, l, c, v, tk,
                                 np.asarray(closed, dtype=bool), "mt5")
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"mt5 {symbol} {tf}: {exc}"
                return None

    # ------------------------------------------------------------------
    # Yahoo Finance fallback (delayed on some venues — labeled)
    # ------------------------------------------------------------------
    _YAHOO_MAP = {
        "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
        "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X",
        "NZDUSD": "NZDUSD=X", "EURGBP": "EURGBP=X", "EURJPY": "EURJPY=X",
        "GBPJPY": "GBPJPY=X",
        "XAUUSD": "GC=F", "XAGUSD": "SI=F",
        "USOUSD": "CL=F", "UKOUSD": "BZ=F",
        "US30": "^DJI", "SPX500": "^GSPC", "USTEC": "^IXIC", "NAS100": "^NDX",
    }
    _YAHOO_TF = {"5m": "5m", "15m": "15m", "1h": "1h"}

    def _fetch_yahoo_klines(self, symbol: str, tf: str, limit: int) -> CandleSet | None:
        ysym = self._YAHOO_MAP.get(symbol)
        if not ysym or tf not in self._YAHOO_TF:
            return None
        try:
            raw = self._get("https://query1.finance.yahoo.com",
                            f"/v8/finance/chart/{ysym}",
                            {"interval": self._YAHOO_TF[tf], "range": "5d"})
            res = raw["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            rows = [(t_, o_, h_, l_, c_, v_) for t_, o_, h_, l_, c_, v_ in
                    zip(ts, q["open"], q["high"], q["low"], q["close"], q["volume"])
                    if o_ is not None and c_ is not None]
            rows = rows[-limit:]
            if len(rows) < 30:
                return None
            t = np.array([int(r[0]) * 1000 for r in rows])
            o = np.array([float(r[1]) for r in rows]); h = np.array([float(r[2]) for r in rows])
            l = np.array([float(r[3]) for r in rows]); c = np.array([float(r[4]) for r in rows])
            v = np.array([float(r[5] or 0) for r in rows])
            return CandleSet(symbol, tf, t, o, h, l, c, v, np.zeros_like(v),
                             np.ones(len(rows), dtype=bool), "yahoo")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"yahoo {symbol}: {exc}"
            return None

    def mt5_symbols(self) -> list[str]:
        """Canonical symbol names offered by the attached MT5 broker (for the
        pair picker). Broker suffixes (".s") are stripped so the universe uses
        one naming scheme; the resolver maps back at fetch time."""
        with self._mt5_ipc:
            mt5 = self._mt5()
            if mt5 is None:
                return []
            try:
                out = set()
                for s in mt5.symbols_get():
                    n = s.name
                    if n.startswith("#"):
                        continue                     # equity CFDs — out of scope
                    out.add(n[:-2] if n.endswith(".s") else n)
                return sorted(out)
            except Exception:  # noqa: BLE001
                return []

    def mt5_tick(self, symbol: str) -> dict | None:
        """MT5 tick with an HONEST age, or None.

        MT5 serves the last known tick forever — on a weekend that is Friday's
        close, and stamping it "now" made a dead feed look live. The broker-
        clock tick time is converted to UTC (same hour-offset inference as the
        bar shift); an unmappable clock or a quote older than the tick-cache
        horizon fail closed (caller falls back to Yahoo, labeled delayed)."""
        with self._mt5_ipc:
            mt5 = self._mt5()
            if mt5 is None:
                return None
            resolved = self._mt5_resolve(symbol)
            if resolved is None:
                return None
            try:
                tk = mt5.symbol_info_tick(resolved)
                if tk is None or tk.bid <= 0:
                    return None
                tick_t = float(getattr(tk, "time", 0) or 0)
                if tick_t <= 0:
                    return None              # no stamp at all → treat as stale
                off_h = round((tick_t - time.time()) / 3600.0)
                if not (0 <= off_h <= 14):
                    return None              # unmappable clock → fail closed
                ts = tick_t - off_h * 3600
                if time.time() - ts > Config.TICK_MAX_AGE_SEC:
                    return None              # frozen feed / weekend
                return {"price": (tk.bid + tk.ask) / 2, "bid": tk.bid,
                        "ask": tk.ask, "source": "mt5", "ts": ts}
            except Exception:  # noqa: BLE001
                return None


    def _fetch_binance_klines(self, symbol: str, tf: str, limit: int) -> CandleSet | None:
        try:
            raw = self._get(Config.BINANCE_BASE, "/api/v3/klines",
                            {"symbol": symbol, "interval": tf, "limit": limit})
            t, o, h, l, c, v, tk, closed = [], [], [], [], [], [], [], []
            now_ms = int(time.time() * 1000)
            for row in raw:
                t.append(int(row[0]))
                o.append(float(row[1])); h.append(float(row[2]))
                l.append(float(row[3])); c.append(float(row[4]))
                v.append(float(row[5])); tk.append(float(row[9]))
                closed.append(int(row[6]) < now_ms)
            return CandleSet(symbol, tf, np.array(t), np.array(o), np.array(h),
                             np.array(l), np.array(c), np.array(v), np.array(tk),
                             np.array(closed, dtype=bool), "binance")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"binance {symbol} {tf}: {exc}"
            return None

    _BYBIT_TF = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}

    def _fetch_bybit_klines(self, symbol: str, tf: str, limit: int) -> CandleSet | None:
        try:
            raw = self._get(Config.BYBIT_PUBLIC_BASE, "/v5/market/kline",
                            {"category": "linear", "symbol": symbol,
                             "interval": self._BYBIT_TF.get(tf, "15"), "limit": limit})
            rows = raw["result"]["list"]  # newest first
            rows = list(reversed(rows))
            t, o, h, l, c, v, tk = [], [], [], [], [], [], []
            for row in rows:
                t.append(int(row[0])); o.append(float(row[1])); h.append(float(row[2]))
                l.append(float(row[3])); c.append(float(row[4])); v.append(float(row[5]))
                tk.append(float(row[5]) * 0.5)  # no taker split on this endpoint
            closed = np.ones(len(rows), dtype=bool)
            if len(rows):
                tf_ms = Config.TF_MINUTES.get(tf, 15) * 60_000
                closed[-1] = (int(time.time() * 1000) - t[-1]) >= tf_ms
            return CandleSet(symbol, tf, np.array(t), np.array(o), np.array(h),
                             np.array(l), np.array(c), np.array(v), np.array(tk),
                             closed, "bybit")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"bybit {symbol} {tf}: {exc}"
            return None

    # ------------------------------------------------------------------
    def _binance_usdt_symbols(self) -> list[str]:
        """USDT-quoted trading symbols on Binance spot."""
        try:
            raw = self._get(Config.BINANCE_BASE, "/api/v3/exchangeInfo", {})
            return sorted(
                s["symbol"] for s in raw["symbols"]
                if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
                and s.get("isSpotTradingAllowed", True))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"exchangeInfo: {exc}"
            return []

    def available_symbols(self) -> list[str]:
        """Full tradable universe for the pair picker: Binance USDT crypto
        plus everything the attached MT5 broker offers (forex, metals,
        indices, energy). Cached 15 min so a terminal started later is
        picked up quickly."""
        if self._avail and time.time() - self._avail_ts < 900:
            return self._avail
        crypto = self._binance_usdt_symbols()
        known = set(crypto)
        broker = [s for s in self.mt5_symbols() if s not in known]
        merged = crypto + broker
        if merged:
            self._avail, self._avail_ts = merged, time.time()
        return self._avail

    # ------------------------------------------------------------------
    def _binance_tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Batch 24h stats for crypto symbols. Any unknown symbol in the
        batch fails the whole Binance call, so only USDT pairs may be sent."""
        try:
            syms = ",".join(f'"{s}"' for s in symbols)
            raw = self._sess.get(f"{Config.BINANCE_BASE}/api/v3/ticker/24hr",
                                 params={"symbols": f"[{syms}]"},
                                 timeout=Config.HTTP_TIMEOUT_SEC)
            raw.raise_for_status()
            out = {}
            for row in raw.json():
                out[row["symbol"]] = {
                    "price": float(row["lastPrice"]),
                    "changePct24h": float(row["priceChangePercent"]),
                    "quoteVol24h": float(row["quoteVolume"]),
                    "high24h": float(row["highPrice"]),
                    "low24h": float(row["lowPrice"]),
                    "source": "binance",
                }
            return out
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"ticker: {exc}"
            return {}

    def _bybit_tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Batch public USDT-linear quotes from KIMI's execution venue."""
        if not symbols:
            return {}
        wanted = set(symbols)
        try:
            raw = self._get(
                Config.BYBIT_PUBLIC_BASE,
                "/v5/market/tickers",
                {"category": "linear"},
            )
            if not isinstance(raw, dict) or int(raw.get("retCode", -1)) != 0:
                raise ValueError("invalid Bybit ticker response")
            rows = (raw.get("result") or {}).get("list") or []
            out = {}
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                if symbol not in wanted:
                    continue
                price = float(row.get("lastPrice") or 0)
                if price <= 0:
                    continue
                out[symbol] = {
                    "price": price,
                    "bid": float(row.get("bid1Price") or 0) or None,
                    "ask": float(row.get("ask1Price") or 0) or None,
                    "changePct24h": float(row.get("price24hPcnt") or 0) * 100.0,
                    "quoteVol24h": float(row.get("turnover24h") or 0) or None,
                    "high24h": float(row.get("highPrice24h") or 0) or None,
                    "low24h": float(row.get("lowPrice24h") or 0) or None,
                    "source": "bybit",
                }
            return out
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"bybit ticker: {exc}"
            return {}

    def _alt_ticker(self, symbol: str) -> dict | None:
        """Price a non-crypto symbol: MT5 tick first, Yahoo last. Source ages
        travel with the quote (``ts``) — never stamped "now" here."""
        tk = self.mt5_tick(symbol)
        if tk:
            return {"price": tk["price"], "bid": tk["bid"], "ask": tk["ask"],
                    "changePct24h": self._mt5_daily_change(symbol),
                    "quoteVol24h": None, "high24h": None, "low24h": None,
                    "source": "mt5", "ts": tk["ts"]}
        y = self._yahoo_last_price(symbol)
        if y is not None:
            px, chg, bar_ts = y
            out = {"price": px, "changePct24h": chg, "quoteVol24h": None,
                   "high24h": None, "low24h": None, "source": "yahoo"}
            if bar_ts:
                out["ts"] = bar_ts
            return out
        return None

    def _mt5_daily_change(self, symbol: str) -> float | None:
        """24h change % from D1 bars, cached 15 min per symbol."""
        cache = getattr(self, "_dchg", None)
        if cache is None:
            cache = self._dchg = {}
        ent = cache.get(symbol)
        if ent and time.time() - ent[0] < 900:
            return ent[1]
        val = None
        with self._mt5_ipc:
            mt5 = self._mt5()
            resolved = self._mt5_resolve(symbol) if mt5 else None
            if resolved:
                try:
                    rates = mt5.copy_rates_from_pos(resolved, mt5.TIMEFRAME_D1, 0, 3)
                    if rates is not None and len(rates) >= 2 and rates[-2][4] > 0:
                        val = round((float(rates[-1][4]) / float(rates[-2][4]) - 1.0)
                                    * 100.0, 2)
                except Exception:  # noqa: BLE001
                    val = None
        cache[symbol] = (time.time(), val)
        return val

    def _yahoo_last_price(self, symbol: str) -> tuple[float, float | None, float | None] | None:
        """(last close, hourly change %, bar epoch) from Yahoo — delayed, and
        the bar's own timestamp travels with the price so consumers treat the
        quote as stale instead of freshly stamped."""
        ysym = self._YAHOO_MAP.get(symbol)
        if not ysym:
            return None
        try:
            raw = self._get("https://query1.finance.yahoo.com",
                            f"/v8/finance/chart/{ysym}",
                            {"interval": "1h", "range": "5d"})
            res = raw["chart"]["result"][0]
            closes = [c for c in res["indicators"]["quote"][0]["close"]
                      if c is not None]
            if not closes:
                return None
            chg = None
            if len(closes) >= 2 and closes[-2] > 0:
                chg = round((float(closes[-1]) / float(closes[-2]) - 1.0) * 100.0, 2)
            ts_list = res.get("timestamp") or []
            bar_ts = float(ts_list[-1]) if ts_list else None
            return float(closes[-1]), chg, bar_ts
        except Exception:  # noqa: BLE001
            return None

    def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """{symbol: {price, changePct24h, ...}} for a mixed universe.

        Crypto goes to Bybit first, with a Binance fallback for symbols absent
        from the linear catalog. Non-crypto symbols are priced
        individually from MT5/Yahoo. Splitting matters: one non-Binance
        symbol in the batch call fails it for every crypto symbol too.
        Failed symbols keep their last-known tick.
        """
        requested = list(dict.fromkeys(symbols))
        if time.time() - self._tick_ts < Config.TICK_POLL_SEC and self._tick_cache:
            return {s: self._tick_cache[s] for s in requested if s in self._tick_cache}
        now = time.time()
        out: dict[str, dict] = {}
        crypto = [s for s in requested if is_crypto(s)]
        if crypto:
            out.update(self._bybit_tickers(crypto))
            missing = [s for s in crypto if s not in out]
            if missing:
                fallback = self._binance_tickers(missing)
                for tk in fallback.values():
                    tk["source"] = "binance_fallback"
                out.update(fallback)

        # Non-crypto symbols are priced one at a time (MT5 IPC, or Yahoo as a
        # last resort). Serially that is a hundred round trips on a portfolio-
        # sized universe, so the per-symbol calls run in a small pool.
        alts = [s for s in requested if not is_crypto(s)]
        if alts:
            workers = max(1, min(int(Config.SCAN_WORKERS), len(alts)))
            if workers > 1 and len(alts) > 1:
                with ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="kimi-tick") as pool:
                    fetched = list(pool.map(self._alt_ticker, alts))
            else:
                fetched = [self._alt_ticker(s) for s in alts]
            for sym, tk in zip(alts, fetched):
                if tk:
                    out[sym] = tk

        # Stamp every entry with the moment it was actually read. Without this
        # a symbol whose feed dies keeps serving its last price forever and
        # looks live: the batch-level _tick_ts only says SOME symbol refreshed,
        # never which. Consumers gate on the per-symbol stamp.
        for tk in out.values():
            tk.setdefault("ts", now)

        if out:
            merged = {**self._tick_cache, **out}
            # Drop entries nothing has refreshed in a long while rather than
            # carrying a dead symbol's last print indefinitely.
            self._tick_cache = {
                sym: tk for sym, tk in merged.items()
                if now - float(tk.get("ts") or 0) <= Config.TICK_MAX_AGE_SEC
            }
            self._tick_ts = now
            self.last_error = None
        return {s: self._tick_cache[s] for s in requested if s in self._tick_cache}
