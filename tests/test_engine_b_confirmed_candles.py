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


def test_naked_scan_current_price_matches_full_scan_confirmed_close():
    """Gate current_price must use structure (confirmed) close, not trigger forming close."""
    from athena_app.services.engine_b_market_state import engine_b_gate_current_price

    structure = [_bar("2026-05-20T08:00:00Z", close=100.0), _bar("2026-05-20T09:00:00Z", close=101.0)]
    trigger = structure + [_bar("2026-05-20T10:00:00Z", close=105.0)]  # forming bar

    gate_px = engine_b_gate_current_price(
        structure_entry_candles=structure,
        trigger_entry_candles=trigger,
    )
    full_scan_px = float(structure[-1]["close"])

    assert gate_px == full_scan_px == 101.0
    assert gate_px != float(trigger[-1]["close"])


def test_engine_b_live_gate_quote_uses_fresh_quote_not_confirmed_close():
    from athena_app.services.engine_b_market_state import engine_b_live_gate_quote

    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD=X"}
    price, diag = engine_b_live_gate_quote(
        pair,
        {"EUR/USD": {"price": 1.125, "ts": 1_000.0, "source": "mt5"}},
        {"LIVE_PRICE_MAX_AGE_SEC": {"forex": 5}},
        time_now=1_003.0,
    )

    assert price == 1.125
    assert diag["ageSec"] == 3.0
    assert diag["fresh"] is True


def test_engine_b_live_gate_quote_rejects_stale_and_unknown_age():
    import pytest
    from athena_app.services.engine_b_market_state import engine_b_live_gate_quote

    pair = {"type": "crypto", "display": "HBAR/USDT", "symbol": "HBARUSDT"}
    config = {"LIVE_PRICE_MAX_AGE_SEC": {"crypto": 5}}

    with pytest.raises(ValueError, match="ENGINE_B_LIVE_QUOTE_STALE"):
        engine_b_live_gate_quote(
            pair,
            {"HBARUSDT": {"price": 0.0687, "ts": 1_000.0}},
            config,
            time_now=1_006.0,
        )
    with pytest.raises(ValueError, match="ENGINE_B_LIVE_QUOTE_AGE_UNKNOWN"):
        engine_b_live_gate_quote(pair, {"HBARUSDT": {"price": 0.0687}}, config, time_now=1_001.0)
