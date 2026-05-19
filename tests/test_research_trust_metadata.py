"""Tests for Research Lab trust metadata."""

from __future__ import annotations

import pandas as pd

from athena_research.trust_metadata import (
    apply_trust_metadata,
    build_trust_context,
    engine_fidelity_for_row,
    trust_tier_for_row,
    trust_legend,
)


def _ready_row(**overrides) -> dict:
    base = {
        "strategy_name": "bollinger_touch",
        "family": "mean_reversion",
        "engine": "ENGINE_A",
        "status": "STRONG_CANDIDATE",
        "recommendation": "ADD",
        "implementation_verdict": "IMPLEMENTATION_READY",
        "implementation_blockers": "",
    }
    base.update(overrides)
    return base


def test_trust_legend_has_four_tiers():
    tiers = {item["tier"] for item in trust_legend()}
    assert tiers == {"USE_NOW", "SCREEN_ONLY", "RETEST", "REJECT"}


def test_bollinger_touch_is_live_aligned_a():
    fid, note = engine_fidelity_for_row("bollinger_touch", "mean_reversion", "ENGINE_A")
    assert fid == "LIVE_ALIGNED_A"
    assert "Engine A" in note


def test_ob_bos_is_proxy_engine_b():
    fid, note = engine_fidelity_for_row("ob_bos", "engine_b_proxy", "ENGINE_B")
    assert fid == "PROXY_ENGINE_B"
    assert "proxy" in note.lower()


def test_micro_breakout_is_live_aligned_b():
    fid, _ = engine_fidelity_for_row("micro_breakout", "engine_d_proxy", "ENGINE_D")
    assert fid == "LIVE_ALIGNED_B"


def test_use_now_when_ready_and_live_aligned():
    tier, summary = trust_tier_for_row(_ready_row())
    assert tier == "USE_NOW"
    assert "Live-aligned" in summary


def test_screen_only_for_proxy_even_when_strong():
    tier, summary = trust_tier_for_row(_ready_row(
        strategy_name="ob_bos",
        family="engine_b_proxy",
        engine="ENGINE_B",
        recommendation="KEEP",
    ))
    assert tier == "SCREEN_ONLY"
    assert "proxy" in summary.lower() or "Proxy" in summary


def test_reject_for_failed_discovery():
    tier, _ = trust_tier_for_row(_ready_row(
        status="REJECT",
        recommendation="REJECT",
        implementation_verdict="NOT_IMPLEMENTABLE",
    ))
    assert tier == "REJECT"


def test_retest_when_not_implementation_ready():
    tier, summary = trust_tier_for_row(_ready_row(
        implementation_verdict="NOT_IMPLEMENTABLE",
        implementation_blockers="robustness_below_strong_floor",
    ))
    assert tier == "RETEST"
    assert "Blocked" in summary


def test_apply_trust_metadata_adds_columns():
    df = pd.DataFrame([_ready_row(), _ready_row(
        strategy_name="ob_bos",
        family="engine_b_proxy",
        engine="ENGINE_B",
    )])
    out = apply_trust_metadata(df)
    assert "trust_tier" in out.columns
    assert "engine_fidelity" in out.columns
    assert out.iloc[0]["trust_tier"] == "USE_NOW"
    assert out.iloc[1]["trust_tier"] == "SCREEN_ONLY"


def test_write_research_summary_csv():
    import tempfile
    from pathlib import Path
    from athena_research.reporting import metrics_to_df, write_research_summary_csv
    from athena_research.metrics import StrategyMetrics

    tmp_path = Path(tempfile.mkdtemp(prefix="athena_research_test_"))
    row = StrategyMetrics(
        run_id="r1",
        symbol="EUR/USD",
        timeframe="H1",
        asset_class="forex",
        family="mean_reversion",
        strategy_name="bollinger_touch",
        params_str="",
        direction="both",
        status="STRONG_CANDIDATE",
        trade_count=40,
        net_return=0.05,
        oos_return=0.02,
        robustness_score=0.7,
    )
    df = metrics_to_df([row])
    path = write_research_summary_csv(df, tmp_path)
    assert path.exists()
    assert (tmp_path / "research_summary.csv").exists()


def test_build_trust_context_counts():
    df = pd.DataFrame([
        _ready_row(),
        _ready_row(strategy_name="ob_bos", family="engine_b_proxy", engine="ENGINE_B"),
    ])
    ctx = build_trust_context(df, validation_run_count=2)
    assert ctx["tier_counts"]["USE_NOW"] == 1
    assert ctx["tier_counts"]["SCREEN_ONLY"] == 1
    assert ctx["validation_run_count"] == 2
    assert "disclaimer" in ctx
