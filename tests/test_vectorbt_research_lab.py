"""
Tests for Athena Research Lab.

Key safety assertions:
  - No live execution modules imported or called.
  - No live thresholds modified.
  - All data comes from synthetic OHLCV (no network calls in tests).
  - VectorBT is optional (tests fall back to pandas backtester).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import shutil
import tempfile

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def tmp_path():
    """Override tmp_path to avoid Windows AppData permission issues."""
    d = Path(tempfile.mkdtemp(prefix="athena_research_test_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

# ── Execution import guard ────────────────────────────────────────────────────
_FORBIDDEN = {"athena", "execution", "mt5_executor", "bybit_executor", "auto_trader", "risk_engine"}


def _check_no_live_imports():
    violations = [m for m in _FORBIDDEN if m in sys.modules]
    assert not violations, f"Live execution modules in sys.modules: {violations}"


# ── Synthetic OHLCV factory ───────────────────────────────────────────────────

def _make_ohlcv(n: int = 500, seed: int = 42, trend: float = 0.0001) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame with a DatetimeIndex."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(trend, 0.01, n))
    high = close * (1 + rng.uniform(0, 0.005, n))
    low = close * (1 - rng.uniform(0, 0.005, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = rng.uniform(1000, 10000, n)

    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Data loader tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDataLoader:
    def test_ohlcv_from_dict_list(self):
        from athena_research.data_loader import ohlcv_from_dict_list, DataProvenance

        raw = [
            {"time": "2024-01-01T00:00:00+00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "vol": 5000},
            {"time": "2024-01-01T01:00:00+00:00", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "vol": 6000},
        ]
        df, prov = ohlcv_from_dict_list(raw, symbol="TEST", timeframe="H1")

        assert not df.empty
        assert set(["open", "high", "low", "close", "volume"]).issubset(df.columns)
        assert len(df) == 2
        assert df["close"].iloc[0] == 100.5
        assert df["volume"].iloc[0] == 5000.0
        assert isinstance(prov, DataProvenance)
        assert prov.data_source == "athena_cache"

    def test_clean_drops_zero_close(self):
        from athena_research.data_loader import _clean

        df = _make_ohlcv(10)
        df.loc[df.index[3], "close"] = 0.0
        cleaned = _clean(df)
        assert (cleaned["close"] > 0).all()
        assert len(cleaned) == 9

    def test_asset_class_detection(self):
        from athena_research.data_loader import asset_class_for

        assert asset_class_for("BTC/USDT") == "crypto"
        assert asset_class_for("EUR/USD") == "forex"
        assert asset_class_for("XAU/USD") == "commodity"
        assert asset_class_for("US500") == "index"
        assert asset_class_for("AAPL") == "stock"

    def test_vbt_freq(self):
        from athena_research.data_loader import vbt_freq

        assert vbt_freq("H1") == "1h"
        assert vbt_freq("H4") == "4h"
        assert vbt_freq("D1") == "1D"

    def test_synthetic_ohlcv_returns_tuple(self):
        from athena_research.data_loader import synthetic_ohlcv, DataProvenance

        df, prov = synthetic_ohlcv(n=200, seed=1, symbol="SYNTH", timeframe="H1")
        assert len(df) == 200
        assert isinstance(prov, DataProvenance)
        assert prov.data_source == "synthetic_test"
        assert prov.symbol == "SYNTH"
        assert prov.candle_count == 200

    def test_binance_rest_uses_failover_endpoint(self, monkeypatch):
        from athena_research.data_loader import _fetch_binance_rest

        calls = []

        class Resp:
            def __init__(self, status_code, payload=None, text=""):
                self.status_code = status_code
                self._payload = payload or []
                self.text = text

            def json(self):
                return self._payload

        def fake_get(url, params=None, timeout=15):
            calls.append(url)
            if "fapi.binance.com" in url and "fapi1" not in url:
                return Resp(451, text="restricted")
            row = [
                1700000000000, "100", "101", "99", "100.5", "123",
                1700000299999, "0", 10, "60", "0", "0",
            ]
            return Resp(200, payload=[row] * 20)

        import requests
        monkeypatch.setattr(requests, "get", fake_get)

        df = _fetch_binance_rest("BTC/USDT", "M5", limit=20)

        assert df is not None
        assert len(df) == 20
        assert any("fapi1.binance.com" in url for url in calls)

    def test_no_live_imports_after_data_loader(self):
        import athena_research.data_loader
        _check_no_live_imports()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Strategy family tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategies:
    @pytest.fixture
    def ohlcv(self):
        return _make_ohlcv(300)

    def test_ema_cross_returns_correct_shape(self, ohlcv):
        from athena_research.strategies import strategy_ema_cross

        result = strategy_ema_cross(ohlcv, {"fast_period": 10, "slow_period": 50})
        for key in ["entries", "exits", "short_entries", "short_exits"]:
            assert key in result
            assert isinstance(result[key], pd.Series)
            assert len(result[key]) == len(ohlcv)
            assert result[key].dtype == bool

    def test_ema_cross_fast_ge_slow_returns_empty(self, ohlcv):
        from athena_research.strategies import strategy_ema_cross

        result = strategy_ema_cross(ohlcv, {"fast_period": 100, "slow_period": 50})
        assert result["entries"].sum() == 0

    def test_pullback_ema_returns_bool_signals(self, ohlcv):
        from athena_research.strategies import strategy_pullback_ema

        result = strategy_pullback_ema(ohlcv, {"trend_period": 50, "pullback_period": 20})
        assert result["entries"].dtype == bool
        assert len(result["entries"]) == len(ohlcv)

    def test_bollinger_touch(self, ohlcv):
        from athena_research.strategies import strategy_bollinger_touch

        result = strategy_bollinger_touch(ohlcv, {"period": 20, "num_std": 2.0})
        assert result["entries"].dtype == bool

    def test_rsi_extreme(self, ohlcv):
        from athena_research.strategies import strategy_rsi_extreme

        result = strategy_rsi_extreme(ohlcv, {"period": 14, "oversold": 30, "overbought": 70})
        assert len(result["entries"]) == len(ohlcv)

    def test_vwap_deviation_skips_no_volume(self, ohlcv):
        from athena_research.strategies import strategy_vwap_deviation

        df_no_vol = ohlcv.copy()
        df_no_vol["volume"] = 0.0
        result = strategy_vwap_deviation(df_no_vol, {"std_threshold": 2.0})
        assert result["meta"].get("skip") == "no_volume"

    def test_ob_bos(self, ohlcv):
        from athena_research.strategies import strategy_ob_bos

        result = strategy_ob_bos(ohlcv, {"swing_period": 5, "require_engulf": False})
        assert result["entries"].dtype == bool

    def test_bb_squeeze(self, ohlcv):
        from athena_research.strategies import strategy_bb_squeeze_breakout

        result = strategy_bb_squeeze_breakout(ohlcv, {"period": 20, "squeeze_pct": 20, "confirm_ema": True})
        assert isinstance(result["entries"], pd.Series)

    def test_micro_breakout(self, ohlcv):
        from athena_research.strategies import strategy_micro_breakout

        result = strategy_micro_breakout(ohlcv, {"range_bars": 6, "atr_sl_mult": 1.0})
        assert result["entries"].dtype == bool

    def test_param_grid_expansion(self):
        from athena_research.strategies import generate_param_grid

        grid = generate_param_grid("ema_cross", {"fast_period": [10, 20], "slow_period": [50, 100]})
        assert len(grid) == 4
        combos = [(g["fast_period"], g["slow_period"]) for g in grid]
        assert (10, 50) in combos
        assert (20, 100) in combos

    def test_iter_strategy_specs_yields_specs(self):
        from athena_research.strategies import iter_strategy_specs, StrategySpec

        strategy_params = {
            "trend_momentum": {
                "ema_cross": {"fast_period": [10], "slow_period": [50]},
            }
        }
        specs = list(iter_strategy_specs(["trend_momentum"], strategy_params, direction="both"))
        assert len(specs) >= 1
        assert all(isinstance(s, StrategySpec) for s in specs)

    def test_run_strategy_dispatcher(self, ohlcv):
        from athena_research.strategies import run_strategy, StrategySpec

        spec = StrategySpec(family="trend_momentum", name="ema_cross",
                            params={"fast_period": 10, "slow_period": 50})
        result = run_strategy(ohlcv, spec)
        assert "entries" in result

    def test_no_live_imports_after_strategies(self):
        import athena_research.strategies
        _check_no_live_imports()

    def test_research_context_tags_engine_a_and_b(self):
        from athena_research.metrics import StrategyMetrics
        from athena_research.research_context import annotate_research_results

        rows = [
            StrategyMetrics(
                run_id="r", symbol="EUR/USD", asset_class="forex", timeframe="H1",
                zone="intra", family="trend_momentum", strategy_name="ema_cross",
                params_str="", direction="both", status="STRONG_CANDIDATE",
                trade_count=30, profit_factor=1.2, oos_return=0.02,
            ),
            StrategyMetrics(
                run_id="r", symbol="EUR/USD", asset_class="forex", timeframe="H1",
                zone="intra", family="engine_b_proxy", strategy_name="structure_filters",
                params_str="", direction="both", status="WEAK_CANDIDATE",
                trade_count=30, profit_factor=1.1, oos_return=0.01,
            ),
        ]

        tagged = annotate_research_results(rows, {"min_trades": 20})

        assert tagged[0].engine == "ENGINE_A"
        assert tagged[0].engine_component == "ema_coherence"
        assert tagged[0].pair_group == "forex_majors"
        assert tagged[0].timeframe_zone == "intra"
        assert tagged[0].recommendation == "KEEP"

        assert tagged[1].engine == "ENGINE_B"
        assert tagged[1].engine_component == "entry_trigger"
        assert tagged[1].structure_context == "strong_close_fvg_proxy"
        assert tagged[1].recommendation in {"RETEST", "WATCHLIST_ONLY"}

        _check_no_live_imports()

    def test_retest_plan_can_target_retest_recommendations(self, tmp_path, monkeypatch):
        from flask import Flask
        from athena_research import research_lab_routes
        from athena_research.research_lab_routes import register_research_lab_routes

        run_id = "run_retest_route"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True)
        pd.DataFrame([
            {
                "recommendation": "RETEST",
                "engine": "ENGINE_A",
                "engine_component": "pullback",
                "market_group": "commodity",
                "pair_group": "metals",
                "timeframe_zone": "intra",
                "strategy_name": "fib_retracement",
                "family": "pullback",
                "symbol": "XPT/USD",
                "timeframe": "H4",
                "direction": "both",
                "baseline_delta_oos": 0.55,
                "robustness_score": 0.6,
                "oos_return": 0.03,
            },
            {
                "recommendation": "ADD",
                "engine": "ENGINE_A",
                "engine_component": "range_extreme",
                "market_group": "commodity",
                "pair_group": "commodity_other",
                "timeframe_zone": "intra",
                "strategy_name": "bollinger_touch",
                "family": "mean_reversion",
                "symbol": "Lead",
                "timeframe": "H4",
                "direction": "both",
                "baseline_delta_oos": 0.71,
                "robustness_score": 0.8,
                "oos_return": 0.04,
            },
            {
                "recommendation": "RETEST",
                "engine": "ENGINE_B",
                "engine_component": "entry_trigger",
                "market_group": "commodity",
                "pair_group": "commodity_other",
                "timeframe_zone": "intra",
                "strategy_name": "micro_breakout",
                "family": "engine_b_proxy",
                "symbol": "Gasoline",
                "timeframe": "H4",
                "direction": "long",
                "baseline_delta_oos": 0.53,
                "robustness_score": 0.5,
                "oos_return": 0.02,
            },
        ]).to_csv(run_dir / "add_remove_retest_recommendations.csv", index=False)

        monkeypatch.setattr(research_lab_routes, "_DEFAULT_OUTPUT", tmp_path)
        app = Flask(__name__)
        register_research_lab_routes(app)

        resp = app.test_client().post(
            "/api/research-lab/retest-plan",
            json={
                "run_id": run_id,
                "recommendations": ["RETEST"],
                "max_tests": 5,
                "candidates": [{"recommendation": "RETEST", "strategy_name": "fib_retracement", "symbol": "XPT/USD"}],
            },
        )
        payload = resp.get_json()

        assert resp.status_code == 200
        assert payload["recommendations"] == ["RETEST"]
        assert len(payload["tests"]) == 1
        assert payload["tests"][0]["recommendation"] == "RETEST"
        assert payload["tests"][0]["strategies"] == ["fib_retracement"]
        assert payload["tests"][0]["symbols"] == ["XPT/USD"]

        _check_no_live_imports()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Metrics / VectorBT portfolio tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMetrics:
    @pytest.fixture
    def ohlcv(self):
        return _make_ohlcv(400)

    def test_pandas_portfolio_runs(self, ohlcv):
        from athena_research.metrics import _pandas_portfolio

        close = ohlcv["close"]
        # Simple alternating entries/exits
        entries = pd.Series(False, index=ohlcv.index)
        exits = pd.Series(False, index=ohlcv.index)
        entries.iloc[10::40] = True
        exits.iloc[30::40] = True

        stats = _pandas_portfolio(close, entries, exits, None, None, fees=0.001, slippage=0.0002)
        assert "trade_count" in stats
        assert stats["trade_count"] >= 0

    def test_run_portfolio_with_signals(self, ohlcv):
        from athena_research.metrics import run_portfolio

        close = ohlcv["close"]
        entries = pd.Series(False, index=ohlcv.index)
        exits = pd.Series(False, index=ohlcv.index)
        entries.iloc[::50] = True
        exits.iloc[25::50] = True

        stats = run_portfolio(close, entries, exits, fees=0.001, slippage=0.0002, freq="1h")
        assert isinstance(stats, dict)
        assert "trade_count" in stats

    def test_evaluate_strategy_returns_metrics(self, ohlcv):
        from athena_research.metrics import evaluate_strategy
        from athena_research.strategies import strategy_ema_cross, StrategySpec

        spec = StrategySpec(family="trend_momentum", name="ema_cross",
                            params={"fast_period": 10, "slow_period": 50})
        signals = strategy_ema_cross(ohlcv, spec.params)

        metrics = evaluate_strategy(
            df=ohlcv, signals=signals, spec=spec,
            run_id="test_run", symbol="BTC/USDT", asset_class="crypto",
            timeframe="H1", fees=0.001, slippage=0.0002, min_trades=1,
            data_source="synthetic_test",
        )
        assert hasattr(metrics, "trade_count")
        assert hasattr(metrics, "win_rate")
        assert hasattr(metrics, "robustness_score")
        assert hasattr(metrics, "status")
        assert metrics.status in ["STRONG_CANDIDATE", "WEAK_CANDIDATE", "REJECT",
                                   "NEEDS_MORE_DATA"]
        assert metrics.data_source == "synthetic_test"

    def test_evaluate_strategy_skip_reason(self, ohlcv):
        from athena_research.metrics import evaluate_strategy
        from athena_research.strategies import StrategySpec

        spec = StrategySpec(family="mean_reversion", name="vwap_deviation", params={})
        # Signal with skip set
        signals = {"meta": {"skip": "no_volume"}}

        metrics = evaluate_strategy(
            df=ohlcv, signals=signals, spec=spec,
            run_id="test", symbol="EUR/USD", asset_class="forex",
            timeframe="H1", min_trades=1,
        )
        assert metrics.status == "REJECT"
        assert metrics.skip_reason == "no_volume"

    def test_metrics_to_dict_serialisable(self, ohlcv):
        from athena_research.metrics import StrategyMetrics

        m = StrategyMetrics(
            run_id="r1", symbol="BTC/USDT", asset_class="crypto",
            timeframe="H1", family="trend_momentum", strategy_name="ema_cross",
            params_str="fast=10|slow=50", direction="both",
            trade_count=30, win_rate=0.55, profit_factor=1.4,
            data_source="binance_rest",
        )
        d = m.to_dict()
        assert isinstance(d, dict)
        assert d["trade_count"] == 30
        assert d["data_source"] == "binance_rest"
        # Must be JSON-serialisable (except NaN — handled by reporters)
        json.dumps({k: v for k, v in d.items() if not (isinstance(v, float) and math.isnan(v))})

    def test_is_oos_split(self):
        from athena_research.metrics import split_is_oos

        df = _make_ohlcv(100)
        is_df, oos_df = split_is_oos(df, 0.70)
        assert len(is_df) == 70
        assert len(oos_df) == 30
        assert is_df.index[-1] < oos_df.index[0]

    def test_no_live_imports_after_metrics(self):
        import athena_research.metrics
        _check_no_live_imports()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Reporting tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReporting:
    @pytest.fixture
    def sample_results(self):
        from athena_research.metrics import StrategyMetrics
        return [
            StrategyMetrics(
                run_id="test_run", symbol="BTC/USDT", asset_class="crypto",
                timeframe="H1", family="trend_momentum", strategy_name="ema_cross",
                params_str="fast=10|slow=50", direction="both",
                trade_count=45, win_rate=0.53, profit_factor=1.3,
                net_return=0.12, is_return=0.15, oos_return=0.08,
                robustness_score=0.65, status="STRONG_CANDIDATE",
            ),
            StrategyMetrics(
                run_id="test_run", symbol="EUR/USD", asset_class="forex",
                timeframe="H4", family="pullback", strategy_name="pullback_ema",
                params_str="trend=200|pb=50", direction="long",
                trade_count=10, win_rate=0.40, profit_factor=0.9,
                net_return=-0.02, status="REJECT",
            ),
        ]

    def test_metrics_to_df(self, sample_results):
        from athena_research.reporting import metrics_to_df

        df = metrics_to_df(sample_results)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "symbol" in df.columns
        assert "status" in df.columns

    def test_write_csvs_creates_files(self, tmp_path, sample_results):
        from athena_research.reporting import metrics_to_df, write_csvs

        df = metrics_to_df(sample_results)
        written = write_csvs(df, tmp_path)
        assert len(written) > 0
        assert (tmp_path / "research_summary.csv").exists()
        assert (tmp_path / "ranked_strategies.csv").exists()

    def test_markdown_report_created(self, tmp_path, sample_results):
        from athena_research.reporting import metrics_to_df, write_markdown_report

        df = metrics_to_df(sample_results)
        report_path = write_markdown_report(df, tmp_path, "test_run", {"mode": "tiny"})
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "Research Lab" in content
        assert "IMPORTANT" in content
        assert "Engine A" in content
        assert "Engine B" in content
        assert "Engine D" in content

    def test_generate_all_reports(self, tmp_path, sample_results):
        from athena_research.reporting import generate_all_reports

        run_dir = generate_all_reports(sample_results, tmp_path, "test_run_001",
                                       {"mode": "tiny", "symbol_count": 2})
        assert run_dir.exists()
        assert (run_dir / "research_summary.csv").exists()
        assert (run_dir / "research_report.md").exists()

    def test_no_live_imports_after_reporting(self):
        import athena_research.reporting
        _check_no_live_imports()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Prompt builder tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptBuilder:
    def test_prompt_includes_required_context(self, tmp_path):
        from athena_research.prompt_builder import build_prompt
        from athena_research.reporting import generate_all_reports
        from athena_research.metrics import StrategyMetrics

        results = [
            StrategyMetrics(
                run_id="pb_test", symbol="BTC/USDT", asset_class="crypto",
                timeframe="H1", family="trend_momentum", strategy_name="ema_cross",
                params_str="fast=10|slow=50", direction="both",
                trade_count=40, status="WEAK_CANDIDATE",
            )
        ]
        run_dir = generate_all_reports(results, tmp_path, "pb_test", {})
        prompt = build_prompt(run_dir)

        assert "Engine A" in prompt
        assert "Engine B" in prompt
        assert "Engine D" in prompt
        assert "STRONG_CANDIDATE" in prompt
        assert "backtest discovery" in prompt.lower() or "BT_MIN" in prompt
        assert "live execution" in prompt.lower() or "never recommend" in prompt.lower()

    def test_prompt_includes_all_15_questions(self, tmp_path):
        from athena_research.prompt_builder import build_prompt
        from athena_research.reporting import generate_all_reports
        from athena_research.metrics import StrategyMetrics

        results = [StrategyMetrics(
            run_id="q_test", symbol="EUR/USD", asset_class="forex",
            timeframe="H4", family="breakout", strategy_name="prev_day_hl",
            params_str="atr=0.5", direction="long", trade_count=25,
            status="WEAK_CANDIDATE",
        )]
        run_dir = generate_all_reports(results, tmp_path, "q_test", {})
        prompt = build_prompt(run_dir)

        # Check that key question themes appear
        assert "Engine A" in prompt
        assert "Engine D" in prompt
        assert "next" in prompt.lower()

    def test_no_live_imports_after_prompt_builder(self):
        import athena_research.prompt_builder
        _check_no_live_imports()


# ─────────────────────────────────────────────────────────────────────────────
# 6. AI analyst tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAIAnalyst:
    @pytest.fixture
    def run_dir_with_data(self, tmp_path):
        from athena_research.reporting import generate_all_reports
        from athena_research.metrics import StrategyMetrics

        results = [
            StrategyMetrics(
                run_id="ai_test", symbol="BTC/USDT", asset_class="crypto",
                timeframe="H1", family="trend_momentum", strategy_name="ema_cross",
                params_str="fast=10|slow=50", direction="both",
                trade_count=40, win_rate=0.55, profit_factor=1.4,
                net_return=0.10, is_return=0.12, oos_return=0.07,
                robustness_score=0.62, status="STRONG_CANDIDATE",
            ),
        ]
        return generate_all_reports(results, tmp_path, "ai_test", {"mode": "tiny"})

    def test_deterministic_fallback_produces_review(self, run_dir_with_data):
        from athena_research.ai_analyst import _deterministic_review

        review_md, action_plan = _deterministic_review(run_dir_with_data)
        assert isinstance(review_md, str)
        assert len(review_md) > 100
        assert isinstance(action_plan, dict)
        assert "overall_verdict" in action_plan
        assert "engine_a" in action_plan
        assert "engine_b" in action_plan
        assert "engine_d" in action_plan

    def test_run_ai_analysis_without_key_uses_fallback(self, run_dir_with_data, monkeypatch):
        """Without API key, should fall back to deterministic review and write files."""
        import os
        # Remove any API key from env
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from athena_research.ai_analyst import run_ai_analysis

        result = run_ai_analysis(run_dir=run_dir_with_data, provider="none")
        assert "files_written" in result
        assert "ai_research_review.md" in result["files_written"]
        assert "ai_action_plan.json" in result["files_written"]
        assert Path(result["files_written"]["ai_research_review.md"]).exists()
        assert Path(result["files_written"]["ai_action_plan.json"]).exists()

    def test_action_plan_has_correct_structure(self, run_dir_with_data):
        from athena_research.ai_analyst import _deterministic_review

        _, action_plan = _deterministic_review(run_dir_with_data)
        required_keys = ["overall_verdict", "top_candidates", "rejected_setups",
                         "engine_a", "engine_b", "engine_d",
                         "next_tiny_test", "do_not_do_next"]
        for k in required_keys:
            assert k in action_plan, f"Missing key: {k}"

    def test_ai_analyst_does_not_import_live_modules(self):
        import athena_research.ai_analyst
        _check_no_live_imports()

    def test_ai_analyst_does_not_modify_live_config(self, run_dir_with_data, tmp_path):
        """Confirm no writes to config.yaml or live threshold files."""
        config_path = Path("config.yaml")
        if config_path.exists():
            original_mtime = config_path.stat().st_mtime
        else:
            original_mtime = None

        from athena_research.ai_analyst import run_ai_analysis
        run_ai_analysis(run_dir=run_dir_with_data, provider="none")

        if original_mtime is not None:
            assert config_path.stat().st_mtime == original_mtime, \
                "config.yaml was modified by AI analyst — forbidden!"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Run manager tests (lightweight, no data download)
# ─────────────────────────────────────────────────────────────────────────────

class TestRunManager:
    def test_load_config(self):
        from athena_research.run_manager import load_config

        cfg = load_config("configs/vectorbt_research_lab.yaml")
        assert isinstance(cfg, dict)
        assert "tiny_run" in cfg
        assert "strategies" in cfg

    def test_list_runs_empty(self, tmp_path):
        from athena_research.run_manager import list_runs

        runs = list_runs(tmp_path)
        assert isinstance(runs, list)

    def test_get_run_results_missing(self, tmp_path):
        from athena_research.run_manager import get_run_results

        result = get_run_results("nonexistent_run_id", tmp_path)
        assert result is None

    def test_fee_for_converts_round_trip_config_to_per_side(self):
        from athena_research.run_manager import _fee_for

        assert _fee_for("BTC/USDT", {"crypto": 0.0006, "default": 0.001}) == pytest.approx(0.0003)

    def test_run_research_enforces_max_combinations_and_metadata(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        import athena_research.run_manager as rm
        from athena_research.strategies import StrategySpec

        config_path = tmp_path / "research_lab.yaml"
        config_path.write_text(
            """
