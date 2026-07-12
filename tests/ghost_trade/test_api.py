from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import time

from flask import Flask, jsonify

from ghost_trade.api import register_ghost_trade_routes
from ghost_trade.config import GhostConfig
from ghost_trade.models import (
    AssetGroup,
    Direction,
    GhostInstrument,
    GhostMode,
    GhostSignal,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)
from ghost_trade.persistence import GhostRepository
from ghost_trade.service import GhostService, ScanAlreadyRunning
from ghost_trade.runtime import build_ghost_trade_service
from ghost_trade.execution.coordinator import CoordinatorResult


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def build_service(tmp_path):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    service = GhostService(
        config=GhostConfig(mode=GhostMode.SHADOW),
        repository=repository,
        universe_providers=(),
        candle_provider=object(),
        clock=lambda: NOW,
    )
    return service, repository


def stored_signal():
    instrument = GhostInstrument(
        venue=Venue.MT5,
        broker_symbol="EURUSD.a",
        canonical_symbol="EUR/USD",
        asset_group=AssetGroup.FOREX,
        asset_subgroup="forex_major",
        base_asset="EUR",
        quote_asset="USD",
    )
    return GhostSignal(
        signal_id="signal-1",
        signal_version="ghost-v1",
        scan_id="scan-seeded",
        instrument=instrument,
        style=Style.INTRADAY,
        direction=Direction.LONG,
        decision_time=NOW,
        confirmed_score=0.64,
        live_adjustment=-0.04,
        display_score=0.60,
        direction_confidence=0.61,
        entry_quality=0.73,
        entry=1.1,
        stop=1.09,
        target=1.12,
        raw_rr=2.0,
        volatility_regime=VolatilityRegime.NORMAL,
        can_execute=False,
        status=SignalStatus.ELIGIBLE,
        reasons=("shadow_mode",),
        components={"D1Structure": {"score": 0.72}},
        confirmed_times={"D1": "d1", "H4": "h4", "H1": "h1"},
    )


def app_client(tmp_path):
    app = Flask(__name__)
    service, repository = build_service(tmp_path)
    registered = register_ghost_trade_routes(app, SimpleNamespace(service=service))
    assert registered is service
    return app, app.test_client(), service, repository


def test_health_config_and_live_enable_rejection(tmp_path):
    _app, client, _service, _repository = app_client(tmp_path)

    health = client.get("/api/ghost-trade/health")
    config = client.get("/api/ghost-trade/config")
    unsafe = client.put(
        "/api/ghost-trade/config", json={"live_trading_allowed": True}
    )

    assert health.status_code == 200
    assert health.get_json()["mode"] == "SHADOW"
    assert health.get_json()["executionStatus"] == "SHADOW ONLY"
    assert config.get_json()["liveTradingAllowed"] is False
    assert unsafe.status_code == 400
    assert unsafe.get_json()["error"] == "live_trading_prohibited"


def test_scan_universe_current_and_provider_errors_are_explicit(tmp_path):
    _app, client, _service, _repository = app_client(tmp_path)

    scan = client.post("/api/ghost-trade/scan", json={"style": "intraday"})
    current = client.get("/api/ghost-trade/scans/current")
    universe = client.get("/api/ghost-trade/universe")

    assert scan.status_code == 202
    assert scan.get_json()["status"] == "STARTED"
    for _ in range(50):
        current = client.get("/api/ghost-trade/scans/current")
        if current.status_code == 200 and current.get_json()["status"] == "COMPLETED":
            break
        time.sleep(0.01)
    assert current.status_code == 200
    assert current.get_json()["status"] == "COMPLETED"
    assert universe.get_json() == {"instruments": [], "count": 0}


def test_signal_filters_detail_dismiss_and_shadow_execute_rejection(tmp_path):
    _app, client, _service, repository = app_client(tmp_path)
    repository.upsert_signal(stored_signal())

    listing = client.get(
        "/api/ghost-trade/signals?asset_group=forex&direction=LONG&minimum_score=0.60"
    )
    detail = client.get("/api/ghost-trade/signals/signal-1")
    execute = client.post("/api/ghost-trade/signals/signal-1/execute-demo", json={})
    dismiss = client.post("/api/ghost-trade/signals/signal-1/dismiss", json={})

    assert listing.status_code == 200
    assert listing.get_json()["count"] == 1
    assert detail.get_json()["signalId"] == "signal-1"
    assert execute.status_code == 403
    assert execute.get_json()["error"] == "shadow_mode_execution_prohibited"
    assert dismiss.status_code == 200
    assert repository.get_signal("signal-1").status is SignalStatus.DISMISSED


def test_positions_performance_and_groups_are_ghost_only(tmp_path):
    _app, client, _service, repository = app_client(tmp_path)
    repository.upsert_signal(stored_signal())

    positions = client.get("/api/ghost-trade/positions")
    history = client.get("/api/ghost-trade/positions/history")
    performance = client.get("/api/ghost-trade/performance")
    groups = client.get("/api/ghost-trade/groups")

    assert positions.get_json() == {"positions": [], "count": 0}
    assert history.get_json() == {"trades": [], "count": 0}
    assert performance.get_json()["totalClosedTrades"] == 0
    assert groups.get_json()["groups"][0]["assetGroup"] == "forex"
    assert groups.get_json()["groups"][0]["signals"] == 1


