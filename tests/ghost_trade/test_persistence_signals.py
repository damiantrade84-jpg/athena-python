from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from ghost_trade.models import (
    AssetGroup,
    Direction,
    GhostInstrument,
    GhostSignal,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)
from ghost_trade.persistence import (
    GhostRepository,
    SignalConflictError,
    signal_id_for,
)


NOW = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)


def instrument(symbol="EURUSD.a", canonical="EUR/USD"):
    return GhostInstrument(
        venue=Venue.MT5,
        broker_symbol=symbol,
        canonical_symbol=canonical,
        asset_group=AssetGroup.FOREX,
        asset_subgroup="forex_major",
        base_asset="EUR",
        quote_asset="USD",
    )


def signal(*, score=0.456789123456, direction=Direction.LONG):
    inst = instrument()
    signal_id = signal_id_for(
        engine_version="ghost-v1",
        venue=inst.venue,
        broker_symbol=inst.broker_symbol,
        style=Style.INTRADAY,
        decision_time=NOW,
        confirmed_times={"D1": "d1", "H4": "h4", "H1": "h1"},
    )
    return GhostSignal(
        signal_id=signal_id,
        signal_version="ghost-v1",
        scan_id="scan-1",
        instrument=inst,
        style=Style.INTRADAY,
        direction=direction,
        decision_time=NOW,
        confirmed_score=score,
        live_adjustment=-0.0123456789,
        display_score=score - 0.0123456789,
        direction_confidence=0.61,
        entry_quality=0.73,
        entry=1.123456789,
        stop=1.12,
        target=1.13,
        raw_rr=2.3456789,
        volatility_regime=VolatilityRegime.NORMAL,
        can_execute=False,
        status=SignalStatus.ANALYSED,
        reasons=("demo_not_verified",),
        components={"D1": {"score": 0.72}},
        confirmed_times={"D1": "d1", "H4": "h4", "H1": "h1"},
    )


def test_migration_creates_only_dedicated_ghost_tables(tmp_path):
    db_path = tmp_path / "ghost_trade.db"
    repository = GhostRepository(db_path)

    repository.migrate()

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ghost_%'"
            )
        }
    assert tables == {
        "ghost_schema_version",
            "ghost_scan_runs",
            "ghost_scan_lease",
        "ghost_instruments",
        "ghost_signals",
        "ghost_signal_components",
        "ghost_execution_attempts",
        "ghost_positions",
        "ghost_position_events",
        "ghost_closed_trades",
        "ghost_runtime_settings",
    }


def test_signal_id_is_deterministic_and_changes_with_confirmed_identity():
    first = signal().signal_id
    same = signal().signal_id
    changed = signal_id_for(
        engine_version="ghost-v1",
        venue=Venue.MT5,
        broker_symbol="EURUSD.a",
        style=Style.INTRADAY,
        decision_time=NOW,
        confirmed_times={"D1": "d1", "H4": "h4", "H1": "later"},
    )

    assert first == same
    assert len(first) == 64
    assert changed != first


def test_signal_upsert_is_idempotent_and_preserves_numeric_values(tmp_path):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    original = signal()

    repository.upsert_signal(original)
    repository.upsert_signal(replace(original, live_adjustment=0.05, display_score=0.506789123456))
    loaded = repository.get_signal(original.signal_id)

    assert loaded is not None
    assert loaded.signal_id == original.signal_id
    assert loaded.confirmed_score == original.confirmed_score
    assert loaded.entry == original.entry
    assert loaded.raw_rr == original.raw_rr
    assert loaded.live_adjustment == 0.05
    assert loaded.components == original.components
    assert repository.count_signals() == 1


def test_signal_filters_and_dismissal_are_server_side(tmp_path):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    long_signal = signal(score=0.70)
    short_instrument = GhostInstrument(
        venue=Venue.BYBIT,
        broker_symbol="BTCUSDT",
        canonical_symbol="BTC/USDT",
        asset_group=AssetGroup.CRYPTO,
        asset_subgroup="crypto_major",
        base_asset="BTC",
        quote_asset="USDT",
    )
    short_signal = replace(
        signal(score=0.40, direction=Direction.SHORT),
        signal_id="short-id",
        instrument=short_instrument,
    )
    repository.upsert_signal(long_signal)
    repository.upsert_signal(short_signal)

    rows = repository.list_signals(
        asset_group=AssetGroup.FOREX,
        direction=Direction.LONG,
        minimum_score=0.60,
    )
    dismissed = repository.dismiss_signal(long_signal.signal_id)

    assert [row.signal_id for row in rows] == [long_signal.signal_id]
    assert dismissed is True
    assert repository.get_signal(long_signal.signal_id).status is SignalStatus.DISMISSED


def test_execution_reservation_is_idempotent_and_version_checked(tmp_path):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    original = signal()
    repository.upsert_signal(original)

    first = repository.reserve_execution(
        original.signal_id,
        expected_version="ghost-v1",
        execution_id="exec-1",
        idempotency_key="signal:manual",
    )
    same = repository.reserve_execution(
        original.signal_id,
        expected_version="ghost-v1",
        execution_id="exec-2",
        idempotency_key="signal:manual",
    )

    assert first == "exec-1"
    assert same == "exec-1"
    with pytest.raises(SignalConflictError):
        repository.reserve_execution(
            original.signal_id,
            expected_version="ghost-v0",
            execution_id="exec-3",
            idempotency_key="other",
        )
