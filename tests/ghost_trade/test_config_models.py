from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ghost_trade.config import GhostConfig, GhostConfigError, load_ghost_config
from ghost_trade.models import (
    AssetGroup,
    GhostInstrument,
    GhostMode,
    Venue,
)


def test_committed_defaults_are_shadow_only_and_live_is_impossible():
    config = load_ghost_config({}, environ={})

    assert config.enabled is True
    assert config.mode is GhostMode.SHADOW
    assert config.live_trading_allowed is False
    assert config.demo_auto_enabled is False
    assert config.effective_demo_auto_enabled is False
    assert config.risk_per_trade == pytest.approx(0.0025)
    assert config.max_risk_per_trade == pytest.approx(0.005)
    assert config.max_total_risk == pytest.approx(0.02)
    assert config.group_profiles["forex"]["maximum_spread_pct"] == pytest.approx(0.0005)


def test_only_named_environment_overrides_are_applied():
    config = load_ghost_config(
        {"ghost_trade": {"mode": "SHADOW", "live_trading_allowed": True}},
        environ={
            "GHOST_TRADE_ENABLED": "false",
            "GHOST_TRADE_MODE": "DEMO_MANUAL",
            "GHOST_TRADE_DEMO_AUTO_ENABLED": "true",
            "GHOST_TRADE_MAX_RISK_PER_TRADE": "0.004",
            "GHOST_TRADE_MAX_TOTAL_RISK": "0.015",
            "GHOST_TRADE_SCAN_INTERVAL_SECONDS": "900",
            "GHOST_TRADE_RISK_PER_TRADE": "0.99",
        },
    )

    assert config.enabled is False
    assert config.mode is GhostMode.DEMO_MANUAL
    assert config.demo_auto_enabled is True
    assert config.effective_demo_auto_enabled is False
    assert config.max_risk_per_trade == pytest.approx(0.004)
    assert config.max_total_risk == pytest.approx(0.015)
    assert config.scan_interval_seconds == 900
    assert config.risk_per_trade == pytest.approx(0.0025)
    assert config.live_trading_allowed is False


@pytest.mark.parametrize(
    "mapping,environ",
    [
        ({"ghost_trade": {"mode": "LIVE"}}, {}),
        ({"ghost_trade": {"risk_per_trade": 0.006}}, {}),
        ({"ghost_trade": {"max_risk_per_trade": 0.006}}, {}),
        ({"ghost_trade": {"max_total_risk": 0.03}}, {}),
        ({"ghost_trade": {"risk_per_trade": 0.005, "max_risk_per_trade": 0.004}}, {}),
        ({}, {"GHOST_TRADE_ENABLED": "maybe"}),
        ({}, {"GHOST_TRADE_MAX_TOTAL_RISK": "not-a-number"}),
    ],
)
def test_invalid_or_risk_widening_configuration_fails_closed(mapping, environ):
    with pytest.raises(GhostConfigError):
        load_ghost_config(mapping, environ=environ)


def test_config_and_domain_models_are_immutable():
    config = GhostConfig.from_mapping({}, environ={})
    instrument = GhostInstrument(
        venue=Venue.MT5,
        broker_symbol="EURUSD.a",
        canonical_symbol="EUR/USD",
        asset_group=AssetGroup.FOREX,
        asset_subgroup="forex_major",
        base_asset="EUR",
        quote_asset="USD",
    )

    with pytest.raises(FrozenInstanceError):
        config.mode = GhostMode.DEMO_AUTO
    with pytest.raises(FrozenInstanceError):
        instrument.broker_symbol = "EURUSD"


def test_instrument_json_preserves_broker_and_canonical_identity():
    instrument = GhostInstrument(
        venue=Venue.BYBIT,
        broker_symbol="BTCUSDT",
        canonical_symbol="BTC/USDT",
        asset_group=AssetGroup.CRYPTO,
        asset_subgroup="crypto_major",
        base_asset="BTC",
        quote_asset="USDT",
        metadata={"category": "linear"},
        skip_reasons=("insufficient_history",),
    )

    assert instrument.to_dict() == {
        "source": "BYBIT",
        "brokerSymbol": "BTCUSDT",
        "canonicalSymbol": "BTC/USDT",
        "assetGroup": "crypto",
        "assetSubgroup": "crypto_major",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "metadata": {"category": "linear"},
        "tradeEnabled": True,
        "eligibleForScoring": False,
        "skipReasons": ["insufficient_history"],
    }


@pytest.mark.parametrize(
    "unsafe",
    [
        {"symbol_overrides": {"EURUSD.a": "forex"}},
        {"group_profiles": {"forex": "enabled"}},
    ],
)
def test_nested_configuration_requires_mapping_values(unsafe):
    with pytest.raises(GhostConfigError):
        load_ghost_config({"ghost_trade": unsafe}, environ={})


def test_runtime_loader_uses_process_environment_when_not_explicitly_supplied(monkeypatch):
    monkeypatch.setenv("GHOST_TRADE_MODE", "DEMO_MANUAL")
    monkeypatch.setenv("GHOST_TRADE_MAX_TOTAL_RISK", "0.015")

    config = load_ghost_config({})

    assert config.mode is GhostMode.DEMO_MANUAL
    assert config.max_total_risk == pytest.approx(0.015)
