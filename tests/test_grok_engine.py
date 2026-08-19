from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from types import SimpleNamespace

from flask import Flask
import pytest

from grok_engine.api import register_grok_routes
from grok_engine.config import GrokConfigError, load_grok_config
from grok_engine.execution import GrokExecutionCoordinator, GrokExecutionError
from grok_engine.indicators import raid_signature, void_map, wilder_atr
from grok_engine.market_data import normalize_closed_candles
from grok_engine.models import Candle, MarketSnapshot, Quote, TIMEFRAME_SECONDS
from grok_engine.persistence import GrokRepository
from grok_engine.replay import _outcome
from grok_engine.scoring import evaluate_snapshot
from grok_engine.sessions import classify_session, market_is_open


NOW = datetime(2026, 3, 17, 14, 32, tzinfo=timezone.utc).timestamp()


def _last_closed_open(timeframe: str, as_of: float = NOW) -> float:
    seconds = TIMEFRAME_SECONDS[timeframe]
    return as_of - (as_of % seconds) - seconds


def _candle(opened: float, open_: float, high: float, low: float, close: float, volume: float = 120.0) -> Candle:
    return Candle(opened, open_, high, low, close, volume, "synthetic")


def _series(timeframe: str, count: int, price_at, *, as_of: float = NOW, wick: float = 0.0004) -> list[Candle]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    last_open = _last_closed_open(timeframe, as_of)
    rows: list[Candle] = []
    previous = float(price_at(last_open - (count - 1) * seconds))
    for index in range(count):
        opened = last_open - (count - 1 - index) * seconds
        close = float(price_at(opened))
        high = max(previous, close) + wick
        low = min(previous, close) - wick
        rows.append(_candle(opened, previous, high, low, close))
        previous = close
    return rows


def _ready_snapshot(*, mirrored: bool = False, display: str = "EURUSD", as_of: float = NOW) -> MarketSnapshot:
    asia_low = 1.09840
    asia_high = 1.10220
    raid_low = 1.09790
    impulse_top = 1.10090
    entry = 1.09920

    def d1_price(opened: float) -> float:
        age_days = max(0, int((_last_closed_open("D1") - opened) / TIMEFRAME_SECONDS["D1"]))
        return 1.0900 + 0.00018 * (80 - age_days)

    def path(opened: float) -> float:
        local = datetime.fromtimestamp(opened, tz=timezone.utc)
        minutes = local.hour * 60 + local.minute
        if minutes < 360:
            wave = 0.00025 * math.sin(opened / 900.0)
            return 1.09980 + wave
        if minutes < 420:
            return 1.09890
        if minutes < 720:
            progress = (minutes - 420) / 300.0
            return 1.09890 + progress * (impulse_top - 1.09890)
        progress = min(1.0, (minutes - 720) / 180.0)
        return impulse_top - progress * (impulse_top - entry)

    d1 = _series("D1", 80, d1_price, as_of=as_of, wick=0.0040)
    last_d1 = d1[-1]
    d1[-1] = _candle(last_d1.time, last_d1.open, 1.10580, 1.09420, last_d1.close, 8000)
    h1 = _series("H1", 90, path, as_of=as_of, wick=0.0008)
    swing = h1[-36]
    h1[-36] = _candle(swing.time, 1.10200, 1.10620, 1.10140, 1.10540, 900)
    trough = h1[-24]
    h1[-24] = _candle(trough.time, 1.10080, 1.10110, 1.09640, 1.09710, 900)
    m15 = _series("M15", 160, path, as_of=as_of, wick=0.00018)
    m5 = _series("M5", 220, path, as_of=as_of, wick=0.00018)

    asia_indexes = [
        index
        for index, candle in enumerate(m15)
        if datetime.fromtimestamp(candle.time, tz=timezone.utc).strftime("%Y-%m-%d") == "2026-03-17"
        and datetime.fromtimestamp(candle.time, tz=timezone.utc).hour < 6
    ]
    for offset, index in enumerate(asia_indexes):
        candle = m15[index]
        close = 1.09990 + 0.00010 * math.sin(offset)
        high = asia_high if offset == 0 else close + 0.00016
        low = asia_low if offset == 1 else close - 0.00016
        m15[index] = _candle(candle.time, close, high, low, close + 0.00004)

    raid_index = len(m15) - 6
    raid = m15[raid_index]
    left = m15[raid_index + 1]
    mid = m15[raid_index + 2]
    right = m15[raid_index + 3]
    pull = m15[raid_index + 4]
    last = m15[raid_index + 5]
    m15[raid_index] = _candle(raid.time, 1.09880, 1.09910, raid_low, 1.09895, 420)
    m15[raid_index + 1] = _candle(left.time, 1.09895, 1.09905, 1.09820, 1.09870, 360)
    m15[raid_index + 2] = _candle(mid.time, 1.09870, 1.10040, 1.09860, 1.10025, 420)
    m15[raid_index + 3] = _candle(right.time, 1.10025, 1.10090, 1.09945, 1.10080, 440)
    m15[raid_index + 4] = _candle(pull.time, 1.10020, 1.10055, 1.09958, 1.10005, 230)
    m15[raid_index + 5] = _candle(last.time, 1.09940, 1.09970, 1.09912, entry, 240)

    # Trigger impulse on M5 into the void.
    m5_closes = [entry - 0.0018 + 0.00012 * step for step in range(8)]
    m5_closes[-1] = entry
    for offset, close in enumerate(m5_closes, start=len(m5) - 8):
        candle = m5[offset]
        open_ = close - 0.00014
        m5[offset] = _candle(candle.time, open_, close + 0.00006, open_ - 0.00004, close, 260)

    frames = {"D1": d1, "H1": h1, "M15": m15, "M5": m5}
    if mirrored:
        axis = 3.0

        def mirror(candle: Candle) -> Candle:
            return Candle(
                candle.time,
                axis - candle.open,
                axis - candle.low,
                axis - candle.high,
                axis - candle.close,
                candle.volume,
                candle.volume_source,
            )

        frames = {timeframe: [mirror(candle) for candle in rows] for timeframe, rows in frames.items()}

    return MarketSnapshot(
        pair={"display": display, "symbol": display, "type": "forex"},
        frames=frames,
        provenance={
            timeframe: {"provider": "synthetic", "bars": len(rows)}
            for timeframe, rows in frames.items()
        },
        as_of_epoch=as_of,
    )


