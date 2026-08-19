"""Focused unit checks for the KIMI Engine math and signal pipeline.
Run: python -m pytest tests/ -q   (from kimi_engine/)
"""
from __future__ import annotations

import threading
import time

import numpy as np

from kimi import indicators as I
from kimi import structure as S
from kimi.scoring import score, session_weight, vol_fit
from kimi.risk import RiskManager


def _mk(n=260, drift=0.05, seed=7):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(drift, 1.0, n))
    h = c + rng.uniform(0, 1, n)
    l = c - rng.uniform(0, 1, n)
    o = c - rng.uniform(-0.5, 0.5, n)
    v = rng.uniform(100, 500, n)
    t = np.arange(n) * 300_000 + 1_700_000_000_000
    return t, o, h, l, c, v


def test_indicator_ranges():
    t, o, h, l, c, v = _mk()
    assert -1.001 <= I.tide(h, l, c)[-1] <= 1.001
    assert 0 <= I.pulse(c, h, l)[-1] <= 100
    assert -1.001 <= I.pressure(v, h, l, c)[-1] <= 1.001
    assert 0 <= I.vol_regime(h, l, c)[-1] <= 1
    vw, s1, s2 = I.vwap_session(t, h, l, c, v)
    assert np.all(np.isfinite(vw)) and np.all(s2 >= s1)


def test_structure_detection_runs():
    t, o, h, l, c, v = _mk()
    sw = S.swings(h, l)
    ev = S.structure_events(o, h, l, c)
    assert isinstance(sw, list) and isinstance(ev, list)
    assert all(e.age >= 0 for e in ev)


def test_score_bounds_and_grades():
    t, o, h, l, c, v = _mk()
    ev = S.structure_events(o, h, l, c)
    card = score("TESTUSDT", +1, tide_bias=0.9, tide_ctx=0.8, pulse_v=80,
                 flow_v=0.6, pressure_v=0.5, vwap_rel=0.004, vol_pct=0.5, events=ev)
    assert 0 <= card.total <= 100
    assert card.grade in ("A+", "A", "B", "—")
    card2 = score("TESTUSDT", +1, tide_bias=-0.9, tide_ctx=-0.8, pulse_v=20,
                  flow_v=-0.6, pressure_v=-0.5, vwap_rel=-0.004, vol_pct=0.05, events=[])
    assert card2.total < card.total


def test_session_and_volfit():
    w, name = session_weight()
    assert 0 <= w <= 1 and name
    assert vol_fit(0.5) == 1.0 and vol_fit(0.0) == 0.0


def test_risk_gates_fail_closed():
    rm = RiskManager()
    rm.kill_switch = True
    v = rm.approve(entry=100, sl=99, equity=100000, open_positions=0)
    assert not v.ok
    rm.kill_switch = False
    v = rm.approve(entry=100, sl=99, equity=100000, open_positions=0)
    assert v.ok and v.qty > 0
    v = rm.approve(entry=100, sl=100, equity=100000, open_positions=0)
    assert not v.ok
    rm.day_start_equity = 100000
    v = rm.approve(entry=100, sl=99, equity=96000, open_positions=0)  # -4% day
    assert not v.ok and "daily" in v.reason


# ----------------------------------------------------------------------
# multi-asset data layer
# ----------------------------------------------------------------------
def test_classify_and_crypto_detection():
    from kimi.datafeed import classify, is_crypto
    assert is_crypto("BTCUSDT") and not is_crypto("EURUSD") and not is_crypto("BTCUSD")
    assert classify("EURUSD") == "forex"
    assert classify("XAUUSD") == "metals"
    assert classify("US30") == "indices"
    assert classify("WTI") == "energy"


def _fake_mt5(names):
    import types
    fake = types.SimpleNamespace()
    fake.TIMEFRAME_M15 = 15
    fake.TIMEFRAME_D1 = 16408
    fake.initialize = lambda: True
    fake.symbols_get = lambda: [types.SimpleNamespace(name=n) for n in names]
    fake.symbol_select = lambda n, v: True
    return fake


