"""MUSE engine: pure-path tests (no athena.py import)."""

from __future__ import annotations

import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest
import shutil

from muse_engine.api import register_muse_routes
from muse_engine.config import MuseConfigError, load_muse_config
from muse_engine.execution import MuseExecutionCoordinator, MuseExecutionError
from muse_engine.market_data import normalize_closed_candles
from muse_engine.models import TIMEFRAME_SECONDS, Candle, MarketSnapshot, Quote
from muse_engine.persistence import MuseRepository
from muse_engine.prisms import halo_field, harmonic_mean, undertow_echo
from muse_engine.scoring import evaluate_snapshot
from muse_engine.sessions import market_is_closed, tide_state


# Tuesday 2026-03-17 13:00 UTC == 09:00 New York (meridian_surge window).
NOW = datetime(2026, 3, 17, 13, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def workdir():
    # The repo's .pytest_tmp is locked on this machine; use a private temp dir.
    path = Path(tempfile.mkdtemp(prefix="muse-test-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _bar(time: float, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(time, o, h, low, c, 100.0, "synthetic")


def _trend_series(timeframe: str, start: float, step: float, count: int) -> list[Candle]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    rows: list[Candle] = []
    price = start
    for index in range(count):
        opened = NOW - (count - index) * seconds
        nxt = price + step
        rows.append(_bar(opened, price, max(price, nxt) + 0.0004, min(price, nxt) - 0.0004, nxt))
        price = nxt
    return rows


def _prime_snapshot() -> MarketSnapshot:
    """Causal LONG candidate: H4 drift up; M15 shelf -> shallow echo -> thrust -> void -> tap; M5 spark."""
    h4 = _trend_series("H4", 1.1000, 0.0012, 120)
    d1 = _trend_series("D1", 1.0500, 0.0015, 100)
    m15 = _trend_series("M15", 1.1800, 0.0006, 145)
    # Shift the grind into the past, then lay a 30-bar flat shelf + 5 scenario
    # bars so the tail bar opens at NOW-900 and closes exactly at NOW.
    m15 = [_bar(c.time - 35 * 900, c.open, c.high, c.low, c.close) for c in m15]
    for _ in range(30):
        prev = m15[-1]
        stamp = prev.time + 900
        drift = 0.00002
        o = prev.close
        c = o + drift
        m15.append(_bar(stamp, o, max(o, c) + 0.0003, min(o, c) - 0.0003, c))
    base = m15  # 175 bars ending NOW-6*900; the five bars below close the gap to NOW
    swing_low = min(c.low for c in base[-13:])
    swing_high = max(c.high for c in base[-13:])
    assert swing_high - swing_low < 0.0025  # shelf must be tight
    d0 = base[-1].close
    t = base[-1].time + 900  # first free slot; tail opens at NOW-900 and closes at NOW
    depth = 0.0004
    # Sell-side echo whose high stays far below the shelf high (no competing SHORT echo).
    dip = _bar(t, d0, d0 + 0.0001, swing_low - depth, swing_low + 0.0002)
    assert dip.high < swing_high
    r1 = _bar(t + 900, dip.close, dip.close + 0.0006, dip.close - 0.00005, dip.close + 0.0005)
    r2 = _bar(t + 1800, r1.close, r1.close + 0.0006, r1.close - 0.00005, r1.close + 0.0005)
    r2h = r2.high
    gap = _bar(t + 2700, r2h + 0.0003, r2h + 0.0006, r2h + 0.0002, r2h + 0.00055)
    tail = _bar(t + 3600, gap.close, gap.close + 0.0001, gap.close - 0.0002, gap.close + 0.00002)
    m15 = base + [dip, r1, r2, gap, tail]
    assert abs(m15[-1].time + 900 - NOW) < 1e-6
    m5 = _trend_series("M5", tail.close - 0.0020, 0.0004, 200)
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    frames = {"D1": d1, "H4": h4, "M15": m15, "M5": m5}
    prov = {tf: {"provider": "synthetic", "timeframe": tf, "bars": len(rows)} for tf, rows in frames.items()}
    return MarketSnapshot(pair=pair, frames=frames, provenance=prov, as_of_epoch=NOW)


def test_config_loads_and_rejects_bad_overrides() -> None:
    config = load_muse_config(None)
    assert config.version == "muse.v1"
    assert config.scan["timeframes"] == {"atlas": "D1", "current": "H4", "vector": "M15", "spark": "M5"}
    with pytest.raises(MuseConfigError):
        load_muse_config({"MUSE_ENGINE": {"version": "muse.v2"}})
    with pytest.raises(MuseConfigError):
        load_muse_config({"MUSE_ENGINE": {"execution": {"risk_fraction": 0.05}}})
    with pytest.raises(MuseConfigError):
        load_muse_config({"MUSE_ENGINE": {"execution": {"live_enabled": True}}})
    with pytest.raises(MuseConfigError):
        load_muse_config({"MUSE_ENGINE": {"scoring": {"prime_threshold": 10.0, "stage_threshold": 90.0}}})


def test_harmonic_mean_punishes_weakest_prism() -> None:
    strong = harmonic_mean([0.9, 0.9, 0.9, 0.9], 0.015)
    one_weak = harmonic_mean([0.9, 0.9, 0.9, 0.2], 0.015)
    assert strong == pytest.approx(0.9, abs=1e-9)
    assert one_weak < 0.55  # harmonic (0.48) drags well below the arithmetic mean (0.725)


def test_undertow_echo_scores_velocity_not_just_depth() -> None:
    atr = 0.0020
    base = _trend_series("M15", 1.2000, 0.0002, 24)
    # Mirror the engine's own baseline: min low of the 13 bars before the tail.
    swing_low = min(c.low for c in list(base[:-5])[-13:])
    dip = _bar(base[-1].time + 900, swing_low + 0.0004, swing_low + 0.0005, swing_low - 0.0010, swing_low + 0.0003)
    reclaim = _bar(dip.time + 900, dip.close, dip.close + 0.0015, dip.close - 0.0001, swing_low + 0.0008)
    extra = _bar(reclaim.time + 900, reclaim.close, reclaim.close + 0.0004,
                 reclaim.close - 0.0003, reclaim.close + 0.0002)
    series = list(base[:-5]) + [dip, reclaim, extra]
    fast = undertow_echo(series, atr, lookback=18, reclaim_bars=5,
                         min_depth_atr=0.10, max_reclaim_bars=4)
    assert fast["available"] is True
    assert fast["direction"] == "LONG"
    assert fast["velocity"] > 0
    assert fast["depthAtr"] == pytest.approx(0.30, abs=0.01)
    # Same shape but a reclaim window too tight to include the dip fails closed.
    stale = undertow_echo(series, atr, lookback=18, reclaim_bars=1,
                          min_depth_atr=0.10, max_reclaim_bars=4)
    assert stale["available"] is False


def test_halo_veto_fails_closed() -> None:
    halo = halo_field({"carryZ": 2.0, "cotZ": 2.0, "eventRisk": {"allowed": False, "reason": "CPI_LOCK"}},
                      "LONG")
    assert halo["veto"] is True
    assert halo["quality"] == 0.0


def test_prime_signal_is_causal_and_gated() -> None:
    config = load_muse_config(None)
    snapshot = _prime_snapshot()
    signal = evaluate_snapshot(snapshot, config, {})
    assert signal["engine"] == "MUSE"
    assert signal["contractVersion"] == "muse.v1"
    assert signal["direction"] == "LONG"
    assert signal["decision"] in ("PRIME", "STAGE")
    assert signal["score"] >= float(config.scoring["stage_threshold"])
    assert signal["timeframes"] == {"atlas": "D1", "current": "H4", "vector": "M15", "spark": "M5"}
    # Must be deterministic.
    again = evaluate_snapshot(snapshot, config, {})
    assert again["signalId"] == signal["signalId"]
    assert again["score"] == signal["score"]
    # Freshness gates all pass on synthetic data.
    assert {g["name"] for g in signal["gates"]} >= {"D1_freshness", "H4_freshness", "M15_freshness",
                                                   "M5_freshness", "spark_recent", "haven_fresh"}


def _prime_fixture() -> dict[str, Any]:
    """Hand-built PRIME signal so execution tests don't depend on scoring margins."""
    from muse_engine.scoring import REQUIRED_GATE_NAMES

    entry, stop, target = 1.2900, 1.2880, 1.2940
    return {
        "signalId": "muse_testprime01",
        "contractVersion": "muse.v1",
        "engine": "MUSE",
        "pair": "EUR/USD",
        "symbol": "EURUSD",
        "assetType": "forex",
        "venue": "mt5",
        "direction": "LONG",
        "setup": "HAVEN_TAP",
        "phase": "RELEASE",
        "decision": "PRIME",
        "decisionReason": "fixture",
        "score": 81.0,
        "generatedAt": datetime.fromtimestamp(NOW, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": 2.0,
        "atr": 0.0010,
        "gates": [{"name": name, "passed": True, "reason": None} for name in sorted(REQUIRED_GATE_NAMES)],
        "blockingReasons": [],
    }


def test_stale_and_empty_inputs_block() -> None:
    config = load_muse_config(None)
    snapshot = _prime_snapshot()
    empty = MarketSnapshot(pair=snapshot.pair, frames={"D1": [], "H4": [], "M15": [], "M5": []},
                           provenance={}, as_of_epoch=NOW,
                           quality_errors=["FETCH_FAILED:M15:Timeout"])
    blocked = evaluate_snapshot(empty, config, {})
    assert blocked["decision"] == "BLOCKED"
    assert blocked["blockingReasons"]
    # Future-dated snapshot fails closed on freshness.
    future_vector = list(snapshot.frames["M15"])
    future = MarketSnapshot(pair=snapshot.pair,
                            frames={**snapshot.frames},
                            provenance=snapshot.provenance, as_of_epoch=NOW - 10_000_000.0)
    aged = evaluate_snapshot(future, config, {})
    assert aged["decision"] == "BLOCKED"


def test_levels_enforce_rr_and_stop_geometry() -> None:
    config = load_muse_config(None)
    snapshot = _prime_snapshot()
    signal = evaluate_snapshot(snapshot, config, {})
    if signal["decision"] == "BLOCKED":
        assert signal["entry"] is None or signal["rr"] is None or signal["rr"] < 1.7
    else:
        assert signal["rr"] is not None and signal["rr"] >= 1.7
        atr = signal["atr"]
        assert atr and abs(signal["entry"] - signal["stop"]) / atr >= 0.24 - 1e-9


def test_tide_clock_classifies_surge_window() -> None:
    config = load_muse_config(None)
    state = tide_state(NOW, config)
    assert state["window"] == "meridian_surge"
    assert state["quality"] == pytest.approx(1.0)
    closed, reason = market_is_closed(NOW, config, "forex")
    assert closed is False and reason is None
    weekend = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc).timestamp()  # Saturday
    closed_sat, _ = market_is_closed(weekend, config, "forex")
    assert closed_sat is True
    closed_crypto, _ = market_is_closed(weekend, config, "crypto")
    assert closed_crypto is False  # crypto not in the weekend gate list


def test_normalize_drops_forming_bar() -> None:
    seconds = TIMEFRAME_SECONDS["M15"]
    now_bar = math.floor(NOW / seconds) * seconds
    rows = [{"time": now_bar - 2 * seconds, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05},
            {"time": now_bar, "open": 1.05, "high": 1.1, "low": 1.0, "close": 1.06}]
    candles, prov, _ = normalize_closed_candles(rows, "M15", now_epoch=NOW, provider="synthetic")
    assert len(candles) == 1
    assert prov["bars"] == 1


def _coordinator(tmp: Path | str, *, root_overrides: dict | None = None):
    from muse_engine.execution import MuseExecutionCoordinator

    config = load_muse_config(None)
    repo = MuseRepository(tmp / "muse_engine.db")
    repo.migrate()
    root = {"EXECUTOR_MODE": "paper", "REAL_ORDERS_ALLOWED": False, **(root_overrides or {})}
    now = NOW

    class Gateway:
        def quote(self, signal):
            return Quote(venue=signal.get("venue", "mt5"), symbol="EURUSD",
                         bid=float(signal["entry"]) - 0.0001, ask=float(signal["entry"]) + 0.0001,
                         timestamp=now - 2.0, source="test")

        def account(self, venue):
            return {"demo": False, "testnet": False, "balance": 10000.0, "equity": 10000.0}

        def positions(self, venue):
            return {}

        def symbol_info(self, signal):
            return {"volume_min": 0.01}

        def execute(self, venue, payload, approval):
            raise AssertionError("paper must never call broker")

    return MuseExecutionCoordinator(config=config, repository=repo, gateway=Gateway(),
                                    root_config=root, kill_switch_fn=lambda: False, now_fn=lambda: now)


def _seed(coord, signal: dict[str, Any]) -> None:
    scan_id = coord.repository.create_scan({"pairs": 1})
    coord.repository.upsert_signal(scan_id, signal)


def test_preview_and_paper_execute_without_broker(workdir: Path) -> None:
    signal = _prime_fixture()
    coord = _coordinator(workdir)
    _seed(coord, signal)
    preview = coord.preview(signal)
    assert preview["executable"] is True
    result = coord.execute(signal, mode="paper", idempotency_key="muse-test-key-001")
    assert result["status"] == "SUCCESS"


def test_live_and_demo_guards_fail_closed(workdir: Path) -> None:
    signal = _prime_fixture()
    coord = _coordinator(workdir)
    with pytest.raises(MuseExecutionError) as exc:
        coord.execute(signal, mode="live", idempotency_key="muse-test-key-002", confirm_live=True)
    assert exc.value.code == "LIVE_EXECUTION_DISABLED"
    with pytest.raises(MuseExecutionError) as exc2:
        coord.execute(signal, mode="demo", idempotency_key="muse-test-key-003")
    assert exc2.value.code == "DEMO_EXECUTION_DISABLED"
    with pytest.raises(MuseExecutionError):
        coord.execute(signal, mode="paper", idempotency_key="short")


def test_kill_switch_blocks_preview(workdir: Path) -> None:
    signal = _prime_fixture()
    coord = _coordinator(workdir)
    coord.kill_switch_fn = lambda: True
    preview = coord.preview(signal)
    assert preview["executable"] is False


def test_api_routes_register_and_filter_decisions() -> None:
    app = Flask(__name__)
    service = SimpleNamespace(
        health=lambda: {"engine": "MUSE"},
        config_dict=lambda: {"engine": "MUSE"},
        accounts=lambda: {"success": True},
        start_scan=lambda **kwargs: {"status": "COMPLETED"},
        current_scan=lambda: None,
        signals=lambda **kwargs: [],
        signal=lambda signal_id: {"signalId": signal_id},
        preview_execution=lambda signal_id: {"executable": True},
        execute_signal=lambda *args, **kwargs: {"status": "SUCCESS"},
        execution_history=lambda **kwargs: [],
        sounding_pair=lambda *args, **kwargs: {"success": True},
        config=SimpleNamespace(execution={"default_mode": "paper"}),
    )
    register_muse_routes(app, SimpleNamespace(service=service))
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/muse/health" in rules
    assert "/api/muse/sounding" in rules
    client = app.test_client()
    assert client.get("/api/muse/signals?decisions=BOGUS").status_code == 400
    assert client.get("/api/muse/signals?decisions=PRIME,STAGE").status_code == 200


def test_repository_idempotency(workdir: Path) -> None:
    repo = MuseRepository(workdir / "muse_engine.db")
    repo.migrate()
    scan_id = repo.create_scan({"pairs": 1})
    signal = evaluate_snapshot(_prime_snapshot(), load_muse_config(None), {})
    repo.upsert_signal(scan_id, signal)
    assert repo.get_signal(signal["signalId"])["score"] == signal["score"]
    first, dup1 = repo.claim_execution(signal_id=signal["signalId"], idempotency_key="abc-123-xyz",
                                       mode="paper", venue="mt5", request={})
    second, dup2 = repo.claim_execution(signal_id=signal["signalId"], idempotency_key="abc-123-xyz",
                                        mode="paper", venue="mt5", request={})
    assert first == second and dup2 is True and dup1 is False
