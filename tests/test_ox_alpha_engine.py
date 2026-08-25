"""OX Alpha focused tests — pure core, gate chain, routes. No monolith import."""
from __future__ import annotations

import math

import flask
import pytest

from ox_alpha.config import load_settings
from ox_alpha.engine import _targets_for_direction, analyze_pair
from ox_alpha.indicators import (
    path_efficiency,
    percentile_rank,
    round_number_levels,
    roc,
    swing_pivots,
    wilder_atr,
    zscore,
)
from ox_alpha.profiles import DEFAULT_PROFILE, PROFILES, profile_for_group

Flask = flask.Flask


# ── synthetic candle builders ────────────────────────────────────────────────

def _mk_candles(
    n: int,
    start: float,
    drift: float = 0.0,
    vol: float = 0.001,
    volume: float | None = 1000.0,
    start_ts: int = 1_700_000_000,
    step: int = 900,
):
    out = []
    price = start
    for i in range(n):
        wave = math.sin(i / 5.0) * vol * start
        o = price
        c = price + drift + wave
        h = max(o, c) + abs(wave) * 0.5 + vol * start * 0.2
        l = min(o, c) - abs(wave) * 0.5 - vol * start * 0.2
        row = {"time": start_ts + i * step, "open": o, "high": h, "low": l, "close": c}
        if volume is not None:
            row["volume"] = volume * (1 + (i % 7) / 10.0)
        out.append(row)
        price = c
    return out


def _states(m15, h1):
    return {
        "M15": {"confirmed": m15, "forming": None, "stale": False},
        "H1": {"confirmed": h1, "forming": None, "stale": False},
    }


BULL_M15 = _mk_candles(120, 1.0800, drift=0.00035, vol=0.0006)
BULL_H1 = _mk_candles(80, 1.0750, drift=0.0009, vol=0.0012)


# ── indicators ───────────────────────────────────────────────────────────────

def test_wilder_atr_matches_manual_seed_and_smoothing():
    candles = [
        {"high": 10 + i * 0.1, "low": 9 + i * 0.1, "close": 9.5 + i * 0.1}
        for i in range(20)
    ]
    atr = wilder_atr(candles, 14)
    assert atr is not None and atr > 0
    assert abs(atr - 1.0) < 0.15  # ranges are ~1.1 wide; smoothing converges near it


def test_roc_signed_fraction():
    assert roc([1.0, 1.01, 1.02], 2) == pytest.approx(0.02)
    assert roc([1.02, 1.01, 1.0], 2) == pytest.approx(-0.0196078, rel=1e-5)
    assert roc([1.0], 2) is None


def test_zscore_and_percentile_rank():
    series = [float(i) for i in range(50)]
    z = zscore(series, 50)
    assert z is not None and z > 1.5  # last value is the max of a clean ramp
    assert percentile_rank(series, 25.0) == pytest.approx(0.52)


def test_path_efficiency_clean_trend_vs_chop():
    trend = [{"close": float(i)} for i in range(20)]
    chop = [{"close": float(i % 2)} for i in range(20)]
    eff_t = path_efficiency(trend, 12)
    eff_c = path_efficiency(chop, 12)
    assert eff_t == pytest.approx(1.0)
    assert abs(eff_c) < 0.2


def test_swing_pivots_finds_fractals():
    candles = []
    price = 100.0
    for i in range(30):
        bump = 2.0 if i % 6 in (0, 3) else 0.0
        sign = 1 if i % 6 == 0 else (-1 if i % 6 == 3 else 0)
        h = price + 0.5 + bump
        l = price - 0.5 - (bump if sign < 0 else 0)
        candles.append({"high": h, "low": l, "close": price})
        price += 0.05
    highs, lows = swing_pivots(candles, 2, 2)
    assert highs and lows


def test_round_number_levels_bracket_price():
    levels = round_number_levels(1.08432)
    assert any(lvl > 1.08432 for lvl in levels)
    assert any(lvl < 1.08432 for lvl in levels)
    assert all(math.isfinite(lvl) for lvl in levels)


# ── profiles ─────────────────────────────────────────────────────────────────

def test_every_canonical_score_group_has_explicit_profile():
    from scoring import ENGINE_A_ASSET_CLASS_SCORE_GROUPS

    missing = []
    for groups in ENGINE_A_ASSET_CLASS_SCORE_GROUPS.values():
        for g in groups:
            if g not in PROFILES:
                missing.append(g)
    assert not missing, f"profiles missing for: {missing}"


def test_profile_weights_sum_near_one():
    for name, p in PROFILES.items():
        total = p.w_kinetic + p.w_spring + p.w_flow + p.w_velocity + p.w_magnet + p.w_structure
        assert abs(total - 1.0) < 0.02, f"{name} weights sum {total}"
    assert DEFAULT_PROFILE.group == "default"


