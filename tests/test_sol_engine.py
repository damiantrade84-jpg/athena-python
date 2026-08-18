from __future__ import annotations

from copy import deepcopy
import math
from types import SimpleNamespace

from flask import Flask
import pytest

from sol_engine.api import register_sol_routes
from sol_engine.config import SolConfigError, load_sol_config
from sol_engine.execution import SolExecutionCoordinator, SolExecutionError
from sol_engine.market_data import normalize_closed_candles
from sol_engine.models import Candle, MarketSnapshot, Quote, TIMEFRAME_SECONDS
from sol_engine.persistence import SolRepository
from sol_engine.replay import _outcome
from sol_engine.scoring import evaluate_snapshot


NOW = 1_800_000_000.0


def _series(
    timeframe: str,
    closes: list[float],
    *,
    volume: float,
    wick: float,
) -> list[Candle]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    count = len(closes)
    previous = closes[0] - 0.02
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        opened_at = NOW - (count - index) * seconds
        rows.append(
            Candle(
                opened_at,
                previous,
                max(previous, close) + wick,
                min(previous, close) - wick,
                close,
                volume,
                "synthetic",
            )
        )
        previous = close
    return rows


def _ready_snapshot(*, mirrored: bool = False, display: str = "SOLTEST") -> MarketSnapshot:
    h4_closes = [100 + index * 0.35 + 0.08 * math.sin(index / 3) for index in range(80)]
    h1_closes = [126 + index * 0.22 + 0.10 * math.sin(index / 4) for index in range(70)]
    h1_closes[-3:] = [140.92, 141.00, 141.08]

    m15_closes = [140.82 + 0.0015 * index + 0.025 * math.sin(index / 2) for index in range(80)]
    m15 = _series("M15", m15_closes, volume=120, wick=0.045)
    prior_low = min(candle.low for candle in m15[-22:-2])
    sweep = m15[-2]
    m15[-2] = Candle(
        sweep.time,
        prior_low + 0.045,
        prior_low + 0.080,
        prior_low - 0.020,
        prior_low + 0.050,
        350,
        "synthetic",
    )
    entry = max(candle.high for candle in m15[:-2]) + 0.005
    last_setup = m15[-1]
    m15[-1] = Candle(
        last_setup.time,
        entry - 0.045,
        entry + 0.020,
        entry - 0.060,
        entry,
        500,
        "synthetic",
    )

    m5_closes = [entry - 0.24 + 0.001 * index + 0.012 * math.sin(index / 3) for index in range(96)]
    m5_closes[-6:] = [entry - 0.17, entry - 0.15, entry - 0.13, entry - 0.09, entry - 0.045, entry]
    m5 = _series("M5", m5_closes, volume=100, wick=0.012)
    for index, volume in zip(range(93, 96), (700, 900, 1200)):
        candle = m5[index]
        m5[index] = Candle(
            candle.time,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            volume,
            "synthetic",
        )

    frames = {
        "H4": _series("H4", h4_closes, volume=1000, wick=0.10),
        "H1": _series("H1", h1_closes, volume=500, wick=0.06),
        "M15": m15,
        "M5": m5,
    }
    if mirrored:
        mirror_axis = 300.0

        def mirror(candle: Candle) -> Candle:
            return Candle(
                candle.time,
                mirror_axis - candle.open,
                mirror_axis - candle.low,
                mirror_axis - candle.high,
                mirror_axis - candle.close,
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
        as_of_epoch=NOW,
    )


def _ready_signal(*, mirrored: bool = False, display: str = "SOLTEST") -> dict:
    return evaluate_snapshot(
        _ready_snapshot(mirrored=mirrored, display=display),
        load_sol_config(),
        generated_at_epoch=NOW,
    )


def _store_signals(repository: SolRepository, signals: list[dict]) -> None:
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
    database: str = "sol.db",
) -> SolExecutionCoordinator:
    repository = SolRepository(tmp_path / database)
    _store_signals(repository, signals)
    return SolExecutionCoordinator(
        config=load_sol_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: kill_switch,
        now_fn=lambda: NOW,
    )


def _passing_quote(signal: dict, *, timestamp: float = NOW) -> Quote:
    ask = float(signal["entry"]) + float(signal["atr"]) * 0.01
    return Quote("mt5", signal["symbol"], ask - 0.002, ask, timestamp, "test_tick")


def test_default_contract_is_valid_and_live_requires_research_validation() -> None:
    config = load_sol_config()

    assert config.version == "sol.v1"
    assert sum(config.scoring["weights"].values()) == 100.0
    assert config.execution["default_mode"] == "paper"
    assert config.execution["live_enabled"] is False
    with pytest.raises(SolConfigError, match="research_status=VALIDATED"):
        load_sol_config({"SOL_ENGINE": {"execution": {"live_enabled": True}}})