def _ready_signal(*, mirrored: bool = False, display: str = "EURUSD") -> dict:
    return evaluate_snapshot(
        _ready_snapshot(mirrored=mirrored, display=display),
        load_grok_config(),
        generated_at_epoch=NOW,
    )


def _store_signals(repository: GrokRepository, signals: list[dict]) -> None:
    repository.migrate()
    scan_id = repository.create_scan({"test": True})
    repository.save_signals(scan_id, signals)
    repository.complete_scan(scan_id, "COMPLETED", {"pairCount": len(signals)})


class _Gateway:
    def __init__(self, quote: Quote, *, account: dict | None = None) -> None:
        self.current_quote = quote
        self.current_account = account or {
            "error": False,
            "demo": True,
            "testnet": False,
            "balance": 10_000.0,
            "equity": 10_000.0,
            "risk_domain": "test",
        }
        self.quote_calls = 0
        self.account_calls = 0
        self.position_calls = 0
        self.execute_calls = 0
        self.last_payload: dict | None = None

    def quote(self, signal: dict) -> Quote:
        self.quote_calls += 1
        return self.current_quote

    def account(self, venue: str) -> dict:
        self.account_calls += 1
        return self.current_account

    def positions(self, venue: str) -> dict:
        self.position_calls += 1
        return {"error": False, "positions": []}

    def symbol_info(self, signal: dict) -> dict:
        return {"error": False, "volume_min": 0.01, "volume_step": 0.01}

    def execute(self, venue: str, payload: dict, approval) -> dict:
        self.execute_calls += 1
        self.last_payload = payload
        return {"success": True, "ticket": "TEST"}


def _coordinator(
    tmp_path,
    signals: list[dict],
    gateway: _Gateway,
    *,
    kill_switch: bool = False,
    database: str = "grok.db",
) -> GrokExecutionCoordinator:
    repository = GrokRepository(tmp_path / database)
    _store_signals(repository, signals)
    return GrokExecutionCoordinator(
        config=load_grok_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: kill_switch,
        now_fn=lambda: NOW,
    )


def _passing_quote(signal: dict, *, timestamp: float = NOW) -> Quote:
    ask = float(signal["entry"]) + float(signal["atr"]) * 0.01
    return Quote("mt5", signal["symbol"], ask - 0.00020, ask, timestamp, "test_tick")


def test_default_contract_is_valid_and_live_requires_research_validation() -> None:
    config = load_grok_config()
    assert config.version == "grok.v1"
    assert sum(config.scoring["weights"].values()) == 100.0
    assert config.execution["default_mode"] == "paper"
    assert config.execution["live_enabled"] is False
    with pytest.raises(GrokConfigError, match="research_status=VALIDATED"):
        load_grok_config({"GROK_ENGINE": {"execution": {"live_enabled": True}}})


