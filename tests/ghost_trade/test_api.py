from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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

    assert scan.status_code == 200
    assert scan.get_json()["discoveredCount"] == 0
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
        service, "scan", lambda **_kwargs: (_ for _ in ()).throw(ScanAlreadyRunning())
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
    assert calls == []