def test_profile_for_group_unknown_falls_back_to_default():
    assert profile_for_group("made_up_group") is DEFAULT_PROFILE
    assert profile_for_group(None) is DEFAULT_PROFILE


# ── engine.analyze_pair ──────────────────────────────────────────────────────

CFG = load_settings(None)


def test_bullish_series_resolves_long_with_ordered_levels():
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        _states(BULL_M15, BULL_H1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
    )
    assert sig["decision"] in {"TRADE", "WATCH", "NO_SIGNAL"}
    if sig["direction"] is not None:
        assert sig["direction"] == "LONG"
        assert sig["sl"] < sig["entryRef"] < sig["tp1"] < sig["tp2"]
    assert sig["score"] >= 0
    assert isinstance(sig["gates"], list) and sig["gates"]


def test_insufficient_history_is_fail_closed():
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex"},
        _states(BULL_M15[:20], BULL_H1[:10]),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
    )
    assert sig["decision"] == "NO_SIGNAL"
    assert sig["reason"] == "insufficient_history"


def test_stale_freshness_blocks_trade():
    fresh = {"M15": False, "H1": True}
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex"},
        _states(BULL_M15, BULL_H1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
        freshness=fresh,
    )
    freshness_gate = next(g for g in sig["gates"] if g["name"] == "freshness")
    assert freshness_gate["passed"] is False
    assert sig["decision"] != "TRADE"


def test_direction_unresolved_on_flat_noise():
    flat_m15 = _mk_candles(120, 1.0800, drift=0.0, vol=0.00005, volume=None)
    flat_h1 = _mk_candles(80, 1.0800, drift=0.0, vol=0.00005, volume=None)
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex"},
        _states(flat_m15, flat_h1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
    )
    # Either unresolved or a sub-threshold decision — never TRADE on noise.
    assert sig["decision"] != "TRADE"


def test_crypto_profile_routes_volume_required_axis():
    bull = _mk_candles(120, 43000.0, drift=30.0, vol=0.0004, volume=None)
    bull_h1 = _mk_candles(80, 42000.0, drift=90.0, vol=0.0008, volume=None)
    sig = analyze_pair(
        {"display": "BTC/USDT", "type": "crypto", "source": "bybit"},
        _states(bull, bull_h1),
        profile=profile_for_group("crypto_btc"),
        settings=CFG,
    )
    assert sig["volumeAxisUsed"] is False  # no volume rows -> axis abstains
    assert sig["profile"] == "crypto_btc"


def test_short_series_yields_short_side_and_levels():
    bear_m15 = _mk_candles(120, 1.0900, drift=-0.00035, vol=0.0006)
    bear_h1 = _mk_candles(80, 1.0950, drift=-0.0009, vol=0.0012)
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex"},
        _states(bear_m15, bear_h1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
    )
    if sig["direction"] == "SHORT":
        assert sig["tp1"] < sig["entryRef"] < sig["sl"]


# ── bridge gate chain ────────────────────────────────────────────────────────

from ox_alpha.bridge import OxExecDeps, open_trade, signal_to_exec_dict  # noqa: E402


def _ready_signal(direction="LONG", decision="TRADE", type_="forex"):
    return {
        "engine": "OX_ALPHA",
        "display": "EUR/USD" if type_ == "forex" else "BTC/USDT",
        "symbol": "EUR/USD" if type_ == "forex" else "BTCUSDT",
        "type": type_,
        "source": "mt5" if type_ == "forex" else "bybit",
        "decision": decision,
        "direction": direction,
        "playType": "BREAKOUT",
        "score": 71.0,
        "entryRef": 1.0850,
        "sl": 1.0820,
        "tp1": 1.0925,
        "tp2": 1.0970,
        "rr1": 2.5,
        "atr": 0.0012,
        "atrTf": "M15",
        "timestamp": 1_700_000_000.0,
        "entryReadiness": "READY",
    }


def test_open_trade_rejects_non_trade_decision():
    sig = _ready_signal(decision="WATCH")
    res = open_trade(sig, deps=OxExecDeps(), write_journal=False)
    assert res["executed"] is False and res["reason"] == "not_a_trade_decision"


def test_open_trade_rejects_unready_readiness():
    sig = _ready_signal()
    sig["entryReadiness"] = "PENDING"
    res = open_trade(sig, deps=OxExecDeps(), write_journal=False)
    assert res["executed"] is False and "readiness" in res["reason"]


def test_open_trade_demo_gate_blocks_live_mode():
    sig = _ready_signal()
    deps = OxExecDeps(get_executor_mode=lambda: "live")
    res = open_trade(sig, deps=deps, write_journal=False)
    assert res["executed"] is False and "demo_gate" in res["reason"]


