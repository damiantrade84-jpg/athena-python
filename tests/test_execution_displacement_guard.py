"""Phase 5a — execution-time fill-model tests.

Covers:
- displacement/chase guard (ATR/spread-normalized, no fixed pip thresholds)
- actual RR at the TRUE fill price + below-min post-fill violation path
  (both executors, mocked internals — no real broker)
- trigger lifecycle (EXPIRED/INVALIDATED/expiry) in the execution refresh gates
- backward compatibility when trigger fields are absent
- execution refresh paths never act as a directional vote
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import execution
from execution import (
    _directional_rr,
    _displacement_guard_block_reason,
    _post_fill_rr_violation,
    _refresh_engine_a_v3_execution_context,
    _refresh_engine_b_execution_context,
    _trigger_lifecycle_block_reason,
)


@pytest.fixture(autouse=True)
def _stub_athena_runtime(monkeypatch):
    """Refresh helpers reach rt().CONFIG deep in the level-stamping path."""
    monkeypatch.setattr(execution, "rt", lambda: SimpleNamespace(CONFIG={}))


# ── _directional_rr ──────────────────────────────────────────────────────────


def test_directional_rr_long_and_short():
    assert _directional_rr(100.0, 99.0, 103.0, "LONG") == pytest.approx(3.0)
    assert _directional_rr(100.0, 101.0, 97.0, "SHORT") == pytest.approx(3.0)


def test_directional_rr_invalid_levels_return_none():
    # zero risk distance
    assert _directional_rr(100.0, 100.0, 103.0, "LONG") is None
    # SL on wrong side for direction
    assert _directional_rr(100.0, 101.0, 103.0, "LONG") is None
    # TP on wrong side (no remaining reward)
    assert _directional_rr(100.0, 99.0, 99.5, "LONG") is None
    assert _directional_rr(100.0, 99.0, 100.5, "SHORT") is None
    # unparseable / missing direction
    assert _directional_rr("x", 99.0, 103.0, "LONG") is None
    assert _directional_rr(100.0, 99.0, 103.0, "") is None


# ── displacement guard ───────────────────────────────────────────────────────


def _guard_sig(**overrides) -> dict:
    sig = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 100.0,
        "sl": 99.0,
        "tp1": 105.0,
        "atr": 1.0,
        "triggerLevel": 100.0,
    }
    sig.update(overrides)
    return sig


def test_displacement_guard_blocks_atr_chase():
    # 2.0 ATR from the trigger level exceeds the 1.5 ATR default cap.
    sig = _guard_sig(price=102.0, sl=99.0, tp1=106.0)
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason == "CHASE_PROTECTION_ATR_DISPLACEMENT"
    assert diag["distanceInAtr"] == pytest.approx(2.0)
    assert diag["distanceFromTriggerAtFill"] == pytest.approx(2.0)


def test_displacement_guard_atr_cap_is_configurable():
    sig = _guard_sig(price=102.0, sl=99.0, tp1=106.0)
    cfg = {"DISPLACEMENT_GUARD": {"MAX_DISPLACEMENT_ATR": 2.5}}
    reason, diag = _displacement_guard_block_reason(sig, cfg)
    assert reason is None
    assert diag["maxDisplacementAtr"] == 2.5


def test_displacement_guard_blocks_excursion_consumed():
    # 72% of the trigger->TP1 path already consumed (> 60% cap), small ATR move.
    sig = _guard_sig(price=103.6, atr=10.0, tp1=105.0)
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason == "CHASE_PROTECTION_EXCURSION_CONSUMED"
    assert diag["expectedExcursionFraction"] == pytest.approx(0.72, abs=1e-2)


def test_displacement_guard_blocks_rr_below_min_at_entry():
    # RR recomputed at current price 104: (105-104)/(104-103) = 1.0 < min 2.0.
    sig = _guard_sig(
        price=104.0, sl=103.0, tp1=105.0, atr=10.0, triggerLevel=103.5, min_rr=2.0
    )
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason == "CHASE_PROTECTION_RR_BELOW_MIN"
    assert diag["rrAtEntry"] == pytest.approx(1.0)
    assert diag["minRrAtEntry"] == 2.0


def test_displacement_guard_records_spread_normalized_distance():
    sig = _guard_sig(price=101.5, quoteSpread=0.5)
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason is None
    # 1.5 price units of chase / 0.5 spread = 3 spreads
    assert diag["distanceInSpreads"] == pytest.approx(3.0)
    assert diag["distanceInAtr"] == pytest.approx(1.5)


def test_displacement_guard_skips_advisory_subchecks_when_inputs_missing():
    # Missing ATR and spread: sub-checks skipped with reasons, not fail-closed.
    sig = _guard_sig(price=101.0)
    del sig["atr"]
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason is None
    assert "atrDisplacement" in diag["skipped"]
    assert "spreadDisplacement" in diag["skipped"]


def test_displacement_guard_fails_closed_on_missing_price():
    reason, _ = _displacement_guard_block_reason(_guard_sig(price=0), {})
    assert reason == "CHASE_PROTECTION_PRICE_MISSING"
    reason, _ = _displacement_guard_block_reason(
        {"direction": "LONG", "sl": 1.0, "tp1": 2.0}, {}
    )
    assert reason == "CHASE_PROTECTION_PRICE_MISSING"


def test_displacement_guard_allows_pullback_entries():
    # Price pulled back against the trade direction: not a chase.
    sig = _guard_sig(price=99.0)
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason is None
    assert diag["distanceInAtr"] == 0.0


def test_displacement_guard_disabled_is_noop():
    sig = _guard_sig(price=110.0)
    cfg = {"DISPLACEMENT_GUARD": {"ENABLED": False}}
    reason, diag = _displacement_guard_block_reason(sig, cfg)
    assert reason is None
    assert diag["enabled"] is False


def test_displacement_guard_uses_signal_price_ref_when_no_trigger_level():
    sig = _guard_sig(price=102.0, tp1=106.0)
    del sig["triggerLevel"]
    sig["signalPriceRef"] = 100.0
    reason, diag = _displacement_guard_block_reason(sig, {})
    assert reason == "CHASE_PROTECTION_ATR_DISPLACEMENT"
    assert diag["distanceFromTriggerAtFill"] == pytest.approx(2.0)


# ── post-fill RR violation ───────────────────────────────────────────────────


def test_post_fill_rr_violation_invalid_risk_distance_fails_closed():
    reason = _post_fill_rr_violation({"direction": "LONG"}, None, {})
    assert reason.startswith("ACTUAL_RR_INVALID_AT_FILL")


def test_post_fill_rr_violation_below_signal_min_rr():
    reason = _post_fill_rr_violation({"min_rr": 1.5}, 0.8, {})
    assert reason.startswith("ACTUAL_RR_BELOW_MIN_AT_FILL")
    reason = _post_fill_rr_violation({"engine_b": {"min_rr": 1.5}}, 0.8, {})
    assert reason.startswith("ACTUAL_RR_BELOW_MIN_AT_FILL")


def test_post_fill_rr_violation_record_only_when_no_floor():
    assert _post_fill_rr_violation({}, 0.5, {}) is None
    assert _post_fill_rr_violation({"min_rr": 1.0}, 1.5, {}) is None


def test_post_fill_rr_violation_uses_configured_floor():
    cfg = {"DISPLACEMENT_GUARD": {"MIN_RR_AT_FILL": 1.2}}
    assert _post_fill_rr_violation({}, 1.0, cfg).startswith(
        "ACTUAL_RR_BELOW_MIN_AT_FILL"
    )
    assert _post_fill_rr_violation({}, 1.5, cfg) is None


# ── trigger lifecycle in the execution refresh gates ─────────────────────────


def _engine_b_refresh_runtime(refreshed: dict):
    def refresh(seed, **kwargs):
        return refreshed, {"display": "TEST"}, None

    return SimpleNamespace(compute_naked_analysis=refresh, CONFIG={})


_OK_REFRESHED = {
    "passed": True,
    "direction": "LONG",
    "current_price": 100.0,
    "execution_sl": 98.0,
    "execution_tp": 104.0,
}


def test_trigger_lifecycle_blocks_expired_and_invalidated():
    sig = {"pair": "TEST", "direction": "LONG", "is_naked": True, "triggerState": "EXPIRED"}
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(_OK_REFRESHED), "intraday"
    )
    assert refreshed is None
    assert err == "TRIGGER_LIFECYCLE_EXPIRED"

    sig = {"pair": "TEST", "direction": "LONG", "is_naked": True, "triggerState": "INVALIDATED"}
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(_OK_REFRESHED), "intraday"
    )
    assert refreshed is None
    assert err == "TRIGGER_LIFECYCLE_INVALIDATED"


def test_trigger_lifecycle_past_expiry_treated_as_expired():
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    sig = {
        "pair": "TEST",
        "direction": "LONG",
        "is_naked": True,
        "triggerState": "EXECUTABLE",
        "triggerExpiresAt": past,
    }
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(_OK_REFRESHED), "intraday"
    )
    assert refreshed is None
    assert err == "TRIGGER_LIFECYCLE_EXPIRED"


def test_trigger_lifecycle_absent_fields_backward_compatible():
    # No triggerState/triggerExpiresAt: current behavior unchanged (refresh runs).
    sig = {"pair": "TEST", "direction": "LONG", "is_naked": True}
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(dict(_OK_REFRESHED)), "intraday"
    )
    assert err is None
    assert refreshed is not None


def test_trigger_lifecycle_future_expiry_does_not_block():
    future = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    sig = {
        "pair": "TEST",
        "direction": "LONG",
        "is_naked": True,
        "triggerState": "EXECUTABLE",
        "triggerExpiresAt": future,
    }
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(dict(_OK_REFRESHED)), "intraday"
    )
    assert err is None
    assert refreshed is not None


def test_trigger_lifecycle_unparseable_expiry_fails_closed():
    assert _trigger_lifecycle_block_reason({"triggerExpiresAt": "not-a-date"}) == (
        "TRIGGER_LIFECYCLE_EXPIRED"
    )


def _v3_signal(**overrides) -> dict:
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
        "validUntil": "2035-01-01T00:00:00+00:00",
        "price": 1.10,
        "sl": 1.09,
        "tp1": 1.11,
        "dataFreshness": {"allowed": True},
        "executionScope": "DEMO_ONLY",
        "validationStatus": "UNVALIDATED",
        "exitPolicy": "SINGLE_TP1",
        "scoringProfile": {"profileId": "fixture-profile", "profileSha256": "a" * 64},
    }
    signal.update(overrides)
    return signal


class _V3Runtime:
    def __init__(self, refreshed):
        self.ALL_PAIRS = [
            {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "source": "mt5"}
        ]
        self.CONFIG = {"MT5_H4_FETCH_RETRY_SLEEP_MS": 0}
        self._refreshed = refreshed
        self.analyze_calls = 0

    def fetch_candles(self, pair, tf, limit):
        return []

    def analyze_pair(self, pair, bias, **kwargs):
        self.analyze_calls += 1
        return self._refreshed


def test_trigger_lifecycle_blocks_engine_a_v3_refresh_before_analysis():
    sig = _v3_signal(triggerState="EXPIRED")
    runtime = _V3Runtime(_v3_signal(signalId="signal-2"))
    err = _refresh_engine_a_v3_execution_context(sig, runtime)
    assert err == "TRIGGER_LIFECYCLE_EXPIRED"
    assert runtime.analyze_calls == 0


def test_trigger_lifecycle_non_v3_signal_unaffected():
    assert _refresh_engine_a_v3_execution_context(
        {"pair": "X", "triggerState": "EXPIRED"}, _V3Runtime(None)
    ) is None


# ── execution refresh is not a directional vote ──────────────────────────────

_CONVICTION_FIELDS = (
    "combinedConviction",
    "executionConviction",
    "conviction",
    "convictionScore",
    "gate_pct",
)


def test_engine_b_refresh_blocks_direction_flip_without_mutating_signal():
    flipped = dict(_OK_REFRESHED, direction="SHORT")
    sig = {"pair": "TEST", "direction": "LONG", "is_naked": True}
    before = dict(sig)
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(flipped), "intraday"
    )
    assert refreshed is None
    assert err.startswith("ENGINE_B_SIGNAL_FLIPPED")
    assert sig == before  # no mutation on the blocked path


def test_engine_b_refresh_never_mutates_direction_or_conviction_fields():
    refreshed_payload = dict(
        _OK_REFRESHED,
        score=9.9,
        max_possible=10.0,
        combinedConviction=0.99,
        executionConviction=0.99,
    )
    sig = {
        "pair": "TEST",
        "direction": "LONG",
        "is_naked": True,
        "combinedConviction": 0.42,
    }
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(refreshed_payload), "intraday"
    )
    assert err is None
    assert refreshed is not None
    # Direction is the scan's decision — execution refresh must not change it.
    assert sig["direction"] == "LONG"
    # No conviction/blend field may be written by the execution refresh path.
    assert sig["combinedConviction"] == 0.42
    for field in _CONVICTION_FIELDS:
        if field == "combinedConviction":
            continue
        assert field not in sig


def test_engine_a_v3_refresh_direction_change_blocks_and_leaves_signal_untouched():
    sig = _v3_signal()
    before = dict(sig)
    refreshed = _v3_signal(signalId="signal-2", direction="SHORT")
    err = _refresh_engine_a_v3_execution_context(sig, _V3Runtime(refreshed))
    assert err == "ENGINE_A_V3_REFRESH_DIRECTION_CHANGED"
    assert sig == before


# ── auto-trader pre-risk parity ──────────────────────────────────────────────


def _auto_trader(monkeypatch):
    from auto_trader import AutoTrader

    trader = AutoTrader()
    monkeypatch.setattr(
        "bybit_executor.bybit_get_account",
        lambda: {"balance": 10000, "equity": 10000},
    )
    monkeypatch.setattr(
        "bybit_executor.bybit_get_positions",
        lambda: {"positions": [], "error": None},
    )
    monkeypatch.setattr(
        "bybit_executor.bybit_get_symbol_info",
        lambda pair: {
            "trade_contract_size": 1,
            "trade_tick_size": 0.01,
            "trade_tick_value": 0.01,
            "volume_min": 0.001,
            "volume_max": 100,
            "volume_step": 0.001,
        },
    )
    monkeypatch.setattr(
        "timeframe_policy.timeframe_policy_execution_block_reason",
        lambda *a, **kw: None,
    )
    return trader


def _auto_signal() -> dict:
    return {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "confluenceScore": 2.7,
        "maxScore": 3.0,
    }


def test_auto_trader_blocks_on_pre_risk_broker_price_error(monkeypatch):
    trader = _auto_trader(monkeypatch)
    writes = []
    monkeypatch.setattr(trader, "_write_error", lambda sig, tag: writes.append(tag))
    monkeypatch.setattr(
        "execution._engine_b_pre_risk_broker_price",
        lambda sig, venue, cfg: ("ENGINE_B_BROKER_DRIFT_TOO_LARGE", {"driftPct": 0.05}),
    )
    monkeypatch.setattr(
        "risk_engine.risk_check",
        lambda *a, **kw: pytest.fail("risk_check must not run after a pre-risk block"),
    )
    sig = _auto_signal()
    assert trader._execute_signal(sig, {}) is False
    assert writes == ["ENGINE_B_BROKER_DRIFT_TOO_LARGE"]
    assert sig["brokerPrecheck"]["stage"] == "pre_risk_broker_price"


def test_auto_trader_blocks_on_rr_reconcile_error(monkeypatch):
    trader = _auto_trader(monkeypatch)
    writes = []
    monkeypatch.setattr(trader, "_write_error", lambda sig, tag: writes.append(tag))
    monkeypatch.setattr(
        "execution._engine_b_pre_risk_broker_price",
        lambda sig, venue, cfg: (None, {"brokerPrice": 50000.0}),
    )
    monkeypatch.setattr(
        "execution._reconcile_engine_b_rr_after_broker_entry",
        lambda sig, cfg: "ENGINE_B_RR_BELOW_MINIMUM_AFTER_REBASE",
    )
    monkeypatch.setattr(
        "risk_engine.risk_check",
        lambda *a, **kw: pytest.fail("risk_check must not run after an RR reconcile block"),
    )
    assert trader._execute_signal(_auto_signal(), {}) is False
    assert writes == ["ENGINE_B_RR_BELOW_MINIMUM_AFTER_REBASE"]


def test_auto_trader_blocks_on_displacement_guard(monkeypatch):
    trader = _auto_trader(monkeypatch)
    writes = []
    monkeypatch.setattr(trader, "_write_error", lambda sig, tag: writes.append(tag))
    monkeypatch.setattr(
        "execution._engine_b_pre_risk_broker_price",
        lambda sig, venue, cfg: (None, {"brokerPrice": 50000.0}),
    )
    monkeypatch.setattr(
        "execution._reconcile_engine_b_rr_after_broker_entry",
        lambda sig, cfg: None,
    )
    monkeypatch.setattr(
        "execution._displacement_guard_block_reason",
        lambda sig, cfg: ("CHASE_PROTECTION_ATR_DISPLACEMENT", {"distanceInAtr": 2.1}),
    )
    monkeypatch.setattr(
        "risk_engine.risk_check",
        lambda *a, **kw: pytest.fail("risk_check must not run after a displacement block"),
    )
    sig = _auto_signal()
    assert trader._execute_signal(sig, {}) is False
    assert writes == ["CHASE_PROTECTION_ATR_DISPLACEMENT"]
    assert sig["displacementGuard"] == {"distanceInAtr": 2.1}


# ── actual RR at the TRUE fill price (mocked executor internals, no broker) ──


def _approval():
    from risk_engine import RiskApproval

    return RiskApproval(
        approved=True,
        volume=0.1,
        risk_amount=100.0,
        risk_pct=0.01,
        portfolio_heat=0.02,
        drawdown_pct=0.0,
        reason="OK",
    )


class _MockBybitExchange:
    def __init__(self, bid, ask, last):
        import time as _t

        self._ticker = {
            "bid": bid,
            "ask": ask,
            "last": last,
            "timestamp": int(_t.time() * 1000),
        }
        self.market_orders = []

    def fetch_ticker(self, symbol):
        return dict(self._ticker)

    def create_market_order(self, symbol, side, amount, params=None):
        self.market_orders.append(
            {"symbol": symbol, "side": side, "amount": amount, "params": params or {}}
        )
        return {"id": "order-1", "info": {"orderId": "order-1"}, "fee": {}}


def _bybit_common_patches(monkeypatch, exchange, fill_price):
    import bybit_executor
    import telegram_notify

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: exchange)
    monkeypatch.setattr(
        bybit_executor,
        "_bybit_resolve_entry_fill",
        lambda *a, **kw: ({"id": "order-1", "info": {}, "fee": {}}, "closed", 0.1),
    )
    monkeypatch.setattr(
        bybit_executor,
        "_bybit_resolve_avg_entry",
        lambda order, *, fallback_price: fill_price,
    )
    monkeypatch.setattr(
        bybit_executor, "_set_trading_stop", lambda *a, **kw: None
    )
    monkeypatch.setattr(telegram_notify, "notify_trade_opened", lambda **kw: None)


def _bybit_signal(**overrides) -> dict:
    sig = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 102.0,
        "min_rr": 1.5,
    }
    sig.update(overrides)
    return sig


def test_bybit_records_actual_rr_from_true_fill_price(monkeypatch):
    import bybit_executor

    exchange = _MockBybitExchange(bid=99.98, ask=100.02, last=100.0)
    _bybit_common_patches(monkeypatch, exchange, fill_price=100.02)

    result = bybit_executor.bybit_execute(_bybit_signal(), _approval())

    assert result["success"] is True
    # (102 - 100.02) / (100.02 - 99) = 1.9412 — computed from the fill, not the quote.
    assert result["actualRrAtFill"] == pytest.approx(1.9412, abs=1e-3)


def test_bybit_records_signal_distance_fields_on_result(monkeypatch):
    import bybit_executor

    exchange = _MockBybitExchange(bid=99.98, ask=100.02, last=100.0)
    _bybit_common_patches(monkeypatch, exchange, fill_price=100.02)

    sig = _bybit_signal(distanceFromSetup=0.4, distanceFromTrigger=0.1)
    result = bybit_executor.bybit_execute(sig, _approval())

    assert result["success"] is True
    assert result["distanceFromSetup"] == 0.4
    assert result["distanceFromTrigger"] == 0.1


def test_bybit_below_min_rr_at_fill_triggers_emergency_close(monkeypatch):
    import bybit_executor

    exchange = _MockBybitExchange(bid=99.98, ask=100.02, last=100.0)
    # Fill slips to 100.50: RR = (102-100.5)/(100.5-99) = 1.0 < min_rr 1.5.
    _bybit_common_patches(monkeypatch, exchange, fill_price=100.50)

    result = bybit_executor.bybit_execute(_bybit_signal(), _approval())

    assert result["success"] is False
    assert result["error"].startswith("ACTUAL_RR_BELOW_MIN_AT_FILL")
    assert result["rolledBack"] is True
    reduce_only = [o for o in exchange.market_orders if o["params"].get("reduceOnly")]
    assert len(reduce_only) == 1  # emergency close sent


def test_bybit_invalid_rr_at_fill_fails_closed(monkeypatch):
    import bybit_executor

    exchange = _MockBybitExchange(bid=99.98, ask=100.02, last=100.0)
    # Fill beyond TP1 leaves no reward distance: invalid RR -> emergency close.
    _bybit_common_patches(monkeypatch, exchange, fill_price=103.0)

    result = bybit_executor.bybit_execute(_bybit_signal(), _approval())

    assert result["success"] is False
    # Post-fill level validation (TP no longer above fill) or RR-invalid — both
    # are the same fail-closed emergency-close path.
    assert result["rolledBack"] is True


class _MockMt5Module:
    def __init__(self, bid, ask):
        import time as _t

        self.ORDER_TYPE_BUY = 0
        self.ORDER_TYPE_SELL = 1
        self.TRADE_ACTION_DEAL = 1
        self.ORDER_TIME_GTC = 0
        self.ORDER_FILLING_IOC = 1
        self.TRADE_RETCODE_DONE = 10009
        self.TRADE_RETCODE_TRADE_DISABLED = 10017
        now = _t.time()
        self._tick = SimpleNamespace(
            bid=bid, ask=ask, time=int(now), time_msc=int(now * 1000)
        )
        self._sym_info = SimpleNamespace(
            digits=5, point=0.00001, trade_stops_level=0, volume_step=0.01, volume_min=0.01
        )

    def symbol_select(self, symbol, visible):
        return True

    def symbol_info_tick(self, symbol):
        return self._tick

    def symbol_info(self, symbol):
        return self._sym_info

    def last_error(self):
        return (0, "ok")


def _mt5_common_patches(monkeypatch, fake_mt5, fill_price, closes):
    import mt5_executor
    import telegram_notify

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: fake_mt5)
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    # The broker-clock tick-age gate is covered elsewhere; not under test here.
    monkeypatch.setattr(mt5_executor, "_mt5_max_tick_age_sec", lambda *_a: None)
    monkeypatch.setattr(mt5_executor, "_mt5_trade_state", lambda *a, **kw: {})
    monkeypatch.setattr(
        mt5_executor, "_mt5_trade_state_block_reason", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        mt5_executor, "_mt5_order_check_with_filling_fallback", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        mt5_executor,
        "_send_order_with_filling_fallback",
        lambda request: SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE,
            order=111,
            volume=0.1,
            price=fill_price,
            comment="done",
        ),
    )
    monkeypatch.setattr(
        mt5_executor, "_mt5_resolve_position_ticket", lambda *a, **kw: 555001
    )
    monkeypatch.setattr(mt5_executor, "_mt5_total_fee_cost", lambda *a, **kw: None)

    def _close(ticket, volume=None):
        closes.append({"ticket": ticket, "volume": volume})
        return {"success": True}

    monkeypatch.setattr(mt5_executor, "mt5_close_position", _close)
    monkeypatch.setattr(telegram_notify, "notify_trade_opened", lambda **kw: None)


def _mt5_signal(**overrides) -> dict:
    sig = {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "price": 1.10,
        "sl": 1.09,
        "tp1": 1.12,
        "min_rr": 1.5,
    }
    sig.update(overrides)
    return sig


def test_mt5_records_actual_rr_from_true_fill_price(monkeypatch):
    import mt5_executor

    fake_mt5 = _MockMt5Module(bid=1.09995, ask=1.10)
    closes = []
    _mt5_common_patches(monkeypatch, fake_mt5, fill_price=1.1010, closes=closes)

    result = mt5_executor.mt5_execute(_mt5_signal(), _approval())

    assert result["success"] is True
    # (1.12 - 1.1010) / (1.1010 - 1.09) = 1.7273 — from the fill, not the quote.
    assert result["actualRrAtFill"] == pytest.approx(1.7273, abs=1e-3)
    assert closes == []  # no emergency close on a valid fill


def test_mt5_below_min_rr_at_fill_triggers_emergency_close(monkeypatch):
    import mt5_executor

    fake_mt5 = _MockMt5Module(bid=1.09995, ask=1.10)
    closes = []
    # Fill at 1.1190: RR = (1.12-1.119)/(1.119-1.09) = 0.034 < min_rr 1.5.
    _mt5_common_patches(monkeypatch, fake_mt5, fill_price=1.1190, closes=closes)

    result = mt5_executor.mt5_execute(_mt5_signal(), _approval())

    assert result["success"] is False
    assert result["error"].startswith("ACTUAL_RR_BELOW_MIN_AT_FILL")
    assert result["rollbackComplete"] is True
    assert closes == [{"ticket": 555001, "volume": None}]


def test_mt5_no_min_rr_records_without_closing(monkeypatch):
    import mt5_executor

    fake_mt5 = _MockMt5Module(bid=1.09995, ask=1.10)
    closes = []
    _mt5_common_patches(monkeypatch, fake_mt5, fill_price=1.1180, closes=closes)

    sig = _mt5_signal()
    del sig["min_rr"]  # no signal floor and no configured floor -> record only
    result = mt5_executor.mt5_execute(sig, _approval())

    assert result["success"] is True
    # (1.12 - 1.1180) / (1.1180 - 1.09) = 0.0714 — recorded, no emergency close.
    assert result["actualRrAtFill"] == pytest.approx(0.0714, abs=1e-3)
    assert closes == []


# ── trigger lifecycle: stamped-key aliases + live tracker consultation ───────


def test_trigger_lifecycle_reads_stamped_trigger_expiry_key():
    # timeframe_policy stamps `triggerExpiry` (ISO) — past => treated EXPIRED.
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert _trigger_lifecycle_block_reason(
        {"triggerState": "EXECUTABLE", "triggerExpiry": past}
    ) == "TRIGGER_LIFECYCLE_EXPIRED"
    future = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    assert _trigger_lifecycle_block_reason(
        {"triggerState": "EXECUTABLE", "triggerExpiry": future}
    ) is None


def test_trigger_lifecycle_consults_live_tracker_when_no_stamped_fields():
    import uuid

    from timeframe_policy import get_trigger_tracker

    sid = f"test-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    get_trigger_tracker().arm(
        signal_id=sid,
        symbol="EURUSD",
        engine="A",
        style="intraday",
        direction="LONG",
        profile="forex",
        regime_tf="D1",
        bias_tf="H4",
        structure_tf="H1",
        setup_tf="M30",
        trigger_tf="M15",
        armed_at=now - timedelta(hours=5),
        ttl_seconds=3600.0,  # expired 4h ago
    )
    assert _trigger_lifecycle_block_reason({"signalId": sid}) == (
        "TRIGGER_LIFECYCLE_EXPIRED"
    )


def test_trigger_lifecycle_live_tracker_active_trigger_does_not_block():
    import uuid

    from timeframe_policy import get_trigger_tracker

    sid = f"test-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    get_trigger_tracker().arm(
        signal_id=sid,
        symbol="EURUSD",
        engine="A",
        style="intraday",
        direction="LONG",
        profile="forex",
        regime_tf="D1",
        bias_tf="H4",
        structure_tf="H1",
        setup_tf="M30",
        trigger_tf="M15",
        armed_at=now,
        ttl_seconds=3600.0,
    )
    assert _trigger_lifecycle_block_reason({"signalId": sid}) is None
    # Unknown signal ids are unaffected (backward compatible).
    assert _trigger_lifecycle_block_reason({"signalId": f"test-{uuid.uuid4()}"}) is None


def test_trigger_lifecycle_invalidated_tracker_record_blocks_refresh():
    import uuid

    from timeframe_policy import get_trigger_tracker

    sid = f"test-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    tracker = get_trigger_tracker()
    tracker.arm(
        signal_id=sid,
        symbol="EURUSD",
        engine="B",
        style="intraday",
        direction="LONG",
        profile="forex",
        regime_tf="D1",
        bias_tf="H4",
        structure_tf="H1",
        setup_tf="M30",
        trigger_tf="M15",
        armed_at=now,
        ttl_seconds=3600.0,
    )
    tracker.invalidate(sid, now, "structure broken")
    sig = {"pair": "TEST", "direction": "LONG", "is_naked": True, "signalId": sid}
    refreshed, err = _refresh_engine_b_execution_context(
        sig, {}, _engine_b_refresh_runtime(dict(_OK_REFRESHED)), "intraday"
    )
    assert refreshed is None
    assert err == "TRIGGER_LIFECYCLE_INVALIDATED"
