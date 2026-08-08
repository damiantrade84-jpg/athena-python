"""Crypto volume provenance regressions (audit 2026-07-31).

Covers the paths where crypto volume reached Engine A/B/D scoring in a state that
looked closed but was not, or that mixed units/venues without saying so.
"""

import time

import pytest

import data_feeds
import volume_profile
from athena.microstructure.aggtrade_volume import trade_bucket_profile_price_consistent
from athena_app.services.market_state import split_market_state


# ── Bybit kline cache must not outlive the forming bar it cached ─────────────

def test_bybit_kline_cache_expiry_caps_at_forming_bar_close():
    now = 1_800_000_000.0
    open_ms = int((now - 3000) * 1000)  # H1 bar opened 50 min ago -> closes in 600s
    candles = [{"open_time": open_ms}]

    expiry = data_feeds._bybit_kline_cache_expiry(candles, "H1", now)

    # Plain TTL (30s) is shorter than the 600s to close, so TTL wins here.
    assert expiry == pytest.approx(now + data_feeds._BYBIT_KLINE_CACHE_TTL)


def test_bybit_kline_cache_expiry_shortens_when_bar_closes_before_ttl():
    now = 1_800_000_000.0
    open_ms = int((now - 3595) * 1000)  # H1 bar closes in 5s, inside the 30s TTL
    candles = [{"open_time": open_ms}]

    expiry = data_feeds._bybit_kline_cache_expiry(candles, "H1", now)

    assert expiry == pytest.approx(now + 5.0)
    assert expiry < now + data_feeds._BYBIT_KLINE_CACHE_TTL


def test_bybit_kline_cache_does_not_serve_partial_bar_as_confirmed(monkeypatch):
    """The cached forming bar must not be re-labelled confirmed after its close.

    Without the expiry cap, _bybit_kline_cache_copy recomputed confirmed against
    'now' while returning the partial-volume rows captured before the close.
    """
    data_feeds.clear_bybit_kline_cache()
    now = time.time()
    tf_sec = 300  # M5
    bucket_open = int(now // tf_sec) * tf_sec
    # Pretend the bar is 5s from closing when we fetch it.
    forming_open_ms = int((bucket_open - tf_sec + 295) * 1000)

    rows = [[str(forming_open_ms), "1", "2", "0.5", "1.5", "10", "15"]]

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"retCode": 0, "result": {"list": rows}}

    monkeypatch.setattr(data_feeds.http_requests, "get", lambda *a, **k: _Resp())

    fetched = data_feeds._fetch_bybit_klines("BTCUSDT", "M5", 1)
    assert fetched and fetched[-1]["confirmed"] is False
    assert fetched[-1]["volSource"] == "bybit"

    key = ("BTCUSDT", "M5", 1)
    _cached_rows, expiry = data_feeds._bybit_kline_cache[key]
    bar_close = (forming_open_ms / 1000.0) + tf_sec

    # Entry expires no later than the bar it cached, so the next read refetches
    # instead of promoting the partial row.
    assert expiry <= bar_close + 1e-6
    data_feeds.clear_bybit_kline_cache()


# ── split_market_state must read the producer's own closed-bar flag ──────────

def _bybit_style_candle(epoch_s: int, *, confirmed: bool) -> dict:
    return {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(epoch_s)),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "vol": 10.0,
        "confirmed": confirmed,
        "closed": confirmed,
    }


