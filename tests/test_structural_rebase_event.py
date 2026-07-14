"""Tests for the F4 structural-rebase observability flag.

The executors (``mt5_execute`` / ``bybit_execute``) parallel-shift SL/TP when
broker price drifts more than 1% from signal price. For Engine A levels this
is correct (distance preserved). For Engine B / naked structural levels the
shift erases structural meaning, so the executor now emits a
``structuralRebase`` event on the result and — only when the config gate
``ATR_FRESHNESS.BLOCK_STRUCTURAL_REBASE`` is true — refuses the order.

Default behaviour MUST stay unchanged. These tests prove that:
1. ``_is_structural_engine_b_execution`` correctly classifies naked / engine_b
   signals so the rebase event is_structural is accurate.
2. The block flag defaults to False in config.yaml.
"""

from __future__ import annotations

from pathlib import Path


def test_is_structural_engine_b_execution_for_naked_signal():
    """Naked / engine_b signals must be flagged structural so F4 fires."""
    import importlib.util
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    execution_py = repo_root / "execution.py"
    spec = importlib.util.spec_from_file_location("execution_under_test", execution_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["execution_under_test"] = mod
    spec.loader.exec_module(mod)

    naked = {"is_naked": True}
    engine_b = {"engine_b": {"recommended_stop_loss": 1.0}}
    engine_a_aligned = {
        "engine": "engine_a",
        "engine_b": {"structural_verdict": "CLEAR"},
        "verdict": "ALIGNED",
    }
    engine_a_alone = {
        "engine": "engine_a",
        "engine_b": {"structural_verdict": "CLEAR"},
        "verdict": "NO_SIGNAL",
    }

    assert mod._is_structural_engine_b_execution(naked) is True
    assert mod._is_structural_engine_b_execution(engine_b) is True
    assert mod._is_structural_engine_b_execution(engine_a_aligned) is True
    # Engine A row without an executable verdict must NOT be structural.
    assert mod._is_structural_engine_b_execution(engine_a_alone) is False


def test_config_block_structural_rebase_defaults_false():
    """The behavior-change knob must default to false in config.yaml."""
    repo_root = Path(__file__).resolve().parent.parent
    cfg_path = repo_root / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    # The ATR_FRESHNESS block must exist and both gates default to false.
    assert "ATR_FRESHNESS:" in text
    # ENABLED line within ATR_FRESHNESS must default to false.
    atr_block = text.split("ATR_FRESHNESS:")[1].split("\n\n")[0]
    assert "ENABLED: false" in atr_block
    assert "BLOCK_EXECUTION_ON_STALE_ATR: false" in atr_block
    # BLOCK_STRUCTURAL_REBASE is intentionally absent (default false via .get).
    # If it ever ships true by default this test should fail loudly.
    assert "BLOCK_STRUCTURAL_REBASE: true" not in text


def test_engine_b_broker_drift_blocks_before_risk_and_uses_atr(monkeypatch):
    import execution
    import sys
    from types import SimpleNamespace

    ticker_price = {"value": 101.0}
    fake_exchange = SimpleNamespace(
        fetch_ticker=lambda _symbol: {"ask": ticker_price["value"], "bid": ticker_price["value"]}
    )
    fake_bybit = SimpleNamespace(
        _get_exchange=lambda: fake_exchange,
        bybit_map_symbol=lambda _value: "BTC/USDT:USDT",
        _bybit_execution_side_price=lambda ticker, _direction: ticker["ask"],
        _bybit_ticker_age_seconds=lambda _ticker: 0.2,
        _bybit_max_tick_age_sec=lambda: 3.0,
        _fetch_bybit_ticker=lambda _symbol, category: {"timestamp": 1, "category": category},
    )
    monkeypatch.setitem(sys.modules, "bybit_executor", fake_bybit)
    cfg = {
        "MAX_SIGNAL_DRIFT_PCT": {"crypto": 0.05},
        "MAX_SIGNAL_DRIFT_ATR_MULT": {"crypto": 0.25},
    }
    signal = {
        "is_naked": True,
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 100.0,
        "atr": 2.0,
        "sl": 98.0,
        "tp1": 104.0,
    }

    error, diag = execution._engine_b_pre_risk_broker_price(signal, "bybit", cfg)
    assert error == "ENGINE_B_BROKER_DRIFT_TOO_LARGE"
    assert diag["driftAtr"] == 0.5
    assert signal["price"] == 100.0

    ticker_price["value"] = 100.4
    error, diag = execution._engine_b_pre_risk_broker_price(signal, "bybit", cfg)
    assert error is None
    assert signal["signalPriceRef"] == 100.0
    assert signal["price"] == 100.4
    assert signal["tp1"] == 104.0  # structural target remains absolute
    assert signal["quoteAgeSec"] == 0.2
    assert signal["quoteFreshness"]["source"] == "bybit_broker_preflight"


def test_engine_b_broker_preflight_uses_bybit_rest_timestamp_fallback(monkeypatch):
    import execution
    import sys
    from types import SimpleNamespace

    fake_exchange = SimpleNamespace(fetch_ticker=lambda _symbol: {"ask": 100.1, "bid": 100.0})

    def _age(ticker):
        return 0.4 if ticker.get("timestamp") else None

    fake_bybit = SimpleNamespace(
        _get_exchange=lambda: fake_exchange,
        bybit_map_symbol=lambda _value: "BTC/USDT:USDT",
        _bybit_execution_side_price=lambda ticker, _direction: ticker["ask"],
        _bybit_ticker_age_seconds=_age,
        _bybit_max_tick_age_sec=lambda: 3.0,
        _fetch_bybit_ticker=lambda symbol, category: {
            "symbol": symbol,
            "category": category,
            "timestamp": 1_000_000,
        },
    )
    monkeypatch.setitem(sys.modules, "bybit_executor", fake_bybit)
    signal = {
        "is_naked": True,
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 100.0,
        "atr": 2.0,
    }
    cfg = {
        "MAX_SIGNAL_DRIFT_PCT": {"crypto": 0.05},
        "MAX_SIGNAL_DRIFT_ATR_MULT": {"crypto": 0.25},
    }

    error, diag = execution._engine_b_pre_risk_broker_price(signal, "bybit", cfg)

    assert error is None
    assert diag["tickAgeSec"] == 0.4
    assert signal["quoteAgeSec"] == 0.4


def test_engine_b_mt5_drift_uses_midpoint_and_anchors_execution_side(monkeypatch):
    import execution
    import sys
    from types import SimpleNamespace

    tick = SimpleNamespace(ask=101.0, bid=99.0)
    fake_mt5 = SimpleNamespace(symbol_info_tick=lambda _symbol: tick)
    fake_mt5_executor = SimpleNamespace(
        _get_mt5=lambda: fake_mt5,
        _mt5_max_tick_age_sec=lambda: 3.0,
        _mt5_tick_age_seconds=lambda _tick: 0.2,
        mt5_connect=lambda: True,
        mt5_map_symbol=lambda _value: "EURX",
    )
    monkeypatch.setitem(sys.modules, "mt5_executor", fake_mt5_executor)
    signal = {
        "is_naked": True,
        "pair": "EURX",
        "type": "index",
        "direction": "LONG",
        "price": 100.0,
        "atr": 2.0,
    }
    cfg = {
        "MAX_SIGNAL_DRIFT_PCT": {"index": 0.05},
        "MAX_SIGNAL_DRIFT_ATR_MULT": {"index": 0.25},
    }

    error, diag = execution._engine_b_pre_risk_broker_price(signal, "mt5", cfg)

    assert error is None
    assert diag["brokerDriftReferencePrice"] == 100.0
    assert signal["price"] == 101.0


def test_engine_a_pre_risk_refreshes_mt5_quote_for_every_asset_family(monkeypatch):
    import execution
    import sys
    from types import SimpleNamespace

    tick = SimpleNamespace(ask=101.0, bid=99.0)
    fake_mt5 = SimpleNamespace(symbol_info_tick=lambda _symbol: tick)
    fake_mt5_executor = SimpleNamespace(
        _get_mt5=lambda: fake_mt5,
        _mt5_max_tick_age_sec=lambda: 5.0,
        _mt5_tick_age_seconds=lambda _tick: 0.2,
        mt5_connect=lambda: True,
        mt5_map_symbol=lambda value: value,
    )
    monkeypatch.setitem(sys.modules, "mt5_executor", fake_mt5_executor)

    for asset_type in ("forex", "commodity", "index", "stock", "etf", "etf_bond"):
        signal = {
            "engine": "engine_a",
            "verdict": "A_ONLY",
            "pair": "TEST",
            "type": asset_type,
            "direction": "LONG",
            "price": 100.0,
            "entry": 100.0,
        }

        error, diag = execution._engine_b_pre_risk_broker_price(signal, "mt5", {})

        assert error is None
        assert diag["tickAgeSec"] == 0.2
        assert signal["price"] == 100.0
        assert signal["entry"] == 100.0
        assert signal["brokerPreflightPrice"] == 101.0
        assert signal["quoteAgeSec"] == 0.2
        assert signal["quoteFreshness"]["source"] == "mt5_broker_preflight"


def test_engine_a_pre_risk_refreshes_bybit_crypto_quote(monkeypatch):
    import execution
    import sys
    from types import SimpleNamespace

    fake_exchange = SimpleNamespace(fetch_ticker=lambda _symbol: {"ask": 100.2, "bid": 100.1})
    fake_bybit = SimpleNamespace(
        _get_exchange=lambda: fake_exchange,
        bybit_map_symbol=lambda _value: "BTC/USDT:USDT",
        _bybit_execution_side_price=lambda ticker, _direction: ticker["ask"],
        _bybit_ticker_age_seconds=lambda _ticker: 0.3,
        _bybit_max_tick_age_sec=lambda: 3.0,
        _fetch_bybit_ticker=lambda _symbol, category: {"timestamp": 1, "category": category},
    )
    monkeypatch.setitem(sys.modules, "bybit_executor", fake_bybit)
    signal = {
        "engine": "engine_a",
        "verdict": "A_ONLY",
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "price": 100.0,
        "entry": 100.0,
    }

    error, diag = execution._engine_b_pre_risk_broker_price(signal, "bybit", {})

    assert error is None
    assert diag["tickAgeSec"] == 0.3
    assert signal["price"] == 100.0
    assert signal["entry"] == 100.0
    assert signal["brokerPreflightPrice"] == 100.2
    assert signal["quoteFreshness"]["source"] == "bybit_broker_preflight"


def test_engine_a_pre_risk_still_rejects_a_stale_broker_tick(monkeypatch):
    import execution
    import sys
    from types import SimpleNamespace

    tick = SimpleNamespace(ask=2_401.0, bid=2_400.0)
    fake_mt5 = SimpleNamespace(symbol_info_tick=lambda _symbol: tick)
    fake_mt5_executor = SimpleNamespace(
        _get_mt5=lambda: fake_mt5,
        _mt5_max_tick_age_sec=lambda: 5.0,
        _mt5_tick_age_seconds=lambda _tick: 6.0,
        mt5_connect=lambda: True,
        mt5_map_symbol=lambda _value: "XAUUSD",
    )
    monkeypatch.setitem(sys.modules, "mt5_executor", fake_mt5_executor)
    signal = {
        "engine": "engine_a",
        "verdict": "A_ONLY",
        "pair": "XAU/USD",
        "type": "commodity",
        "direction": "LONG",
        "price": 2_400.0,
        "quoteTimestamp": 1.0,
    }

    error, diag = execution._engine_b_pre_risk_broker_price(signal, "mt5", {})

    assert error == "BROKER_TICK_STALE"
    assert diag == {"tickAgeSec": 6.0, "tickAgeLimitSec": 5.0}
    assert signal["quoteTimestamp"] == 1.0
    assert "brokerPreflightPrice" not in signal


def test_reconcile_engine_b_rr_resynthesizes_tp_when_broker_entry_drops_rr():
    import execution

    cfg = {"ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True}
    sig = {
        "is_naked": True,
        "pair": "XAG/USD",
        "type": "commodity",
        "direction": "SHORT",
        "style": "intraday",
        "price": 59.4,
        "entry": 59.4,
        "sl": 60.0,
        "tp1": 58.7,
        "tp2": 58.7,
        "min_rr": 1.3,
        "fallback_rr": 1.8,
        "engine_b_status": {"tp1_source": "structural", "min_rr": 1.3},
    }

    err = execution._reconcile_engine_b_rr_after_broker_entry(sig, cfg)

    assert err is None
    assert sig.get("engine_b_broker_rebase_rr_resynth") is True
    assert sig["tp1"] < 58.7
    new_rr = (59.4 - sig["tp1"]) / (60.0 - 59.4)
    assert new_rr >= 1.3 - 1e-9


def test_reconcile_engine_b_rr_noop_when_rr_still_meets_floor():
    import execution

    cfg = {"ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True}
    sig = {
        "is_naked": True,
        "pair": "XAG/USD",
        "type": "commodity",
        "direction": "SHORT",
        "price": 59.5,
        "sl": 60.0,
        "tp1": 58.7,
        "min_rr": 1.3,
        "engine_b_status": {"tp1_source": "structural"},
    }
    original_tp = sig["tp1"]

    err = execution._reconcile_engine_b_rr_after_broker_entry(sig, cfg)

    assert err is None
    assert sig["tp1"] == original_tp
    assert "engine_b_broker_rebase_rr_resynth" not in sig


def test_reconcile_skips_when_tp_already_synthetic():
    import execution

    cfg = {"ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True}
    sig = {
        "is_naked": True,
        "direction": "SHORT",
        "price": 59.4,
        "sl": 60.0,
        "tp1": 58.7,
        "min_rr": 1.3,
        "engine_b_status": {"tp1_source": "fallback_rr"},
    }

    err = execution._reconcile_engine_b_rr_after_broker_entry(sig, cfg)

    assert err is None
    assert sig["tp1"] == 58.7


def test_reconcile_scale_out_tp2_fallback_does_not_skip_tp1_resynth():
    """Scale-out runner TP2 may be fallback_rr while structural TP1 still needs rebase resynth."""
    import execution

    cfg = {
        "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True,
        "ENGINE_C_EXEC_MIN_RR": 1.0,
        "NAKED_ENGINE": {
            "score_group_overrides": {
                "crypto_alt_majors": {"intraday": {"min_rr": 1.4, "fallback_rr": 1.8}},
            }
        },
    }
    sig = {
        "engine": "engine_a",
        "level_source": "engine_b_execution",
        "pair": "DOT/USDT",
        "type": "crypto",
        "direction": "LONG",
        "style": "intraday",
        "price": 0.854,
        "sl": 0.8307078471161137,
        "tp1": 0.8722851148310247,
        "tp2": 0.8938258751909953,
        "engine_b_status": {"tp1_source": "structural", "tp2_source": "fallback_rr"},
    }

    err = execution._reconcile_engine_b_rr_after_broker_entry(sig, cfg)

    assert err is None
    assert sig.get("engine_b_broker_rebase_rr_resynth") is True
    assert sig["tp1"] > 0.8722851148310247


def test_reconcile_engine_a_preserved_b_levels_after_broker_rebase():
    """Engine A Quick Exec with preserved B levels must reconcile even without ALIGNED verdict."""
    import execution

    cfg = {
        "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True,
        "ENGINE_C_EXEC_MIN_RR": 1.0,
        "NAKED_ENGINE": {
            "score_group_overrides": {
                "crypto_alt_majors": {"intraday": {"min_rr": 1.4, "fallback_rr": 1.8}},
            }
        },
    }
    sig = {
        "engine": "engine_a",
        "verdict": "WATCHLIST",
        "enginesAligned": True,
        "level_source": "engine_b_execution",
        "pair": "DOT/USDT",
        "type": "crypto",
        "direction": "LONG",
        "style": "intraday",
        "price": 0.855,
        "entry": 0.855,
        "sl": 0.8307078471161137,
        "tp1": 0.8722851148310247,
        "tp2": 0.8938258751909953,
        "engine_b_execution_plan": "scale_out",
        "engine_b": {"execution_sl": 0.8307078471161137, "execution_tp1": 0.8722851148310247},
        "engine_b_status": {"tp1_source": "structural"},
    }

    err = execution._reconcile_engine_b_rr_after_broker_entry(sig, cfg)

    assert err is None
    assert sig.get("engine_b_broker_rebase_rr_resynth") is True
    assert sig["tp1"] > 0.8722851148310247
    new_rr = (sig["tp1"] - 0.855) / (0.855 - 0.8307078471161137)
    assert new_rr >= 1.4 - 1e-9
    assert sig["tp2"] == sig["tp1"]
    assert sig["engine_b_execution_plan"] == "single_structural_tp1"
