"""Tests for ATR freshness diagnostics (F2 additive observability).

These tests guard the contracts added by the ATR forensic audit:
- ``resolve_atr_for_levels_with_tf`` returns the same value as
  ``athena._atr_for_levels`` plus the timeframe that won.
- ``build_engine_a_diagnostics`` produces the expected shape and derives age
  in seconds from the last candle timestamp.
- ``build_engine_c_diagnostics`` inherits provenance from sig_a and records
  sl_method/tp_method.
- ``build_engine_d_diagnostics`` always reports ``atr_tf == "M15"``.
- The diagnostics tool's stale classifier respects per-TF thresholds.

No scoring, gate, level, or rebase behavior is exercised here — these are
pure observability contracts. They MUST NOT influence execution decisions.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _make_candles(last_ts_iso: str, count: int = 30) -> list[dict]:
    candles = []
    for i in range(count):
        candles.append(
            {
                "time": last_ts_iso if i == count - 1 else f"2026-01-01T00:00:0{i}Z",
                "open": 1.0,
                "high": 1.01,
                "low": 0.99,
                "close": 1.0,
            }
        )
    return candles


def test_resolve_atr_priority_swing_default():
    import atr_diagnostics as ad

    atr, tf = ad.resolve_atr_for_levels_with_tf(
        d1_snap={"atr": 0.005},
        h4_snap={"atr": 0.003},
        h1_snap={"atr": 0.001},
        pair_type="forex",
        style="swing",
        level_atr_priority={},
    )
    assert atr == 0.005
    assert tf == "D1"


def test_resolve_atr_priority_intraday_picks_h4():
    import atr_diagnostics as ad

    atr, tf = ad.resolve_atr_for_levels_with_tf(
        d1_snap={"atr": 0.005},
        h4_snap={"atr": 0.003},
        h1_snap={"atr": 0.001},
        pair_type="forex",
        style="intraday",
        level_atr_priority={},
    )
    assert atr == 0.003
    assert tf == "H4"


def test_resolve_atr_priority_scalp_picks_h1():
    import atr_diagnostics as ad

    atr, tf = ad.resolve_atr_for_levels_with_tf(
        d1_snap={"atr": 0.005},
        h4_snap={"atr": 0.003},
        h1_snap={"atr": 0.001},
        pair_type="forex",
        style="scalp",
        level_atr_priority={},
    )
    assert atr == 0.001
    assert tf == "H1"


def test_resolve_atr_priority_returns_none_when_all_zero():
    import atr_diagnostics as ad

    atr, tf = ad.resolve_atr_for_levels_with_tf(
        d1_snap={"atr": 0},
        h4_snap={"atr": 0},
        h1_snap={"atr": 0},
        pair_type="forex",
        style="swing",
        level_atr_priority={},
    )
    assert atr is None
    assert tf is None


def test_resolve_atr_priority_crypto_default_order():
    """Crypto default order is H4 > D1 > H1 when no config override."""
    import atr_diagnostics as ad

    atr, tf = ad.resolve_atr_for_levels_with_tf(
        d1_snap={"atr": 0.005},
        h4_snap={"atr": 0.003},
        h1_snap={"atr": 0.001},
        pair_type="crypto",
        style="swing",
        level_atr_priority={},
    )
    assert atr == 0.003
    assert tf == "H4"


def test_resolve_atr_priority_honours_config_override():
    """LEVEL_ATR_PRIORITY config must take precedence over defaults."""
    import atr_diagnostics as ad

    priority = {"forex": {"swing": ["H1", "H4", "D1"]}}
    atr, tf = ad.resolve_atr_for_levels_with_tf(
        d1_snap={"atr": 0.005},
        h4_snap={"atr": 0.003},
        h1_snap={"atr": 0.001},
        pair_type="forex",
        style="swing",
        level_atr_priority=priority,
    )
    assert atr == 0.001
    assert tf == "H1"


def test_build_engine_a_diagnostics_age_from_iso():
    import atr_diagnostics as ad

    fresh_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    d1 = _make_candles(fresh_ts, count=20)

    diag = ad.build_engine_a_diagnostics(
        atr_value=0.005,
        atr_tf="D1",
        atr_source="signal",
        d1_candles=d1,
        h4_candles=None,
        h1_candles=None,
    )
    assert diag["atr_value"] == 0.005
    assert diag["atr_tf"] == "D1"
    assert diag["atr_source"] == "signal"
    assert diag["atr_source_engine"] == "engine_a"
    assert diag["atr_candle_last_ts"] == fresh_ts
    assert 100 <= diag["atr_age_seconds"] <= 200
    assert diag["atr_confirmed_only"] is True


def test_build_engine_a_diagnostics_handles_missing_candles():
    import atr_diagnostics as ad

    diag = ad.build_engine_a_diagnostics(
        atr_value=None,
        atr_tf=None,
        atr_source=None,
        d1_candles=None,
        h4_candles=None,
        h1_candles=None,
    )
    assert diag["atr_value"] is None
    assert diag["atr_tf"] is None
    assert diag["atr_candle_last_ts"] is None
    assert diag["atr_age_seconds"] is None


def test_build_engine_a_diagnostics_handles_epoch_ms():
    import atr_diagnostics as ad

    last_dt = datetime.now(timezone.utc) - timedelta(seconds=60)
    epoch_ms = int(last_dt.timestamp() * 1000)
    d1 = [
        {"time": epoch_ms, "open": 1, "high": 1, "low": 1, "close": 1}
        for _ in range(20)
    ]

    diag = ad.build_engine_a_diagnostics(
        atr_value=0.001,
        atr_tf="D1",
        atr_source="signal",
        d1_candles=d1,
        h4_candles=None,
        h1_candles=None,
    )
    assert 40 <= diag["atr_age_seconds"] <= 120


def test_build_engine_b_diagnostics_records_bybit_flag():
    import atr_diagnostics as ad

    fresh_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=30)
    ).isoformat()
    candles = _make_candles(fresh_ts, count=20)

    diag = ad.build_engine_b_diagnostics(
        atr_value=0.0048,
        atr_tf="H4",
        atr_source="bybit_levels",
        atr_candles=candles,
        bybit_atr_available=True,
    )
    assert diag["atr_value"] == 0.0048
    assert diag["atr_tf"] == "H4"
    assert diag["atr_source"] == "bybit_levels"
    assert diag["atr_source_engine"] == "engine_b"
    assert diag["bybit_atr_available"] is True
    assert diag["atr_confirmed_only"] is True


def test_build_engine_c_diagnostics_inherits_from_sig_a():
    import atr_diagnostics as ad

    sig_a_diag = {
        "atr_value": 0.005,
        "atr_tf": "D1",
        "atr_source": "signal",
        "atr_source_engine": "engine_a",
        "atr_candle_last_ts": "2026-05-20T15:00:00+00:00",
        "atr_age_seconds": 1800.0,
        "atr_confirmed_only": True,
    }
    sig_b_diag = {
        "atr_value": 0.0048,
        "atr_tf": "H4",
        "atr_source": "candle_atr_tf",
        "atr_source_engine": "engine_b",
        "atr_candle_last_ts": "2026-05-20T15:00:00+00:00",
        "atr_age_seconds": 1800.0,
        "atr_confirmed_only": True,
    }
    diag = ad.build_engine_c_diagnostics(
        atr_value=0.005,
        sig_a_diag=sig_a_diag,
        sig_b_diag=sig_b_diag,
        sl_method="structural",
        tp_method="atr",
    )
    assert diag["atr_source_engine"] == "engine_c"
    assert diag["atr_tf"] == "D1"
    assert diag["sl_method"] == "structural"
    assert diag["tp_method"] == "atr"
    assert diag["engine_a_atr_diagnostics"]["atr_tf"] == "D1"
    assert diag["engine_b_atr_diagnostics"]["atr_tf"] == "H4"


def test_build_engine_d_diagnostics_is_always_m15():
    import atr_diagnostics as ad

    fresh_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=200)
    ).isoformat()
    m15 = _make_candles(fresh_ts, count=20)

    diag = ad.build_engine_d_diagnostics(atr_value=0.0006, m15_candles=m15)
    assert diag["atr_tf"] == "M15"
    assert diag["atr_source"] == "scalp_m15_candles"
    assert diag["atr_source_engine"] == "engine_d"
    assert diag["atr_value"] == 0.0006
    assert 150 <= diag["atr_age_seconds"] <= 250


def test_stale_classifier_threshold_tf_specific():
    """The tool's per-TF threshold logic should mark stale only when limit exceeded."""
    tool_path = (
        Path(__file__).resolve().parent.parent
        / "tools"
        / "atr_freshness_diagnostics.py"
    )
    spec = importlib.util.spec_from_file_location(
        "atr_freshness_diagnostics_under_test", tool_path
    )
    assert spec and spec.loader
    t = importlib.util.module_from_spec(spec)
    sys.modules["atr_freshness_diagnostics_under_test"] = t
    spec.loader.exec_module(t)

    # 8h age vs H4 limit 5h → stale
    diag = {"atr_tf": "H4", "atr_age_seconds": 8 * 3600}
    stale, reason = t._classify_stale(diag, {"H4": 5 * 3600})
    assert stale is True
    assert "H4" in (reason or "")

    # 30min age vs H1 limit 2h → fresh
    diag = {"atr_tf": "H1", "atr_age_seconds": 1800}
    stale, reason = t._classify_stale(diag, {"H1": 7200})
    assert stale is False
    assert reason is None

    # No age → stale with atr_age_unknown
    stale, reason = t._classify_stale({"atr_tf": "D1"}, {"D1": 100000})
    assert stale is True
    assert reason == "atr_age_unknown"

    # Limit not configured for TF → not stale (diagnostic tool fail-open by design)
    stale, reason = t._classify_stale(
        {"atr_tf": "M1", "atr_age_seconds": 10}, {"H1": 7200}
    )
    assert stale is False