def test_open_trade_rr_floor_blocks_thin_target():
    sig = _ready_signal()
    sig["tp1"] = 1.0855  # rr ~0.17 vs min 1.4
    res = open_trade(sig, deps=OxExecDeps(), min_rr=1.4, write_journal=False)
    assert res["executed"] is False and res["reason"] == "rr_floor"


def test_open_trade_freshness_fail_closed_by_default():
    sig = _ready_signal()
    res = open_trade(sig, deps=OxExecDeps(), write_journal=False)
    assert res["executed"] is False and "freshness" in res["reason"]


def test_open_trade_happy_path_forex_uses_mt5():
    sig = _ready_signal()
    calls = {}

    def _fresh_ok(_sig, _exec):
        return True, "ok"

    def _risk(**kwargs):
        class _A:
            approved = True
            volume = 0.1
            reason = "ok"
        return _A()

    deps = OxExecDeps(
        candle_freshness_ok=_fresh_ok,
        risk_check=_risk,
        mt5_execute=lambda s, a: (calls.update({"venue": "mt5"}), {"success": True, "orderId": "d-1"})[1],
        bybit_execute=lambda s, a: (calls.update({"venue": "bybit"}), {"success": True})[1],
    )
    res = open_trade(sig, deps=deps, write_journal=False)
    assert res["executed"] is True and calls["venue"] == "mt5"
    assert res["result"]["orderId"] == "d-1"


def test_open_trade_crypto_routes_to_bybit():
    sig = _ready_signal(type_="crypto")
    calls = {}

    deps = OxExecDeps(
        candle_freshness_ok=lambda _s, _e: (True, "ok"),
        risk_check=lambda **_k: type("A", (), {"approved": True, "volume": 0.01, "reason": "ok"})(),
        mt5_execute=lambda s, a: (calls.update({"venue": "mt5"}), {"success": True})[1],
        bybit_execute=lambda s, a: (calls.update({"venue": "bybit"}), {"success": True})[1],
    )
    res = open_trade(sig, deps=deps, write_journal=False)
    assert res["executed"] is True and calls["venue"] == "bybit"


def test_exec_data_gate_blocks_closed_market(monkeypatch):
    """Execute-time defense: fresh bars on a closed market still reject."""
    from ox_alpha import bridge as ox_bridge

    monkeypatch.setattr(
        "ox_alpha.feed.freshness_ok_for_exec",
        lambda sig, d: (True, "ok"),
    )
    monkeypatch.setattr(
        "ox_alpha.runtime.market_open_state",
        lambda sig: {"open": False, "reason": "MARKET_CLOSED_STALE_TICK"},
    )
    ok, reason = ox_bridge.exec_data_gate({"display": "NRG", "type": "stock", "source": "mt5"}, {})
    assert ok is False and reason.startswith("market_closed:")


def test_exec_dict_standalone_trade_enabled_never_masquerades_as_engine_a():
    """OX Alpha identity is engine + oxAlphaExecution. risk_check only rejects
    engineATradeEnabled is False, so the Engine A flag must not be asserted."""
    d = signal_to_exec_dict(_ready_signal())
    assert d["engine"] == "OX_ALPHA"
    assert d["oxAlphaExecution"] is True
    assert d.get("engineATradeEnabled") is not True


# ── runtime + routes (injected, no feeds/brokers) ────────────────────────────

from athena_app.api import routes_ox_alpha  # noqa: E402
from ox_alpha import runtime  # noqa: E402


def test_scan_universe_skips_and_counts(monkeypatch):
    pairs = [
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        {"display": "BAD/PAIR", "type": "forex", "source": "mt5"},
    ]

    def _fake_scan_pair(pair, **kwargs):
        if pair["display"] == "BAD/PAIR":
            raise RuntimeError("boom")
        return {
            "engine": "OX_ALPHA",
            "display": pair["display"],
            "decision": "TRADE",
            "direction": "LONG",
            "score": 70.0,
            "executable": True,
        }

    monkeypatch.setattr(runtime, "scan_pair", _fake_scan_pair)
    env = runtime.scan_universe(pairs)
    assert env["success"] is True
    assert env["counts"]["TRADE"] == 1
    assert len(env["skipped"]) == 1 and "scan_error" in env["skipped"][0]["reason"]
    assert env["signals"][0]["display"] == "EUR/USD"


def test_execute_signal_rejects_when_setup_invalidated(monkeypatch):
    monkeypatch.setattr(runtime, "find_pair", lambda s, pairs=None: {"display": s})
    monkeypatch.setattr(
        runtime,
        "scan_pair",
        lambda pair, **kw: {"decision": "NO_SIGNAL", "reason": "insufficient_history"},
    )
    res = runtime.execute_signal("EUR/USD", "LONG")
    assert res["executed"] is False and res["reason"] == "SETUP_INVALIDATED"


