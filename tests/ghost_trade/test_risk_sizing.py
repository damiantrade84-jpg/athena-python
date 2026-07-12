from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ghost_trade.config import GhostConfig
from ghost_trade.models import (
    AssetGroup,
    Direction,
    GhostInstrument,
    GhostPosition,
    GhostSignal,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)
from ghost_trade.position_sizing import size_bybit, size_mt5
from ghost_trade.risk import GhostRiskService


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def make_signal(signal_id="signal-1", canonical="EUR/USD", direction=Direction.LONG):
    instrument = GhostInstrument(
        venue=Venue.MT5,
        broker_symbol="EURUSD.a",
        canonical_symbol=canonical,
        asset_group=AssetGroup.FOREX,
        asset_subgroup="forex_major",
        base_asset="EUR",
        quote_asset="USD",
    )
    return GhostSignal(
        signal_id=signal_id, signal_version="ghost-v1", scan_id="scan-1",
        instrument=instrument, style=Style.INTRADAY, direction=direction,
        decision_time=NOW, confirmed_score=0.7, live_adjustment=0,
        display_score=0.7, direction_confidence=0.6, entry_quality=0.7,
        entry=1.1, stop=1.09, target=1.12, raw_rr=2,
        volatility_regime=VolatilityRegime.NORMAL, can_execute=True,
        status=SignalStatus.ELIGIBLE,
    )


def make_position(signal_id="open-signal", canonical="GBP/USD", direction=Direction.LONG, risk_fraction=0.0025):
    return GhostPosition(
        position_id=f"position-{signal_id}", signal_id=signal_id, venue=Venue.MT5,
        canonical_symbol=canonical, asset_group=AssetGroup.FOREX, mode="DEMO",
        status="OPEN", direction=direction, entry=1.2, stop=1.19, target=1.22,
        initial_risk=0.01, quantity=0.1, opened_at=NOW, closed_at=None,
        volatility_regime=VolatilityRegime.NORMAL, entry_quality=0.7,
        signal_score=0.7, payload={"risk_fraction": risk_fraction},
    )


def test_mt5_sizing_uses_tick_value_and_rounds_down_to_volume_step():
    result = size_mt5(
        equity=10_000,
        risk_fraction=0.0025,
        entry=1.1000,
        stop=1.0950,
        symbol_info=SimpleNamespace(
            trade_tick_size=0.0001, trade_tick_value=1.0,
            trade_contract_size=100_000, volume_min=0.01,
            volume_max=100.0, volume_step=0.01,
        ),
    )

    assert result.approved is True
    assert result.risk_cash == pytest.approx(25)
    assert result.loss_per_unit == pytest.approx(50)
    assert result.quantity == pytest.approx(0.50)
    assert result.estimated_risk_cash == pytest.approx(25)


def test_mt5_sizing_rejects_missing_value_metadata_and_unsafe_minimum_volume():
    missing = size_mt5(
        equity=10_000, risk_fraction=0.0025, entry=1.1, stop=1.09,
        symbol_info=SimpleNamespace(volume_min=0.01, volume_max=1, volume_step=0.01),
    )
    too_small = size_mt5(
        equity=100, risk_fraction=0.0025, entry=1.1, stop=1.0,
        symbol_info=SimpleNamespace(
            trade_tick_size=0.0001, trade_tick_value=1,
            volume_min=0.01, volume_max=1, volume_step=0.01,
        ),
    )

    assert missing.reason == "tick_value_unavailable"
    assert too_small.reason == "quantity_below_minimum"


def test_bybit_sizing_rounds_down_and_enforces_minimum_notional():
    approved = size_bybit(
        equity=10_000, risk_fraction=0.0025, entry=100, stop=95,
        quantity_step=0.01, minimum_quantity=0.01, minimum_notional=5,
    )
    rejected = size_bybit(
        equity=100, risk_fraction=0.0025, entry=1, stop=0.5,
        quantity_step=0.01, minimum_quantity=0.01, minimum_notional=10,
    )

    assert approved.quantity == pytest.approx(5.0)
    assert approved.estimated_risk_cash == pytest.approx(25)
    assert rejected.reason == "minimum_notional_not_met"


def test_position_size_never_increases_for_expanding_or_extreme_regimes():
    normal = size_bybit(
        equity=10_000, risk_fraction=0.0025, entry=100, stop=95,
        quantity_step=0.01, minimum_quantity=0.01, minimum_notional=5,
        volatility_regime=VolatilityRegime.NORMAL,
    )
    expanding = size_bybit(
        equity=10_000, risk_fraction=0.0025, entry=100, stop=95,
        quantity_step=0.01, minimum_quantity=0.01, minimum_notional=5,
        volatility_regime=VolatilityRegime.EXPANDING,
    )
    extreme = size_bybit(
        equity=10_000, risk_fraction=0.0025, entry=100, stop=95,
        quantity_step=0.01, minimum_quantity=0.01, minimum_notional=5,
        volatility_regime=VolatilityRegime.EXTREME,
    )

    assert normal.quantity == expanding.quantity == extreme.quantity


def test_ghost_risk_blocks_duplicates_repeated_signal_and_portfolio_heat():
    risk = GhostRiskService(GhostConfig(), clock=lambda: NOW)
    signal = make_signal()

    duplicate = risk.evaluate(signal, [make_position(canonical="EUR/USD")])
    repeated = risk.evaluate(signal, [make_position(signal_id="signal-1")])
    heat = risk.evaluate(
        signal,
        [make_position(signal_id=f"s{i}", risk_fraction=0.005) for i in range(4)],
    )

    assert "duplicate_symbol_direction" in duplicate.reasons
    assert "signal_already_open" in repeated.reasons
    assert "max_total_risk_exceeded" in heat.reasons


def test_cooldown_and_max_positions_fail_closed_but_other_engine_positions_do_not_count():
    config = GhostConfig(max_open_positions=2, signal_cooldown_seconds=3600)
    risk = GhostRiskService(config, clock=lambda: NOW)
    signal = make_signal()
    recent = [{"canonical_symbol": "EUR/USD", "closed_at": NOW - timedelta(minutes=30)}]

    cooldown = risk.evaluate(signal, [], recent_closed=recent)
    max_positions = risk.evaluate(signal, [make_position("a"), make_position("b")])
    other_engines_ignored = risk.evaluate(
        signal,
        [],
        other_engine_positions=[{"symbol": "EUR/USD", "direction": "LONG"}],
    )

    assert cooldown.reasons == ("signal_cooldown_active",)
    assert "max_open_positions_reached" in max_positions.reasons
    assert other_engines_ignored.approved is True
    assert other_engines_ignored.warnings == ("other_engine_symbol_conflict",)
