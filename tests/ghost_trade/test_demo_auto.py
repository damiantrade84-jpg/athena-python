from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ghost_trade.config import GhostConfig
from ghost_trade.execution.base import DemoAttestation
from ghost_trade.execution.coordinator import CoordinatorResult
from ghost_trade.market_data import CandleBundle
from ghost_trade.models import (
    AssetGroup,
    Candle,
    DemoVerificationStatus,
    Direction,
    GhostInstrument,
    GhostMode,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)
from ghost_trade.persistence import GhostRepository
from ghost_trade.service import GhostService
from ghost_trade.universe import UniverseResult


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def item():
    return GhostInstrument(
        venue=Venue.MT5, broker_symbol="EURUSD.a", canonical_symbol="EUR/USD",
        asset_group=AssetGroup.FOREX, asset_subgroup="forex_major",
        base_asset="EUR", quote_asset="USD",
    )


def candles():
    rows = []
    for index in range(80):
        opened = NOW - timedelta(hours=80 - index)
        rows.append(Candle(opened, opened + timedelta(hours=1), 100, 101, 99, 100, 100))
    series = tuple(rows)
    return CandleBundle(d1=series, h4=series, h1=series, as_of=NOW)


def score():
    return SimpleNamespace(
        direction=Direction.LONG, direction_confidence=0.61,
        confirmed_score=0.7, selected=True, reasons=(),
        entry_quality=SimpleNamespace(score=0.7),
        volatility=SimpleNamespace(regime=VolatilityRegime.NORMAL),
        structure=SimpleNamespace(direction=1, strength=0.7),
        momentum=SimpleNamespace(direction=0.4),
        exhaustion=SimpleNamespace(penalty=0),
        geometry=SimpleNamespace(geometry=SimpleNamespace(
            entry=100, stop=95, target=110, raw_rr=2,
        )),
    )


class Provider:
    def discover(self):
        return UniverseResult((item(),))


class Candles:
    def load(self, *_args):
        return candles()


class Coordinator:
    def __init__(self, repository):
        self.repository = repository
        self.verified = True
        self.calls = []
        self.config = GhostConfig(mode=GhostMode.DEMO_AUTO)
        self.risk_service = SimpleNamespace(config=self.config)

    def attestations(self):
        return {
            Venue.MT5: DemoAttestation(
                Venue.MT5, self.verified,
                DemoVerificationStatus.DEMO_VERIFIED if self.verified else DemoVerificationStatus.DEMO_NOT_VERIFIED,
                "demo" if self.verified else "real", "server",
            )
        }

    def venue_verified(self, _venue):
        return self.verified

    def execute(self, signal_id, *, expected_version, idempotency_key):
        latest = self.repository.latest_scan()
        assert latest is not None and latest["status"] == "COMPLETED"
        self.calls.append((signal_id, expected_version, idempotency_key))
        return CoordinatorResult(True, "SUBMITTED", "exec-auto")


def build(tmp_path, *, configured_auto=True):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    service = GhostService(
        config=GhostConfig(
            mode=GhostMode.DEMO_AUTO, demo_auto_enabled=configured_auto,
            effective_demo_auto_enabled=False,
        ),
        repository=repository,
        universe_providers=(Provider(),),
        candle_provider=Candles(),
        score_fn=lambda *_args, **_kwargs: score(),
        clock=lambda: NOW,
    )
    coordinator = Coordinator(repository)
    service.set_execution_coordinator(coordinator)
    return service, repository, coordinator


def test_demo_auto_always_starts_effectively_off_even_when_configured_true(tmp_path):
    service, _repository, _coordinator = build(tmp_path, configured_auto=True)

    assert service.config.demo_auto_enabled is True
    assert service.health()["demoAutoEnabled"] is False


def test_explicit_toggle_requires_mode_and_current_demo_attestation(tmp_path):
    service, _repository, coordinator = build(tmp_path)
    coordinator.verified = False

    with pytest.raises(PermissionError, match="demo_not_verified"):
        service.set_demo_auto_enabled(True, operator_context={"operator": "test"})

    service.config = GhostConfig(mode=GhostMode.DEMO_MANUAL)
    coordinator.verified = True
    with pytest.raises(PermissionError, match="demo_auto_mode_required"):
        service.set_demo_auto_enabled(True, operator_context={"operator": "test"})


def test_auto_consumes_only_after_committed_scan_and_records_runtime_toggle(tmp_path):
    service, repository, coordinator = build(tmp_path)
    service.set_demo_auto_enabled(True, operator_context={"operator": "test"})

    result = service.scan(style=Style.INTRADAY)

    assert result.auto_execution_ids == ("exec-auto",)
    assert len(coordinator.calls) == 1
    assert coordinator.calls[0][2].startswith("auto:")
    setting = repository.get_runtime_setting("demo_auto_effective")
    assert setting["value"] is True
    assert setting["operator_context"] == {"operator": "test"}


def test_restart_does_not_restore_effective_auto_state(tmp_path):
    first, repository, _coordinator = build(tmp_path)
    first.set_demo_auto_enabled(True, operator_context={"operator": "test"})

    restarted = GhostService(
        config=first.config, repository=repository, universe_providers=(),
        candle_provider=object(), clock=lambda: NOW,
    )

    assert repository.get_runtime_setting("demo_auto_effective")["value"] is True
    assert restarted.health()["demoAutoEnabled"] is False


def test_verification_loss_prevents_order_and_turns_auto_off(tmp_path):
    service, _repository, coordinator = build(tmp_path)
    service.set_demo_auto_enabled(True, operator_context={"operator": "test"})
    coordinator.verified = False

    result = service.scan()

    assert result.auto_execution_ids == ()
    assert coordinator.calls == []
    assert service.health()["demoAutoEnabled"] is False