def test_candle_normalization_drops_forming_future_and_malformed_rows() -> None:
    rows = [
        {"open_time": NOW - 600, "open": 100, "high": 102, "low": 99, "close": 101},
        {"open_time": NOW - 120, "open": 101, "high": 102, "low": 100, "close": 101.5},
        {"open_time": NOW + 20, "open": 101, "high": 102, "low": 100, "close": 101.5},
        {"open_time": NOW - 1200, "open": 100, "high": 100.5, "low": 99, "close": 101},
    ]

    candles, provenance, errors = normalize_closed_candles(rows, "M5", now_epoch=NOW, provider="test")

    assert len(candles) == 1
    assert provenance["formingBarsDropped"] == 1
    assert provenance["futureBarsDropped"] == 1
    assert provenance["malformedBarsDropped"] == 1
    assert any(error.startswith("FUTURE_CANDLES:M5:1") for error in errors)
    assert any(error.startswith("MALFORMED_CANDLES:M5:1") for error in errors)


def test_post_as_of_bar_is_dropped_without_false_future_error() -> None:
    rows = [
        {"open_time": NOW - 600, "open": 100, "high": 102, "low": 99, "close": 101},
        {"open_time": NOW + 20, "open": 101, "high": 102, "low": 100, "close": 101.5},
    ]

    candles, provenance, errors = normalize_closed_candles(
        rows,
        "M5",
        now_epoch=NOW,
        observed_at_epoch=NOW + 120,
        provider="test",
    )

    assert len(candles) == 1
    assert provenance["postAsOfBarsDropped"] == 1
    assert not any(error.startswith("FUTURE_CANDLES") for error in errors)


@pytest.mark.parametrize(("mirrored", "direction"), [(False, "LONG"), (True, "SHORT")])
def test_ready_signal_is_symmetric_auditable_and_fully_gated(mirrored: bool, direction: str) -> None:
    signal = _ready_signal(mirrored=mirrored)

    assert signal["decision"] == "READY"
    assert signal["direction"] == direction
    assert signal["setup"] == "SWEEP_RECLAIM"
    assert signal["score"] >= signal["readyThreshold"]
    assert sum(component["maxScore"] for component in signal["components"]) == 100.0
    assert sum(component["score"] for component in signal["components"]) == pytest.approx(signal["score"])
    assert all(gate["passed"] for gate in signal["gates"])
    assert signal["blockingReasons"] == []
    assert signal["setupEventAt"]
    assert abs(signal["indicatorState"]["participationImpulse"]["z"]) < 100
    if direction == "LONG":
        assert signal["stop"] < signal["entry"] < signal["target"]
    else:
        assert signal["target"] < signal["entry"] < signal["stop"]


def test_same_setup_keeps_one_identity_across_trigger_refreshes() -> None:
    config = load_sol_config()
    snapshot = _ready_snapshot()
    first = evaluate_snapshot(snapshot, config, generated_at_epoch=NOW)
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    prior = frames["M5"][-1]
    refreshed_close = prior.close + 0.024
    frames["M5"].append(
        Candle(
            NOW,
            prior.close,
            refreshed_close + 0.004,
            prior.close - 0.002,
            refreshed_close,
            1400,
            "synthetic",
        )
    )
    refreshed = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, NOW + 300),
        config,
        generated_at_epoch=NOW + 300,
    )

    assert refreshed["decision"] == "READY"
    assert refreshed["barClosedAt"] != first["barClosedAt"]
    assert refreshed["setupEventAt"] == first["setupEventAt"]
    assert refreshed["signalId"] == first["signalId"]


def test_future_closed_bar_fails_closed_even_when_the_score_is_high() -> None:
    snapshot = _ready_snapshot()
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    prior = frames["H4"][-1]
    frames["H4"].append(
        Candle(NOW, prior.close, prior.close + 0.2, prior.close - 0.1, prior.close + 0.1, 1000, "synthetic")
    )

    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, NOW),
        load_sol_config(),
        generated_at_epoch=NOW,
    )

    assert signal["decision"] == "BLOCKED"
    assert any(reason.startswith("FUTURE_DATA:H4:") for reason in signal["blockingReasons"])