def test_ny_silver_bullet_clock_and_weekday_session_gate() -> None:
    config = load_grok_config()
    clock = classify_session(NOW, config)
    assert clock["primaryKind"] == "silver_bullet"
    assert clock["quality"] == 1.0
    assert market_is_open(NOW, config, "forex") is True
    friday_close = datetime(2026, 3, 13, 21, 30, tzinfo=timezone.utc).timestamp()
    assert market_is_open(friday_close, config, "forex") is False
    assert market_is_open(friday_close, config, "crypto") is True


def test_candle_normalization_drops_forming_future_and_malformed_rows() -> None:
    rows = [
        {"open_time": NOW - 600, "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11},
        {"open_time": NOW - 120, "open": 1.11, "high": 1.12, "low": 1.10, "close": 1.115},
        {"open_time": NOW + 20, "open": 1.11, "high": 1.12, "low": 1.10, "close": 1.115},
        {"open_time": NOW - 1200, "open": 1.10, "high": 1.105, "low": 1.09, "close": 1.11},
    ]
    candles, provenance, errors = normalize_closed_candles(rows, "M5", now_epoch=NOW, provider="test")
    assert len(candles) == 1
    assert provenance["formingBarsDropped"] == 1
    assert provenance["futureBarsDropped"] == 1
    assert provenance["malformedBarsDropped"] == 1
    assert any(error.startswith("FUTURE_CANDLES:M5:1") for error in errors)


def test_raid_and_void_indicators_are_directional() -> None:
    snapshot = _ready_snapshot()
    atr = wilder_atr(snapshot.frames["M15"], 14)
    raid = raid_signature(
        snapshot.frames["M15"],
        {"asiaLow": 1.09840, "asiaHigh": 1.10220},
        {"pdl": 1.09420, "pdh": 1.10580},
        atr=atr,
        lookback=16,
        recent_bars=6,
        min_excursion_atr=0.08,
    )
    void = void_map(snapshot.frames["M15"], lookback=36, atr=atr)
    assert raid["available"] is True
    assert raid["direction"] == 1
    assert void["available"] is True
    assert void["direction"] == 1
    assert void["open"] is True


@pytest.mark.parametrize(("mirrored", "direction"), [(False, "LONG"), (True, "SHORT")])
def test_ready_signal_is_symmetric_auditable_and_fully_gated(mirrored: bool, direction: str) -> None:
    signal = _ready_signal(mirrored=mirrored)
    assert signal["decision"] == "READY", signal["blockingReasons"]
    assert signal["direction"] == direction
    assert signal["setup"] in {"SILVER_BULLET", "JUDAS_RECLAIM", "CISD_CONTINUATION", "OTE_DELIVERY"}
    assert signal["score"] >= signal["readyThreshold"]
    assert sum(component["maxScore"] for component in signal["components"]) == 100.0
    assert sum(component["score"] for component in signal["components"]) == pytest.approx(signal["score"])
    assert all(gate["passed"] for gate in signal["gates"])
    assert signal["blockingReasons"] == []
    if direction == "LONG":
        assert signal["stop"] < signal["entry"] < signal["target"]
    else:
        assert signal["target"] < signal["entry"] < signal["stop"]


def test_outside_killzone_cannot_be_ready() -> None:
    lunch = datetime(2026, 3, 17, 16, 20, tzinfo=timezone.utc).timestamp()
    snapshot = _ready_snapshot(as_of=lunch)
    signal = evaluate_snapshot(snapshot, load_grok_config(), generated_at_epoch=lunch)
    assert signal["decision"] != "READY"
    assert any("OUTSIDE_KILLZONE" in reason for reason in signal["blockingReasons"])


def test_future_closed_bar_fails_closed() -> None:
    snapshot = _ready_snapshot()
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    prior = frames["H1"][-1]
    frames["H1"].append(Candle(NOW, prior.close, prior.close + 0.002, prior.close - 0.001, prior.close + 0.001, 1000, "synthetic"))
    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, NOW),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    assert signal["decision"] == "BLOCKED"
    assert any(reason.startswith("FUTURE_DATA:H1:") for reason in signal["blockingReasons"])