def test_scan_conflict_returns_409(tmp_path, monkeypatch):
    _app, client, service, _repository = app_client(tmp_path)
    monkeypatch.setattr(
        service, "start_scan", lambda **_kwargs: (_ for _ in ()).throw(ScanAlreadyRunning())
    )

    response = client.post("/api/ghost-trade/scan", json={})

    assert response.status_code == 409
    assert response.get_json()["error"] == "scan_already_running"


def test_registration_does_not_replace_existing_engine_routes(tmp_path):
    app = Flask(__name__)

    @app.get("/api/health")
    def existing_health():
        return jsonify({"engine": "existing"})

    service, _repository = build_service(tmp_path)
    register_ghost_trade_routes(app, SimpleNamespace(service=service))

    client = app.test_client()
    assert client.get("/api/health").get_json() == {"engine": "existing"}
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert rules.count("/api/health") == 1
    assert "/api/ghost-trade/health" in rules


def test_runtime_builder_is_lazy_and_uses_dedicated_database(tmp_path):
    calls = []
    runtime = SimpleNamespace(
        CONFIG={"ghost_trade": {"mode": "SHADOW"}},
        AUDIT_DB=str(tmp_path / "audit.db"),
        fetch_mt5=lambda *_args: calls.append("mt5"),
        fetch_bybit_klines=lambda *_args: calls.append("bybit"),
        mt5_client=SimpleNamespace(),
        bybit_client=SimpleNamespace(),
    )

    service = build_ghost_trade_service(runtime)

    assert service.config.mode is GhostMode.SHADOW
    assert service.repository.db_path == tmp_path / "ghost_trade.db"
    assert service._execution_coordinator is not None
    assert calls == []


def test_runtime_mt5_candles_use_exact_discovered_symbol_without_static_pair_allowlist(tmp_path):
    calls = []

    class MT5:
        TIMEFRAME_D1 = 1
        TIMEFRAME_H4 = 2
        TIMEFRAME_H1 = 3
        TIMEFRAME_M15 = 4

        def copy_rates_from_pos(self, symbol, timeframe, start, limit):
            calls.append((symbol, timeframe, start, limit))
            return [
                {"time": 1_700_000_000 + index * 86_400, "open": 100, "high": 101,
                 "low": 99, "close": 100, "tick_volume": 10}
                for index in range(20)
            ]

    runtime = SimpleNamespace(
        CONFIG={"ghost_trade": {"mode": "SHADOW"}},
        AUDIT_DB=str(tmp_path / "audit.db"),
        fetch_mt5=lambda *_args: (_ for _ in ()).throw(AssertionError("static loader used")),
        fetch_bybit_klines=lambda *_args: [],
        mt5_client=MT5(), bybit_client=SimpleNamespace(),
    )
    service = build_ghost_trade_service(runtime)

    loaded = service._candle_provider.load(stored_signal().instrument, Style.INTRADAY, NOW)

    assert [call[0] for call in calls] == ["EURUSD.a"] * 4
    assert len(loaded.d1) == len(loaded.h4) == len(loaded.h1) == 20


def test_execute_demo_route_uses_coordinator_and_server_signal_version(tmp_path):
    _app, client, service, repository = app_client(tmp_path)
    repository.upsert_signal(stored_signal())
    service.config = GhostConfig(mode=GhostMode.DEMO_MANUAL)
    calls = []

    class Coordinator:
        def execute(self, signal_id, *, expected_version, idempotency_key):
            calls.append((signal_id, expected_version, idempotency_key))
            return CoordinatorResult(
                True, "SUBMITTED", "execution-1", broker_order_id="order-1",
                broker_position_id="position-1",
            )

    service.set_execution_coordinator(Coordinator())

    response = client.post(
        "/api/ghost-trade/signals/signal-1/execute-demo",
        json={"idempotencyKey": "manual-click-1"},
    )

    assert response.status_code == 200
    assert response.get_json()["executionId"] == "execution-1"
    assert calls == [("signal-1", "ghost-v1", "manual-click-1")]


def test_demo_close_and_close_all_routes_use_execution_coordinator(tmp_path, monkeypatch):
    _app, client, service, repository = app_client(tmp_path)
    service.config = GhostConfig(mode=GhostMode.DEMO_MANUAL)
    monkeypatch.setattr(
        repository,
        "get_position",
        lambda position_id: SimpleNamespace(position_id=position_id, mode="DEMO"),
    )

    class Coordinator:
        def close_position(self, position_id):
            return CoordinatorResult(True, "CLOSED", f"close-{position_id}")

        def close_all(self):
            return [CoordinatorResult(True, "CLOSED", "close-all-1")]

    service.set_execution_coordinator(Coordinator())

    single = client.post("/api/ghost-trade/positions/position-1/close", json={})
    all_positions = client.post("/api/ghost-trade/positions/close-all", json={})

    assert single.status_code == 200
    assert single.get_json()["status"] == "CLOSED"
    assert all_positions.status_code == 200
    assert all_positions.get_json()["results"][0]["executionId"] == "close-all-1"


def test_safe_mode_update_propagates_to_execution_and_risk_services(tmp_path):
    _app, client, service, _repository = app_client(tmp_path)
    coordinator = SimpleNamespace(
        config=service.config,
        risk_service=SimpleNamespace(config=service.config),
    )
    service.set_execution_coordinator(coordinator)

    response = client.put(
        "/api/ghost-trade/config", json={"mode": "DEMO_MANUAL"}
    )

    assert response.status_code == 200
    assert service.config.mode is GhostMode.DEMO_MANUAL
    assert coordinator.config.mode is GhostMode.DEMO_MANUAL
    assert coordinator.risk_service.config.mode is GhostMode.DEMO_MANUAL
