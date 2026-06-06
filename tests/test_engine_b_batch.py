"""Tests for the Engine B batch orchestrator (engine_b_batch).

These exercise the orchestration/aggregation logic only -- they inject a
synchronous in-process executor and patch ``backtest_pair_naked`` so no
subprocesses or real candle fetches are involved. Per-pair logic is unchanged
and covered by tests/test_backtest_integrity.py.
"""
from concurrent.futures import Future

import backtest_runner
import engine_b_batch


class _InlineExecutor:
    """Runs submitted work synchronously, in-process (no subprocesses)."""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def submit(self, fn, *args, **kwargs):
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f


def _fake_naked(pair, **kwargs):
    # Deterministic per-pair result echoing the params so we can assert pass-through.
    return {
        "pair": pair["display"],
        "engine": "NAKED",
        "totalTrades": 1,
        "sqn": 1.23,
        "_params": kwargs,
    }


def test_batch_matches_direct_serial(monkeypatch):
    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", _fake_naked)
    pairs = [
        {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"},
        {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"},
    ]
    out = engine_b_batch.run_engine_b_batch(
        pairs,
        style="naked",
        validation_mode="standard",
        purge_gap=200,
        folds=3,
        executor_factory=lambda: _InlineExecutor(),
    )
    assert out["success"] is True
    assert out["totalPairs"] == 2
    assert [r["pairKey"] for r in out["rows"]] == ["AAPL", "BTCUSDT"]  # input order preserved
    for r in out["rows"]:
        assert r["ok"] is True
        direct = _fake_naked(
            {"display": r["pairKey"]},
            style="naked",
            validation_mode="standard",
            purge_gap=200,
            folds=3,
        )
        assert r["result"]["_params"] == direct["_params"]
        assert r["result"]["sqn"] == direct["sqn"]


def test_mt5_pairs_run_serially_not_in_pool(monkeypatch):
    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", _fake_naked)
    submitted = []

    class _RecordingExecutor(_InlineExecutor):
        def submit(self, fn, *args, **kwargs):
            submitted.append(args)
            return super().submit(fn, *args, **kwargs)

    pairs = [
        {"display": "EURUSD", "symbol": "EURUSD", "type": "forex", "source": "mt5"},
        {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"},
    ]
    out = engine_b_batch.run_engine_b_batch(
        pairs,
        executor_factory=lambda: _RecordingExecutor(),
    )
    pool_pairs = [a[0][0]["symbol"] for a in submitted]  # args = ((pair, params),)
    assert pool_pairs == ["BTCUSDT"]  # only REST pair went to the pool
    assert {r["pairKey"] for r in out["rows"]} == {"EURUSD", "BTCUSDT"}
    assert all(r["ok"] for r in out["rows"])  # MT5 pair ran serially and succeeded


def test_per_pair_error_isolated(monkeypatch):
    def _boom(pair, **kwargs):
        if pair["symbol"] == "BAD":
            raise RuntimeError("kaboom")
        return {"pair": pair["display"], "totalTrades": 0}

    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", _boom)
    pairs = [
        {"display": "BAD", "symbol": "BAD", "type": "stock", "source": "eodhd"},
        {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"},
    ]
    out = engine_b_batch.run_engine_b_batch(pairs, executor_factory=lambda: _InlineExecutor())
    by_key = {r["pairKey"]: r for r in out["rows"]}
    assert by_key["BAD"]["ok"] is False and "kaboom" in by_key["BAD"]["error"]
    assert by_key["AAPL"]["ok"] is True


def test_none_result_is_failed_row(monkeypatch):
    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", lambda pair, **k: None)
    pairs = [{"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}]
    out = engine_b_batch.run_engine_b_batch(pairs, executor_factory=lambda: _InlineExecutor())
    assert out["rows"][0]["ok"] is False
    assert "Insufficient" in out["rows"][0]["error"]


def test_error_result_dict_is_failed_row(monkeypatch):
    monkeypatch.setattr(
        backtest_runner,
        "backtest_pair_naked",
        lambda pair, **k: {"success": False, "error": "no signals"},
    )
    pairs = [{"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}]
    out = engine_b_batch.run_engine_b_batch(pairs, executor_factory=lambda: _InlineExecutor())
    assert out["rows"][0]["ok"] is False
    assert out["rows"][0]["error"] == "no signals"


def test_single_flight_rejects_concurrent(monkeypatch):
    monkeypatch.setattr(engine_b_batch, "_batch_running", True, raising=False)
    out = engine_b_batch.run_engine_b_batch(
        [{"display": "AAPL", "symbol": "AAPL", "source": "eodhd"}],
        executor_factory=lambda: _InlineExecutor(),
    )
    assert out["success"] is False
    assert "already in progress" in out["error"]


def test_resolve_pairs_empty_means_all_non_jse():
    all_pairs = [
        {"display": "AAPL", "symbol": "AAPL"},
        {"display": "JSE:NPN", "symbol": "NPN"},
    ]
    jse = [{"display": "JSE:NPN", "symbol": "NPN"}]
    resolved = engine_b_batch.resolve_engine_b_batch_pairs([], all_pairs, jse)
    assert [p["symbol"] for p in resolved] == ["AAPL"]


def test_resolve_pairs_by_token():
    all_pairs = [
        {"display": "AAPL", "symbol": "AAPL"},
        {"display": "BTCUSDT", "symbol": "BTCUSDT"},
    ]
    resolved = engine_b_batch.resolve_engine_b_batch_pairs(["btcusdt"], all_pairs, [])
    assert [p["symbol"] for p in resolved] == ["BTCUSDT"]
