"""Standalone ASE backtest path."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest


def test_backtest_pair_ase_uses_ase_engine_type():
    import backtest_runner

    pair = {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"}
    trades = [{"resultR": 1.0, "date": "2024-01-01T00:00:00+00:00"}]
    result = backtest_runner._format_backtest_results(trades, pair, engine_type="ASE")
    assert result["engine"] == "ASE"
    assert result["totalTrades"] == 1


def test_ase_backtest_config_defaults():
    from config import CONFIG

    assert CONFIG.get("ASE_BT_ENABLED") is True
    assert int(CONFIG.get("ASE_BT_LOOKBACK_DAYS", 0) or 0) >= 30


def test_ase_backtest_resolves_yahoo_forex_symbols_from_all_pairs():
    import athena_ase.backtest as ase_backtest

    eur_gbp = ase_backtest._resolve_instrument(
        {"symbol": "EURGBP=X", "display": "EUR/GBP", "type": "forex"}
    )
    usd_chf = ase_backtest._resolve_instrument(
        {"symbol": "USDCHF=X", "display": "USD/CHF", "type": "forex"}
    )

    assert eur_gbp is not None
    assert eur_gbp.symbol == "EURGBP"
    assert usd_chf is not None
    assert usd_chf.symbol == "USDCHF"


def test_ase_empty_backtest_message_names_ase_engine():
    import backtest_runner

    pair = {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"}
    result = backtest_runner._format_backtest_results([], pair, engine_type="ASE")

    assert result["error"] == "No signals generated for BTC/USDT in ASE mode"


def test_run_ase_pair_backtest_returns_simulated_barrier_trade_fields(monkeypatch):
    from athena_ase.signals.arbitrate import Candidate
    import athena_ase.backtest as ase_backtest
    import athena_ase.inference.predict as predict_mod
    import athena_ase.signals.engine as engine_mod
    import athena_research.ase.event_backtest as event_mod

    candidate = Candidate(
        instrument="BTCUSDT",
        family="crypto",
        horizon="intraday",
        decision_time_ms=1_700_000_000_000,
        bar_index=12,
        direction=1,
        sigma_bar=0.01,
        entry_log=4.605170185988092,
    )
    signal = SimpleNamespace(
        decisionStatus="TRADE",
        direction="LONG",
        expectedNetR=0.42,
        probabilityPositive=0.61,
    )
    outcome = SimpleNamespace(
        net_R=1.23456,
        gross_R=1.5,
        cost_R=0.26544,
        mae_R=0.2,
        mfe_R=1.8,
        decision_time_ms=candidate.decision_time_ms,
        direction=1,
        horizon="intraday",
        instrument="BTCUSDT",
        family="crypto",
        exit_reason="target",
        hold_bars=7,
        entry_log=candidate.entry_log,
        sigma_bar=candidate.sigma_bar,
    )

    monkeypatch.setattr(ase_backtest, "PTISStore", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(ase_backtest.time, "time", lambda: 1_700_000_900)
    monkeypatch.setattr(engine_mod, "iter_candidates", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr(predict_mod, "predict_batch", lambda *_args, **_kwargs: [signal])
    monkeypatch.setattr(event_mod, "simulate_candidate", lambda *_args, **_kwargs: outcome)

    result = ase_backtest.run_ase_pair_backtest(
        {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"},
        horizon="intraday",
        lookback_days=45,
        ptis_root="unused",
    )

    assert result["diagnostics"]["lookbackDays"] == 45
    trade = result["trades"][0]
    assert trade["entry"] == pytest.approx(100.0)
    assert trade["sl"] < trade["entry"]
    assert trade["tp"] == trade["tp1"]
    assert trade["tp"] > trade["entry"]
    assert trade["grossR"] == pytest.approx(1.5)
    assert trade["costR"] == pytest.approx(0.26544)
    assert trade["maeR"] == pytest.approx(0.2)
    assert trade["mfeR"] == pytest.approx(1.8)


def test_backtest_pair_ase_passes_lookback_and_persists_history(monkeypatch, tmp_path):
    import backtest_runner
    from athena_runtime import set_runtime

    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                pair TEXT NOT NULL,
                asset_type TEXT,
                engine TEXT,
                trades INTEGER,
                win_rate REAL,
                profit_factor REAL,
                expectancy REAL,
                sqn REAL,
                sharpe REAL,
                sortino REAL,
                is_score REAL,
                oos_score REAL,
                max_dd_pct REAL,
                bt_min REAL,
                atr_source TEXT,
                notes TEXT
            )
            """
        )

    captured: dict[str, object] = {}

    def fake_run_ase_pair_backtest(pair, *, horizon, lookback_days=None, **_kwargs):
        captured["horizon"] = horizon
        captured["lookback_days"] = lookback_days
        return {
            "trades": [
                {
                    "resultR": 1.0,
                    "date": "2024-01-01T00:00:00+00:00",
                    "direction": "LONG",
                    "horizon": horizon,
                },
                {
                    "resultR": -0.5,
                    "date": "2024-01-02T00:00:00+00:00",
                    "direction": "SHORT",
                    "horizon": horizon,
                },
            ],
            "diagnostics": {
                "candidateCount": 4,
                "tradeCount": 2,
                "signalCounts": {"TRADE": 2},
                "horizons": [horizon],
                "lookbackDays": lookback_days,
                "instrument": pair["symbol"],
                "family": pair["type"],
            },
        }

    monkeypatch.setattr("athena_ase.backtest.run_ase_pair_backtest", fake_run_ase_pair_backtest)
    set_runtime(SimpleNamespace(AUDIT_DB=str(db_path)))

    pair = {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"}
    result = backtest_runner.backtest_pair_ase(pair, horizon="swing", lookback_days=90)

    assert captured == {"horizon": "swing", "lookback_days": 90}
    assert result is not None
    assert result["engine"] == "ASE"
    assert result["aseDiagnostics"]["lookbackDays"] == 90
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT engine, trades, atr_source, notes FROM backtest_results"
        ).fetchone()
    assert row == ("ase", 2, "ASE_PTIS", "horizon=swing;lookback_days=90;candidates=4;trades=2")


def test_backtest_pair_ase_error_response_is_json_visible(monkeypatch):
    import backtest_runner

    def fake_run_ase_pair_backtest(*_args, **_kwargs):
        return {
            "error": "No ASE TRADE outcomes",
            "diagnostics": {"candidateCount": 3, "tradeCount": 0},
        }

    monkeypatch.setattr("athena_ase.backtest.run_ase_pair_backtest", fake_run_ase_pair_backtest)

    pair = {"symbol": "BTCUSDT", "display": "BTC/USDT", "type": "crypto"}
    result = backtest_runner.backtest_pair_ase(pair)

    assert result == {
        "success": False,
        "error": "No ASE TRADE outcomes",
        "aseDiagnostics": {"candidateCount": 3, "tradeCount": 0},
    }