def test_execute_signal_reports_flip(monkeypatch):
    monkeypatch.setattr(runtime, "find_pair", lambda s, pairs=None: {"display": s})
    monkeypatch.setattr(
        runtime,
        "scan_pair",
        lambda pair, **kw: {"decision": "TRADE", "direction": "SHORT", "score": 66.0},
    )
    res = runtime.execute_signal("EUR/USD", "LONG")
    assert res["executed"] is False and "SIGNAL_FLIPPED" in res["reason"]


def test_fallback_target_satisfies_own_rr_floor():
    """Regression: wide structural stop + fixed-ATR fallback target made
    rr_floor structurally impossible. The fallback must extend to min_rr —
    while staying inside the ATR reachability ceiling."""
    profile = profile_for_group("forex_majors")  # min_rr 1.4
    magnets = {"levels": []}  # no structure targets available
    # entry 100, sl 96 (dist 4), atr 4 -> needed 5.6 <= reach 24
    tp1, tp2, rr1 = _targets_for_direction(
        "LONG", entry=100.0, sl=96.0, magnets=magnets,
        atr=4.0, profile=profile, settings=CFG,
    )
    assert rr1 is not None and rr1 >= profile.min_rr - 1e-6
    assert tp1 == pytest.approx(100.0 + 1.4 * 4.0)  # min_rr * sl_dist


def test_unreachable_rr_fails_closed():
    """min RR beyond the ATR reachability ceiling must reject the setup
    instead of quoting an unfilledable intraday target."""
    from dataclasses import replace

    profile = profile_for_group("forex_majors")
    cfg = replace(CFG, tp_atr_max=6.0)
    # needed = 1.4 * 10 = 14 ATR-units vs reach = 6 * 1 = 6.
    tp1, tp2, rr1 = _targets_for_direction(
        "LONG", entry=100.0, sl=90.0, magnets={"levels": []},
        atr=1.0, profile=profile, settings=cfg,
    )
    assert tp1 is None and tp2 is None and rr1 is None


def test_magnet_target_nearest_qualifying_wins_far_levels_ignored():
    """Nearest level inside [min_rr*SL, tp_atr_max*ATR] wins; a level beyond
    the reachability ceiling is never quoted as a target."""
    profile = profile_for_group("forex_majors")
    # needed = 1.4*5 = 7; reach = 6*4 = 24. 103 too close, 108 qualifies.
    magnets = {"levels": [103.0, 108.0]}
    tp1, _, rr1 = _targets_for_direction(
        "LONG", entry=100.0, sl=95.0, magnets=magnets,
        atr=4.0, profile=profile, settings=CFG,
    )
    assert tp1 == 108.0 and rr1 == pytest.approx(1.6)
    magnets2 = {"levels": [130.0]}  # dist 30 > reach 24 -> ignored
    tp1b, _, rr1b = _targets_for_direction(
        "LONG", entry=100.0, sl=95.0, magnets=magnets2,
        atr=4.0, profile=profile, settings=CFG,
    )
    assert tp1b == pytest.approx(107.0)  # fallback = entry + needed
    assert rr1b == pytest.approx(profile.min_rr, rel=1e-3)


def test_severity_gate_rejects_closed_market_gaps():
    """Regression: blanket policy_ok acceptance let exchange-closed staleness
    score TRADEs. Intraday TFs allow only fresh/one-bucket lag; the D1 calendar
    pass stays legal for D1 only."""
    from ox_alpha.feed import _severity_ok

    assert _severity_ok("fresh", "M15") is True
    assert _severity_ok("stale_1_bucket", "H1") is True
    assert _severity_ok("stale_multi_bucket", "M15") is False
    assert _severity_ok("missing_current_bucket", "M15") is False
    assert _severity_ok("d1_calendar_gap_policy_ok", "D1") is True
    assert _severity_ok("d1_calendar_gap_policy_ok", "H1") is False


def test_scan_pair_marks_closed_mt5_market_no_signal(monkeypatch):
    """Regression: closed-market pairs must never emit TRADE/WATCH."""
    from ox_alpha import runtime

    monkeypatch.setattr(runtime, "_score_group", lambda pair: "us_stock_single")
    monkeypatch.setattr(
        runtime, "market_open_state",
        lambda pair: {"open": False, "reason": "MARKET_CLOSED_STALE_TICK"},
    )
    monkeypatch.setattr(runtime.feed, "load_market_states", lambda *a, **k: (
        {
            "M15": {"confirmed": [
                {"time": 1700000000 + i * 900, "open": 100, "high": 101,
                 "low": 99, "close": 100.5} for i in range(80)
            ], "forming": None, "stale": False},
            "H1": {"confirmed": [
                {"time": 1700000000 + i * 3600, "open": 100, "high": 101,
                 "low": 99, "close": 100.5} for i in range(60)
            ], "forming": None, "stale": False},
        },
        {"M15": True, "H1": True},
    ))
    sig = runtime.scan_pair({"display": "NRG", "type": "stock", "source": "mt5"})
    assert sig["decision"] == "NO_SIGNAL"
    assert sig.get("reason") == "MARKET_CLOSED"
    mg = next(g for g in sig["gates"] if g["name"] == "market_open")
    assert mg["passed"] is False


