"""FABLE engine: pure-path tests (no athena.py import)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

from flask import Flask
import pytest

from fable_engine.api import register_fable_routes
from fable_engine.chronicle import run_chronicle
from fable_engine.config import FableConfigError, load_fable_config
from fable_engine.execution import FableExecutionCoordinator, FableExecutionError
from fable_engine.market_data import normalize_closed_candles
from fable_engine.models import TIMEFRAME_SECONDS, Candle, LiquidityPool, MarketSnapshot, Quote, utc_iso
from fable_engine.narrative import coherence_score, evaluate_snapshot, tier_for
from fable_engine.persistence import FableRepository
from fable_engine.sessions import session_state
from fable_engine.structure import fair_value_gaps, find_raids, find_shift, fractal_swings, swing_sequence_bias


# Friday 2027-01-29 08:00 UTC == 03:00 New York (London open window), M15-aligned.
NOW = 1_801_209_600.0
assert NOW % 900 == 0
assert datetime.fromtimestamp(NOW, tz=timezone.utc).strftime("%a %H:%M") == "Fri 08:00"


@pytest.fixture
def workdir():
    # The repo's .pytest_tmp is locked on this machine; use a private temp dir.
    path = Path(tempfile.mkdtemp(prefix="fable-test-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _bar(time: float, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> Candle:
    return Candle(time, open_, high, low, close, volume, "synthetic")


def _series(timeframe: str, closes: list[float], *, volume: float = 100.0, wick: float = 0.0004) -> list[Candle]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    count = len(closes)
    previous = closes[0]
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        opened_at = NOW - (count - index) * seconds
        rows.append(_bar(opened_at, previous, max(previous, close) + wick, min(previous, close) - wick, close, volume))
        previous = close
    return rows


def _resample(m15: list[Candle], seconds: int) -> list[Candle]:
    """Aggregate M15 bars into a higher timeframe, keeping only bars closed by NOW."""
    groups: dict[int, list[Candle]] = {}
    for candle in m15:
        groups.setdefault(int(candle.time // seconds), []).append(candle)
    out: list[Candle] = []
    for key in sorted(groups):
        start = key * seconds
        if start + seconds > NOW:
            continue
        rows = groups[key]
        out.append(_bar(start, rows[0].open, max(r.high for r in rows), min(r.low for r in rows), rows[-1].close, sum(r.volume or 0 for r in rows)))
    return out


def _story_snapshot(*, return_to_array: bool = True) -> MarketSnapshot:
    """One M15 path resampled to H1/H4/D1: uptrend, pullback, sellside raid of an H1 swing low,
    displacement leaving an FVG in the OTE band, and (optionally) a return into that FVG."""
    n = 3200
    closes = [1.10 + 0.00002 * i + 0.0020 * math.sin(i / 40.0) for i in range(n)]
    top = closes[n - 150]
    for k in range(150):
        closes[n - 150 + k] = top - 0.0030 * (k / 150) + 0.0003 * math.sin(k / 2.5)
    m15 = _series("M15", closes, wick=0.0003)
    N = len(m15)
    P = m15[N - 49].close - 0.0016  # the pool: an M15/H1 swing low below the pullback

    def put(offset: int, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> None:
        index = N - offset
        m15[index] = _bar(m15[index].time, P + open_, P + high, P + low, P + close, volume)

    for offset in range(48, 44, -1):  # flat shelf above the pool
        put(offset, 0.0012, 0.0014, 0.0010, 0.0012 + (0.0001 if offset % 2 else -0.0001))
    put(44, 0.0011, 0.0012, 0.0000, 0.0007, 160)  # swing low = the pool at P (11h before the raid: a confirmed H1 fractal)
    put(43, 0.0007, 0.0012, 0.0006, 0.0011)
    for offset in range(42, 30, -1):  # hover above the pool while the H1 swing confirms
        put(offset, 0.0011, 0.0014, 0.0009, 0.0011 + (0.0002 if offset % 3 == 0 else 0.0001))
    put(30, 0.0012, 0.0013, 0.0010, 0.0011)
    put(29, 0.0011, 0.0012, 0.0009, 0.0010)
    put(28, 0.0010, 0.0011, 0.0009, 0.0010)
    put(27, 0.0010, 0.0012, 0.0009, 0.0011)
    put(26, 0.0011, 0.0013, 0.0010, 0.0012)
    put(25, 0.0012, 0.0014, 0.0011, 0.0013)
    put(24, 0.0013, 0.0015, 0.0012, 0.0014)
    put(23, 0.0014, 0.0015, 0.0013, 0.0014)
    put(22, 0.0014, 0.0016, 0.0012, 0.0013)  # pre-raid swing high (the level the shift must break)
    put(21, 0.0013, 0.0014, 0.0011, 0.0012)
    put(20, 0.0012, 0.0013, 0.0010, 0.0011)
    put(19, 0.0011, 0.0012, 0.0009, 0.0010)
    put(18, 0.0010, 0.0011, 0.0008, 0.0009)
    put(17, 0.0009, 0.0010, 0.0006, 0.0007)
    put(16, 0.0007, 0.0008, 0.0004, 0.0005)
    put(15, 0.0005, 0.0006, -0.0004, 0.0003, 420)  # RAID: sweep below the pool, close back above
    put(14, 0.0003, 0.0004, 0.0002, 0.00035, 140)
    put(13, 0.00035, 0.0017, 0.0003, 0.0016, 560)  # displacement candle
    put(12, 0.0016, 0.0022, 0.0009, 0.0021, 380)  # FVG = [N-14.high, N-12.low] = [0.0004, 0.0009]
    put(11, 0.0021, 0.0022, 0.0014, 0.0015, 200)
    if return_to_array:
        put(10, 0.0015, 0.0016, 0.0009, 0.0010, 150)
        put(9, 0.0010, 0.0011, 0.0004, 0.0005, 150)  # back inside the FVG, ~0.67 retrace of the raid-to-leg-high span
        level = 0.00045
        for offset in range(8, 0, -1):
            put(offset, level, level + 0.00015, level - 0.00015, level + (0.00001 if offset % 2 else -0.00001), 110)
            level = level + (0.00001 if offset % 2 else -0.00001)
    else:
        level = 0.0015
        for offset in range(10, 0, -1):
            put(offset, level, level + 0.00015, level - 0.00015, level + (0.00001 if offset % 2 else -0.00001), 110)
            level = level + (0.00001 if offset % 2 else -0.00001)
    frames = {
        "M15": m15[-320:],
        "H1": _resample(m15, 3600)[-240:],
        "H4": _resample(m15, 14400)[-180:],
        "D1": _resample(m15, 86400)[-120:],
    }
    provenance = {tf: {"provider": "synthetic", "bars": len(series)} for tf, series in frames.items()}
    return MarketSnapshot({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}, frames, provenance, NOW)


# ── config ──────────────────────────────────────────────────────────────


def test_default_config_loads_and_validates() -> None:
    config = load_fable_config({})
    assert config.version == "fable.v1"
    assert sum(config.scoring["weights"].values()) == 100.0
    assert config.execution["default_mode"] == "paper"


def test_config_rejects_bad_overrides() -> None:
    with pytest.raises(FableConfigError):
        load_fable_config({"FABLE_ENGINE": {"scoring": {"weights": {"draw": 50, "raid": 50, "shift": 0, "return": 0, "chorus": 10}}}})
    with pytest.raises(FableConfigError):
        load_fable_config({"FABLE_ENGINE": {"execution": {"risk_fraction": 0.05}}})
    with pytest.raises(FableConfigError):
        load_fable_config({"FABLE_ENGINE": {"execution": {"live_enabled": True}}})


# ── market data ─────────────────────────────────────────────────────────


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def test_normalize_drops_forming_and_future_bars() -> None:
    rows = [
        {"time": _iso(NOW - 2700), "open": 1, "high": 1.2, "low": 0.9, "close": 1.1, "vol": 5, "volSource": "mt5_tick"},
        {"time": _iso(NOW - 1800), "open": 1, "high": 1.2, "low": 0.9, "close": 1.1, "vol": 5, "volSource": "mt5_tick"},
        {"time": _iso(NOW - 900), "open": 1, "high": 1.2, "low": 0.9, "close": 1.1, "confirmed": False},
        {"open_time": (NOW + 3600) * 1000, "open": 1, "high": 1.2, "low": 0.9, "close": 1.1},
    ]
    candles, meta, errors = normalize_closed_candles(rows, "M15", now_epoch=NOW, provider="mt5_terminal")
    assert [c.time for c in candles] == [NOW - 2700, NOW - 1800]
    assert meta["formingBarsDropped"] == 1
    assert meta["futureBarsDropped"] == 1
    assert meta["volumeSources"] == ["mt5_tick"]
    assert any(error.startswith("FUTURE_CANDLES") for error in errors)


def test_normalize_drops_bar_that_is_not_closed_at_as_of() -> None:
    rows = [{"time": _iso(NOW - 900), "open": 1, "high": 1.2, "low": 0.9, "close": 1.1}]
    candles, meta, _ = normalize_closed_candles(rows, "M15", now_epoch=NOW - 5, provider="mt5_terminal")
    assert candles == [] and meta["formingBarsDropped"] == 1


def test_normalize_reports_source_error() -> None:
    candles, meta, errors = normalize_closed_candles({"error": True, "detail": "MT5 not connected"}, "H1", now_epoch=NOW, provider="mt5_terminal")
    assert candles == []
    assert errors == ["SOURCE_ERROR:H1:MT5 not connected"]


# ── structure primitives ────────────────────────────────────────────────


def test_fractal_swings_and_bias() -> None:
    candles = []
    pattern = [(1.00, 1.02, 0.98), (1.05, 1.06, 1.03), (1.02, 1.03, 1.00), (1.10, 1.11, 1.07), (1.06, 1.07, 1.04),
               (1.15, 1.16, 1.12), (1.11, 1.12, 1.09), (1.20, 1.21, 1.17), (1.16, 1.17, 1.14), (1.25, 1.26, 1.22), (1.21, 1.22, 1.19)]
    for index, (close, high, low) in enumerate(pattern):
        candles.append(_bar(NOW - (len(pattern) - index) * 14400, close, high, low, close))
    swings = fractal_swings(candles, 1)
    assert [swing.kind for swing in swings].count("high") >= 3
    bias, strength = swing_sequence_bias(swings)
    assert bias == "LONG"
    assert strength == 1.0


def test_fair_value_gap_detection() -> None:
    candles = [
        _bar(NOW - 2700, 1.0, 1.01, 0.99, 1.005),
        _bar(NOW - 1800, 1.005, 1.03, 1.004, 1.028),
        _bar(NOW - 900, 1.028, 1.05, 1.02, 1.045),
    ]
    gaps = fair_value_gaps(candles, 0, 2, "LONG")
    assert len(gaps) == 1
    assert gaps[0].low == 1.01 and gaps[0].high == 1.02


def test_raid_requires_reclaim_and_prior_pool() -> None:
    m15 = _story_snapshot().frames["M15"]
    n = len(m15)
    pool_low = m15[n - 44].low
    pool = LiquidityPool(pool_low, "sellside", "H1_swing", 0.5, m15[n - 44].time)
    raids = find_raids(m15, [pool], atr=0.0006, lookback=32, max_excursion_bars=3, min_depth_atr=0.05, max_depth_atr=2.5, participation_baseline=40)
    assert raids and raids[0].direction == "LONG"
    assert raids[0].reclaim_index == n - 15
    assert raids[0].participation_z is not None and raids[0].participation_z > 2
    shift = find_shift(m15, raids[0], atr=0.0006, swing_strength=2, min_displacement_atr=1.0, min_body_atr=0.55, max_bars_after_raid=24, participation_baseline=40)
    assert shift is not None
    assert shift.direction == "LONG"
    assert shift.break_index == n - 12
    gap = next(item for item in shift.imbalances if item.kind == "fvg")
    assert gap.low == pytest.approx(pool_low + 0.0004) and gap.high == pytest.approx(pool_low + 0.0009)


# ── scoring ─────────────────────────────────────────────────────────────


def test_coherence_is_geometric_and_punishes_weak_acts() -> None:
    weights = {"draw": 20.0, "raid": 20.0, "shift": 20.0, "return": 20.0, "chorus": 20.0}
    balanced = coherence_score({name: 0.8 for name in weights}, weights, 0.02)
    lopsided = coherence_score({"draw": 1.0, "raid": 1.0, "shift": 1.0, "return": 1.0, "chorus": 0.0}, weights, 0.02)
    assert round(balanced, 1) == 80.0
    assert lopsided < 50.0
    assert tier_for(81, {"LEGEND": 80, "SAGA": 64, "TALE": 50}) == "LEGEND"
    assert tier_for(10, {"LEGEND": 80, "SAGA": 64, "TALE": 50}) == "SKETCH"


def test_long_story_reaches_execute_with_levels() -> None:
    config = load_fable_config({})
    signal = evaluate_snapshot(_story_snapshot(), config, generated_at_epoch=NOW, context={"carryZ": 0.8, "cotZ": 0.5})
    assert signal["direction"] == "LONG"
    assert signal["decision"] == "EXECUTE", (signal["decisionReason"], signal["voidReasons"], signal["coherence"])
    assert signal["voidReasons"] == []
    assert all(gate["passed"] for gate in signal["gates"])
    assert signal["stop"] < signal["entry"] < signal["target"]
    assert signal["rr"] >= config.levels["minimum_rr"]
    assert signal["coherence"] >= config.scoring["execute_threshold"]
    assert [act["name"] for act in signal["acts"]] == ["draw", "raid", "shift", "return", "chorus"]
    assert signal["returnState"] == "inside"
    assert signal["annotations"]["array"]["kind"] == "fvg"
    assert signal["acts"][3]["evidence"]["inOte"] is True
    assert signal["target2"] is not None and signal["rr2"] > signal["rr"]  # external draw beyond the leg high
    assert signal["dataFreshness"]["M15"]["status"] == "FRESH"
    assert signal["session"]["window"] == "london_open"
    assert signal["signalId"].startswith("fable_")
    assert "raided sellside liquidity" in signal["narrative"]


def test_story_without_return_is_staged_not_executed() -> None:
    config = load_fable_config({})
    signal = evaluate_snapshot(_story_snapshot(return_to_array=False), config, generated_at_epoch=NOW)
    assert signal["returnState"] == "pending"
    assert signal["decision"] in {"STAGE", "OBSERVE"}
    assert signal["coherencePotential"] > signal["coherence"]


def test_signal_id_is_stable_for_the_same_narrative() -> None:
    config = load_fable_config({})
    first = evaluate_snapshot(_story_snapshot(), config, generated_at_epoch=NOW)
    second = evaluate_snapshot(_story_snapshot(), config, generated_at_epoch=NOW + 60)
    assert first["signalId"] == second["signalId"]


def test_stale_data_voids_the_story() -> None:
    config = load_fable_config({})
    snapshot = _story_snapshot()
    stale = MarketSnapshot(snapshot.pair, snapshot.frames, snapshot.provenance, NOW + 4 * 900)
    signal = evaluate_snapshot(stale, config, generated_at_epoch=NOW + 4 * 900)
    assert signal["decision"] == "VOID"
    assert "DATA_STALE:M15" in signal["voidReasons"]


def test_event_blackout_voids_the_story() -> None:
    config = load_fable_config({})
    signal = evaluate_snapshot(_story_snapshot(), config, generated_at_epoch=NOW, context={"eventRisk": {"allowed": False, "reason": "NFP in 30m"}})
    assert signal["decision"] == "VOID"
    assert "EVENT_BLACKOUT" in signal["voidReasons"]


def test_session_gate_blocks_dead_window_for_forex() -> None:
    config = load_fable_config({})
    lunch = NOW + 9 * 3600 + 30 * 60  # 12:30 New York
    assert session_state(lunch, config)["window"] == "ny_lunch"
    snapshot = _story_snapshot()
    shift = 9 * 3600 + 1800
    shifted = MarketSnapshot(
        snapshot.pair,
        {tf: [Candle(c.time + shift, c.open, c.high, c.low, c.close, c.volume, c.volume_source) for c in series] for tf, series in snapshot.frames.items()},
        snapshot.provenance,
        lunch,
    )
    signal = evaluate_snapshot(shifted, config, generated_at_epoch=lunch)
    assert signal["decision"] == "VOID"
    assert "SESSION_WINDOW_CLOSED" in signal["voidReasons"]


def test_crypto_is_not_session_gated() -> None:
    config = load_fable_config({})
    lunch = NOW + 9 * 3600 + 30 * 60
    snapshot = _story_snapshot()
    shift = 9 * 3600 + 1800
    crypto = MarketSnapshot(
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
        {tf: [Candle(c.time + shift, c.open, c.high, c.low, c.close, c.volume, c.volume_source) for c in series] for tf, series in snapshot.frames.items()},
        snapshot.provenance,
        lunch,
    )
    signal = evaluate_snapshot(crypto, config, generated_at_epoch=lunch, context={"fundingRate": 0.0001})
    assert signal["venue"] == "bybit"
    assert "SESSION_WINDOW_CLOSED" not in signal["voidReasons"]


# ── chronicle ───────────────────────────────────────────────────────────


def test_chronicle_replays_prefixes_causally() -> None:
    config = load_fable_config({"FABLE_ENGINE": {"chronicle": {"default_bars": 150, "maximum_bars": 400}}})
    snapshot = _story_snapshot()
    result = run_chronicle(pair=snapshot.pair, frames=snapshot.frames, provenance=snapshot.provenance, config=config, bars=150)
    assert result["evidenceStatus"] in {"INSUFFICIENT_SAMPLE", "SAMPLE_OK"}
    assert result["barsEvaluated"] > 0
    assert set(result["decisions"]) <= {"EXECUTE", "STAGE", "OBSERVE", "VOID"}
    assert result["decisions"].get("EXECUTE", 0) >= 1
    for chapter in result["chapters"]:
        assert chapter["outcome"] in {"STOP", "TARGET", "HORIZON", "OPEN"}
        assert chapter["stop"] < chapter["entry"] < chapter["target"]


# ── execution coordinator ───────────────────────────────────────────────


class _Gateway:
    def __init__(self, quote: Quote, *, account: dict | None = None, positions: dict | None = None, symbol_info: dict | None = None):
        self._quote = quote
        self.account_payload = account or {"error": False, "demo": True, "accountEnvironment": "demo", "balance": 10_000.0, "equity": 10_000.0, "risk_domain": "forex:mt5:test:1"}
        self.positions_payload = positions or {"error": False, "positions": []}
        self.symbol_info_payload = symbol_info or {"error": False, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100}
        self.executed: list[dict] = []

    def quote(self, signal):
        return self._quote

    def account(self, venue):
        return self.account_payload

    def positions(self, venue):
        return self.positions_payload

    def symbol_info(self, signal):
        return self.symbol_info_payload

    def execute(self, venue, payload, approval):
        self.executed.append(payload)
        return {"success": True, "ticket": 4242, "entryPrice": payload["price"], "symbol": "EURUSD.s", "direction": payload["direction"], "volume": approval.volume}


def _execute_signal() -> dict:
    config = load_fable_config({})
    signal = evaluate_snapshot(_story_snapshot(), config, generated_at_epoch=NOW, context={"carryZ": 0.8})
    assert signal["decision"] == "EXECUTE"
    return signal


def _passing_quote(signal: dict, *, now: float = NOW) -> Quote:
    entry = float(signal["entry"])
    return Quote("mt5", "EURUSD.s", entry - 0.00003, entry + 0.00003, now - 1.0, "mt5_tick")


def _coordinator(workdir, signals, gateway, *, root=None, now=NOW, kill=False):
    config = load_fable_config(root or {})
    repository = FableRepository(workdir / "fable_engine.db")
    repository.migrate()
    scan_id = repository.create_scan({"test": True})
    repository.save_signals(scan_id, signals)
    repository.complete_scan(scan_id, "COMPLETED", {})
    return FableExecutionCoordinator(
        config=config,
        repository=repository,
        gateway=gateway,
        root_config=root or {},
        kill_switch_fn=lambda: kill,
        now_fn=lambda: now,
    )


def _preview_error(coordinator, signal) -> str | None:
    return coordinator.preview(signal)["error"]


def test_preview_attests_quote_and_geometry(workdir) -> None:
    signal = _execute_signal()
    coordinator = _coordinator(workdir, [signal], _Gateway(_passing_quote(signal)))
    preview = coordinator.preview(signal)
    assert preview["executable"], preview["error"]
    assert preview["liveRr"] >= coordinator.config.levels["minimum_rr"]
    assert preview["liveStop"] == signal["stop"]


def test_preview_rejects_wide_spread_and_stale_quote(workdir) -> None:
    signal = _execute_signal()
    entry = float(signal["entry"])
    wide = Quote("mt5", "EURUSD.s", entry - 0.001, entry + 0.001, NOW - 1.0, "mt5_tick")
    assert _preview_error(_coordinator(workdir / "a", [signal], _Gateway(wide)), signal) == "SPREAD_TOO_WIDE"
    stale = Quote("mt5", "EURUSD.s", entry - 0.00003, entry + 0.00003, NOW - 120.0, "mt5_tick")
    assert _preview_error(_coordinator(workdir / "b", [signal], _Gateway(stale)), signal) == "BROKER_QUOTE_STALE"


def test_preview_rejects_drifted_quote(workdir) -> None:
    signal = _execute_signal()
    entry = float(signal["entry"])
    drifted = Quote("mt5", "EURUSD.s", entry - 0.003, entry - 0.0029, NOW - 1.0, "mt5_tick")
    assert _preview_error(_coordinator(workdir, [signal], _Gateway(drifted)), signal) == "QUOTE_DRIFT_EXCEEDS_LIMIT"


def test_preview_rejects_stale_narrative_bar(workdir) -> None:
    # A freshly re-stamped signal whose narrative bar closed four M15 buckets ago.
    signal = dict(_execute_signal(), generatedAt=utc_iso(NOW + 3600))
    coordinator = _coordinator(workdir, [signal], _Gateway(_passing_quote(signal, now=NOW + 3600)), now=NOW + 3600)
    assert _preview_error(coordinator, signal) == "NARRATIVE_BAR_STALE"


def test_stage_signal_cannot_execute(workdir) -> None:
    config = load_fable_config({})
    signal = evaluate_snapshot(_story_snapshot(return_to_array=False), config, generated_at_epoch=NOW)
    signal = dict(signal, decision="STAGE")
    quote = Quote("mt5", "EURUSD.s", 1.18, 1.18006, NOW - 1.0, "mt5_tick")
    coordinator = _coordinator(workdir, [signal], _Gateway(quote))
    assert coordinator.preview(signal)["error"] in {"SIGNAL_NOT_EXECUTE", "SIGNAL_LEVELS_INVALID", "SIGNAL_COHERENCE_INVALID"}


def test_paper_execution_is_idempotent_and_never_touches_broker(workdir) -> None:
    signal = _execute_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(workdir, [signal], gateway)
    first = coordinator.execute(signal, mode="paper", idempotency_key="fable-test:1")
    assert first["status"] == "SUCCESS" and first["result"]["mode"] == "paper"
    assert gateway.executed == []
    second = coordinator.execute(signal, mode="paper", idempotency_key="fable-test:1")
    assert second["idempotent"] is True
    third = coordinator.execute(signal, mode="paper", idempotency_key="fable-test:2")
    assert third["idempotent"] is True  # one live reservation per narrative


def test_demo_execution_requires_demo_mode_and_demo_account(workdir) -> None:
    signal = _execute_signal()
    gateway = _Gateway(_passing_quote(signal))
    paper_only = _coordinator(workdir / "a", [signal], gateway)
    with pytest.raises(FableExecutionError) as excinfo:
        paper_only.execute(signal, mode="demo", idempotency_key="fable-demo:1")
    assert excinfo.value.code == "DEMO_EXECUTION_DISABLED"

    real_account = {"error": False, "demo": False, "accountEnvironment": "real", "balance": 10_000.0, "equity": 10_000.0}
    coordinator = _coordinator(workdir / "b", [signal], _Gateway(_passing_quote(signal), account=real_account), root={"EXECUTOR_MODE": "demo"})
    result = coordinator.execute(signal, mode="demo", idempotency_key="fable-demo:2")
    assert result["status"] == "REJECTED"
    assert result["result"]["error"] == "DEMO_ACCOUNT_ATTESTATION_FAILED"


def test_demo_execution_routes_through_shared_risk_gates(workdir, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _execute_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(workdir, [signal], gateway, root={"EXECUTOR_MODE": "demo"})
    seen: dict = {}

    def fake_pre_trade_check(payload, positions, account, positions_raw=None):
        seen["guardian"] = deepcopy(payload)
        return True, "OK"

    def fake_risk_check(payload, **kwargs):
        seen["risk"] = deepcopy(payload)
        seen["risk_kwargs"] = kwargs
        return risk_engine.RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK", "calculated", "calculated")

    monkeypatch.setattr(guardian, "pre_trade_check", fake_pre_trade_check)
    monkeypatch.setattr(risk_engine, "risk_check", fake_risk_check)
    result = coordinator.execute(signal, mode="demo", idempotency_key="fable-demo:3")
    assert result["status"] == "SUCCESS", result["result"]
    payload = seen["risk"]
    assert payload["engine"] == "FABLE" and payload["fableExecution"] is True
    assert payload["candleFreshness"]["M15"]["status"] == "FRESH"
    assert "stalenessSeverity" not in payload["candleFreshness"]["M15"]
    assert payload["sl"] == signal["stop"] and payload["tp1"] == payload["tp2"]
    assert seen["risk_kwargs"]["execution_context"] == "fable_engine"
    assert seen["risk_kwargs"]["volume_mode"] == "calculated"
    # 0.25% of 10k equity = 25 risk; the approval risked 100 so the volume was downsized to 0.25 lots
    assert result["result"]["riskApproval"]["volume"] == pytest.approx(0.25)
    assert result["result"]["riskApproval"]["reason"] == "OK_FABLE_DOWNSIZED"
    assert gateway.executed and gateway.executed[0]["sl"] == signal["stop"]


def test_guardian_rejection_is_recorded(workdir, monkeypatch) -> None:
    import guardian

    signal = _execute_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(workdir, [signal], gateway, root={"EXECUTOR_MODE": "demo"})
    monkeypatch.setattr(guardian, "pre_trade_check", lambda *args, **kwargs: (False, "MAX_POSITIONS"))
    result = coordinator.execute(signal, mode="demo", idempotency_key="fable-demo:4")
    assert result["status"] == "REJECTED"
    assert result["result"]["error"] == "GUARDIAN_REJECTED"
    assert result["result"]["detail"] == "MAX_POSITIONS"
    assert gateway.executed == []


def test_kill_switch_blocks_execution(workdir) -> None:
    signal = _execute_signal()
    coordinator = _coordinator(workdir, [signal], _Gateway(_passing_quote(signal)), kill=True)
    assert coordinator.preview(signal)["error"] == "KILL_SWITCH_ACTIVE"


def test_live_mode_locked_by_default(workdir) -> None:
    signal = _execute_signal()
    coordinator = _coordinator(workdir, [signal], _Gateway(_passing_quote(signal)), root={"EXECUTOR_MODE": "live", "REAL_ORDERS_ALLOWED": True})
    assert coordinator.capabilities()["modes"]["live"]["enabled"] is False
    with pytest.raises(FableExecutionError) as excinfo:
        coordinator.execute(signal, mode="live", idempotency_key="fable-live:1", confirm_live=True)
    assert excinfo.value.code == "LIVE_EXECUTION_DISABLED"


def test_demo_is_default_when_executor_mode_is_demo(workdir) -> None:
    signal = _execute_signal()
    coordinator = _coordinator(workdir, [signal], _Gateway(_passing_quote(signal)), root={"EXECUTOR_MODE": "demo"})
    assert coordinator.capabilities()["defaultMode"] == "demo"
    paper = _coordinator(workdir / "p", [signal], _Gateway(_passing_quote(signal)), root={"EXECUTOR_MODE": "paper"})
    assert paper.capabilities()["defaultMode"] == "paper"


# ── API ─────────────────────────────────────────────────────────────────


class _Service:
    def __init__(self, coordinator, signal):
        self.execution = coordinator
        self.config = coordinator.config
        self._signal = signal

    def health(self):
        return {"success": True, "engine": "FABLE", "scanStatus": "IDLE"}

    def accounts(self):
        return {"success": True, "venues": {}}

    def config_dict(self):
        return {}

    def universe(self):
        return []

    def current_scan(self):
        return None

    def signals(self, **kwargs):
        return [self._signal]

    def signal(self, signal_id):
        if signal_id != self._signal["signalId"]:
            raise LookupError("signal_not_found")
        return self._signal

    def preview_execution(self, signal_id):
        return self.execution.preview(self.signal(signal_id))

    def execute_signal(self, signal_id, **kwargs):
        return self.execution.execute(self.signal(signal_id), **kwargs)

    def execution_history(self, limit=100):
        return []

    def positions(self):
        return {"success": True, "positions": [], "venues": {}, "count": 0}


def test_routes_register_and_preview_reports_gate(workdir) -> None:
    signal = _execute_signal()
    entry = float(signal["entry"])
    wide = Quote("mt5", "EURUSD.s", entry - 0.001, entry + 0.001, NOW - 1.0, "mt5_tick")
    coordinator = _coordinator(workdir, [signal], _Gateway(wide))
    app = Flask(__name__)
    register_fable_routes(app, SimpleNamespace(service=_Service(coordinator, signal)))
    client = app.test_client()
    assert client.get("/api/fable/health").get_json()["engine"] == "FABLE"
    preview = client.post(f"/api/fable/signals/{signal['signalId']}/preview", json={})
    assert preview.status_code == 200
    assert preview.get_json()["error"] == "SPREAD_TOO_WIDE"
    missing = client.post("/api/fable/signals/nope/preview", json={})
    assert missing.status_code == 404
    executed = client.post(f"/api/fable/signals/{signal['signalId']}/execute", json={"mode": "paper", "idempotencyKey": "api:1"})
    assert executed.status_code == 403
    assert executed.get_json()["result"]["error"] == "SPREAD_TOO_WIDE"
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/fable/signals/<signal_id>/execute" in rules
    assert "/api/fable/chronicle" in rules
    assert "/api/fable/positions" in rules
    bad = client.get("/api/fable/signals?decisions=READY")
    assert bad.status_code == 400


# ── weekend / closed-market gates and audit fixes ───────────────────────


def _empty_snapshot(pair: dict, as_of: float) -> MarketSnapshot:
    return MarketSnapshot(pair, {tf: [] for tf in ("D1", "H4", "H1", "M15")}, {}, as_of)


def test_closed_market_is_voided_without_data_and_crypto_stays_open() -> None:
    from fable_engine.sessions import market_is_closed

    config = load_fable_config({})
    saturday = NOW + 30 * 3600  # Sat 14:00 UTC
    assert market_is_closed(saturday, "forex", config) == (True, "WEEKEND")
    assert market_is_closed(saturday, "crypto", config) == (False, None)
    assert market_is_closed(NOW, "forex", config) == (False, None)
    signal = evaluate_snapshot(_empty_snapshot({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}, saturday), config, generated_at_epoch=saturday)
    assert signal["decision"] == "VOID" and signal["decisionReason"] == "MARKET_CLOSED"
    assert signal["voidReasons"] == ["MARKET_CLOSED"]
    crypto = evaluate_snapshot(_empty_snapshot({"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}, saturday), config, generated_at_epoch=saturday)
    assert crypto["decisionReason"] != "MARKET_CLOSED"


def test_friday_daily_bar_is_still_fresh_on_monday() -> None:
    from fable_engine.narrative import _data_gates

    config = load_fable_config({})
    friday_close = datetime(2027, 1, 29, 21, 0, tzinfo=timezone.utc).timestamp()  # broker D1 closes Fri 21:00 UTC
    monday = datetime(2027, 2, 1, 12, 0, tzinfo=timezone.utc).timestamp()

    def series(tf: str, count: int, last_close: float) -> list[Candle]:
        sec = TIMEFRAME_SECONDS[tf]
        return [_bar(last_close - sec * (count - i), 1.0, 1.1, 0.9, 1.0) for i in range(count)]

    frames = {"D1": series("D1", 40, friday_close), "H4": series("H4", 80, monday - 600), "H1": series("H1", 120, monday - 600), "M15": series("M15", 200, monday - 60)}
    forex = MarketSnapshot({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}, frames, {}, monday)
    gates, fresh = _data_gates(forex, config, None)
    assert all(gate["passed"] for gate in gates), [g["reason"] for g in gates if not g["passed"]]
    assert fresh["D1"]["closedMarketSec"] == pytest.approx(48 * 3600)
    assert fresh["D1"]["ageBuckets"] < 1.0
    crypto = MarketSnapshot({"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}, frames, {}, monday)
    _, crypto_fresh = _data_gates(crypto, config, None)
    assert crypto_fresh["D1"]["status"] == "STALE"  # 24/7 venues get no weekend credit


def test_session_extremes_come_from_h1_not_the_short_m15_window() -> None:
    from fable_engine.structure import ny_zone, session_extremes

    snapshot = _story_snapshot()
    zone = ny_zone("America/New_York", -4.0)
    h1_sources = {pool.source for pool in session_extremes(snapshot.frames["H1"], as_of_epoch=NOW, zone=zone)}
    m15_sources = {pool.source for pool in session_extremes(snapshot.frames["M15"], as_of_epoch=NOW, zone=zone)}
    assert {"PDH", "PDL", "PWH", "PWL"} <= h1_sources
    assert "PWH" in h1_sources and len(snapshot.frames["M15"]) * 900 < 5 * 86400
    assert m15_sources <= h1_sources


def test_target_ladder_takes_nearest_untaken_level() -> None:
    from fable_engine.models import Raid, Shift
    from fable_engine.narrative import build_levels

    atr = 0.0010
    price = 1.1000
    pool = LiquidityPool(1.0900, "sellside", "H1_swing", 0.5, NOW - 40 * 900)
    raid = Raid(pool, "LONG", 10, 12, 1.0985, 1.0, 0.5, 5, None)  # stop = 1.0985 - buffer (~1.75 ATR)
    shift = Shift("LONG", 1.1010, 5, 14, 1.0985, 1.1060, 16, 7.5, 1.0, (), None)
    leg_end_time = NOW - 20 * 900
    inside_old = LiquidityPool(1.1040, "buyside", "H1_swing", 0.5, NOW - 60 * 900)  # traded through by the leg
    inside_new = LiquidityPool(1.1035, "buyside", "H1_swing", 0.5, NOW - 5 * 900)  # pullback lower high
    beyond = LiquidityPool(1.1120, "buyside", "PDH", 0.85, NOW - 300 * 900)
    cfg = load_fable_config({}).levels_for("forex")
    levels, gates = build_levels(direction="LONG", price=price, atr=atr, raid=raid, shift=shift, pools=[beyond, inside_old, inside_new], draw_target=beyond, levels_cfg=cfg, leg_end_time=leg_end_time)
    assert all(gate["passed"] for gate in gates)
    assert levels["targetSource"] == "H1_swing" and levels["target"] == pytest.approx(inside_new.price - atr * cfg["target_liquidity_buffer_atr"])
    without_pullback, _ = build_levels(direction="LONG", price=price, atr=atr, raid=raid, shift=shift, pools=[beyond, inside_old], draw_target=beyond, levels_cfg=cfg, leg_end_time=leg_end_time)
    assert without_pullback["targetSource"] == "leg_high"


def test_preview_rejects_price_that_left_the_imbalance(workdir) -> None:
    signal = _execute_signal()
    array_high = float(signal["annotations"]["array"]["high"])
    run_up = array_high + 0.6 * float(signal["atr"])
    quote = Quote("mt5", "EURUSD.s", run_up - 0.00003, run_up + 0.00003, NOW - 1.0, "mt5_tick")
    preview = _coordinator(workdir, [signal], _Gateway(quote)).preview(signal)
    assert preview["error"] == "PRICE_LEFT_IMBALANCE"
    assert next(g for g in preview["gates"] if g["name"] == "quote_drift")["passed"] is True


def test_failed_broker_execution_blocks_reexecution(workdir, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _execute_signal()
    gateway = _Gateway(_passing_quote(signal))
    gateway.execute = lambda venue, payload, approval: (_ for _ in ()).throw(RuntimeError("socket closed after order send"))
    coordinator = _coordinator(workdir, [signal], gateway, root={"EXECUTOR_MODE": "demo"})
    monkeypatch.setattr(guardian, "pre_trade_check", lambda *a, **k: (True, "OK"))
    monkeypatch.setattr(risk_engine, "risk_check", lambda payload, **k: risk_engine.RiskApproval(True, 0.1, 10.0, 0.001, 0.01, 0.0, "OK", "calculated", "calculated"))
    first = coordinator.execute(signal, mode="demo", idempotency_key="fable-fail:1")
    assert first["status"] == "FAILED" and first["result"]["error"] == "EXECUTION_INTERNAL_ERROR"
    second = coordinator.execute(signal, mode="demo", idempotency_key="fable-fail:2")
    assert second["idempotent"] is True and second["status"] == "FAILED"  # broker state unknown: never re-send


def test_step_decimals_handle_scientific_notation() -> None:
    from fable_engine.execution import _step_decimals

    assert [_step_decimals(s) for s in (0.01, 0.001, 1e-05, 1.0, 100.0)] == [2, 3, 5, 0, 0]
    assert round(0.00436, _step_decimals(1e-05)) == pytest.approx(0.00436)


def test_config_rejects_version_override() -> None:
    with pytest.raises(FableConfigError):
        load_fable_config({"FABLE_ENGINE": {"version": "fable.v2"}})
