"""FX scanner candidate construction and ranking."""

from __future__ import annotations

from athena_fx.models import ACTION_BUY, ACTION_NO_TRADE, FxTradeCandidate, GateResult
import athena_fx.scanner as scanner_module
from athena_fx.scanner import (
    build_candidate_from_decision,
    scan_forex,
    _fetch_mt5_recent_d1_bars,
)


def _bars() -> list[dict]:
    return [
        {
            "date": f"2026-01-{day:02d}",
            "open": 1.1000,
            "high": 1.1100,
            "low": 1.0900,
            "close": 1.1050,
        }
        for day in range(1, 25)
    ]


def _buy_decision(**overrides) -> dict:
    base = {
        "symbol": "EURUSD",
        "as_of_date": "2026-01-15",
        "action": ACTION_BUY,
        "direction": "LONG",
        "aligned_families": ["carry", "momentum"],
        "blocked_families": [],
        "factor_scores": {
            "carry": {"score": 0.02, "direction": "bullish", "points": 14.0},
            "momentum": {"score": 0.8, "direction": "bullish", "points": 28.0},
            "value": {"score": 0.1, "direction": "neutral", "points": 0.0},
        },
        "total_score": 42.0,
        "cot_overlay": {"action": "none"},
        "gate_results": [
            GateResult("data_quality", "pass", "data_ok").to_dict(),
            GateResult("volatility", "pass", "vol_ok").to_dict(),
            GateResult(
                "cost",
                "pass",
                "cost_ok",
                numeric_inputs={"spread_pips": 0.8, "atr_pips": 20.0},
            ).to_dict(),
        ],
        "size_multiplier": 1.0,
        "reason": "2 families aligned long; total_score=42.00",
    }
    base.update(overrides)
    return base


def test_trade_candidate_serializes_gate_summary_reason() -> None:
    candidate = FxTradeCandidate(
        scan_id="scan-1",
        symbol="EURUSD",
        action=ACTION_BUY,
        direction="LONG",
        tradable_now=True,
        factor_eligible=True,
        execution_eligible=True,
        reason="2 families aligned long",
        gate_results={"cost": {"result": "pass", "reason_code": "cost_ok"}},
    )

    payload = candidate.to_dict()

    assert payload["symbol"] == "EURUSD"
    assert payload["tradable_now"] is True
    assert payload["gate_summary"] == "cost:pass:cost_ok"
    assert payload["reason"] == "2 families aligned long"


def test_build_candidate_from_buy_decision_includes_trade_levels() -> None:
    candidate = build_candidate_from_decision(
        _buy_decision(),
        scan_id="scan-1",
        bid=1.1000,
        ask=1.1002,
        bars=_bars(),
        execution_allowed=True,
        execution_block_reason=None,
    )

    assert candidate.action == ACTION_BUY
    assert candidate.direction == "LONG"
    assert candidate.factor_eligible is True
    assert candidate.execution_eligible is True
    assert candidate.tradable_now is True
    assert candidate.entry == 1.1002
    assert candidate.sl < candidate.entry < candidate.tp1 < candidate.tp2
    assert candidate.rr1 > 0
    assert candidate.spread_pips > 0
    assert candidate.atr_pips > 0


def test_cost_gate_failure_blocks_candidate_and_keeps_reason() -> None:
    decision = _buy_decision(
        gate_results=[
            GateResult("data_quality", "pass", "data_ok").to_dict(),
            GateResult("volatility", "pass", "vol_ok").to_dict(),
            GateResult(
                "cost",
                "fail",
                "cost_exceeds_atr_fraction",
                recommended_action="block",
                size_multiplier=0.0,
            ).to_dict(),
        ],
        reason="gate failure: cost (cost_exceeds_atr_fraction)",
    )

    candidate = build_candidate_from_decision(
        decision,
        scan_id="scan-1",
        bid=1.1000,
        ask=1.1200,
        bars=_bars(),
        execution_allowed=True,
        execution_block_reason=None,
    )

    assert candidate.action == ACTION_NO_TRADE
    assert candidate.factor_eligible is True
    assert candidate.execution_eligible is False
    assert candidate.tradable_now is False
    assert "cost_exceeds_atr_fraction" in candidate.reason
    assert candidate.gate_results["cost"]["result"] == "fail"