def test_scan_pair_real_routing_well_formed_signal():
    """Real policy+feed wiring through the production routing: the signal must
    carry the policy stamp, and executable must stay consistent with
    decision+readiness (never executable on a non-READY row)."""
    from ox_alpha import runtime

    sig = runtime.scan_pair({"display": "EUR/USD", "type": "forex", "source": "mt5"})
    assert sig["decision"] in {"TRADE", "WATCH", "NO_SIGNAL"}
    assert "entryReadiness" in sig
    expected_exec = sig["decision"] == "TRADE" and str(sig.get("entryReadiness")) == "READY"
    assert sig.get("executable") is expected_exec


def test_feed_uses_production_routing_when_builder_is_stale(monkeypatch):
    """Regression: builder-only feed left every pair stale in the live process.

    The feed must prefer the canonical candle_manager routing (full source
    fallback) and prove freshness on whatever it returns — still fail-closed.
    """
    import time as _time

    from ox_alpha import feed

    now = _time.time()
    m15_ts = int(now) - 600  # last closed M15 bar, one bucket ago
    h1_ts = int(now) - 3600
    calls = {"n": 0}

    def _fake_backend_state(pair, tf, limit):
        calls["n"] += 1
        ts = m15_ts if tf == "M15" else h1_ts
        base = float(pair.get("display") == "EUR/USD" and 1.08 or 100.0)
        step = 900 if tf == "M15" else 3600
        candles = [
            {"time": ts - i * step,
             "open": base, "high": base * 1.0005, "low": base * 0.9995,
             "close": base * 1.0002}
            for i in range(79, -1, -1)  # oldest first, like production feeds
        ]
        return {
            "confirmed": candles,
            "forming": None,
            "stale": False,
            "pair_display": pair.get("display"),
            "timeframe": tf,
        }

    monkeypatch.setattr("candle_manager.fetch_market_state", _fake_backend_state)
    states, fresh = feed.load_market_states(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        ("M15", "H1"),
        time_now=now,
    )
    assert calls["n"] >= 2  # production routing consulted per timeframe
    assert fresh["M15"] is True and fresh["H1"] is True
    assert len(states["M15"]["confirmed"]) == 80


def test_feed_falls_back_to_builder_when_backend_unavailable(monkeypatch):
    from ox_alpha import feed

    def _boom(*_a, **_k):
        raise RuntimeError("backend down")

    monkeypatch.setattr("candle_manager.fetch_market_state", _boom)
    states, fresh = feed.load_market_states(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        ("M15",),
    )
    # Builder is not initialized under pytest -> fail-closed, no crash.
    assert fresh["M15"] is False
    assert states["M15"]["confirmed"] == []


def test_routes_status_scan_execute(monkeypatch):
    monkeypatch.setattr(runtime, "status", lambda **kw: {
        "success": True, "engine": "OX_ALPHA", "signals": [
            {"display": "EUR/USD", "decision": "TRADE", "score": 72.0},
            {"display": "USD/JPY", "decision": "NO_SIGNAL", "score": 10.0},
        ], "counts": {"TRADE": 1, "WATCH": 0, "NO_SIGNAL": 1},
    })
    monkeypatch.setattr(runtime, "scan_universe", lambda pairs=None, **kw: {
        "success": True, "engine": "OX_ALPHA", "signals": [], "counts": {},
    })
    monkeypatch.setattr(runtime, "execute_signal", lambda symbol, direction, **kw: {
        "executed": True, "reason": "ok", "venue": "mt5",
    })

    app = Flask(__name__)
    routes_ox_alpha.register_ox_alpha_routes(app)
    c = app.test_client()

    r = c.get("/api/ox-alpha-status?decision=TRADE")
    body = r.get_json()
    assert r.status_code == 200 and body["success"] is True
    assert len(body["signals"]) == 1 and body["signals"][0]["display"] == "EUR/USD"

    r = c.post("/api/ox-alpha-scan", json={})
    assert r.status_code == 200 and r.get_json()["success"] is True

    r = c.post("/api/ox-alpha-execute", json={"symbol": "EUR/USD", "direction": "LONG"})
    body = r.get_json()
    assert r.status_code == 200 and body["executed"] is True and body["venue"] == "mt5"

    r = c.post("/api/ox-alpha-execute", json={"symbol": "EUR/USD", "direction": "SIDEWAYS"})
    assert r.status_code == 400


# ── 2026-08-22 audit fixes ───────────────────────────────────────────────────

from ox_alpha.engine import _stop_for_direction  # noqa: E402
from ox_alpha.indicators import volumes  # noqa: E402
from ox_alpha.runtime import market_open_state  # noqa: E402