def test_same_bar_stop_and_target_is_scored_stop_first() -> None:
    signal = {"direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0}
    future = [Candle(NOW, 100.0, 103.0, 98.0, 101.0, 1, "synthetic")]
    assert _outcome(signal, future) == {"outcome": "LOSS", "rMultiple": -1.0, "barsHeld": 1, "exit": 99.0}


def test_paper_execution_attests_quote_and_never_calls_broker_order(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    first = coordinator.execute(signal, mode="paper", idempotency_key="paper-1")
    second = coordinator.execute(signal, mode="paper", idempotency_key="paper-1")
    assert first["status"] == "SUCCESS"
    assert first["result"]["mode"] == "paper"
    assert first["result"]["riskCash"] == 25.0
    assert second["idempotent"] is True
    assert gateway.quote_calls == 1
    assert gateway.execute_calls == 0


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (NOW - 20, "BROKER_QUOTE_STALE"),
        (NOW + 5, "BROKER_QUOTE_TIMESTAMP_INVALID"),
    ],
)
def test_quote_time_anomalies_reject_before_execution(tmp_path, timestamp: float, expected: str) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal, timestamp=timestamp))
    coordinator = _coordinator(tmp_path, [signal], gateway, database=f"{int(timestamp)}.db")
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == expected
    assert gateway.execute_calls == 0


def test_stale_contract_and_kill_switch_reject_before_quote(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway, kill_switch=True)
    preview = coordinator.preview({**signal, "contractVersion": "grok.old"})
    assert preview["executable"] is False
    assert preview["error"] == "SIGNAL_CONTRACT_STALE"
    assert any(gate["reason"] == "KILL_SWITCH_ACTIVE" for gate in preview["gates"])
    assert gateway.quote_calls == 0


