from __future__ import annotations

import inspect

import execution
import execution_lifecycle
from bybit_executor import bybit_demo_endpoint_verified
from engine_a_v3.execution import (
    attest_demo_execution,
    merge_refreshed_signal,
    verify_refreshed_signal,
)


def _signal(**overrides) -> dict:
    signal = {
        "contractVersion": "3.0.0",
        "engine": "ENGINE_A_V3",
        "signalId": "signal-1",
        "pair": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "horizon": "intraday",
        "setupId": "fx_trend_pullback",
        "decision": "TRADE",
        "qualified": True,
        "engineATradeEnabled": True,
        "direction": "LONG",
        "decisionTime": "2026-06-13T10:00:00+00:00",
        "lastConfirmedCandleTs": "2026-06-13T10:00:00+00:00",
        "validUntil": "2035-01-01T00:00:00+00:00",
        "entryZone": [1.099, 1.101],
        "price": 1.10,
        "sl": 1.09,
        "tp1": 1.11,
        "tp2": 1.12,
        "rr1": 1.0,
        "rr2": 2.0,
        "dataFreshness": {"allowed": True},
        "executionScope": "DEMO_ONLY",
        "validationStatus": "UNVALIDATED",
    }
    signal.update(overrides)
    return signal


def test_demo_attestation_requires_explicit_mode_and_verified_venue():
    assert attest_demo_execution(
        executor_mode="demo",
        venue="mt5",
        mt5_trade_mode=0,
    ).allowed
    assert attest_demo_execution(
        executor_mode="demo",
        venue="bybit",
        bybit_demo=True,
    ).allowed

    assert not attest_demo_execution(
        executor_mode="live",
        venue="mt5",
        mt5_trade_mode=0,
    ).allowed
    assert not attest_demo_execution(
        executor_mode="demo",
        venue="mt5",
        mt5_trade_mode=None,
    ).allowed
    assert not attest_demo_execution(
        executor_mode="demo",
        venue="bybit",
        bybit_demo=None,
    ).allowed


def test_refresh_requires_same_setup_direction_horizon_and_trade_eligibility():
    original = _signal()

    assert verify_refreshed_signal(original, _signal(signalId="signal-2")) == (True, None)
    assert verify_refreshed_signal(
        original,
        _signal(setupId="fx_session_breakout_retest"),
    )[0] is False
    assert verify_refreshed_signal(original, _signal(direction="SHORT"))[0] is False
    assert verify_refreshed_signal(original, _signal(horizon="swing"))[0] is False
    assert verify_refreshed_signal(
        original,
        _signal(decision="WATCH", qualified=False),
    )[0] is False
    assert verify_refreshed_signal(
        original,
        _signal(executionScope="LIVE_ALLOWED"),
    )[0] is False


def test_refreshed_signal_replaces_authoritative_contract_and_levels():
    original = _signal(price=1.10, sl=1.09, tp1=1.11, tp2=1.12)
    refreshed = _signal(
        signalId="signal-2",
        decisionTime="2026-06-13T11:00:00+00:00",
        lastConfirmedCandleTs="2026-06-13T11:00:00+00:00",
        entryZone=[1.109, 1.111],
        price=1.11,
        sl=1.10,
        tp1=1.12,
        tp2=1.13,
    )

    merged = merge_refreshed_signal(original, refreshed)

    assert merged["signalId"] == "signal-2"
    assert merged["price"] == 1.11
    assert merged["sl"] == 1.10
    assert merged["tp2"] == 1.13
    assert merged["entryZone"] == [1.109, 1.111]


def test_manual_routes_refresh_and_attest_before_risk_without_force_or_overrides():
    for route in (execution.api_quick_execute, execution.api_execute):
        source = inspect.getsource(route)
        assert "_refresh_engine_a_v3_execution_context" in source
        assert "_engine_a_v3_demo_attestation_error" in source
        assert source.index("_refresh_engine_a_v3_execution_context") < source.index(
            "risk_check("
        )
        assert source.index("_engine_a_v3_demo_attestation_error") < source.index(
            "risk_check("
        )
        assert "ENGINE_A_V3_LEVEL_OVERRIDE_FORBIDDEN" in source

    regular_source = inspect.getsource(execution.api_execute)
    assert "ENGINE_A_V3_FORCE_FORBIDDEN" in regular_source
    assert '"min_lot"' in regular_source


def test_ai_review_is_advisory_for_v3_but_legacy_veto_is_preserved():
    conflict = {"vision_output": {"confirms_direction": False}}

    assert execution_lifecycle._vision_blocks_execution(_signal(**conflict)) is False
    assert execution_lifecycle._vision_blocks_execution(
        {"engine": "ENGINE_A", **conflict}
    ) is True


def test_bybit_demo_attestation_requires_demo_endpoint_not_only_flag():
    class _Exchange:
        urls = {"api": {"public": "https://api-demo.{hostname}"}}
        options = {"enableDemoTrading": True}
        hostname = "bybit.com"

    assert bybit_demo_endpoint_verified(_Exchange(), demo_enabled=True, testnet_enabled=False)
    assert not bybit_demo_endpoint_verified(
        _Exchange(), demo_enabled=True, testnet_enabled=True
    )
    assert not bybit_demo_endpoint_verified(
        type("_Live", (), {"urls": {"api": "https://api.bybit.com"}})(),
        demo_enabled=True,
        testnet_enabled=False,
    )