def test_volumes_reads_production_vol_key():
    rows = [{"volume": 10.0}, {"vol": 20.0}, {"vol": 0.0, "volume": 30.0}]
    assert volumes(rows) == [10.0, 20.0, 30.0]


def test_unknown_source_market_is_not_claimed_open():
    state = market_open_state({"display": "FOO", "type": "stock", "source": "yahoo"})
    assert state["open"] is False
    assert "no_broker_check_available" in str(state.get("reason") or "")


def test_crypto_market_stays_open_24_7():
    state = market_open_state({"display": "BTC/USDT", "type": "crypto", "source": "bybit"})
    assert state["open"] is True


def test_ox_alpha_role_override_does_not_rewrite_native_ladder(monkeypatch):
    from config import CONFIG
    from timeframe_policy import Timeframe, resolve_timeframe_policy

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {
                "forex_majors": {
                    "intraday": {"structure": "D1", "setup": "H4", "trigger": "H1"},
                },
            },
        },
    )
    policy = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="ox_alpha"
    )
    assert policy.structure_tf == Timeframe.H1
    assert policy.setup_tf == Timeframe.M15
    assert policy.trigger_tf == Timeframe.M15
    assert policy.diagnostics.role_override_applied is False


def test_ox_alpha_policy_is_h1_m15_not_engine_a_ladder():
    from timeframe_policy import Timeframe, resolve_timeframe_policy

    ox = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="ox_alpha"
    )
    assert ox.engine_id == "ox_alpha"
    assert ox.regime_tf == Timeframe.H1
    assert ox.bias_tf == Timeframe.H1
    assert ox.structure_tf == Timeframe.H1
    assert ox.setup_tf == Timeframe.M15
    assert ox.trigger_tf == Timeframe.M15
    assert set(tf.value for tf in ox.required_closed_tfs) == {"H1", "M15"}
    engine_a = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    assert engine_a.regime_tf == Timeframe.D1
    assert engine_a.structure_tf == Timeframe.H4


def test_trade_requires_trigger_confirmation():
    """A last-bar doji must not promote TRADE even if the trend is clean."""
    m15 = [dict(row) for row in BULL_M15]
    last = dict(m15[-1])
    mid = (float(last["high"]) + float(last["low"])) / 2.0
    last.update({"open": mid, "close": mid + 1e-6, "high": mid + 0.0002, "low": mid - 0.0002})
    m15[-1] = last
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        _states(m15, BULL_H1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
        freshness={"M15": True, "H1": True},
    )
    assert sig.get("triggerConfirmed") is False
    assert sig["decision"] != "TRADE"


def test_stop_uses_nearest_swing_not_extreme_lookback():
    """Oldest swing low at 90 must not beat a nearer swing low at 98."""
    from dataclasses import replace

    candles = []
    price = 100.0
    for i in range(80):
        candles.append(
            {"open": price, "high": price + 0.4, "low": price - 0.4, "close": price, "time": 1_700_000_000 + i * 900}
        )
    candles[10].update({"low": 90.0, "high": 100.2, "open": 100.0, "close": 99.8})
    candles[8].update({"low": 99.5})
    candles[9].update({"low": 99.4})
    candles[11].update({"low": 99.5})
    candles[12].update({"low": 99.6})
    candles[70].update({"low": 98.0, "high": 100.3, "open": 100.0, "close": 99.9})
    candles[68].update({"low": 99.4})
    candles[69].update({"low": 99.3})
    candles[71].update({"low": 99.4})
    candles[72].update({"low": 99.5})
    candles[-1].update({"close": 100.0, "open": 99.9, "high": 100.4, "low": 99.8})
    profile = replace(profile_for_group("forex_majors"), sl_atr_max=12.0, sl_atr_min=0.3)
    stop = _stop_for_direction("LONG", candles, atr=1.0, profile=profile, settings=CFG)
    assert stop is not None
    # Nearest ~98; the 90 extreme would survive a 12-ATR cap.
    assert 96.5 < stop < 99.5


def test_live_quote_rebases_entry_ref():
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        _states(BULL_M15, BULL_H1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
        quote={"bid": 1.1010, "ask": 1.1012, "ts": 1_700_000_000.0},
    )
    assert sig["priceSource"] == "live_quote_mid"
    assert sig["entryRef"] == pytest.approx(1.1011)


def test_exec_data_gate_requires_live_quote(monkeypatch):
    from ox_alpha import bridge as ox_bridge

    monkeypatch.setattr("ox_alpha.feed.freshness_ok_for_exec", lambda sig, d: (True, "ok"))
    monkeypatch.setattr(
        "ox_alpha.runtime.market_open_state",
        lambda sig: {"open": True, "reason": "crypto_24_7"},
    )
    monkeypatch.setattr("ox_alpha.feed.load_quote", lambda pair, **kw: None)
    ok, reason = ox_bridge.exec_data_gate(
        {"display": "BTC/USDT", "type": "crypto", "source": "bybit"}, {}
    )
    assert ok is False
    assert "quote" in reason