def test_same_bar_stop_and_target_is_scored_stop_first() -> None:
    signal = {"direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0}
    future = [Candle(NOW, 100.0, 103.0, 98.0, 101.0, 1, "synthetic")]

    assert _outcome(signal, future) == {
        "outcome": "LOSS",
        "rMultiple": -1.0,
        "barsHeld": 1,
        "exit": 99.0,
    }


def test_signal_upsert_preserves_execution_foreign_key(tmp_path) -> None:
    repository = SolRepository(tmp_path / "ledger.db")
    repository.migrate()
    signal = _ready_signal()
    first_scan = repository.create_scan({"scan": 1})
    repository.save_signals(first_scan, [signal])
    reservation, created = repository.reserve_execution(
        signal_id=signal["signalId"],
        idempotency_key="first",
        mode="paper",
        venue="mt5",
        request_payload={"signal": signal},
    )
    assert created is True
    repository.complete_execution(reservation["execution_id"], "SUCCESS", {"success": True})

    second_scan = repository.create_scan({"scan": 2})
    updated = {**signal, "score": signal["score"] + 0.01}
    repository.save_signals(second_scan, [updated])

    assert repository.get_signal(signal["signalId"])["score"] == updated["score"]
    duplicate, duplicate_created = repository.reserve_execution(
        signal_id=signal["signalId"],
        idempotency_key="second",
        mode="paper",
        venue="mt5",
        request_payload={"signal": updated},
    )
    assert duplicate_created is False
    assert duplicate["status"] == "SUCCESS"


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
    assert second["status"] == "SUCCESS"
    assert gateway.quote_calls == 1
    assert gateway.account_calls == 0
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
    stale_contract = {**signal, "contractVersion": "sol.old"}

    preview = coordinator.preview(stale_contract)

    assert preview["executable"] is False
    assert preview["error"] == "SIGNAL_CONTRACT_STALE"
    assert any(gate["reason"] == "KILL_SWITCH_ACTIVE" for gate in preview["gates"])
    assert gateway.quote_calls == 0


def test_tampered_deterministic_gate_proof_rejects_before_quote(tmp_path) -> None:
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
    assert gateway.account_calls == 1
    assert gateway.position_calls == 0
    assert gateway.execute_calls == 0


def test_demo_order_reaches_broker_with_freshness_and_quote_contract(tmp_path, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    approval = risk_engine.RiskApproval(
        True,
        0.1,
        10.0,
        0.001,
        0.001,
        0.0,
        "OK",
        "calculated",
        "calculated",
    )
    monkeypatch.setattr(guardian, "pre_trade_check", lambda *args, **kwargs: (True, "OK"))
    monkeypatch.setattr(risk_engine, "risk_check", lambda *args, **kwargs: approval)

    result = coordinator.execute(signal, mode="demo", idempotency_key="demo-contract")

    assert result["status"] == "SUCCESS"
    assert gateway.execute_calls == 1
    assert gateway.last_payload is not None
    assert gateway.last_payload["solExecution"] is True
    assert gateway.last_payload["quoteTimestamp"] == NOW
    assert set(gateway.last_payload["candleFreshness"]) == {"H4", "H1", "M15", "M5"}
    assert all(row["status"] == "FRESH" for row in gateway.last_payload["candleFreshness"].values())
    assert gateway.last_payload["sl"] == signal["stop"]
    assert gateway.last_payload["tp1"] == signal["target"]


def test_risk_gateway_cannot_mutate_sol_levels(tmp_path, monkeypatch) -> None:
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


def test_idempotency_key_cannot_be_reused_for_another_signal(tmp_path) -> None:
    first_signal = _ready_signal(display="SOL-ONE")
    second_signal = _ready_signal(display="SOL-TWO")
    gateway = _Gateway(_passing_quote(first_signal))
    coordinator = _coordinator(tmp_path, [first_signal, second_signal], gateway)
    coordinator.execute(first_signal, mode="paper", idempotency_key="shared-key")

    with pytest.raises(SolExecutionError) as error:
        coordinator.execute(second_signal, mode="paper", idempotency_key="shared-key")

    assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert gateway.execute_calls == 0


def test_live_mode_is_disabled_by_default(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)

    with pytest.raises(SolExecutionError) as error:
        coordinator.execute(signal, mode="live", idempotency_key="live", confirm_live=True)

    assert error.value.code == "LIVE_EXECUTION_DISABLED"
    assert gateway.quote_calls == 0


def test_api_routes_register_under_the_sol_namespace() -> None:
    config = load_sol_config()

    class Service:
        def health(self):
            return {"success": True, "engine": "SOL"}

        def config_dict(self):
            return config.public_dict()

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
    register_sol_routes(app, SimpleNamespace(service=service))

    response = app.test_client().get("/api/sol/health")
    preview = app.test_client().post("/api/sol/signals/test/preview", json={})
    rules = {rule.rule for rule in app.url_map.iter_rules()}

    assert response.status_code == 200
    assert response.get_json()["engine"] == "SOL"
    assert preview.status_code == 200
    assert preview.get_json()["error"] == "SPREAD_TOO_WIDE"
    assert "/api/sol/signals/<signal_id>/execute" in rules
    assert "/api/sol/replay" in rules
