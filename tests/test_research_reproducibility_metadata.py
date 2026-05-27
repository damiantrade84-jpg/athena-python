from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def tmp_path():
    d = Path(tempfile.mkdtemp(prefix="athena_repro_test_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _ohlcv(rows: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=rows, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(rows)],
            "high": [101.0 + i for i in range(rows)],
            "low": [99.0 + i for i in range(rows)],
            "close": [100.5 + i for i in range(rows)],
            "volume": [1000.0 + i for i in range(rows)],
        },
        index=idx,
    )


def test_hash_ohlcv_frame_is_stable_for_identical_candles() -> None:
    from athena_research.reproducibility import hash_ohlcv_frame

    first = _ohlcv()
    second = _ohlcv()

    assert hash_ohlcv_frame(first) == hash_ohlcv_frame(second)

    changed = second.copy()
    changed.iloc[-1, changed.columns.get_loc("close")] += 0.01
    assert hash_ohlcv_frame(first) != hash_ohlcv_frame(changed)


def test_git_metadata_degrades_when_git_unavailable(tmp_path) -> None:
    from athena_research.reproducibility import collect_git_metadata

    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    meta = collect_git_metadata(tmp_path, run_cmd=missing_git)

    assert meta["git_commit"] is None
    assert meta["git_branch"] is None
    assert meta["git_dirty"] is None
    assert meta["git_diff_hash"] is None
    assert meta["unavailable_reason"] == "git_unavailable"


def test_dirty_git_hash_is_deterministic(tmp_path) -> None:
    from athena_research.reproducibility import collect_git_metadata

    responses = {
        ("git", "rev-parse", "HEAD"): "abc123\n",
        ("git", "branch", "--show-current"): "main\n",
        ("git", "status", "--porcelain"): " M athena_research/run_manager.py\n",
        ("git", "diff", "--binary", "HEAD"): "diff --git a/x b/x\n+changed\n",
    }

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=responses[tuple(args)], stderr="")

    first = collect_git_metadata(tmp_path, run_cmd=fake_run)
    second = collect_git_metadata(tmp_path, run_cmd=fake_run)

    assert first["git_dirty"] is True
    assert first["git_diff_hash"] == second["git_diff_hash"]
    assert len(first["git_diff_hash"]) == 64


def test_config_hashes_are_deterministic(tmp_path) -> None:
    from athena_research.reproducibility import hash_file_bytes, hash_stable_json

    cfg_path = tmp_path / "research.yaml"
    cfg_path.write_text("research_lab:\n  version: '1.0'\n  min_trades: 20\n", encoding="utf-8")

    assert hash_file_bytes(cfg_path) == hash_file_bytes(cfg_path)
    assert hash_stable_json({"b": 2, "a": [1, 3]}) == hash_stable_json({"a": [1, 3], "b": 2})


def test_strategy_registry_fingerprint_changes_with_specs() -> None:
    from athena_research.reproducibility import build_strategy_registry_metadata
    from athena_research.strategies import StrategySpec

    specs_a = [StrategySpec("trend_momentum", "ema_cross", {"fast_period": 10}, "both")]
    specs_b = [StrategySpec("trend_momentum", "ema_cross", {"fast_period": 11}, "both")]

    meta_a = build_strategy_registry_metadata(specs_a)
    meta_b = build_strategy_registry_metadata(specs_b)

    assert meta_a["registry_hash"] == build_strategy_registry_metadata(specs_a)["registry_hash"]
    assert meta_a["family_map_hash"] == build_strategy_registry_metadata(specs_a)["family_map_hash"]
    assert meta_a["strategy_specs_hash"] != meta_b["strategy_specs_hash"]
    assert meta_a["formula_source"] == "athena_research.strategies:pandas_native"


