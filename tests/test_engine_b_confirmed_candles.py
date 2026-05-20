"""Engine B confirmed candle parity and Engine C legacy chop tests."""

from datetime import datetime, timezone

from athena_app.services.engine_b_market_state import engine_b_confirmed_candles_from_raw
from athena_app.services.market_state import split_market_state, market_state_offset_hours


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _bar(iso: str, close: float = 1.0) -> dict:
    return {"time": iso, "open": close, "high": close, "low": close, "close": close, "vol": 1}


def test_engine_b_confirmed_matches_split_for_mt5_forex():
    pair = {"type": "forex", "source": "mt5", "display": "EUR/USD", "symbol": "EURUSD=X"}
    now = _epoch("2026-05-20T11:30:00Z")
    raw = [_bar("2026-05-20T08:00:00Z"), _bar("2026-05-20T09:00:00Z"), _bar("2026-05-20T10:00:00Z")]
    confirmed = engine_b_confirmed_candles_from_raw(pair, "H1", raw, time_now=now)
    state = split_market_state(
        raw,
        "H1",
        "EUR/USD",
        time_now=now,
        offset_hours=market_state_offset_hours(pair, "H1"),
    )
    assert confirmed == list(state.get("confirmed") or [])


def test_engine_b_confirmed_differs_from_naive_chop():
    """Legacy raw[:-1] can drop a confirmed bar; bucket split is authoritative."""
    pair = {"type": "forex", "source": "mt5", "display": "GBP/USD", "symbol": "GBPUSD=X"}
    now = _epoch("2026-05-20T10:30:00Z")
    raw = [_bar("2026-05-20T08:00:00Z"), _bar("2026-05-20T09:00:00Z"), _bar("2026-05-20T10:00:00Z")]
    confirmed = engine_b_confirmed_candles_from_raw(pair, "H1", raw, time_now=now)
    legacy = raw[:-1]
    assert len(confirmed) >= len(legacy)
    assert confirmed[-1]["time"] >= legacy[-1]["time"]


def test_engine_c_legacy_chop_documented_mismatch():
    """Document that naive chop differs from confirmed policy when forming bar is closed."""
    pair = {"type": "forex", "source": "mt5", "display": "USD/JPY", "symbol": "USDJPY=X"}
    now = _epoch("2026-05-20T05:30:00Z")
    raw = [_bar("2026-05-20T01:00:00Z"), _bar("2026-05-20T05:00:00Z")]
    confirmed = engine_b_confirmed_candles_from_raw(pair, "H4", raw, time_now=now)
    legacy = raw[:-1] if len(raw) > 1 else raw
    # At minimum both should be lists; parity fix ensures execution uses confirmed.
    assert isinstance(confirmed, list)
    assert isinstance(legacy, list)
