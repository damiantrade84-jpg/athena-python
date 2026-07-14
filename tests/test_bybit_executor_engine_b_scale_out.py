"""Engine B dual-TP scale-out wiring in bybit_executor (paper/demo only).

Covers the fail-closed plan resolver and the order-shape contract:
- static mode: reduce-only partial at TP1 + position TP at TP2
- config gate off (default): legacy single-TP behavior, no partial
- malformed levels: scale-out never engages
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import bybit_executor
from risk_engine import RiskApproval


SCALE_OUT = "scale_out_structural_tp1_fallback_tp2"


def _signal(**overrides):
    base = {
        "pair": "BTC/USDT",
        "engine": "engine_b",
        "direction": "LONG",
        "type": "crypto",
        "price": 67000.0,
        "sl": 66000.0,
        "tp1": 68000.0,
        "tp2": 69000.0,
        "engine_b_exit_strategy": SCALE_OUT,
        "exit_mode": "traditional_static",
    }
    base.update(overrides)
    return base


class TestEngineBScaleOutPlan:
    """_engine_b_scale_out_plan is fail-closed on every field."""

    def test_disabled_by_default(self):
        assert bybit_executor._engine_b_scale_out_plan(
            _signal(type="forex")
        ) is None

    def test_valid_static_plan_resolves_runner_to_tp2(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        assert bybit_executor._engine_b_scale_out_plan(_signal()) == "runner_to_tp2"

    def test_short_direction_requires_tp2_below_tp1(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        ok = _signal(direction="SHORT", sl=68000.0, tp1=66000.0, tp2=65000.0)
        assert bybit_executor._engine_b_scale_out_plan(ok) == "runner_to_tp2"
        bad = _signal(direction="SHORT", sl=68000.0, tp1=65000.0, tp2=66000.0)
        assert bybit_executor._engine_b_scale_out_plan(bad) is None

    def test_fail_closed_matrix(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        # Non-Engine-B identity
        assert bybit_executor._engine_b_scale_out_plan(_signal(engine="engine_a")) is None
        assert bybit_executor._engine_b_scale_out_plan(_signal(engine="")) is None
        # Single / invalid exit strategy
        assert (
            bybit_executor._engine_b_scale_out_plan(
                _signal(engine_b_exit_strategy="single_fallback_rr_tp")
            )
            is None
        )
        assert (
            bybit_executor._engine_b_scale_out_plan(
                _signal(engine_b_exit_strategy="invalid_levels")
            )
            is None
        )
        assert (
            bybit_executor._engine_b_scale_out_plan(_signal(engine_b_exit_strategy=None))
            is None
        )
        # Missing / zero / malformed TP levels
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp1=0)) is None
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp2=0)) is None
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp1=None)) is None
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp2=None)) is None
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp2="junk")) is None
        # Degenerate plan: TP2 not beyond TP1
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp2=68000.0)) is None
        assert bybit_executor._engine_b_scale_out_plan(_signal(tp2=67500.0)) is None
        # Missing direction
        assert bybit_executor._engine_b_scale_out_plan(_signal(direction="")) is None

    def test_timed_mode_degrades_to_fixed_tp2_bracket(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        sig = _signal(exit_mode="time_based")
        assert bybit_executor._engine_b_scale_out_plan(sig) == "runner_to_tp2"

    def test_trail_mode_degrades_when_no_trailing_manager(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        # TIMED_EXIT.tp_mode default is "fixed": no chandelier runs, so the
        # runner must keep the fixed TP2 bracket rather than run unmanaged.
        monkeypatch.setitem(bybit_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "fixed"})
        sig = _signal(exit_mode="adaptive_trail")
        assert bybit_executor._engine_b_scale_out_plan(sig) == "runner_to_tp2"

    def test_trail_mode_with_trailing_manager_resolves_runner_trail(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        monkeypatch.setitem(
            bybit_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"}
        )
        sig = _signal(exit_mode="adaptive_trail")
        assert bybit_executor._engine_b_scale_out_plan(sig) == "runner_trail"


class _MockExchange:
    def __init__(self):
        self.market_orders = []

    def fetch_ticker(self, symbol):
        return {
            "ask": 67000.0,
            "bid": 66990.0,
            "last": 66995.0,
            "timestamp": int(__import__("time").time() * 1000),
        }

    def create_market_order(self, symbol, side, amount, params=None):
        self.market_orders.append((symbol, side, amount, params))
        return {
            "id": "entry-1",
            "average": 67000.0,
            "filled": amount,
            "status": "closed",
            "fee": {"cost": 0.1},
        }

    def market(self, symbol):
        return {
            "precision": {"amount": 0.001},
            "limits": {"amount": {"min": 0.001}},
        }


def _run_execute(monkeypatch, signal, *, partial_calls, stop_calls):
    exchange = _MockExchange()
    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: exchange)
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda s: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda ex, sym: None)
    monkeypatch.setattr(
        bybit_executor,
        "_bybit_resolve_entry_fill",
        lambda ex, sym, order, requested_volume: (order, "closed", requested_volume),
    )
    monkeypatch.setattr(
        bybit_executor,
        "_set_trading_stop",
        lambda ex, sym, sl=None, tp=None: stop_calls.append({"sl": sl, "tp": tp}),
    )
    monkeypatch.setattr(
        bybit_executor,
        "_place_reduce_only_partial",
        lambda ex, sym, direction, filled, price, fraction, label, **kw: (
            partial_calls.append(
                {"price": price, "fraction": fraction, "label": label}
            )
            or "partial-1"
        ),
    )
    approval = RiskApproval(True, 0.01, 10.0, 0.001, 0.001, 0.0, "OK")
    return bybit_executor.bybit_execute(signal, approval)


class TestEngineBScaleOutExecution:
    def test_static_scale_out_places_partial_at_tp1_and_broker_tp_at_tp2(
        self, monkeypatch
    ):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "EXECUTION_SINGLE_LEG_ONLY", True)
        partial_calls, stop_calls = [], []

        result = _run_execute(
            monkeypatch, _signal(), partial_calls=partial_calls, stop_calls=stop_calls
        )

        assert result["success"] is True
        assert result["engineBScaleOut"] is True
        assert result["runnerDirective"] == "runner_to_tp2"
        # Position TP carries the runner target (TP2).
        assert stop_calls and stop_calls[-1]["tp"] == pytest.approx(69000.0)
        assert stop_calls[-1]["sl"] == pytest.approx(66000.0)
        # Reduce-only partial banks the TP1 clip.
        assert len(partial_calls) == 1
        assert partial_calls[0]["price"] == pytest.approx(68000.0)
        assert partial_calls[0]["fraction"] == pytest.approx(0.5)
        assert partial_calls[0]["label"] == "Engine B TP1"
        assert result["partialOrderId"] == "partial-1"

    def test_flag_off_keeps_legacy_single_tp_and_no_partial(self, monkeypatch):
        # Default config: ENGINE_B_LIVE_SCALE_OUT_ENABLED is False.
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", False)
        monkeypatch.setitem(bybit_executor.CONFIG, "EXECUTION_SINGLE_LEG_ONLY", False)
        partial_calls, stop_calls = [], []

        result = _run_execute(
            monkeypatch, _signal(), partial_calls=partial_calls, stop_calls=stop_calls
        )

        assert result["success"] is True
        assert result["engineBScaleOut"] is False
        assert result["runnerDirective"] is None
        # Legacy: full-position TP at TP1, no partial order.
        assert stop_calls and stop_calls[-1]["tp"] == pytest.approx(68000.0)
        assert partial_calls == []

    def test_single_leg_only_allows_one_position_partial_scale_out(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "EXECUTION_SINGLE_LEG_ONLY", True)
        partial_calls, stop_calls = [], []

        result = _run_execute(
            monkeypatch, _signal(), partial_calls=partial_calls, stop_calls=stop_calls
        )

        assert result["success"] is True
        assert result["engineBScaleOut"] is True
        assert stop_calls and stop_calls[-1]["tp"] == pytest.approx(69000.0)
        assert len(partial_calls) == 1

    def test_partial_opt_in_off_blocks_scale_out(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", False)
        monkeypatch.setitem(bybit_executor.CONFIG, "EXECUTION_SINGLE_LEG_ONLY", True)
        partial_calls, stop_calls = [], []

        result = _run_execute(
            monkeypatch, _signal(), partial_calls=partial_calls, stop_calls=stop_calls
        )

        assert result["engineBScaleOut"] is False
        assert stop_calls and stop_calls[-1]["tp"] == pytest.approx(68000.0)
        assert partial_calls == []

    def test_malformed_tp2_falls_back_to_legacy_single_tp(self, monkeypatch):
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ENABLED", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "ENGINE_B_LIVE_SCALE_OUT_ALLOW_SINGLE_POSITION_PARTIAL", True)
        monkeypatch.setitem(bybit_executor.CONFIG, "EXECUTION_SINGLE_LEG_ONLY", False)
        partial_calls, stop_calls = [], []

        result = _run_execute(
            monkeypatch,
            _signal(tp2=0),
            partial_calls=partial_calls,
            stop_calls=stop_calls,
        )

        assert result["success"] is True
        assert result["engineBScaleOut"] is False
        assert stop_calls and stop_calls[-1]["tp"] == pytest.approx(68000.0)
        assert partial_calls == []