def _make_ohlcv_series(count: int = 30, step: float = 0.01) -> list[dict]:
    candles = []
    price = 1.0
    for i in range(count):
        candles.append(
            {
                "time": f"2026-01-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z",
                "open": price,
                "high": price + step,
                "low": price - step,
                "close": price + step * 0.5,
            }
        )
        price += step * 0.5
    return candles


def test_attach_engine_a_v3_atr_provenance_forex_intraday_uses_h1_signal_feed():
    import atr_diagnostics as ad

    d1 = _make_ohlcv_series()
    h4 = _make_ohlcv_series()
    h1 = _make_ohlcv_series()
    signal: dict = {}

    ad.attach_engine_a_v3_atr_provenance(
        signal,
        {"type": "forex", "source": "mt5", "display": "EUR/USD"},
        "intraday",
        d1,
        h4,
        h1,
        config={"ATR_FRESHNESS": {"ENABLED": False}},
    )

    diag = signal["atrDiagnostics"]
    assert diag["atr_tf"] == "H1"
    assert diag["atr_source"] == "mt5"
    assert diag["atr_value"] is not None and diag["atr_value"] > 0
    assert diag["atr_confirmed_only"] is True
    assert "atrFreshness" in signal


def test_attach_engine_a_v3_atr_provenance_crypto_prefers_bybit_levels_feed():
    import atr_diagnostics as ad

    d1 = _make_ohlcv_series()
    h4 = _make_ohlcv_series()
    h1 = _make_ohlcv_series()
    bybit_candles = _make_ohlcv_series()

    def _fake_bybit(_pair, _style):
        return 123.45, bybit_candles

    signal: dict = {}
    ad.attach_engine_a_v3_atr_provenance(
        signal,
        {"type": "crypto", "source": "binance", "display": "BTC/USDT"},
        "intraday",
        d1,
        h4,
        h1,
        config={
            "ENGINE_A_CRYPTO_LEVELS_FEED": "bybit",
            "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK": False,
            "ATR_FRESHNESS": {"ENABLED": False},
        },
        bybit_atr_and_candles_fn=_fake_bybit,
    )

    diag = signal["atrDiagnostics"]
    assert diag["atr_value"] == 123.45
    assert diag["atr_tf"] == "H4"
    assert diag["atr_source"] == "bybit"
    assert diag["atr_confirmed_only"] is True