def test_cache_hit_preserves_source_fetch_timestamp_and_updates_loaded_at(tmp_path, monkeypatch) -> None:
    import athena_research.data_loader as dl

    df = _ohlcv()
    key = "BTC_USDT_H1"
    (tmp_path / f"{key}.parquet").write_text("placeholder", encoding="utf-8")
    (tmp_path / f"{key}.provenance.json").write_text(
        json.dumps(
            {
                "symbol": "BTC/USDT",
                "timeframe": "H1",
                "data_source": "binance_rest",
                "candle_count": len(df),
                "missing_candle_count": 0,
                "start_time": df.index[0].isoformat(),
                "end_time": df.index[-1].isoformat(),
                "timezone": "UTC",
                "cached": False,
                "source_fetch_timestamp": "2024-01-01T00:00:00+00:00",
                "loaded_at": "2024-01-01T00:05:00+00:00",
                "data_hash": "old_hash",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(pd, "read_parquet", lambda *_args, **_kwargs: df)
    monkeypatch.setattr(dl, "_available_parquet_engine", lambda: "test")

    loaded_df, prov = dl._load_cache(tmp_path, key, "H1")

    assert loaded_df.equals(df)
    assert prov.cached is True
    assert prov.source_fetch_timestamp == "2024-01-01T00:00:00+00:00"
    assert prov.fetch_timestamp == "2024-01-01T00:00:00+00:00"
    assert prov.loaded_at != "2024-01-01T00:05:00+00:00"


def test_load_ohlcv_cache_hit_provenance_matches_limited_frame(tmp_path, monkeypatch) -> None:
    import athena_research.data_loader as dl

    df = _ohlcv(6)
    source_fetch_timestamp = "2024-01-01T00:00:00+00:00"
    cached_prov = dl.DataProvenance(
        symbol="BTC/USDT",
        timeframe="H1",
        data_source="binance_rest",
        candle_count=len(df),
        missing_candle_count=0,
        start_time=df.index[0].isoformat(),
        end_time=df.index[-1].isoformat(),
        timezone="UTC",
        cached=True,
        source_fetch_timestamp=source_fetch_timestamp,
        loaded_at="2024-01-02T00:00:00+00:00",
        data_hash="full_cache_hash",
        cache_key="BTC_USDT_H1",
        limit=6,
    )

    monkeypatch.setattr(dl, "_load_cache", lambda *_args, **_kwargs: (df, cached_prov))

    loaded, prov = dl.load_ohlcv("BTC/USDT", "H1", cache_dir=tmp_path, limit=3)

    assert len(loaded) == 3
    assert prov.source_fetch_timestamp == source_fetch_timestamp
    assert prov.candle_count == 3
    assert prov.start_time == df.index[-3].isoformat()
    assert prov.end_time == df.index[-1].isoformat()
    assert prov.limit == 3
    assert prov.data_hash != "full_cache_hash"


def test_run_research_persists_schema_v2_reproducibility_blocks(tmp_path, monkeypatch) -> None:
    import athena_research.run_manager as rm
    from athena_research.data_loader import DataProvenance
    from athena_research.strategies import StrategySpec

    cfg_path = tmp_path / "research_lab.yaml"
    cache_dir = tmp_path / "cache"
    cfg_path.write_text(
        f"""
research_lab:
  version: "1.0"
  fees:
    crypto: 0.0006
    default: 0.001
  slippage:
    default: 0.0002
  is_pct: 0.70
  min_trades: 1
  zones:
    intra:
      timeframes: [H1]
  data:
    cache_dir: {cache_dir.as_posix()}
    ALLOW_YFINANCE_FALLBACK: false
    max_bars:
      H1: 25
  symbols:
    crypto: [BTC/USDT]
  tiny_run:
    symbols: [BTC/USDT]
    timeframes: [H1]
    families: [trend_momentum]
  strategies:
    trend_momentum:
      ema_cross:
        fast_period: [10]
        slow_period: [20]
""",
        encoding="utf-8",
    )
    df = _ohlcv(80)
    prov = DataProvenance(
        symbol="BTC/USDT",
        timeframe="H1",
        data_source="binance_rest",
        candle_count=len(df),
        missing_candle_count=0,
        start_time=df.index[0].isoformat(),
        end_time=df.index[-1].isoformat(),
        timezone="UTC",
        cached=False,
        source_fetch_timestamp="2024-01-01T00:00:00+00:00",
        loaded_at="2024-01-01T00:01:00+00:00",
        data_hash="fixture_hash",
        provider_symbol="BTCUSDT",
        provider_interval="1h",
        provider_endpoint_family="binance_futures_klines",
        cache_key="BTC_USDT_H1",
        limit=25,
    )

    monkeypatch.setattr(
        rm,
        "iter_strategy_specs",
        lambda *args, **kwargs: iter([StrategySpec("trend_momentum", "ema_cross", {"fast_period": 10}, "both")]),
    )
    monkeypatch.setattr(rm, "load_ohlcv_multi", lambda *args, **kwargs: {"BTC/USDT": (df, prov)})
    monkeypatch.setattr(rm, "_run_symbol_tf", lambda *args, **kwargs: [])

    result = rm.run_research(
        mode="tiny",
        config_path=cfg_path,
        output_dir=tmp_path / "runs",
        run_id="run_repro_schema",
        max_workers=1,
    )

    meta = json.loads((Path(result["run_dir"]) / "run_meta.json").read_text(encoding="utf-8"))

    assert meta["schema_version"] == 2
    for block in ["code", "config", "environment", "costs", "data", "strategy_registry", "session_calendar"]:
        assert block in meta
    assert meta["config"]["config_path"] == str(cfg_path)
    assert len(meta["config"]["config_hash"]) == 64
    assert len(meta["config"]["effective_config_hash"]) == 64
    assert meta["costs"]["fees_config"]["crypto"] == 0.0006
    assert meta["costs"]["per_side_fee_by_asset_class"]["crypto"] == 0.0003
    assert meta["data"]["datasets"][0]["data_hash"] == "fixture_hash"
    assert meta["data"]["datasets"][0]["provider_symbol"] == "BTCUSDT"
    assert meta["data"]["candle_policy"]["providers"]["binance_rest"]["intervals"]["H1"] == "1h"
    assert meta["strategy_registry"]["formula_source"] == "athena_research.strategies:pandas_native"


def test_list_runs_reads_old_and_schema_v2_metadata(tmp_path) -> None:
    from athena_research.run_manager import list_runs

    old_dir = tmp_path / "run_old"
    old_dir.mkdir()
    (old_dir / "run_meta.json").write_text(json.dumps({"run_id": "run_old", "mode": "tiny"}), encoding="utf-8")

    new_dir = tmp_path / "run_new"
    new_dir.mkdir()
    (new_dir / "run_meta.json").write_text(
        json.dumps({"schema_version": 2, "run_id": "run_new", "mode": "tiny", "code": {}}),
        encoding="utf-8",
    )

    runs = {row["run_id"]: row for row in list_runs(tmp_path)}

    assert runs["run_old"]["mode"] == "tiny"
    assert runs["run_new"]["schema_version"] == 2
