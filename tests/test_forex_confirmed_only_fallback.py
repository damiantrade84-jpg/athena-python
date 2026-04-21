from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_athena_module():
    path = Path(__file__).resolve().parents[1] / "athena.py"
    spec = spec_from_file_location("athena_forex_confirmed_only_test", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mt5_forex_fallback_market_state_uses_confirmed_only(monkeypatch):
    athena_module = _load_athena_module()

    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD=X",
        "type": "forex",
        "source": "mt5",
        "enabled": True,
    }

    confirmed_h1 = [
        {
            "time": f"2026-04-21T{(i % 24):02d}:00:00+00:00",
            "open": 1.10 + i * 0.0001,
            "high": 1.20 + i * 0.0001,
            "low": 1.00 + i * 0.0001,
            "close": 1.15 + i * 0.0001,
            "vol": 100 + i,
        }
        for i in range(60)
    ]
    forming_h1 = {
        "time": "2026-04-21T08:00:00+00:00",
        "open": 1.18,
        "high": 1.25,
        "low": 1.16,
        "close": 1.24,
        "vol": 50,
    }
    confirmed_h4 = [
        {
            "time": f"2026-03-{(i % 28) + 1:02d}T00:00:00+00:00",
            "open": 1.10 + i * 0.0002,
            "high": 1.20 + i * 0.0002,
            "low": 1.00 + i * 0.0002,
            "close": 1.18 + i * 0.0002,
            "vol": 200 + i,
        }
        for i in range(60)
    ]
    confirmed_d1 = [
        {
            "time": f"2025-{((i // 28) % 12) + 1:02d}-{(i % 28) + 1:02d}T00:00:00+00:00",
            "open": 1.05 + i * 0.0003,
            "high": 1.20 + i * 0.0003,
            "low": 1.00 + i * 0.0003,
            "close": 1.18 + i * 0.0003,
            "vol": 500 + i,
        }
        for i in range(230)
    ]

    monkeypatch.setattr(athena_module, "scan_candle_limits", lambda _pair=None: {"D1": 10, "H4": 10, "H1": 10})
    def _fake_fetch_candles(pair_obj, tf, limit):
        if tf == "D1":
            return confirmed_d1
        if tf == "H4":
            return confirmed_h4
        return None

    monkeypatch.setattr(athena_module, "fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(athena_module, "fetch_usd_relative_strength_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(athena_module, "_build_style_levels", lambda **kwargs: {})
    monkeypatch.setattr(athena_module, "_resolve_scan_style", lambda style, pair_obj: "swing")

    def _fake_indicator_pack(candles, pair_type):
        close = candles[-1]["close"]
        return {
            "snap": {
                "close": close,
                "atr": 0.01,
                "ema21": close,
                "ema50": close,
                "ema200": close,
                "adx": 30.0,
                "bbWidth_pct": 0.2,
            }
        }

    monkeypatch.setattr(athena_module, "calc_indicators_with_normalized", _fake_indicator_pack)
    monkeypatch.setattr(athena_module, "calc_fib", lambda candles: {})
    monkeypatch.setattr(athena_module, "calc_fib_proximity", lambda close, fib: 0.0)
    monkeypatch.setattr(athena_module, "calc_sma", lambda values, period: [1.0 for _ in values])
    monkeypatch.setattr(athena_module, "calc_stochastic", lambda *args, **kwargs: {"k": [50.0], "d": [50.0]})

    captured = {}

    class _FakeForexResult:
        final_score = 1.0
        direction = "LONG"
        signal_type = "TREND_PULLBACK"
        components = {
            "trend_gate": True,
            "session_active": True,
            "momentum_confirm": 0.0,
            "adx_filter": 0.7,
            "entry_quality": 0.5,
            "cot_boost": 0.0,
        }

    def _fake_compute_forex_score(**kwargs):
        captured["h1_candles"] = list(kwargs.get("h1_candles") or [])
        return _FakeForexResult()

    import forex_scoring
    monkeypatch.setattr(forex_scoring, "compute_forex_score", _fake_compute_forex_score)

    import types

    fake_candle_manager = types.ModuleType("candle_manager")

    def _fake_fetch_market_state(pair_obj, tf, limit):
        if tf == "H1":
            return {"confirmed": confirmed_h1, "forming": forming_h1, "is_live": True}
        if tf == "H4":
            return {"confirmed": confirmed_h4, "forming": None, "is_live": False}
        if tf == "D1":
            return {"confirmed": confirmed_d1, "forming": None, "is_live": False}
        return {"confirmed": [], "forming": None, "is_live": False}

    fake_candle_manager.fetch_market_state = _fake_fetch_market_state
    sys.modules["candle_manager"] = fake_candle_manager

    athena_module.analyze_pair(pair, btc_bias=0)

    assert captured["h1_candles"] == confirmed_h1
    assert all(c["close"] != forming_h1["close"] for c in captured["h1_candles"])