def test_split_market_state_honours_bybit_confirmed_false_key():
    """Bybit REST emits `confirmed`/`closed`, not `confirm`.

    Only `confirm` was checked, so REST-sourced bars fell through to bucket
    comparison and an unconfirmed bar could land in the closed series.
    """
    now = 1_800_000_000.0
    tf_sec = 3600
    prev_open = int((now // tf_sec) * tf_sec) - tf_sec
    candles = [
        _bybit_style_candle(prev_open - tf_sec, confirmed=True),
        # Bar sits in a past bucket but the venue says it is still open.
        _bybit_style_candle(prev_open, confirmed=False),
    ]

    state = split_market_state(candles, "H1", "BTC/USDT", time_now=now)

    assert len(state["confirmed"]) == 1
    assert state["forming"] is not None
    assert state["forming"]["confirmed"] is False


def test_split_market_state_does_not_promote_current_bucket_on_confirmed_true():
    """An explicit True must not override the current-bucket check (fail-safe)."""
    now = 1_800_000_000.0
    tf_sec = 3600
    current_open = int(now // tf_sec) * tf_sec
    candles = [
        _bybit_style_candle(current_open - tf_sec, confirmed=True),
        _bybit_style_candle(current_open, confirmed=True),
    ]

    state = split_market_state(candles, "H1", "BTC/USDT", time_now=now)

    assert len(state["confirmed"]) == 1
    assert state["forming"] is not None


# ── Trade-bucket VP venue consistency ───────────────────────────────────────

def _candles(low: float, high: float, count: int = 24) -> list[dict]:
    return [{"high": high, "low": low, "close": (high + low) / 2} for _ in range(count)]


def test_trade_bucket_vp_accepted_when_poc_inside_our_price_range():
    vp = {"valid": True, "poc": 100.0}
    ok, reason = trade_bucket_profile_price_consistent(vp, _candles(98.0, 102.0))
    assert ok is True
    assert reason is None


def test_trade_bucket_vp_rejected_when_poc_outside_our_price_range():
    """Buckets and OHLCV can come from different venues/symbols."""
    vp = {"valid": True, "poc": 3500.0}
    ok, reason = trade_bucket_profile_price_consistent(vp, _candles(98.0, 102.0))
    assert ok is False
    assert reason == "aggtrade_vp_venue_price_mismatch"


def test_trade_bucket_vp_tolerates_normal_perp_basis():
    """A few bps of basis keeps the POC inside range and must not be rejected."""
    vp = {"valid": True, "poc": 102.03}  # 0.03% above our high
    ok, _ = trade_bucket_profile_price_consistent(vp, _candles(98.0, 102.0))
    assert ok is True


def test_trade_bucket_vp_check_is_inert_without_candles():
    vp = {"valid": True, "poc": 3500.0}
    assert trade_bucket_profile_price_consistent(vp, None) == (True, None)
    assert trade_bucket_profile_price_consistent(vp, []) == (True, None)


# ── Volume profile must report partial range-proxy substitution ─────────────

def _vp_candle(high: float, low: float, vol: float) -> dict:
    return {"high": high, "low": low, "close": (high + low) / 2, "vol": vol}


def test_fixed_range_vp_reports_mixed_range_proxy_when_some_bars_lack_volume():
    """Range units land in the same bins as real volume — that must be visible."""
    candles = [_vp_candle(101.0 + i, 100.0 + i, 500.0) for i in range(10)]
    candles[4]["vol"] = 0.0  # single zero-volume bar gets range-proxied

    vp = volume_profile.compute_fixed_range_volume_profile(candles, bins=8)

    assert vp["profile_valid"] is True
    assert vp["volume_source"] == "mixed_range_proxy"
    assert vp["range_proxy_bars"] == 1


def test_fixed_range_vp_reports_candle_volume_when_every_bar_has_volume():
    candles = [_vp_candle(101.0 + i, 100.0 + i, 500.0) for i in range(10)]

    vp = volume_profile.compute_fixed_range_volume_profile(candles, bins=8)

    assert vp["volume_source"] == "candle_volume"
    assert vp["range_proxy_bars"] == 0


# ── Engine D crypto forming-bar trim ────────────────────────────────────────

def test_scalp_drop_forming_bar_uses_bybit_confirmed_flag():
    import scalp_engine

    candles = [
        {"time": 1_800_000_000, "close": 1.0, "confirmed": True},
        {"time": 1_800_000_900, "close": 1.1, "confirmed": False},
    ]
    assert len(scalp_engine._scalp_drop_forming_bar(candles, "M15")) == 1


def test_scalp_drop_forming_bar_falls_back_to_bucket_compare():
    """Binance/routed crypto candles carry no closed-bar flag."""
    import scalp_engine

    now = time.time()
    bucket = int(now // 900) * 900
    candles = [
        {"time": bucket - 900, "close": 1.0},
        {"time": bucket, "close": 1.1},  # current M15 bucket -> forming
    ]
    out = scalp_engine._scalp_drop_forming_bar(candles, "M15")
    assert len(out) == 1
    assert out[-1]["time"] == bucket - 900


def test_scalp_drop_forming_bar_keeps_fully_closed_series():
    import scalp_engine

    now = time.time()
    bucket = int(now // 900) * 900
    candles = [
        {"time": bucket - 1800, "close": 1.0},
        {"time": bucket - 900, "close": 1.1},
    ]
    assert scalp_engine._scalp_drop_forming_bar(candles, "M15") == candles
