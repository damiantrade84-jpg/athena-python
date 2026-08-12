"""Engine B chart-review context assembly."""

from __future__ import annotations

from ai_review.engine_b_context import assemble_engine_b_context
from ai_review.prompt_builder import build_chart_review_prompt
from style_resolver import resolve_auto_style


def _pair():
    return {
        "symbol": "BTCUSDT",
        "display": "BTC/USDT",
        "type": "crypto",
        "source": "binance",
    }


def _naked_result():
    return {
        "score": 4.5,
        "max_possible": 6.0,
        "min_score_used": 4.0,
        "passed": True,
        "checklist_passed": True,
        "current_price": 65000.0,
        "recommended_stop_loss": 63000.0,
        "recommended_take_profit": 68000.0,
        "rr_used_for_gate": 1.8,
        "structural_verdict": "CLEAR",
        "regime": "TRENDING",
        "atr_source": "candle_atr_tf",
        "nearest_support_zone": {"lower": 64000.0, "upper": 64500.0},
        "nearest_resistance_zone": {"lower": 67000.0, "upper": 67500.0},
    }


def test_assemble_engine_b_context_uses_direction_from_meta():
    def resolve_pair(_symbol: str):
        return _pair()

    def naked_analysis_fn(sig, overlay_only=False):
        assert sig["direction"] == "LONG"
        assert overlay_only is True
        return _naked_result(), _pair(), None

    ctx = assemble_engine_b_context(
        "BTCUSDT",
        "H4",
        screenshot_meta={"candidate_direction": "LONG", "analyze_style": "intraday"},
        resolve_pair_fn=resolve_pair,
        naked_analysis_fn=naked_analysis_fn,
    )
    assert ctx is not None
    assert ctx["primary_engine"] == "B"
    assert ctx["direction"] == "LONG"
    assert ctx["passed"] is True
    assert ctx["structure_context"]["structural_verdict"] == "CLEAR"


def test_assemble_engine_b_context_fail_closed_without_direction():
    ctx = assemble_engine_b_context(
        "BTCUSDT",
        "H4",
        screenshot_meta={},
        resolve_pair_fn=lambda _s: _pair(),
        naked_analysis_fn=lambda *_a, **_k: (_naked_result(), _pair(), None),
        last_engine_b_rows_fn=lambda: [],
    )
    assert ctx is None


def test_assemble_engine_b_context_auto_style_uses_shared_pair_policy():
    stock = {
        "symbol": "AAPL.US",
        "display": "Apple",
        "type": "stock",
        "source": "mt5",
    }
    seen: dict[str, str] = {}

    def naked_analysis_fn(sig, overlay_only=False):
        seen["style"] = sig["style"]
        return _naked_result(), stock, None

    ctx = assemble_engine_b_context(
        "AAPL.US",
        "H4",
        screenshot_meta={"candidate_direction": "LONG", "analyze_style": "auto"},
        resolve_pair_fn=lambda _s: stock,
        naked_analysis_fn=naked_analysis_fn,
    )

    assert ctx is not None
    # Single-name stocks moved to the intraday role ladder in 2a1171e0
    # (2026-08-07); style_resolver.py:112-113 states the rationale. The test's
    # purpose is that Engine B uses the *shared* pair policy rather than its own
    # inference, so it asserts against the shared resolver.
    expected = resolve_auto_style("auto", stock, asset_type=stock["type"])
    assert expected == "intraday"
    assert seen["style"] == expected
    assert ctx["analyze_style"] == expected


def test_assemble_engine_b_context_keeps_spot_chart_identity_separate_from_catalog_proxy():
    gold = {
        "symbol": "GC=F",
        "display": "XAU/USD",
        "type": "commodity",
        "source": "mt5",
        "futures_proxy": "GC=F",
        "price_series_kind": "spot_cfd",
    }
    seen: dict[str, str] = {}

    def naked_analysis_fn(sig, overlay_only=False):
        seen["seed_symbol"] = sig["symbol"]
        return _naked_result(), gold, None

    ctx = assemble_engine_b_context(
        "XAU/USD",
        "H4",
        screenshot_meta={"candidate_direction": "LONG", "analyze_style": "intraday"},
        resolve_pair_fn=lambda _s: gold,
        naked_analysis_fn=naked_analysis_fn,
    )

    assert ctx is not None
    assert seen["seed_symbol"] == "GC=F"
    assert ctx["symbol"] == "XAU/USD"
    assert ctx["catalog_symbol"] == "GC=F"


def test_build_chart_review_prompt_b_mode_uses_engine_b_playbook_only():
    ctx = {
        "primary_engine": "B",
        "symbol": "BTCUSDT",
        "timeframe": "H4",
        "asset_group": "crypto",
        "direction": "LONG",
        "passed": True,
        "confluence_score": 4.5,
        "threshold": 4.0,
        "structure_context": {"structural_verdict": "CLEAR"},
        "engine_snapshots": {"engineB": {"score": 4.5, "passed": True, "direction": "LONG"}},
        "atr": {"atr_value": 1200.0, "atr_tf": "H4"},
        "geometry": {"candidate_entry": 65000.0, "stop_loss": 63000.0, "take_profit": 68000.0, "rr": 1.8},
        "mismatch_warnings": [],
        "chart_snapshot": {},
    }
    prompt = build_chart_review_prompt(ctx)
    assert "Engine B playbook" in prompt or "Engine B Structure" in prompt
    assert "engineAVerdictComparison" not in prompt
    assert "engineBVerdictComparison" in prompt
    assert "engine_b_chart" in prompt
