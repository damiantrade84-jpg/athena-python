from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def tmp_path():
    d = Path(tempfile.mkdtemp(prefix="athena_fast_gate_lab_test_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


_FORBIDDEN = {"athena", "execution", "auto_trader", "mt5_executor", "bybit_executor"}


def _forbidden_baseline() -> set[str]:
    return {name for name in _FORBIDDEN if name in sys.modules}


def _assert_no_new_forbidden_imports(baseline: set[str]) -> None:
    violations = sorted(name for name in _FORBIDDEN if name in sys.modules and name not in baseline)
    assert violations == []


def _make_ohlcv(days: int = 240, freq: str = "1h", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    periods = days * (24 if freq == "1h" else 6)
    idx = pd.date_range("2025-09-22", periods=periods, freq=freq, tz="UTC")
    drift = np.linspace(0, 0.25, periods)
    close = 100.0 + drift + rng.normal(0, 0.4, periods).cumsum() * 0.03
    high = close + rng.uniform(0.05, 0.4, periods)
    low = close - rng.uniform(0.05, 0.4, periods)
    open_ = close + rng.normal(0, 0.1, periods)
    volume = rng.uniform(1000, 5000, periods)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_fast_gate_lab_imports_do_not_pull_execution_modules():
    baseline = _forbidden_baseline()

    import athena_research.fast_gate_lab  # noqa: F401

    _assert_no_new_forbidden_imports(baseline)


def test_default_threshold_lookup_stays_offline(monkeypatch):
    from athena_research import fast_gate_lab

    monkeypatch.delenv("FAST_GATE_LAB_USE_RUNTIME_RESOLVERS", raising=False)
    for name in ("scoring", "market_structure", "config"):
        sys.modules.pop(name, None)

    engine_a_threshold, pair_group = fast_gate_lab._current_engine_a_threshold("BTC/USDT", "crypto")
    engine_b_threshold = fast_gate_lab._current_engine_b_threshold("intra")

    assert engine_a_threshold == pytest.approx(2.0)
    assert pair_group == "crypto_btc"
    assert engine_b_threshold == pytest.approx(4.0)
    assert "scoring" not in sys.modules
    assert "market_structure" not in sys.modules
    assert "config" not in sys.modules


def test_filter_recent_window_keeps_only_requested_lookback():
    from athena_research.fast_gate_lab import filter_recent_window

    df = _make_ohlcv(days=240)
    filtered = filter_recent_window(df, lookback_days=180)

    assert filtered.index.min() >= df.index.max() - pd.Timedelta(days=180)
    assert filtered.index.max() == df.index.max()
    assert 4300 <= len(filtered) <= 4321


def test_gate_sweep_math_for_engine_a_and_engine_b():
    from athena_research.fast_gate_lab import gate_sweep_values

    assert gate_sweep_values("A", 2.0) == pytest.approx([2.0, 1.8, 1.9, 2.1, 2.2])
    assert gate_sweep_values("B", 4.0) == pytest.approx([4.0, 3.5, 4.5])


def test_engine_labels_distinguish_live_aligned_and_proxy():
    from athena_research.fast_gate_lab import engine_research_label

    assert engine_research_label("A") == "live_aligned_engine_a"
    assert engine_research_label("B") == "proxy_engine_b"


def test_fast_gate_lab_writes_expected_report_files(tmp_path, monkeypatch):
    from athena_research import fast_gate_lab
    from athena_research.data_loader import DataProvenance
    from athena_research.metrics import StrategyMetrics
    from athena_research.strategies import StrategySpec

    df = _make_ohlcv(days=60)
    prov = DataProvenance(
        symbol="BTC/USDT",
        timeframe="H1",
        data_source="synthetic_test",
        candle_count=len(df),
        missing_candle_count=0,
        start_time=df.index[0].isoformat(),
        end_time=df.index[-1].isoformat(),
        timezone="UTC",
        cached=False,
    )

    def fake_load_symbol_frames(config):
        return {("BTC/USDT", "H1"): (df, prov)}

    def fake_specs(config, engine, timeframe):
        if engine == "A":
            return [StrategySpec("trend_momentum", "ema_cross", {"fast_period": 3, "slow_period": 8})]
        return [StrategySpec("engine_b_proxy", "ob_bos", {"lookback": 10})]

    def fake_evaluate_strategy(**kwargs):
        spec = kwargs["spec"]
        return StrategyMetrics(
            run_id=kwargs["run_id"],
            symbol=kwargs["symbol"],
            asset_class="crypto",
            timeframe=kwargs["timeframe"],
            family=spec.family,
            strategy_name=spec.name,
            params_str="x=1",
            direction=spec.direction,
            trade_count=25,
            win_rate=0.56,
            net_return=0.03,
            oos_return=0.01,
            status="STRONG_CANDIDATE",
            data_source="synthetic_test",
        )

    monkeypatch.setattr(fast_gate_lab, "load_symbol_frames", fake_load_symbol_frames)
    monkeypatch.setattr(fast_gate_lab, "_strategy_specs_for_engine", fake_specs)
    monkeypatch.setattr(fast_gate_lab, "_current_engine_a_threshold", lambda symbol, market_group: (2.0, "crypto_majors"))
    monkeypatch.setattr(fast_gate_lab, "_current_engine_b_threshold", lambda style: 4.0)
    monkeypatch.setattr(fast_gate_lab, "evaluate_strategy", fake_evaluate_strategy)

    result = fast_gate_lab.run_fast_gate_lab(
        fast_gate_lab.FastGateConfig(
            output_dir=tmp_path,
            run_id="unit_run",
            market_group="crypto",
            style="intra",
            symbols=("BTC/USDT",),
            timeframes=("H1",),
            engines=("A", "B"),
            lookback_days=30,
            top_n=2,
            max_candidates=4,
        )
    )

    expected = {
        "run_meta.json",
        "gate_matrix.csv",
        "indicator_attribution.csv",
        "pair_summary.csv",
        "top_candidates.csv",
        "screening_details.json",
        "report.html",
    }
    assert expected == {path.name for path in result["files"].values()}
    for name in expected:
        assert (tmp_path / "unit_run" / name).exists(), name

    meta = json.loads((tmp_path / "unit_run" / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["engines"] == ["A", "B"]
    assert meta["read_only"] is True
    assert meta["source_config_values"]["engine_a"]["BTC/USDT"]["threshold"] == 2.0
    assert meta["source_config_values"]["engine_b"]["threshold"] == 4.0
