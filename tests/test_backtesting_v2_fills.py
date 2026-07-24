"""Phase 5b fill-model tests for athena_backtesting_v2.

Covers the NEXT_AVAILABLE_QUOTE fallback ladder (tick -> M1 open -> lower-TF
open -> trigger-TF next-bar open), fill_source recording on every trade, the
same-candle fill prohibition with the explicit market_on_close opt-in, and
actual_rr_at_fill computed from the true fill price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import pytest

from athena_backtesting_v2.models import CostModel, RunRequest
from athena_backtesting_v2.simulator import (
    FILL_SOURCE_LIMIT_ZONE_TOUCH,
    FILL_SOURCE_LOWER_TF_OPEN,
    FILL_SOURCE_M1_OPEN,
    FILL_SOURCE_MOC_DECISION_CLOSE,
    FILL_SOURCE_TICK,
    FILL_SOURCE_TRIGGER_TF_NEXT_OPEN,
    EventDrivenSimulator,
    SignalIntent,
)

ZERO_COSTS = CostModel(spread_bps=0, commission_bps_per_side=0, slippage_bps=0)

H1_TIMES = pd.date_range("2025-01-01T00:00:00Z", periods=6, freq="1h")
DECISION = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)  # close of the 00:00 H1 bar


def _ohlc(times, opens) -> pd.DataFrame:
    values = [float(value) for value in opens]
    return pd.DataFrame(
        {
            "time": list(times),
            "open": values,
            "high": [value + 1.0 for value in values],
            "low": [value - 1.0 for value in values],
            "close": values,
        }
    )


def _h1_bars(opens=(100.0, 101.0, 102.0, 103.0, 104.0, 105.0)) -> pd.DataFrame:
    return _ohlc(H1_TIMES, opens)


def _intent(decision_time=DECISION, **overrides) -> SignalIntent:
    kwargs = dict(
        engine="A",
        symbol="TEST",
        asset_type="crypto",
        style="intraday",
        direction="LONG",
        decision_time=decision_time,
        valid_until=None,
        reference_price=100.0,
        stop_price=90.0,
        targets=(115.0,),
        exit_policy="SINGLE_TP1",
    )
    kwargs.update(overrides)
    return SignalIntent(**kwargs)


def test_trigger_tf_only_fills_next_bar_open_regression():
    """Default behaviour with only trigger-TF data equals the legacy fill."""
    bars = _h1_bars()
    trades, diagnostics = EventDrivenSimulator(ZERO_COSTS, max_hold_bars=10).simulate(
        "run", [_intent()], bars
    )
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fill_source == FILL_SOURCE_TRIGGER_TF_NEXT_OPEN
    assert trade.entry_time.startswith("2025-01-01T01:00:00")
    assert trade.entry_price == 101.0  # legacy next-bar-open fill, unchanged
    assert diagnostics["fillSourceCounts"] == {FILL_SOURCE_TRIGGER_TF_NEXT_OPEN: 1}
    assert diagnostics["sameCandleFillViolationCount"] == 0
    assert diagnostics["marketOnClose"] is False

    # Adverse slippage is still applied exactly as before.
    slipped, _ = EventDrivenSimulator(
        CostModel(spread_bps=0, commission_bps_per_side=0, slippage_bps=10),
        max_hold_bars=10,
    ).simulate("run", [_intent()], bars)
    assert slipped[0].entry_price == pytest.approx(101.0 * 1.001)


def test_m1_open_preferred_when_m1_bars_available():
    m1_times = pd.date_range("2025-01-01T00:00:00Z", periods=6 * 60, freq="1min")
    m1 = _ohlc(m1_times, [200.0 + 0.01 * index for index in range(6 * 60)])
    intent = _intent(stop_price=190.0, targets=(210.0,))
    trades, diagnostics = EventDrivenSimulator(
        ZERO_COSTS, max_hold_bars=10, fill_frames={"M1": m1}
    ).simulate("run", [intent], _h1_bars())
    assert len(trades) == 1
    assert trades[0].fill_source == FILL_SOURCE_M1_OPEN
    assert trades[0].entry_price == pytest.approx(200.60)  # M1 open at 01:00
    assert trades[0].entry_time.startswith("2025-01-01T01:00:00")
    assert diagnostics["fillSourceCounts"] == {FILL_SOURCE_M1_OPEN: 1}


def test_lower_tf_open_used_when_no_m1_available():
    m5_times = pd.date_range("2025-01-01T00:00:00Z", periods=6 * 12, freq="5min")
    m5 = _ohlc(m5_times, [150.0 + 0.01 * index for index in range(6 * 12)])
    intent = _intent(stop_price=140.0, targets=(160.0,))
    trades, _ = EventDrivenSimulator(
        ZERO_COSTS, max_hold_bars=10, fill_frames={"M5": m5}
    ).simulate("run", [intent], _h1_bars())
    assert len(trades) == 1
    assert trades[0].fill_source == FILL_SOURCE_LOWER_TF_OPEN
    assert trades[0].entry_price == pytest.approx(150.0 + 0.01 * 12)  # M5 open at 01:00


def test_tick_preferred_over_m1_and_requires_strictly_after_decision():
    m1_times = pd.date_range("2025-01-01T00:00:00Z", periods=6 * 60, freq="1min")
    m1 = _ohlc(m1_times, [200.0] * (6 * 60))
    ticks = pd.DataFrame(
        {
            # A tick exactly at the decision close is the close itself and must
            # not be used; the first fillable tick is strictly after it.
            "time": [
                pd.Timestamp("2025-01-01T01:00:00Z"),
                pd.Timestamp("2025-01-01T01:00:05Z"),
            ],
            "price": [300.0, 300.5],
        }
    )
    intent = _intent(stop_price=290.0, targets=(310.0,))
    trades, diagnostics = EventDrivenSimulator(
        ZERO_COSTS, max_hold_bars=10, fill_frames={"TICK": ticks, "M1": m1}
    ).simulate("run", [intent], _h1_bars())
    assert len(trades) == 1
    assert trades[0].fill_source == FILL_SOURCE_TICK
    assert trades[0].entry_price == pytest.approx(300.5)
    assert trades[0].entry_time.startswith("2025-01-01T01:00:05")
    assert diagnostics["fillSourceCounts"] == {FILL_SOURCE_TICK: 1}


def test_limit_fill_records_zone_touch_source():
    intent = _intent(order_type="limit", limit_price=100.5)
    trades, _ = EventDrivenSimulator(ZERO_COSTS, max_hold_bars=10).simulate(
        "run", [intent], _h1_bars()
    )
    assert len(trades) == 1
    assert trades[0].fill_source == FILL_SOURCE_LIMIT_ZONE_TOUCH
    assert trades[0].entry_price == pytest.approx(100.5)


def test_same_candle_fill_prohibited_without_market_on_close(monkeypatch, caplog):
    """A fill resolving before the decision close is logged and skipped."""
    bars = _h1_bars()
    simulator = EventDrivenSimulator(ZERO_COSTS, max_hold_bars=10)
    decision_ns = pd.Timestamp(DECISION).value
    monkeypatch.setattr(
        simulator,
        "_find_fill",
        lambda *_args, **_kwargs: (
            1,
            101.0,
            FILL_SOURCE_TRIGGER_TF_NEXT_OPEN,
            decision_ns - 1,  # same-candle violation: before the decision close
        ),
    )
    caplog.set_level(logging.WARNING, logger="athena_backtesting_v2.simulator")
    trades, diagnostics = simulator.simulate("run", [_intent()], bars)
    assert trades == []
    assert diagnostics["sameCandleFillViolationCount"] == 1
    assert diagnostics["fillSourceCounts"] == {}
    assert "same-candle fill prohibited" in caplog.text


def test_market_on_close_opt_in_fills_at_decision_close():
    bars = _h1_bars()
    trades, diagnostics = EventDrivenSimulator(
        ZERO_COSTS, max_hold_bars=10, market_on_close=True
    ).simulate("run", [_intent()], bars)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fill_source == FILL_SOURCE_MOC_DECISION_CLOSE
    assert trade.entry_price == pytest.approx(100.0)  # decision bar's own close
    assert trade.entry_time.startswith("2025-01-01T01:00:00")
    assert diagnostics["marketOnClose"] is True
    assert diagnostics["fillSourceCounts"] == {FILL_SOURCE_MOC_DECISION_CLOSE: 1}


def test_market_on_close_refused_when_decision_is_not_a_bar_close():
    mid_bar = datetime(2025, 1, 1, 0, 30, tzinfo=timezone.utc)
    trades, diagnostics = EventDrivenSimulator(
        ZERO_COSTS, max_hold_bars=10, market_on_close=True
    ).simulate("run", [_intent(mid_bar)], _h1_bars())
    assert trades == []
    assert diagnostics["expiredOrderCount"] == 1


def test_actual_rr_at_fill_uses_true_fill_price():
    bars = _h1_bars()
    trades, _ = EventDrivenSimulator(ZERO_COSTS, max_hold_bars=10).simulate(
        "run", [_intent()], bars
    )
    # LONG fill at 101 (next-bar open), stop 90, TP1 115 -> 14 / 11.
    assert trades[0].actual_rr_at_fill == pytest.approx(14.0 / 11.0, abs=1e-6)

    short = _intent(direction="SHORT", stop_price=112.0, targets=(86.0,))
    trades, _ = EventDrivenSimulator(ZERO_COSTS, max_hold_bars=10).simulate(
        "run", [short], bars
    )
    # SHORT fill at 101, stop 112, TP1 86 -> 15 / 11.
    assert trades[0].actual_rr_at_fill == pytest.approx(15.0 / 11.0, abs=1e-6)

    moc, _ = EventDrivenSimulator(
        ZERO_COSTS, max_hold_bars=10, market_on_close=True
    ).simulate("run", [_intent()], bars)
    # MOC fill at the 100.0 decision close, stop 90, TP1 115 -> exactly 1.5R.
    assert moc[0].actual_rr_at_fill == pytest.approx(1.5)


def test_run_request_parses_market_on_close_opt_in():
    payload = {
        "engines": ["A"],
        "symbols": ["BTC/USDT"],
        "mode": "EXPLORATORY",
        "validation": {"enabled": False},
        "marketOnClose": True,
    }
    request = RunRequest.from_payload(payload)
    assert request.market_on_close is True
    assert request.to_dict()["market_on_close"] is True

    default_request = RunRequest.from_payload(
        {key: value for key, value in payload.items() if key != "marketOnClose"}
    )
    assert default_request.market_on_close is False
