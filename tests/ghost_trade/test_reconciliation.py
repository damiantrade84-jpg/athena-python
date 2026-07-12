from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ghost_trade.config import GhostConfig
from ghost_trade.execution.base import DemoAttestation, DemoExecutionResult
from ghost_trade.execution.coordinator import GhostExecutionCoordinator
from ghost_trade.reconciliation import DemoPositionReconciler
from ghost_trade.models import (
    AssetGroup,
    DemoVerificationStatus,
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
from ghost_trade.position_sizing import SizeResult
from ghost_trade.risk import GhostRiskService


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def signal():
    item = GhostInstrument(
        venue=Venue.MT5, broker_symbol="EURUSD.a", canonical_symbol="EUR/USD",
        asset_group=AssetGroup.FOREX, asset_subgroup="forex_major",
        base_asset="EUR", quote_asset="USD",
    )
    return GhostSignal(
        signal_id="signal-1", signal_version="ghost-v1", scan_id="scan-1",
        instrument=item, style=Style.INTRADAY, direction=Direction.LONG,
        decision_time=NOW, confirmed_score=0.7, live_adjustment=0,
        display_score=0.7, direction_confidence=0.6, entry_quality=0.7,
        entry=1.1, stop=1.09, target=1.12, raw_rr=2,
        volatility_regime=VolatilityRegime.NORMAL, can_execute=True,
        status=SignalStatus.ELIGIBLE,
    )


class FakeAdapter:
    def __init__(self, result=None, verified=True):
        self.result = result or DemoExecutionResult(
            True, "SUBMITTED", broker_order_id="order-1",
            broker_position_id="position-1", entry_price=1.1001,
            raw_response={"success": True},
        )
        self.verified = verified
        self.submit_calls = []
        self.close_calls = []

    def attest(self):
        return DemoAttestation(
            Venue.MT5, self.verified,
            DemoVerificationStatus.DEMO_VERIFIED if self.verified else DemoVerificationStatus.DEMO_NOT_VERIFIED,
            "demo" if self.verified else "real", "server",
            None if self.verified else "not_demo",
        )

    def submit(self, item, approval):
        self.submit_calls.append((item, approval))
        return self.result

    def close(self, broker_position_id, *, direction, quantity):
        self.close_calls.append((broker_position_id, direction, quantity))
        return DemoExecutionResult(
            True, "SUBMITTED", broker_order_id="close-1",
            entry_price=1.11, raw_response={"success": True, "exitPrice": 1.11},
        )


def coordinator(
    tmp_path,
    *,
    adapter=None,
    guardian=lambda *_args: (True, "OK"),
    size_fn=lambda *_args, **_kwargs: SizeResult(True, 0.5, 25, 50, 25),
    symbol_info_provider=lambda _signal: {
        "trade_tick_size": 0.0001, "bid": 1.0999, "ask": 1.1001,
    },
):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    repository.upsert_signal(signal())
    fake = adapter or FakeAdapter()
    approval = SimpleNamespace(
        approved=True, volume=0.5, risk_amount=25, risk_pct=0.0025,
        portfolio_heat=0.01, drawdown_pct=0, reason="OK",
    )
    service = GhostExecutionCoordinator(
        config=GhostConfig(mode=GhostMode.DEMO_MANUAL),
        repository=repository,
        risk_service=GhostRiskService(GhostConfig(), clock=lambda: NOW),
        adapters={Venue.MT5: fake},
        account_provider=lambda _venue: {"balance": 10_000, "equity": 10_000},
        positions_provider=lambda _venue: [],
        symbol_info_provider=symbol_info_provider,
        size_fn=size_fn,
        guardian_fn=guardian,
        approval_fn=lambda *_args, **_kwargs: approval,
        clock=lambda: NOW,
    )
    return service, repository, fake


def test_preflight_fails_closed_without_aborting_when_sizing_raises(tmp_path):
    execution, repository, _adapter = coordinator(
        tmp_path, size_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad tick value"))
    )

    result = execution.preflight(repository.get_signal("signal-1"))

    assert result.approved is False
    assert result.quantity is None
    assert any(reason.startswith("preflight_unavailable:") for reason in result.reasons)


def test_verified_manual_execution_is_persisted_and_idempotent(tmp_path):
    execution, repository, adapter = coordinator(tmp_path)

    first = execution.execute("signal-1", expected_version="ghost-v1", idempotency_key="manual-1")
    second = execution.execute("signal-1", expected_version="ghost-v1", idempotency_key="manual-1")

    assert first.success is True
    assert second.success is True
    assert len(adapter.submit_calls) == 1
    attempt = repository.get_execution_attempt(first.execution_id)
    assert attempt["status"] == "SUBMITTED"
    positions = repository.list_positions(status="OPEN")
    assert len(positions) == 1
    assert positions[0].mode == "DEMO"
    assert positions[0].payload["broker_order_id"] == "order-1"


def test_guardian_rejection_is_recorded_before_broker_submission(tmp_path):
    execution, repository, adapter = coordinator(
        tmp_path, guardian=lambda *_args: (False, "SPREAD_TOO_WIDE")
    )

    result = execution.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-guarded"
    )

    assert result.success is False
    assert result.error_code == "guardian_rejected:SPREAD_TOO_WIDE"
    assert adapter.submit_calls == []
    assert repository.get_execution_attempt(result.execution_id)["status"] == "REJECTED"


