"""OX Book focused tests - pure paths only, no athena.py import."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ox_book import settings
from ox_book.contracts import MarketEvaluation, OxMetrics, OxParams
from ox_book.core import (
    desired_position,
    metrics,
    simulate,
    split_is_oos,
)
from ox_book.evidence import build_book, evaluate_market
from ox_book.significance import (
    TrialRegistry,
    clears_promotion_bar,
    haircut_expectancy,
)


def synth_candles(n=1200, seed=7, drift=0.0006, vol=0.009, start=100.0,
                  start_date="2020-01-01"):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, size=n)
    close = start * np.cumprod(1.0 + rets)
    open_px = np.empty(n)
    open_px[0] = start
    open_px[1:] = close[:-1]
    spread = np.abs(rng.normal(0.0, vol / 2.0, size=n))
    high = np.maximum(open_px, close) * (1.0 + spread)
    low = np.minimum(open_px, close) * (1.0 - spread)
    times = pd.date_range(start_date, periods=n, freq="D")
    return pd.DataFrame(
        {"time": times, "open": open_px, "high": high, "low": low, "close": close}
    )


CANONICAL = OxParams(fast=15, slow=60, atr_n=14, atr_mult=3.0, long_only=True,
                     cost_per_side=0.0002)


@pytest.fixture()
def permissive_gates(monkeypatch):
    monkeypatch.setattr(settings, "min_bars", lambda: 260)
    monkeypatch.setattr(settings, "min_trades", lambda: 3)
    monkeypatch.setattr(settings, "min_edge_quality", lambda: 0.0)
    monkeypatch.setattr(settings, "sqn_floor", lambda: 0.0)
    monkeypatch.setattr(settings, "plateau_min_pass_frac", lambda: 0.0)
    monkeypatch.setattr(settings, "cost_stress_mult", lambda: 1.0)
    monkeypatch.setattr(settings, "min_positive_eras", lambda: 0)


def test_desired_position_is_long_only_binary():
    df = synth_candles(n=400, seed=3)
    pos = desired_position(df, CANONICAL)
    assert set(np.unique(pos)).issubset({0.0, 1.0})


def test_simulate_has_no_lookahead():
    prefix = synth_candles(n=900, seed=11)
    extension = synth_candles(n=300, seed=12, start=float(prefix["close"].iloc[-1]),
                              start_date=str(prefix["time"].iloc[-1] + pd.Timedelta(days=1)))
    extended = pd.concat([prefix, extension], ignore_index=True)
    tr_prefix = simulate(prefix, CANONICAL)
    tr_ext = simulate(extended, CANONICAL)

    def key(tr):
        return (tr.entry_time, tr.exit_time, tr.direction, round(tr.R, 10))

    last_prefix_time = prefix["time"].iloc[-1]
    settled_prefix = [key(t) for t in tr_prefix if t.exit_time < last_prefix_time]
    settled_ext = [
        key(t)
        for t in tr_ext
        if t.exit_time < last_prefix_time and t.entry_time >= prefix["time"].iloc[0]
    ]
    assert settled_prefix == settled_ext
    assert len(settled_prefix) > 0


def test_metrics_on_trending_series():
    df = synth_candles(n=1000, seed=5, drift=0.001)
    m = metrics(simulate(df, CANONICAL))
    assert m.n >= 2
    assert m.sqn100 is not None
    assert m.t_stat is not None
    assert 0.0 < m.win_rate <= 1.0


def test_split_is_oos_partitions_all_trades():
    df = synth_candles(n=1000, seed=21)
    trades = simulate(df, CANONICAL)
    is_tr, oos_tr = split_is_oos(trades)
    assert len(is_tr) + len(oos_tr) == len(trades)
    assert len(oos_tr) > 0


def test_evaluate_market_rejects_short_history():
    ev = evaluate_market("X", synth_candles(n=80, seed=1), CANONICAL)
    assert not ev.qualifies
    assert ev.reasons == ["insufficient_bars"]


def test_evaluate_market_permissive_pass(permissive_gates):
    ev = evaluate_market("TRENDY", synth_candles(n=1100, seed=9, drift=0.0008), CANONICAL)
    assert ev.qualifies, f"reasons={ev.reasons}"
    assert ev.metrics_full.n >= 3
    assert ev.edge_quality is not None


def test_evaluate_market_sqn_floor_gate(permissive_gates, monkeypatch):
    monkeypatch.setattr(settings, "sqn_floor", lambda: 999.0)
    ev = evaluate_market("TRENDY", synth_candles(n=1100, seed=9, drift=0.0008), CANONICAL)
    assert not ev.qualifies
    assert "sqn_below_floor" in ev.reasons


def test_cost_stress_never_beats_base(permissive_gates):
    monkeypatch_free = evaluate_market(
        "TRENDY", synth_candles(n=1100, seed=9, drift=0.0008), CANONICAL
    )
    assert monkeypatch_free.stressed_sqn100 is not None
    assert monkeypatch_free.metrics_full.sqn100 is not None
    assert monkeypatch_free.stressed_sqn100 <= monkeypatch_free.metrics_full.sqn100 + 1e-9


def test_trial_registry_records_and_counts(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        reg = TrialRegistry(path=str(td + "/trials.jsonl"))
        assert reg.count() == 0
        reg.record({"symbol": "A", "t_stat": 1.2})
        reg.record({"symbol": "B", "t_stat": 3.4})
        assert reg.count() == 2


def test_clears_promotion_bar(monkeypatch):
    monkeypatch.setattr(settings, "t_stat_hurdle", lambda: 3.0)
    assert clears_promotion_bar(None) is False
    assert clears_promotion_bar(2.99) is False
    assert clears_promotion_bar(3.01) is True


def test_haircut_expectancy_matches_decay_evidence(monkeypatch):
    monkeypatch.setattr(settings, "decay_haircut", lambda: 0.58)
    assert haircut_expectancy(None) is None
    assert haircut_expectancy(1.0) == pytest.approx(0.42)
    assert haircut_expectancy(0.378) == pytest.approx(0.378 * 0.42)


def _ev(symbol, qualifies, quality, reasons=None):
    return MarketEvaluation(
        symbol=symbol,
        qualifies=qualifies,
        reasons=list(reasons or []),
        edge_quality=quality,
        metrics_full=OxMetrics(n=40),
    )


def test_build_book_correlation_cap_and_rejections():
    same = synth_candles(n=600, seed=42)
    other = synth_candles(n=600, seed=99)
    candles = {"AAA": same, "BBB": same, "CCC": other, "DDD": other}

    evals = [
        _ev("AAA", True, 0.36),
        _ev("BBB", True, 0.34),
        _ev("CCC", True, 0.30),
        _ev("BAD", False, 0.05, reasons=["edge_quality_below_floor"]),
    ]
    verdict = build_book(evals, candles, CANONICAL)
    member_symbols = [m.symbol for m in verdict.members]
    assert member_symbols == ["AAA", "CCC"]
    rejected_map = {r.symbol: r for r in verdict.rejected}
    assert any(r.startswith("correlated_with_AAA") for r in rejected_map["BBB"].reasons)
    assert rejected_map["BAD"].reasons == ["edge_quality_below_floor"]


def test_config_yaml_parses_with_ox_book_block():
    import yaml

    root = os_path_repo_root()
    with open(root + "/config.yaml", "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    ox = cfg.get("OX_BOOK")
    assert isinstance(ox, dict)
    assert ox["EMA_FAST"] == 15
    assert ox["EMA_SLOW"] == 60
    assert ox["ENABLED"] is True
    assert ox["AUTO_EXECUTE"] is False
    assert ox["EXECUTION_MODE"] == "MANUAL_DEMO_ONLY"
    assert ox["SIGNAL_TIMEFRAME"] == "D1"
    assert ox["DECAY_HAIRCUT"] == 0.58


def os_path_repo_root() -> str:
    import os

    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
