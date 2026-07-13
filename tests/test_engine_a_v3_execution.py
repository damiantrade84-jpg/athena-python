from __future__ import annotations

import inspect

import pytest

import execution
import execution_lifecycle
import mt5_executor
import bybit_executor
from bybit_executor import bybit_demo_endpoint_verified
from engine_a_v3.execution import (
    attest_demo_execution,
    exact_split_sizes,
    merge_refreshed_signal,
    verify_refreshed_signal,
)


def test_exact_split_sizes_never_rounds_to_an_unequal_broker_allocation():
    assert exact_split_sizes(0.04, step=0.01, minimum=0.01) == (0.02, 0.02)
    assert exact_split_sizes(0.03, step=0.01, minimum=0.01) is None
    assert exact_split_sizes(0.01, step=0.01, minimum=0.01) is None


def _signal(**overrides) -> dict:
    signal = {
        "contractVersion": "3.1.0",
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
        "signalTier": "trade",
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
        "exitPolicy": "SINGLE_TP1",
        "scoringProfile": {"profileId": "fixture-profile", "profileSha256": "a" * 64},
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
    assert verify_refreshed_signal(original, _signal(exitPolicy="SPLIT_50_50"))[0] is False
    assert verify_refreshed_signal(
        original,
        _signal(scoringProfile={"profileId": "changed", "profileSha256": "b" * 64}),
    )[0] is False


@pytest.mark.parametrize("original_tier", [None, "watchlist", "skip"])
def test_refresh_cannot_promote_missing_or_nontrade_original_scanner_tier(original_tier):
    original = _signal(signalTier=original_tier)
    refreshed = _signal(signalId="signal-2", signalTier="trade")

    assert verify_refreshed_signal(original, refreshed) == (
        False,
        "ENGINE_A_V3_REFRESH_ORIGINAL_SCAN_TIER_NOT_TRADE",
    )


def _forming_m30_series(*, forming: bool = True, bars: int = 60) -> list[dict]:
    """Build an M30 series whose last bar is (or is not) the current bucket."""
    import time as _t
    from datetime import datetime, timezone

    from athena_app.services.market_state import get_bucket_start_epoch

    current = get_bucket_start_epoch("M30", _t.time(), offset_hours=0.0)
    # When forming is False, shift every bar back one bucket so the newest bar is a
    # just-closed (confirmed) bar and split_market_state reports no forming bar.
    newest = current if forming else current - 1800
    series: list[dict] = []
    for i in range(bars, -1, -1):
        ts = newest - i * 1800
        series.append(
            {
                "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "open": 1.10,
                "high": 1.11,
                "low": 1.09,
                "close": 1.105,
                "vol": 100.0,
            }
        )
    return series


class _FakeV3Runtime:
    """Minimal analyze_pair/fetch_candles runtime for the V3 execute-time refresh."""

    def __init__(self, m30_batches: list[list[dict]], refreshed: dict | None = None):
        self.ALL_PAIRS = [
            {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "source": "mt5"}
        ]
        self.CONFIG = {"MT5_H4_FETCH_RETRY_SLEEP_MS": 0}
        self._m30_batches = list(m30_batches)
        self._refreshed = refreshed if refreshed is not None else _signal(signalId="signal-2")
        self.fetch_calls: list[str] = []
        self.captured: dict = {}

    def fetch_candles(self, pair, tf, limit):
        if str(tf).upper() == "M30":
            self.fetch_calls.append("M30")
            idx = min(len(self.fetch_calls), len(self._m30_batches)) - 1
            return self._m30_batches[idx] if self._m30_batches else []
        return []

    def analyze_pair(self, pair, bias, style=None, preloaded_market_state=None):
        self.captured["style"] = style
        self.captured["preloaded_market_state"] = preloaded_market_state
        return self._refreshed


def test_v3_refresh_preloads_live_entry_forming_bar():
    runtime = _FakeV3Runtime([_forming_m30_series(forming=True)])
    sig = _signal()

    err = execution._refresh_engine_a_v3_execution_context(sig, runtime)

    assert err is None
    pms = runtime.captured["preloaded_market_state"]
    assert isinstance(pms, dict) and "M30" in pms
    assert pms["M30"].get("forming") is not None
    assert pms["M30"].get("is_live") is True
    # A single fetch already had a forming bar — no retry needed.
    assert len(runtime.fetch_calls) == 1


def test_v3_refresh_retries_to_recover_missing_forming_bar():
    # First two fetches lack the forming bar; the third recovers it.
    runtime = _FakeV3Runtime(
        [
            _forming_m30_series(forming=False),
            _forming_m30_series(forming=False),
            _forming_m30_series(forming=True),
        ]
    )
    sig = _signal()

    err = execution._refresh_engine_a_v3_execution_context(sig, runtime)

    assert err is None
    assert len(runtime.fetch_calls) == 3
    pms = runtime.captured["preloaded_market_state"]
    assert pms["M30"].get("forming") is not None


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
    assert merged["exitPolicy"] == "SINGLE_TP1"
    assert merged["timestamp"] != refreshed["decisionTime"]
    assert merged["scoredAt"] == merged["timestamp"]


def test_v3_contract_timestamp_is_scan_time_not_candle_time():
    from datetime import datetime, timedelta, timezone

    from engine_a_v3.evaluator import evaluate_engine_a_v3
    from tests.test_engine_a_v3 import _trend_pullback_candles

    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
    candles = _trend_pullback_candles()
    before = datetime.now(timezone.utc)
    payload = evaluate_engine_a_v3(pair, candles, horizon="intraday").to_dict()
    after = datetime.now(timezone.utc)

    ts = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
    assert before <= ts <= after + timedelta(seconds=2)
    assert payload["timestamp"] != payload["decisionTime"]


def test_guardian_pre_trade_accepts_v3_with_null_factor_diagnostics():
    from guardian import pre_trade_check

    signal = _signal(
        pair="USD/SGD",
        type="forex",
        factorDiagnostics=None,
    )
    ok, reason = pre_trade_check(
        signal,
        [],
        {"balance": 10_000.0, "equity": 10_000.0},
    )
    assert ok is True
    assert reason == "OK"


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
    # Manual volume mode is resolved by risk_engine (EXECUTION_VOLUME config),
    # not hard-forced to min_lot for v3.
    assert '_volume_mode = "min_lot"' not in regular_source
    quick_source = inspect.getsource(execution.api_quick_execute)
    assert '_volume_mode = "min_lot"' not in quick_source


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


def test_v3_broker_exit_policy_is_strict_and_other_engines_are_unchanged():
    assert mt5_executor._engine_a_v3_exit_policy(_signal()) == "SINGLE_TP1"
    assert bybit_executor._engine_a_v3_exit_policy(_signal(exitPolicy="SPLIT_50_50")) == "SPLIT_50_50"
    assert mt5_executor._engine_a_v3_exit_policy({"engine": "ENGINE_B", "exitPolicy": "SPLIT_50_50"}) is None
    assert bybit_executor._engine_a_v3_exit_policy({"engine": "ENGINE_D"}) is None

    for module in (mt5_executor, bybit_executor):
        try:
            module._engine_a_v3_exit_policy(_signal(exitPolicy="UNKNOWN"))
        except ValueError as exc:
            assert str(exc) == "ENGINE_A_V3_EXIT_POLICY_INVALID"
        else:
            raise AssertionError("invalid V3 exit policy must fail closed")