def test_mt5_symbol_resolution(monkeypatch):
    """Broker suffixes (ATFX '.s') must resolve from canonical names."""
    import sys
    from kimi.datafeed import DataFeed
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        _fake_mt5(("EURUSD.s", "XAUUSD.s", "US30.s", "BTCUSD")))
    feed = DataFeed()
    assert feed._mt5_resolve("EURUSD") == "EURUSD.s"   # suffix resolution
    assert feed._mt5_resolve("BTCUSD") == "BTCUSD"     # exact match
    assert feed._mt5_resolve("US30") == "US30.s"
    assert feed._mt5_resolve("NOPE") is None


def test_mt5_resolves_hash_prefixed_shares(monkeypatch):
    import sys
    from kimi.datafeed import DataFeed
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        _fake_mt5(("#AAPL", "#FB", "EURUSD.s")))
    feed = DataFeed()
    assert feed._mt5_resolve("AAPL") == "#AAPL"
    assert feed._mt5_resolve("FB") == "#FB"
    assert feed._mt5_resolve("EURUSD") == "EURUSD.s"


def test_mt5_symbols_canonical(monkeypatch):
    import sys
    from kimi.datafeed import DataFeed
    monkeypatch.setitem(sys.modules, "MetaTrader5",
                        _fake_mt5(("EURUSD.s", "XAUUSD.s", "#ABNB", "BTCUSD")))
    feed = DataFeed()
    assert feed.mt5_symbols() == ["BTCUSD", "EURUSD", "XAUUSD"]  # no .s, no stocks


def test_available_symbols_merges_crypto_and_broker(monkeypatch):
    from kimi.datafeed import DataFeed
    feed = DataFeed()
    monkeypatch.setattr(feed, "_binance_usdt_symbols", lambda: ["BTCUSDT"])
    monkeypatch.setattr(feed, "mt5_symbols", lambda: ["EURUSD", "XAUUSD"])
    assert feed.available_symbols() == ["BTCUSDT", "EURUSD", "XAUUSD"]


def test_mixed_tickers_split_and_last_known(monkeypatch):
    """Non-crypto must route away from the Bybit batch and failed refreshes
    keep last-known prices."""
    from kimi.datafeed import DataFeed
    feed = DataFeed()
    calls = {"bybit": [], "alt": []}
    monkeypatch.setattr(feed, "_bybit_tickers",
                        lambda syms: calls["bybit"].append(list(syms))
                        or {s: {"price": 1.0} for s in syms})
    monkeypatch.setattr(feed, "_binance_tickers", lambda syms: {})
    monkeypatch.setattr(feed, "_alt_ticker",
                        lambda s: calls["alt"].append(s) or {"price": 2.0})
    out = feed.tickers(["BTCUSDT", "EURUSD", "XAUUSD"])
    assert calls["bybit"] == [["BTCUSDT"]]
    assert calls["alt"] == ["EURUSD", "XAUUSD"]
    assert out["BTCUSDT"]["price"] == 1.0 and out["EURUSD"]["price"] == 2.0

    monkeypatch.setattr(feed, "_bybit_tickers", lambda syms: {})
    monkeypatch.setattr(feed, "_alt_ticker", lambda s: None)
    feed._tick_ts = 0.0
    out = feed.tickers(["BTCUSDT", "EURUSD"])
    assert "BTCUSDT" not in out or out.get("BTCUSDT", {}).get("price") == 1.0
    assert out["EURUSD"]["price"] == 2.0     # last-known retained


def test_crypto_tickers_prefer_bybit_with_binance_fallback(monkeypatch):
    """KIMI executes USDT instruments on Bybit, so its displayed live quote
    must use the same venue first and label any Binance fallback explicitly."""
    from kimi.datafeed import DataFeed
    feed = DataFeed()
    calls = {"bybit": [], "binance": []}
    monkeypatch.setattr(
        feed,
        "_bybit_tickers",
        lambda syms: calls["bybit"].append(list(syms))
        or {"BTCUSDT": {"price": 101.0, "source": "bybit"}},
    )
    monkeypatch.setattr(
        feed,
        "_binance_tickers",
        lambda syms: calls["binance"].append(list(syms))
        or {s: {"price": 99.0, "source": "binance_fallback"} for s in syms},
    )

    out = feed.tickers(["BTCUSDT", "ETHUSDT"])

    assert calls["bybit"] == [["BTCUSDT", "ETHUSDT"]]
    assert calls["binance"] == [["ETHUSDT"]]
    assert out["BTCUSDT"]["source"] == "bybit"
    assert out["ETHUSDT"]["source"] == "binance_fallback"