def test_unverified_attestation_stops_before_sizing_guardian_and_order(tmp_path):
    adapter = FakeAdapter(verified=False)
    execution, repository, _adapter = coordinator(tmp_path, adapter=adapter)

    result = execution.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-unverified"
    )

    assert result.error_code == "demo_not_verified"
    assert adapter.submit_calls == []
    assert repository.get_execution_attempt(result.execution_id)["status"] == "REJECTED"


def test_partial_fill_is_visible_and_not_reported_as_fully_open(tmp_path):
    adapter = FakeAdapter(
        result=DemoExecutionResult(
            True, "PARTIAL_FILL", broker_order_id="order-partial",
            broker_position_id="position-partial", entry_price=1.1,
            raw_response={"success": True, "filled": 0.2, "remaining": 0.3},
        )
    )
    execution, repository, _adapter = coordinator(tmp_path, adapter=adapter)

    result = execution.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-partial"
    )

    assert result.status == "PARTIAL_FILL"
    assert repository.get_execution_attempt(result.execution_id)["status"] == "PARTIAL_FILL"
    assert repository.list_positions(status="OPEN") == []


def test_demo_reconciliation_closes_matched_position_and_flags_missing_broker_state(tmp_path):
    execution, repository, _adapter = coordinator(tmp_path)
    opened = execution.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-reconcile"
    )
    position = repository.list_positions(status="OPEN")[0]
    reconciler = DemoPositionReconciler(
        repository,
        broker_positions_provider=lambda _venue: [
            {
                "id": "position-1", "status": "closed", "exit_price": 1.12,
                "exit_reason": "TP", "closed_at": NOW.isoformat(),
            }
        ],
        clock=lambda: NOW,
    )

    result = reconciler.run()

    assert opened.success is True
    assert result == {"checked": 1, "closed": 1, "mismatches": 0, "errors": []}
    assert repository.get_position(position.position_id).status == "CLOSED"
    assert repository.list_closed_trades()[0]["exit_reason"] == "TP"

    execution2, repository2, _adapter2 = coordinator(tmp_path / "second")
    execution2.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-missing"
    )
    missing = DemoPositionReconciler(
        repository2, broker_positions_provider=lambda _venue: [], clock=lambda: NOW
    ).run()
    assert missing["mismatches"] == 1
    assert repository2.list_positions()[0].status == "RECONCILIATION_MISMATCH"


def test_verified_manual_close_and_close_all_persist_outcomes(tmp_path):
    execution, repository, adapter = coordinator(tmp_path)
    execution.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-close"
    )
    position = repository.list_positions(status="OPEN")[0]

    closed = execution.close_position(position.position_id)

    assert closed.success is True
    assert adapter.close_calls == [("position-1", "LONG", 0.5)]
    assert repository.get_position(position.position_id).status == "CLOSED"
    assert repository.list_closed_trades()[0]["exit_reason"] == "MANUAL_CLOSE"

    repository.upsert_signal(signal())
    execution.execute(
        "signal-1", expected_version="ghost-v1", idempotency_key="manual-close-all"
    )
    results = execution.close_all()
    assert len(results) == 1
    assert results[0].success is True
    assert repository.list_positions(status="OPEN") == []