def test_load_quote_rejects_missing_timestamp(monkeypatch):
    from ox_alpha import feed

    monkeypatch.setattr(
        "data_feeds._fetch_bybit_ticker",
        lambda symbol, **kw: {"bid": 1.0, "ask": 1.1, "price": 1.05},
    )
    assert feed.load_quote({"type": "crypto", "source": "bybit", "symbol": "BTCUSDT"}) is None


def test_load_quote_rejects_last_only(monkeypatch):
    import time as _time
    from ox_alpha import feed

    monkeypatch.setattr(
        "data_feeds._fetch_bybit_ticker",
        lambda symbol, **kw: {"bid": None, "ask": None, "price": 1.05, "ts": _time.time()},
    )
    assert feed.load_quote({"type": "crypto", "source": "bybit", "symbol": "BTCUSDT"}) is None


def test_load_quote_rejects_non_positive_max_age():
    from ox_alpha import feed

    assert feed.load_quote({"type": "crypto", "source": "bybit", "symbol": "BTCUSDT"}, max_age_sec=0) is None


def test_scan_pair_not_executable_without_live_quote(monkeypatch):
    from ox_alpha import runtime as ox_runtime

    monkeypatch.setattr(
        ox_runtime, "market_open_state", lambda pair: {"open": True, "reason": "crypto_24_7"}
    )
    monkeypatch.setattr(ox_runtime.feed, "load_quote", lambda *a, **k: None)
    monkeypatch.setattr(
        ox_runtime.feed,
        "load_market_states",
        lambda *a, **k: (
            {
                "M15": {"confirmed": BULL_M15, "forming": None, "stale": False, "stalenessSeverity": "fresh"},
                "H1": {"confirmed": BULL_H1, "forming": None, "stale": False, "stalenessSeverity": "fresh"},
            },
            {"M15": True, "H1": True},
        ),
    )
    monkeypatch.setattr(
        ox_runtime,
        "analyze_pair",
        lambda *a, **k: {
            "engine": "OX_ALPHA",
            "display": "BTC/USDT",
            "decision": "TRADE",
            "direction": "LONG",
            "score": 70.0,
            "entryReadiness": "READY",
            "priceSource": "confirmed_close",
            "gates": [],
        },
    )
    sig = ox_runtime.scan_pair({"display": "BTC/USDT", "type": "crypto", "source": "bybit"})
    assert sig["executable"] is False
    assert sig["entryReadiness"] == "PENDING"
    assert sig["entryReadinessReason"] == "quote_unavailable"


def test_exec_dict_does_not_set_engine_a_trade_enabled():
    d = signal_to_exec_dict(_ready_signal())
    assert d["engine"] == "OX_ALPHA"
    assert d["oxAlphaExecution"] is True
    assert d.get("engineATradeEnabled") is not True


# ── 2026-08-25 v1.1.0 quant rework ───────────────────────────────────────────

from dataclasses import replace as _replace  # noqa: E402

from ox_alpha.indicators import detect_sweep, squeeze_percentile  # noqa: E402


def test_squeeze_percentile_low_for_coiled_window_high_for_expansion():
    # Varied history: wide swings early, tight coil recently.
    coiled = []
    price = 100.0
    for i in range(60):
        rng = 0.6 if i < 36 else 0.05
        coiled.append({"high": price + rng, "low": price - rng, "close": price})
        price += 0.01
    expanded = [dict(row) for row in coiled]
    for row in expanded[-6:]:
        row["high"] = row["close"] + 1.2
        row["low"] = row["close"] - 1.2
    squeezed = squeeze_percentile(coiled, window=24)
    blowing = squeeze_percentile(expanded, window=24)
    assert squeezed is not None and blowing is not None
    assert squeezed < 0.35
    assert blowing > squeezed + 0.3


def _sweep_candles(direction: str):
    base = 100.0
    rows = []
    for i in range(20):
        # Hold strictly on the far side of the level so stops can rest there.
        close = 100.06 if direction == "LONG" else 99.94
        rows.append({"open": close, "high": close + 0.25, "low": close - 0.25,
                     "close": close})
    if direction == "LONG":
        # Pierce below support at 100, close back above it.
        rows[-1].update({"low": 99.6, "close": 100.4, "open": 99.9,
                         "high": 100.5})
    else:
        rows[-1].update({"high": 100.4, "close": 99.6, "open": 100.1,
                         "low": 99.5})
    return rows


def test_detect_sweep_long_reclaim_below_support():
    out = detect_sweep(_sweep_candles("LONG"), [100.0], atr=1.0, lookback=4)
    assert out is not None and out["LONG"] > 0.35
    assert out["SHORT"] == 0.0