def test_ticker_cache_is_scoped_to_requested_symbols():
    """Changing the scan universe must not return cached quotes for removed pairs."""
    from kimi.datafeed import DataFeed
    feed = DataFeed()
    now = time.time()
    feed._tick_ts = now
    feed._tick_cache = {
        "BTCUSDT": {"price": 100.0, "ts": now, "source": "bybit"},
        "XAUUSD": {"price": 2000.0, "ts": now, "source": "mt5"},
    }

    assert set(feed.tickers(["XAUUSD"])) == {"XAUUSD"}


def test_removed_symbol_cannot_reappear_from_inflight_scan(monkeypatch, tmp_path):
    """A scan started before Save must not repopulate a pair removed by Save."""
    import kimi.engine as engmod
    from kimi.datafeed import CandleSet

    entered = threading.Event()
    release = threading.Event()
    now_ms = int(time.time() * 1000)
    n = 60
    t = np.array([now_ms - i * 300_000 for i in range(n)][::-1])
    c = np.linspace(100, 101, n)
    candles = CandleSet(
        "BTCUSDT", "5m", t, c, c + 0.5, c - 0.5, c,
        np.full(n, 10.0), np.zeros(n), np.ones(n, dtype=bool), "test",
    )
    eng = engmod.Engine()
    eng.paper.state_path = str(tmp_path / "st.json")
    eng.symbols = ["BTCUSDT"]
    monkeypatch.setattr(
        eng.feed,
        "tickers",
        lambda syms: {"BTCUSDT": {"price": 101.0, "ts": time.time(), "source": "bybit"}},
    )

    def blocked_klines(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return candles

    monkeypatch.setattr(eng.feed, "klines", blocked_klines)
    monkeypatch.setattr(engmod, "evaluate", lambda *args, **kwargs: (None, []))

    scan = threading.Thread(target=eng._scan_once_inner)
    scan.start()
    assert entered.wait(1)
    assert eng.set_symbols(["XAUUSD"])["ok"]
    release.set()
    scan.join(3)

    assert not scan.is_alive()
    assert "BTCUSDT" not in eng.snapshot()["symbols"]


def test_noncrypto_position_marks(tmp_path):
    """Paper TP/SL marking is asset-agnostic once a price exists."""
    from kimi.broker import PaperBroker, Position
    pb = PaperBroker(str(tmp_path / "st.json"))
    pos = Position(id="PX1", symbol="EURUSD", direction=1, qty=10000,
                   entry=1.1000, sl=1.0950, tp1=1.1075, tp2=1.1150,
                   opened_at=0.0)
    pb.positions[pos.id] = pos
    events = pb.mark({"EURUSD": 1.0949})     # through SL
    assert events and events[0]["type"] == "SL"
    assert not pb.positions


def test_stale_entry_data_suppresses_signals(monkeypatch, tmp_path):
    """Weekend/MT5-stale or delayed (Yahoo) entry candles must never emit
    executable signals — scores still update for the UI."""
    import time as _t
    import kimi.engine as engmod
    from kimi.datafeed import CandleSet
    from kimi.scoring import ScoreCard
    from kimi.signals import Signal

    def mk_cs(sym, tf, age_min):
        n = 60
        end = int(_t.time() * 1000) - age_min * 60_000
        t = np.array([end - i * 300_000 for i in range(n)][::-1])
        c = np.linspace(100, 101, n)
        return CandleSet(sym, tf, t, c, c + 0.5, c - 0.5, c,
                         np.full(n, 10.0), np.zeros(n),
                         np.ones(n, dtype=bool), "test")

    card = ScoreCard("EURUSD", 1, 90.0, "A+", {}, [], True)

    def mk_engine(age_min):
        eng = engmod.Engine()
        eng.paper.state_path = str(tmp_path / f"st{age_min}.json")
        eng.symbols = ["EURUSD"]
        monkeypatch.setattr(eng.feed, "tickers", lambda syms: {})
        monkeypatch.setattr(eng.feed, "klines",
                            lambda sym, tf, **kw: mk_cs(sym, tf, age_min))
        sig = Signal(id=f"KT{age_min}", symbol="EURUSD", direction=1, entry=101,
                     sl=100, tp1=102.5, tp2=104, atr=0.5, card=card,
                     valid_until=_t.time() + 3600)
        monkeypatch.setattr(engmod, "evaluate", lambda *a, **k: (sig, [card]))
        return eng

    stale = mk_engine(age_min=999)
    stale._scan_once_inner()
    assert not stale.signals
    assert stale.scores["EURUSD"]["fresh"] is False

    future = mk_engine(age_min=-200)   # broker-clock (UTC+3) style future bars
    future._scan_once_inner()
    assert not future.signals
    assert future.scores["EURUSD"]["fresh"] is False

    fresh_eng = mk_engine(age_min=0)
    fresh_eng._scan_once_inner()
    assert fresh_eng.signals
    assert fresh_eng.scores["EURUSD"]["fresh"] is True


def test_mt5_bars_normalized_to_utc(monkeypatch):
    """ATFX stamps bars on the broker clock (UTC+3); klines must be shifted
    back to UTC or every freshness gate reads them as future/never-stale."""
    import sys
    import types
    from kimi.datafeed import DataFeed
    now = int(time.time())
    shift = 3 * 3600
    bars = [(now + shift - i * 900, 1.10, 1.11, 1.09, 1.105, 10)
            for i in range(39, -1, -1)]          # 40 bars, newest last
    fake = _fake_mt5(("EURUSD.s",))
    fake.TIMEFRAME_M15 = 15
    fake.copy_rates_from_pos = lambda sym, tf, pos, n: bars
    fake.symbol_info_tick = lambda sym: types.SimpleNamespace(
        time=now + shift, bid=1.104, ask=1.105)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    feed = DataFeed()
    cs = feed._fetch_mt5_klines("EURUSD", "15m", 40)
    assert cs is not None and cs.source == "mt5"
    assert abs(cs.t[-1] / 1000 - now) < 900       # newest bar ≈ current bucket
    assert cs.closed[-1] in (True, False)         # sanity: no crash on flags


def test_mt5_frozen_tick_keeps_bars_visibly_stale(monkeypatch):
    """Weekend: frozen tick → no shift, bars stay old → freshness gate blocks."""
    import sys
    import types
    from kimi.datafeed import DataFeed
    now = int(time.time())
    shift = 3 * 3600
    # last bar "Friday 23:55 broker" and tick frozen there; now is Saturday
    last_bar = now - 20 * 3600 + shift
    bars = [(last_bar - i * 900, 1.10, 1.11, 1.09, 1.105, 10)
            for i in range(39, -1, -1)]
    fake = _fake_mt5(("EURUSD.s",))
    fake.TIMEFRAME_M15 = 15
    fake.copy_rates_from_pos = lambda sym, tf, pos, n: bars
    fake.symbol_info_tick = lambda sym: types.SimpleNamespace(
        time=last_bar + 299, bid=1.104, ask=1.105)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    feed = DataFeed()
    cs = feed._fetch_mt5_klines("EURUSD", "15m", 40)
    assert cs is not None
    age_h = (now - cs.t[-1] / 1000) / 3600
    assert age_h > 15          # unshifted AND old — engine gate must see stale


def test_bybit_venue_rejects_noncrypto(tmp_path):
    import kimi.engine as engmod
    from kimi.scoring import ScoreCard
    from kimi.signals import Signal
    eng = engmod.Engine()
    eng.paper.state_path = str(tmp_path / "st.json")
    card = ScoreCard("EURUSD", 1, 90.0, "A+", {}, [], True)
    sig = Signal(id="KV1", symbol="EURUSD", direction=1, entry=101,
                 sl=100, tp1=102.5, tp2=104, atr=0.5, card=card,
                 valid_until=time.time() + 3600)
    eng.signals[sig.id] = sig
    r = eng.execute(sig.id, venue="bybit")
    assert not r["ok"] and "crypto" in r["error"]


# ----------------------------------------------------------------------
# host execution bridge (Athena pipeline) — host modules mocked
# ----------------------------------------------------------------------
def _fake_host_modules(monkeypatch, *, account_env="demo", severity="fresh",
                       risk_ok=True, exec_ok=True, bybit_acct_ok=False):
    import sys
    import types

    calls = {"guardian": None, "risk": None, "exec": None}

    cfg = types.ModuleType("config")
    cfg.CONFIG = {}

    mt5_exec = types.ModuleType("mt5_executor")
    mt5_exec._effective_mt5_symbol_map = lambda: {
        "EUR/USD": "EURUSD.s", "Dow Jones": "US30.s", "XAU/USD": "XAUUSD.s"}
    mt5_exec.mt5_get_account = lambda: {
        "error": False, "balance": 50000.0, "equity": 50000.0,
        "accountEnvironment": account_env}
    mt5_exec.mt5_get_positions = lambda: {"error": False, "positions": []}
    mt5_exec.mt5_get_symbol_info = lambda pair: {
        "error": False, "volume_min": 0.01, "volume_max": 100.0}

    bybit_exec = types.ModuleType("bybit_executor")
    bybit_exec.bybit_get_account = lambda: (
        {"error": False, "balance": 10000.0, "equity": 10000.0}
        if bybit_acct_ok else {"error": True, "detail": "no keys"})
    bybit_exec.bybit_get_positions = lambda: {"error": False, "positions": []}
    bybit_exec.bybit_get_symbol_info = lambda pair: {"error": False}

    class _Appr:
        def __init__(self, ok):
            self.approved = ok
            self.volume = 0.01
            self.reason = "OK" if ok else "TEST_BLOCK"

    risk = types.ModuleType("risk_engine")
    def _risk_check(**kw):
        calls["risk"] = kw
        return _Appr(risk_ok)
    risk.risk_check = _risk_check

    guard = types.ModuleType("guardian")
    def _ptc(sig, positions, account, raw):
        calls["guardian"] = sig
        return True, "OK"
    guard.pre_trade_check = _ptc

    lifec = types.ModuleType("execution_lifecycle")
    def _rme(venue, signal, approval):
        calls["exec"] = (venue, signal)
        if exec_ok:
            return {"success": True, "ticket": 12345, "volume": approval.volume,
                    "entryPrice": signal["price"], "sl": signal["sl"],
                    "tp": signal["tp1"]}
        return {"success": False, "error": "BROKER_FAIL"}
    lifec.run_managed_execution = _rme

    ms = types.ModuleType("athena_app.services.market_state")
    ms.candle_freshness_diagnostic = lambda pair, tf, candles: {
        "stalenessSeverity": severity, "lastBarEpoch": int(time.time()),
        "lastBarIso": "2026-08-18T00:00:00Z", "lastBarAgeSec": 1,
        "hasCurrentBucket": severity == "fresh"}

    for name, mod in {"config": cfg, "risk_engine": risk, "guardian": guard,
                      "execution_lifecycle": lifec, "mt5_executor": mt5_exec,
                      "bybit_executor": bybit_exec,
                      "athena_app.services.market_state": ms}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    import kimi.host_exec as hx
    hx._reset_cache()
    monkeypatch.setattr(hx, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(hx, "_notify", lambda *a, **k: None)
    return calls


def _host_engine(monkeypatch, tmp_path, symbol="EURUSD"):
    import kimi.engine as engmod
    from kimi.datafeed import CandleSet
    eng = engmod.Engine()
    eng.paper.state_path = str(tmp_path / "st.json")

    def fake_klines(sym, tf, **kw):
        n = 60
        now = int(time.time() * 1000)
        t = np.array([now - i * 300_000 for i in range(n)][::-1])
        c = np.linspace(100, 101, n)
        return CandleSet(sym, tf, t, c, c + 0.5, c - 0.5, c,
                         np.full(n, 10.0), np.zeros(n),
                         np.ones(n, dtype=bool), "mt5")

    monkeypatch.setattr(eng.feed, "klines", fake_klines)
    monkeypatch.setattr(eng.feed, "_mt5_resolve", lambda s: s + ".s")
    monkeypatch.setattr(eng.feed, "mt5_tick",
                        lambda s: {"price": 101.0, "bid": 100.9, "ask": 101.1,
                                   "source": "mt5"})
    from kimi.scoring import ScoreCard
    from kimi.signals import Signal
    card = ScoreCard(symbol, 1, 90.0, "A+", {}, [], True)
    sig = Signal(id="KH1", symbol=symbol, direction=1, entry=101.0,
                 sl=100.0, tp1=102.5, tp2=104.0, atr=0.5, card=card,
                 valid_until=time.time() + 3600)
    eng.signals[sig.id] = sig
    return eng, sig


def test_host_exec_full_pipeline_mt5(monkeypatch, tmp_path):
    calls = _fake_host_modules(monkeypatch)
    eng, sig = _host_engine(monkeypatch, tmp_path)
    r = eng.execute(sig.id, venue="host")
    assert r["ok"] and r["venue"] == "host-mt5"
    assert r["pair"] == "EUR/USD"           # canonical → host display name
    venue, payload = calls["exec"]
    assert venue == "mt5"
    assert payload["direction"] == "LONG" and payload["sl"] == 100.0
    assert payload["type"] == "forex"
    assert payload["candleConsistency"]["M5"]["status"] == "OK"
    assert calls["risk"]["execution_context"] == "kimi_engine"
    assert calls["guardian"]["pair"] == "EUR/USD"
    assert sig.id not in eng.signals        # marked EXECUTED + removed
    assert eng.events[-1]["type"] == "HOST" and eng.events[-1]["ticket"] == 12345


def test_host_exec_demo_gate_blocks_real_account(monkeypatch, tmp_path):
    calls = _fake_host_modules(monkeypatch, account_env="real")
    eng, sig = _host_engine(monkeypatch, tmp_path)
    r = eng.execute(sig.id, venue="host")
    assert not r["ok"] and "demo" in r["error"].lower()
    assert calls["exec"] is None and calls["risk"] is None


def test_host_exec_blocks_stale_data(monkeypatch, tmp_path):
    calls = _fake_host_modules(monkeypatch, severity="stale_multi_bucket")
    eng, sig = _host_engine(monkeypatch, tmp_path)
    r = eng.execute(sig.id, venue="host")
    assert not r["ok"] and "stale" in r["error"].lower()
    assert calls["exec"] is None and calls["risk"] is None


def test_host_exec_risk_rejection(monkeypatch, tmp_path):
    calls = _fake_host_modules(monkeypatch, risk_ok=False)
    eng, sig = _host_engine(monkeypatch, tmp_path)
    r = eng.execute(sig.id, venue="host")
    assert not r["ok"] and "TEST_BLOCK" in r["error"]
    assert calls["exec"] is None            # risk gate precedes broker


def test_host_exec_bybit_account_unavailable(monkeypatch, tmp_path):
    calls = _fake_host_modules(monkeypatch, bybit_acct_ok=False)
    eng, sig = _host_engine(monkeypatch, tmp_path, symbol="BTCUSDT")
    r = eng.execute(sig.id, venue="host")
    assert not r["ok"] and "bybit account unavailable" in r["error"]
    assert calls["exec"] is None


def test_auto_venue_prefers_host_when_available(monkeypatch, tmp_path):
    calls = _fake_host_modules(monkeypatch)
    eng, sig = _host_engine(monkeypatch, tmp_path)
    r = eng.execute(sig.id, venue="auto")
    assert r["ok"] and r["venue"] == "host-mt5"
    assert calls["exec"] is not None


def test_min_score_drops_subthreshold_signals(tmp_path):
    import kimi.engine as engmod
    from kimi.scoring import ScoreCard
    from kimi.signals import Signal
    eng = engmod.Engine()
    eng.paper.state_path = str(tmp_path / "st.json")
    keep = Signal(id="KHI", symbol="EURUSD", direction=1, entry=101,
                  sl=100, tp1=102.5, tp2=104, atr=0.5,
                  card=ScoreCard("EURUSD", 1, 80.0, "A", {}, [], True),
                  valid_until=time.time() + 3600)
    drop = Signal(id="KLO", symbol="GBPUSD", direction=1, entry=101,
                  sl=100, tp1=102.5, tp2=104, atr=0.5,
                  card=ScoreCard("GBPUSD", 1, 70.0, "B", {}, [], True),
                  valid_until=time.time() + 3600)
    eng.signals[keep.id] = keep
    eng.signals[drop.id] = drop
    r = eng.set_min_score(75)
    assert r["ok"] and r["minScore"] == 75
    assert keep.id in eng.signals
    assert drop.id not in eng.signals
    snap = eng.snapshot()
    ids = {s["id"] for s in snap["signals"]}
    assert "KHI" in ids and "KLO" not in ids


def test_host_exec_disabled_falls_back_to_paper(monkeypatch, tmp_path):
    _fake_host_modules(monkeypatch)
    from kimi.config import Config
    monkeypatch.setattr(Config, "HOST_EXEC", False)
    eng, sig = _host_engine(monkeypatch, tmp_path)
    r = eng.execute(sig.id, venue="auto")
    assert r["ok"] and r["venue"] == "paper"
    assert eng.paper.positions              # paper position opened