def test_attach_engine_a_v3_atr_provenance_crypto_bybit_unavailable_fail_closed():
    import atr_diagnostics as ad

    d1 = _make_ohlcv_series()
    h4 = _make_ohlcv_series()
    h1 = _make_ohlcv_series()
    signal: dict = {}

    ad.attach_engine_a_v3_atr_provenance(
        signal,
        {"type": "crypto", "source": "binance", "display": "BTC/USDT"},
        "intraday",
        d1,
        h4,
        h1,
        config={
            "ENGINE_A_CRYPTO_LEVELS_FEED": "bybit",
            "ENGINE_A_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK": False,
            "ATR_FRESHNESS": {"ENABLED": False},
        },
        bybit_atr_and_candles_fn=lambda *_a, **_k: (None, None),
    )

    diag = signal["atrDiagnostics"]
    assert diag["atr_source"] == "bybit_unavailable"
    assert diag["atr_value"] is None


def test_cascade_provenance_accepts_attached_engine_a_atr_diagnostics():
    from cascade_scan import _provenance_blockers

    import atr_diagnostics as ad

    d1 = _make_ohlcv_series()
    h4 = _make_ohlcv_series()
    h1 = _make_ohlcv_series()
    signal: dict = {
        "candleFetchMeta": {
            "D1": {"stalenessSeverity": "fresh"},
            "H4": {"stalenessSeverity": "fresh"},
            "H1": {"stalenessSeverity": "fresh"},
        },
        "atrFreshness": {"enabled": False, "stale": False},
    }
    ad.attach_engine_a_v3_atr_provenance(
        signal,
        {"type": "forex", "source": "mt5"},
        "intraday",
        d1,
        h4,
        h1,
        config={"ATR_FRESHNESS": {"ENABLED": False}},
    )

    blockers = _provenance_blockers(signal)
    assert "missing_atr_diagnostics" not in blockers
    assert "invalid_atr_value" not in blockers
    assert "missing_atr_tf" not in blockers
    assert "missing_atr_source" not in blockers
