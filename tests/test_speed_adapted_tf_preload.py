"""Speed/liquidity demotion must load M30 before entry readiness checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from timeframe_policy import LiquidityClass, SpeedClass, SpeedThresholds


def test_ensure_speed_adapted_lower_tfs_loads_m30_when_thin(monkeypatch):
    import scanner as scanner_mod

    calls: list[str] = []

    def _fake_fetch(runtime, pair, tf, limit, force_refresh=False):
        calls.append(str(tf).upper())
        bars = [
            {
                "time": f"2026-01-01T00:{i:02d}:00Z",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
            }
            for i in range(5)
        ]
        return bars, {"bars": 5, "upstream": "test"}

    monkeypatch.setattr(scanner_mod, "_fetch_ab_crypto_signal_candles", _fake_fetch)

    def _fake_state(pair, tf, candles=None, provider_symbol=None):
        return {
            "confirmed": list(candles or []),
            "forming": None,
            "stale": False,
        }

    monkeypatch.setattr(
        "athena_app.services.market_state.get_tf_market_state",
        _fake_state,
    )

    speed = SimpleNamespace(
        live_speed_class=SpeedClass.FAST,
        history_ready=True,
        liquidity_class=LiquidityClass.THIN,
        missing_inputs=(),
        speed_percentile=10.0,
        candidate_speed_class=SpeedClass.NORMAL,
        candidate_age_h1_bars=0,
        transition_pending=False,
        last_speed_transition_utc=None,
        thresholds=SpeedThresholds(),
    )
    raw = {"M15": [{"time": "t", "open": 1, "high": 1, "low": 1, "close": 1}] * 3}
    state = {"M15": {"confirmed": list(raw["M15"]), "forming": None, "stale": False}}
    pre_a = dict(raw)
    lower: dict = {}

    loaded = scanner_mod._ensure_speed_adapted_lower_tfs(
        SimpleNamespace(),
        {
            "display": "BTC/USDT",
            "symbol": "BTCUSDT",
            "type": "crypto",
            "source": "binance",
        },
        style="intraday",
        score_group="crypto_btc",
        speed_state=speed,
        lim={"M15": 100, "M30": 100, "M5": 100},
        raw_candles=raw,
        preloaded_market_state=state,
        preloaded_candles_for_a=pre_a,
        lower_results=lower,
        force_refresh=False,
    )

    assert "M30" in loaded
    assert "M30" in calls
    assert state.get("M30", {}).get("confirmed")
    assert len(state["M30"]["confirmed"]) >= 1