def test_tampered_gate_proof_rejects_before_quote(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    tampered = deepcopy(signal)
    tampered["gates"][0]["passed"] = False
    preview = coordinator.preview(tampered)
    assert preview["executable"] is False
    assert preview["error"] == "SIGNAL_GATE_PROOF_INVALID"
    assert gateway.quote_calls == 0


def test_demo_order_rejects_real_account_attestation(tmp_path) -> None:
    signal = _ready_signal()
    real_account = {
        "error": False,
        "demo": False,
        "testnet": False,
        "accountEnvironment": "real",
        "balance": 10_000.0,
        "equity": 10_000.0,
    }
    gateway = _Gateway(_passing_quote(signal), account=real_account)
    coordinator = _coordinator(tmp_path, [signal], gateway)
    result = coordinator.execute(signal, mode="demo", idempotency_key="demo-real-mismatch")
    assert result["status"] == "REJECTED"
    assert result["result"]["error"] == "DEMO_ACCOUNT_ATTESTATION_FAILED"
    assert gateway.execute_calls == 0


def test_demo_order_reaches_broker_with_freshness_and_quote_contract(tmp_path, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    approval = risk_engine.RiskApproval(True, 0.1, 10.0, 0.001, 0.001, 0.0, "OK", "calculated", "calculated")
    monkeypatch.setattr(guardian, "pre_trade_check", lambda *args, **kwargs: (True, "OK"))
    monkeypatch.setattr(risk_engine, "risk_check", lambda *args, **kwargs: approval)
    result = coordinator.execute(signal, mode="demo", idempotency_key="demo-contract")
    assert result["status"] == "SUCCESS"
    assert gateway.execute_calls == 1
    assert gateway.last_payload["grokExecution"] is True
    assert gateway.last_payload["engine"] == "GROK"
    assert set(gateway.last_payload["candleFreshness"]) == {"D1", "H1", "M15", "M5"}
    assert gateway.last_payload["sl"] == signal["stop"]
    assert gateway.last_payload["tp1"] == signal["target"]


def test_risk_gateway_cannot_mutate_grok_levels(tmp_path, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    approval = risk_engine.RiskApproval(True, 0.1, 10.0, 0.001, 0.001, 0.0, "OK")

    def mutating_guardian(payload, *args, **kwargs):
        payload["sl"] = float(payload["sl"]) - 0.01
        return True, "OK"

    monkeypatch.setattr(guardian, "pre_trade_check", mutating_guardian)
    monkeypatch.setattr(risk_engine, "risk_check", lambda *args, **kwargs: approval)
    result = coordinator.execute(signal, mode="demo", idempotency_key="mutating-risk")
    assert result["status"] == "REJECTED"
    assert result["result"]["error"] == "IMMUTABLE_LEVELS_MUTATED_BY_RISK_GATE"
    assert gateway.execute_calls == 0


def test_grok_execute_blocked_when_engine_disabled(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    repository = GrokRepository(tmp_path / "disabled.db")
    _store_signals(repository, [signal])
    coordinator = GrokExecutionCoordinator(
        config=load_grok_config({"GROK_ENGINE": {"enabled": False}}),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: False,
        now_fn=lambda: NOW,
    )
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "GROK_ENGINE_DISABLED"
    with pytest.raises(GrokExecutionError) as error:
        coordinator.execute(signal, mode="paper", idempotency_key="disabled")
    assert error.value.code == "GROK_ENGINE_DISABLED"
    assert gateway.quote_calls == 0
    assert gateway.execute_calls == 0


def test_grok_kill_switch_alone_rejects_execute(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway, kill_switch=True)
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "KILL_SWITCH_ACTIVE"
    assert gateway.quote_calls == 0


def test_grok_execute_rejects_stale_closed_bars_inside_signal_age(tmp_path) -> None:
    signal = _ready_signal()
    later = NOW + 9 * 60
    gateway = _Gateway(_passing_quote(signal, timestamp=later))
    repository = GrokRepository(tmp_path / "stale-bars.db")
    _store_signals(repository, [signal])
    coordinator = GrokExecutionCoordinator(
        config=load_grok_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: False,
        now_fn=lambda: later,
    )
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert str(preview["error"] or "").startswith("STALE_DATA:M5:")
    assert gateway.execute_calls == 0


def test_grok_execute_rejects_after_session_close(tmp_path) -> None:
    signal = _ready_signal()
    friday = datetime(2026, 3, 20, 21, 20, tzinfo=timezone.utc).timestamp()
    stamp = "2026-03-20T21:10:00Z"
    signal["generatedAt"] = stamp
    for gate in signal.get("gates") or []:
        if isinstance(gate, dict) and str(gate.get("name") or "").endswith("_freshness"):
            gate["lastClosedAt"] = stamp
    for meta in (signal.get("dataProvenance") or {}).values():
        if isinstance(meta, dict):
            meta["lastClosedAt"] = stamp
    gateway = _Gateway(_passing_quote(signal, timestamp=friday))
    repository = GrokRepository(tmp_path / "session-close.db")
    _store_signals(repository, [signal])
    coordinator = GrokExecutionCoordinator(
        config=load_grok_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: False,
        now_fn=lambda: friday,
    )
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "SESSION_CLOSED"
    assert gateway.quote_calls == 0


def test_grok_execute_rejects_pair_removed_from_active_book(tmp_path) -> None:
    from grok_engine.service import GrokService

    signal = _ready_signal()
    repository = GrokRepository(tmp_path / "pair-book.db")
    _store_signals(repository, [signal])
    service = GrokService(
        config=load_grok_config(),
        repository=repository,
        market_data=None,
        pair_provider=lambda: [],
        execution=None,
        log=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )
    with pytest.raises(GrokExecutionError) as error:
        service._executable_signal(signal["signalId"])
    assert error.value.code == "PAIR_NOT_IN_ACTIVE_BOOK"


def test_grok_risk_policy_stays_fail_closed_and_not_engine_a() -> None:
    from tp_sl_rr_gate_policy import engine_ab_profitability_gates_enforced, resolve_profitability_gate_engine

    signal = {"engine": "GROK", "grokExecution": True, "source": "grok_engine"}
    assert resolve_profitability_gate_engine(signal) is None
    assert (
        engine_ab_profitability_gates_enforced(
            {"ENGINE_AB_PROFITABILITY_GATES_ENFORCED": False},
            signal=signal,
            engine="grok",
        )
        is True
    )


def test_live_mode_is_disabled_by_default(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    with pytest.raises(GrokExecutionError) as error:
        coordinator.execute(signal, mode="live", idempotency_key="live", confirm_live=True)
    assert error.value.code == "LIVE_EXECUTION_DISABLED"
    assert gateway.quote_calls == 0


def test_api_routes_register_under_the_grok_namespace() -> None:
    config = load_grok_config()

    class Service:
        def health(self):
            return {"success": True, "engine": "GROK"}

        def config_dict(self):
            return config.public_dict()

        def accounts(self):
            return {"success": True, "venues": {}}

        def current_scan(self):
            return None

        def signals(self, **kwargs):
            return []

        def execution_history(self, **kwargs):
            return []

        def preview_execution(self, signal_id):
            return {
                "executable": False,
                "error": "SPREAD_TOO_WIDE",
                "gates": [{"name": "spread", "passed": False, "reason": "SPREAD_TOO_WIDE"}],
            }

    service = Service()
    service.config = config
    app = Flask(__name__)
    register_grok_routes(app, SimpleNamespace(service=service))
    response = app.test_client().get("/api/grok/health")
    preview = app.test_client().post("/api/grok/signals/test/preview", json={})
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert response.status_code == 200
    assert response.get_json()["engine"] == "GROK"
    assert preview.status_code == 200
    assert preview.get_json()["error"] == "SPREAD_TOO_WIDE"
    assert "/api/grok/signals/<signal_id>/execute" in rules
    assert "/api/grok/accounts" in rules
    assert "/api/grok/replay" in rules
