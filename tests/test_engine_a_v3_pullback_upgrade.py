"""Engine A pullback upgrade across families.

Does not change trade-eligibility. Failed rejection is WATCH, not a pair demotion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine_a_v3.routing import route_specialist
from engine_a_v3.setups import _pullback_candidate, detect_setup


def _trend_bars(*, count: int = 80, base: float = 2000.0, step: float = 0.08) -> list[dict]:
    start = datetime(2025, 6, 2, 8, 0, tzinfo=timezone.utc)  # London/NY session
    rows = []
    for idx in range(count):
        close = base + step * idx
        open_ = close - 0.03
        rows.append(
            {
                "time": (start + timedelta(hours=1) * idx).isoformat(),
                "open": open_,
                "high": close + 0.04,
                "low": open_ - 0.02,
                "close": close,
                "vol": 1000 + idx,
            }
        )
    return rows


def _long_pullback_pair(*, rejection_wick: bool) -> tuple[list[dict], list[dict]]:
    from engine_a_v3.setups import _ema

    context = _trend_bars()
    primary = [dict(row) for row in context]
    ema_now = _ema([float(row["close"]) for row in primary], 21)[-1]
    if rejection_wick:
        # Dip into the EMA with a long lower wick, then confirm back up.
        primary[-2].update(
            {
                "open": ema_now + 0.04,
                "high": ema_now + 0.06,
                "low": ema_now - 0.24,
                "close": ema_now - 0.02,
            }
        )
    else:
        # Body-driven dip: close sits on the low, so there is no rejection wick.
        primary[-2].update(
            {
                "open": ema_now + 0.04,
                "high": ema_now + 0.05,
                "low": ema_now - 0.10,
                "close": ema_now - 0.09,
            }
        )
    primary[-1].update(
        {
            "open": ema_now - 0.01,
            "high": ema_now + 0.08,
            "low": ema_now - 0.02,
            "close": ema_now + 0.04,
        }
    )
    return primary, context


def test_xau_intraday_setup_ids_include_pullback():
    route = route_specialist(
        {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
    )
    assert route.setup_ids("intraday") == (
        "precious_london_ny_breakout_retest",
        "precious_trend_pullback",
    )


def test_xau_intraday_pullback_can_be_disabled(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_XAU_INTRADAY_PULLBACK_ENABLED", False)
    route = route_specialist(
        {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
    )
    assert route.setup_ids("intraday") == ("precious_london_ny_breakout_retest",)


def test_pullback_rejection_applies_to_every_family():
    families = ("forex", "crypto", "commodity", "index", "equity_etf")
    primary_ok, context_ok = _long_pullback_pair(rejection_wick=True)
    primary_flat, context_flat = _long_pullback_pair(rejection_wick=False)
    for family in families:
        ok = _pullback_candidate(
            f"{family}_trend_pullback", primary_ok, context_ok, family=family
        )
        assert ok.decision == "TRADE", family
        assert any(
            item.name == "pullback_rejection_wick" and item.passed
            for item in ok.predicates
        ), family

        blocked = _pullback_candidate(
            f"{family}_trend_pullback", primary_flat, context_flat, family=family
        )
        assert blocked.decision == "WATCH", family
        assert "pullback_rejection_missing" in blocked.rejection_reasons, family


def test_energy_and_stock_intraday_catalog_includes_pullback():
    oil = route_specialist(
        {"display": "WTI Oil", "symbol": "USOIL", "type": "commodity"}
    )
    assert "energy_oil_trend_pullback" in oil.setup_ids("intraday")
    aapl = route_specialist(
        {"display": "AAPL", "symbol": "AAPL.US", "type": "stock"}
    )
    assert "us_stock_single_trend_pullback" in aapl.setup_ids("intraday")


def test_xau_detect_setup_intraday_can_select_pullback():
    primary, context = _long_pullback_pair(rejection_wick=True)
    candles = {"H4": context, "H1": primary, "D1": context[::6] or context}
    route = route_specialist(
        {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"}
    )
    candidate = detect_setup(route, "intraday", candles)
    assert candidate.decision in {"TRADE", "WATCH"}
    assert candidate.setup_id in {
        "precious_london_ny_breakout_retest",
        "precious_trend_pullback",
    }
    assert "active_session_utc" in {item.name for item in candidate.predicates}