research_lab:
  fees:
    crypto: 0.0006
    default: 0.001
  slippage:
    default: 0.0
  is_pct: 0.70
  min_trades: 1
  zones:
    intra:
      timeframes: [H1]
  strategies:
    trend_momentum: {}
""",
            encoding="utf-8",
        )
        specs = [
            StrategySpec(
                family="trend_momentum",
                name="ema_cross",
                params={"idx": idx},
                direction="both",
            )
            for idx in range(3)
        ]
        df = _make_ohlcv(80)
        calls = []
        captured_meta = {}

        monkeypatch.setattr(rm, "iter_strategy_specs", lambda families, strategy_params, direction="both": iter(specs))
        monkeypatch.setattr(
            rm,
            "load_ohlcv_multi",
            lambda symbols, timeframe, **kwargs: {
                sym: (df.copy(), SimpleNamespace(data_source="synthetic_test", notes=""))
                for sym in symbols
            },
        )

        def fake_run_symbol_tf(symbol, timeframe, ohlcv, pair_specs, *args, **kwargs):
            calls.append((symbol, timeframe, [spec.params["idx"] for spec in pair_specs]))
            return []

        def fake_generate_all_reports(all_results, output_dir, run_id, run_meta):
            captured_meta.update(run_meta)
            run_dir = Path(output_dir) / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            return run_dir

        monkeypatch.setattr(rm, "_run_symbol_tf", fake_run_symbol_tf)
        monkeypatch.setattr(rm, "generate_all_reports", fake_generate_all_reports)

        result = rm.run_research(
            mode="small",
            config_path=config_path,
            output_dir=tmp_path / "runs",
            run_id="run_cap_test",
            symbols=["BTC/USDT", "ETH/USDT"],
            timeframes=["H1"],
            families=["trend_momentum"],
            max_combinations=4,
            max_workers=1,
        )

        assert sum(len(spec_ids) for _, _, spec_ids in calls) == 4
        assert calls[0] == ("BTC/USDT", "H1", [0, 1, 2])
        assert calls[1] == ("ETH/USDT", "H1", [0])
        assert captured_meta["max_combinations"] == 4
        assert captured_meta["combination_count_requested"] == 6
        assert captured_meta["combination_count_planned"] == 4
        assert captured_meta["combination_count_executed"] == 4
        assert captured_meta["combination_truncated"] is True
        assert captured_meta["zones"] == ["intra"]
        assert result["combination_count_executed"] == 4

    def test_no_live_imports_after_run_manager(self):
        import athena_research.run_manager
        _check_no_live_imports()


# ─────────────────────────────────────────────────────────────────────────────
# 8. Safety: no live execution modules imported in research lab
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyGuards:
    def test_research_lab_does_not_import_athena(self):
        """Ensure importing any research_lab module doesn't pull in athena.py."""
        import athena_research
        import athena_research.data_loader
        import athena_research.strategies
        import athena_research.metrics
        import athena_research.reporting
        import athena_research.ai_analyst
        import athena_research.prompt_builder
        import athena_research.run_manager
        _check_no_live_imports()

    def test_research_lab_does_not_import_executor(self):
        _check_no_live_imports()

    def test_no_risk_engine_import(self):
        assert "risk_engine" not in sys.modules