def test_candidate_recomputes_stale_missing_atr_cost_gate_from_live_context() -> None:
    decision = _buy_decision(
        gate_results=[
            GateResult("data_quality", "pass", "data_ok").to_dict(),
            GateResult("volatility", "pass", "vol_ok").to_dict(),
            GateResult(
                "cost",
                "fail",
                "missing_atr",
                recommended_action="block",
                size_multiplier=0.0,
            ).to_dict(),
        ],
        reason="gate failure: cost (missing_atr)",
    )

    candidate = build_candidate_from_decision(
        decision,
        scan_id="scan-1",
        bid=1.1000,
        ask=1.1002,
        bars=_bars(),
        execution_allowed=True,
        execution_block_reason=None,
    )

    assert candidate.action == ACTION_BUY
    assert candidate.execution_eligible is True
    assert candidate.tradable_now is True
    assert candidate.gate_results["cost"]["result"] == "pass"
    assert candidate.gate_results["cost"]["reason_code"] == "cost_ok"
    assert candidate.atr_pips is not None


def test_scan_ranks_tradable_before_blocked_and_no_trade() -> None:
    no_trade = _buy_decision(
        symbol="GBPUSD",
        action=ACTION_NO_TRADE,
        direction=None,
        aligned_families=[],
        reason="insufficient_family_alignment",
    )
    blocked = _buy_decision(
        symbol="USDJPY",
        gate_results=[GateResult("cost", "fail", "missing_spread").to_dict()],
        reason="gate failure: cost (missing_spread)",
    )

    result = scan_forex(
        symbols=["GBPUSD", "USDJPY", "EURUSD"],
        decisions_by_symbol={
            "GBPUSD": no_trade,
            "USDJPY": blocked,
            "EURUSD": _buy_decision(),
        },
        price_provider=lambda _symbol: (1.1000, 1.1002),
        bars_provider=lambda _symbol: _bars(),
        execution_allowed=True,
    )

    symbols = [c["symbol"] for c in result["candidates"]]
    assert symbols[0] == "EURUSD"
    assert result["summary"]["tradable_count"] == 1
    assert result["summary"]["blocked_count"] == 1
    assert result["summary"]["no_trade_count"] == 1


def test_scan_recomputes_live_decision_when_persisted_decision_has_missing_atr(monkeypatch) -> None:
    stale_decision = _buy_decision(
        symbol="AUDCAD",
        action=ACTION_NO_TRADE,
        direction=None,
        gate_results=[GateResult("cost", "fail", "missing_atr").to_dict()],
        reason="gate failure: cost (missing_atr)",
    )

    def live_decision_provider(symbol, as_of_date, spread_pips, bars, strict):
        assert symbol == "AUDCAD"
        assert as_of_date == "2026-01-15"
        assert spread_pips is not None and spread_pips > 0
        assert len(bars) >= 15
        assert strict is True
        return _buy_decision(symbol="AUDCAD")

    monkeypatch.setattr(scanner_module.fx_store, "load_decisions", lambda as_of_date=None: [stale_decision])

    result = scan_forex(
        symbols=["AUDCAD"],
        price_provider=lambda _symbol: (0.9000, 0.9002),
        bars_provider=lambda _symbol: _bars(),
        execution_allowed=True,
        live_decision_provider=live_decision_provider,
    )

    candidate = result["candidates"][0]
    assert candidate["symbol"] == "AUDCAD"
    assert candidate["action"] == ACTION_BUY
    assert candidate["tradable_now"] is True
    assert candidate["gate_results"]["cost"]["reason_code"] == "cost_ok"


def test_fetch_mt5_recent_d1_bars_returns_ohlc(monkeypatch) -> None:
    class _FakeMt5:
        TIMEFRAME_D1 = 1440

        def symbol_select(self, symbol, enabled):
            return symbol == "AUDCAD"

        def copy_rates_from_pos(self, symbol, timeframe, start, count):
            assert symbol == "AUDCAD"
            assert timeframe == self.TIMEFRAME_D1
            assert start == 0
            assert count == 3
            return [
                {"time": 1764547200, "open": 0.9000, "high": 0.9100, "low": 0.8900, "close": 0.9050},
                {"time": 1764633600, "open": 0.9050, "high": 0.9150, "low": 0.8950, "close": 0.9100},
            ]

    monkeypatch.setattr("mt5_executor._get_mt5", lambda: _FakeMt5())
    monkeypatch.setattr("mt5_executor.mt5_connect", lambda: True)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda symbol: symbol)

    bars = _fetch_mt5_recent_d1_bars("AUDCAD", limit=3)

    assert len(bars) == 2
    assert bars[0]["date"] == "2025-12-01"
    assert bars[0]["high"] == 0.91