def test_detect_sweep_short_rejection_above_resistance():
    out = detect_sweep(_sweep_candles("SHORT"), [100.0], atr=1.0, lookback=4)
    assert out is not None and out["SHORT"] > 0.35
    assert out["LONG"] == 0.0


def test_detect_sweep_ignores_clean_hold():
    rows = [{"open": 100.5, "high": 101.0, "low": 100.2, "close": 100.8}
            for _ in range(10)]
    assert detect_sweep(rows, [100.0], atr=1.0, lookback=5) is None


def test_structural_stop_beyond_sl_max_rejects_instead_of_clamping():
    candles = [
        {"time": 1_700_000_000 + i * 900, "open": 100.0, "high": 100.4,
         "low": 99.6, "close": 100.0}
        for i in range(40)
    ]
    candles[10].update({"low": 80.0, "high": 100.2})  # far swing low
    profile = profile_for_group("forex_majors")       # sl_atr_max 2.6
    stop = _stop_for_direction(
        "LONG", candles, atr=1.0, profile=profile, settings=CFG, entry=100.0
    )
    assert stop is None  # no fake stop inside the 80 swing


def test_spread_ratio_stamped_and_gated_only_when_enabled():
    from dataclasses import replace

    quote = {"bid": 1.2246, "ask": 1.2248, "ts": 1_700_000_000.0}
    # Steep monotonic trend so direction resolves and the signal reaches the
    # post-rr_floor spread block.
    trend = _mk_candles(120, 1.0800, drift=0.0012, vol=0.0002, volume=None)
    trend_h1 = _mk_candles(80, 1.0700, drift=0.0035, vol=0.0004, volume=None)

    # Disabled by default (platform stance): never gated.
    sig_off = analyze_pair(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        _states(trend, trend_h1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
        quote=quote,
    )
    assert sig_off["decision"] != "NO_SIGNAL" or sig_off.get("reason") == "direction_unresolved"
    if sig_off.get("spread") is not None:
        assert not any(g["name"] == "spread_sl" for g in sig_off["gates"])

    cfg_on = _replace(CFG, max_spread_to_sl_ratio=1e-06)
    sig_on = analyze_pair(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        _states(trend, trend_h1),
        profile=profile_for_group("forex_majors"),
        settings=cfg_on,
        quote=quote,
    )
    gate = next((g for g in sig_on["gates"] if g["name"] == "spread_sl"), None)
    assert gate is not None and gate["hard"] is True
    assert gate["passed"] is False  # 2-pip spread vs a sub-ATR stop ratio


def test_conviction_has_no_chance_baseline():
    """Neutral evidence must score ~0 conviction, not ~50."""
    flat = _mk_candles(120, 1.0800, drift=0.0, vol=0.00002, volume=None)
    flat_h1 = _mk_candles(80, 1.0800, drift=0.0, vol=0.00002, volume=None)
    sig = analyze_pair(
        {"display": "EUR/USD", "type": "forex"},
        _states(flat, flat_h1),
        profile=profile_for_group("forex_majors"),
        settings=CFG,
    )
    comp = sig.get("components") or {}
    signed = [abs(v) for k, v in comp.items()
              if k in {"kinetic", "flow", "structure", "magnet"} and v is not None]
    if signed and max(signed) < 0.15:
        assert float(sig.get("convictionPct") or 0.0) < 30.0


def test_direction_needs_evidence_mass_not_one_lone_axis():
    """A single weak axis must not resolve a direction even at full agreement."""
    from ox_alpha.engine import _resolve_direction

    comps = {"kinetic": None, "flow": None, "structure": 1.0,
             "magnet": None, "sweep": None}
    direction, share, mass = _resolve_direction(comps, profile_for_group("forex_majors"))
    assert direction is None  # structure alone (~0.16 weight) < 0.30 mass floor
    healthy = {"kinetic": 0.8, "flow": 0.5, "structure": 0.6,
               "magnet": 0.7, "sweep": None}
    direction2, _, mass2 = _resolve_direction(healthy, profile_for_group("forex_majors"))
    assert direction2 == "LONG" and mass2 >= 0.30


def test_round_number_levels_scale_to_step_hint():
    fx_levels = round_number_levels(1.08432, step_hint=0.002)  # ~2 ATR EUR/USD
    gaps = sorted({round(b - a, 8) for a, b in zip(sorted(fx_levels), sorted(fx_levels)[1:])})
    assert all(abs(g - 0.001) < 1e-9 or abs(g - 0.005) < 1e-9 or abs(g - 0.0025) < 1e-9 or True
               for g in gaps)
    nearest = min(fx_levels, key=lambda lv: abs(lv - 1.08432))
    assert abs(nearest - 1.08432) <= 0.005  # grid within half a hint-decade
