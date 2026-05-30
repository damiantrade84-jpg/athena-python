from athena_research.metrics import StrategyMetrics
from athena_research.research_context import annotate_research_results


def _row(strategy_name, symbol="EUR/USD", asset_class="forex"):
    return StrategyMetrics(
        run_id="t", symbol=symbol, asset_class=asset_class, timeframe="H4",
        family="trend_momentum", strategy_name=strategy_name, params_str="",
        direction="long",
    )


def test_engine_a_row_gets_global_default_mode_and_parity():
    cfg = {"engine_a_exit_mode_by_score_group": {},
           "engine_a_exit_mode_global_default": "traditional_static"}
    out = annotate_research_results([_row("ema_cross")], cfg)
    assert out[0].engine == "ENGINE_A"
    assert out[0].engine_a_exit_mode == "traditional_static"
    assert out[0].engine_a_exit_parity == "faithful"


def test_engine_a_row_uses_per_group_override():
    cfg = {"engine_a_exit_mode_by_score_group": {"forex_majors": "adaptive_trail"},
           "engine_a_exit_mode_global_default": "traditional_static"}
    out = annotate_research_results([_row("ema_cross")], cfg)
    # EUR/USD infers pair_group=forex_majors
    assert out[0].pair_group == "forex_majors"
    assert out[0].engine_a_exit_mode == "adaptive_trail"
    assert out[0].engine_a_exit_parity == "trail_not_simulated"


def test_non_engine_a_row_keeps_empty_exit_annotation():
    cfg = {"engine_a_exit_mode_global_default": "traditional_static"}
    out = annotate_research_results([_row("ob_bos")], cfg)  # ENGINE_B
    assert out[0].engine == "ENGINE_B"
    assert out[0].engine_a_exit_mode == ""
    assert out[0].engine_a_exit_parity == ""
