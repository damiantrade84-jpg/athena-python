from __future__ import annotations

import pytest

from athena_app.services.engine_b_hotlist import (
    _crypto_anchors_from_config,
    _select_slot,
    _thresholds_from_config,
    build_engine_b_hotlist,
)


_THRESHOLDS = _thresholds_from_config({})
_ANCHORS = _crypto_anchors_from_config(None)


def _cand(symbol: str, score: float, *, asset_type: str = "forex", fresh: str = "fresh", price: float | None = 1.0) -> dict:
    return {
        "symbol": symbol,
        "display": symbol,
        "asset_type": asset_type,
        "strength_score": score,
        "freshness_status": fresh,
        "latest_price": price,
        "reason": "test candidate",
    }


def _slot_for(ranked, *, group="forex", prior=None, action=None, anchors=None, time_now=1_000.0):
    return _select_slot(
        group=group,
        ranked=ranked,
        prior=prior,
        action=action,
        thresholds=_THRESHOLDS,
        anchors=anchors if anchors is not None else _ANCHORS,
        time_now=time_now,
    )


def _pair(symbol: str, display: str, pair_type: str, source: str = "mt5") -> dict:
    return {
        "symbol": symbol,
        "display": display,
        "type": pair_type,
        "source": source,
        "enabled": True,
    }


def test_hotlist_ranks_strongest_candidate_per_group():
    pairs = [
        _pair("EURUSD=X", "EUR/USD", "forex"),
        _pair("GBPUSD=X", "GBP/USD", "forex"),
        _pair("BTCUSDT", "BTC/USDT", "crypto", source="binance"),
        _pair("ETHUSDT", "ETH/USDT", "crypto", source="binance"),
    ]
    live_prices = {
        "EUR/USD": {"price": 1.101, "bid": 1.1009, "ask": 1.1011, "ts": 1_000.0, "change_pct": 0.55},
        "GBP/USD": {"price": 1.25, "bid": 1.2499, "ask": 1.2502, "ts": 820.0, "change_pct": 0.02},
        "BTC/USDT": {"price": 68_000.0, "bid": 67_995.0, "ask": 68_005.0, "ts": 1_000.0, "change_pct": 3.2},
        "ETH/USDT": {"price": 3_500.0, "bid": 3_499.0, "ask": 3_501.0, "ts": 970.0, "change_pct": 0.5},
    }

    def fetch_candles(pair, _tf, _limit):
        if pair["display"] == "EUR/USD":
            return [
                {"time": "2026-05-21T00:00:00Z", "open": 1.09, "high": 1.091, "low": 1.0895, "close": 1.0905},
                {"time": "2026-05-21T01:00:00Z", "open": 1.0905, "high": 1.094, "low": 1.09, "close": 1.0938},
                {"time": "2026-05-21T02:00:00Z", "open": 1.0938, "high": 1.1012, "low": 1.093, "close": 1.101},
            ]
        if pair["display"] == "BTC/USDT":
            return [
                {"time": "2026-05-21T00:00:00Z", "open": 66_000.0, "high": 66_400.0, "low": 65_900.0, "close": 66_300.0},
                {"time": "2026-05-21T01:00:00Z", "open": 66_300.0, "high": 67_000.0, "low": 66_200.0, "close": 66_950.0},
                {"time": "2026-05-21T02:00:00Z", "open": 66_950.0, "high": 68_100.0, "low": 66_900.0, "close": 68_000.0},
            ]
        return [
            {"time": "2026-05-21T00:00:00Z", "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0},
            {"time": "2026-05-21T01:00:00Z", "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0},
            {"time": "2026-05-21T02:00:00Z", "open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0},
        ]

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["forex", "crypto"],
        top_per_group=2,
        timeframe="H1",
        time_now=1_000.0,
    )

    assert payload["groups"]["forex"]["winner"]["symbol"] == "EURUSD"
    assert payload["groups"]["crypto"]["winner"]["symbol"] == "BTCUSDT"
    assert payload["groups"]["forex"]["candidates"][0]["rank"] == 1
    assert payload["groups"]["crypto"]["candidates"][0]["strength_score"] >= payload["groups"]["crypto"]["candidates"][1]["strength_score"]


def test_hotlist_handles_missing_live_prices_and_candles_without_crashing():
    pairs = [
        _pair("XAUUSD", "XAUUSD", "commodity"),
        _pair("NAS100", "NAS100", "index"),
    ]

    def fetch_candles(pair, _tf, _limit):
        if pair["symbol"] == "XAUUSD":
            raise RuntimeError("provider unavailable")
        return []

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices={},
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["commodity", "index"],
        top_per_group=1,
        timeframe="H1",
        time_now=1_000.0,
    )

    assert payload["success"] is True
    assert payload["groups"]["commodity"]["winner"]["symbol"] == "XAUUSD"
    assert payload["groups"]["index"]["winner"]["symbol"] == "NAS100"
    assert payload["groups"]["commodity"]["winner"]["latest_price"] is None


def test_hotlist_uses_asx_display_not_yahoo_index_ticker():
    pairs = [_pair("^AXJO", "ASX 200", "index")]
    live_prices = {
        "ASX 200": {"price": 8810.0, "ts": 1_000.0, "change_pct": 0.4},
        "^AXJO": {"price": 8810.0, "ts": 1_000.0, "change_pct": 0.4},
    }

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=lambda *_a, **_k: [],
        config={},
        groups_override=["index"],
        top_per_group=1,
        timeframe="H1",
        time_now=1_000.0,
    )

    winner = payload["groups"]["index"]["winner"]
    assert winner["symbol"] == "ASX 200"
    assert winner["display"] == "ASX 200"
    assert winner["latest_price"] == pytest.approx(8810.0)


def test_hotlist_returns_selected_symbols_and_read_only_scoring_contract():
    pairs = [
        _pair("EURUSD=X", "EUR/USD", "forex"),
        _pair("BTCUSDT", "BTC/USDT", "crypto", source="binance"),
    ]
    live_prices = {
        "EUR/USD": {"price": 1.101, "ts": 1_000.0, "change_pct": 0.3},
        "BTC/USDT": {"price": 68_000.0, "ts": 1_000.0, "change_pct": 2.2},
    }
    calls: list[tuple[str, str, int]] = []

    def fetch_candles(pair, tf, limit):
        calls.append((pair["display"], tf, limit))
        return [
            {"time": "2026-05-21T00:00:00Z", "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0},
            {"time": "2026-05-21T01:00:00Z", "open": 1.0, "high": 1.02, "low": 0.99, "close": 1.01},
        ]

    payload = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["forex", "crypto"],
        top_per_group=1,
        timeframe="M15",
        time_now=1_000.0,
    )

    assert payload["selectedSymbols"] == ["EURUSD", "BTCUSDT"]
    assert payload["scoring"] == {
        "usesAi": False,
        "usesScreenshots": False,
        "usesFullEngineB": False,
    }
    assert calls == [("EUR/USD", "M15", 8), ("BTC/USDT", "M15", 8)]


# ---------------------------------------------------------------------------
# Sticky slot / hysteresis behaviour
# ---------------------------------------------------------------------------


def test_weak_top_candidate_does_not_replace_current_symbol():
    ranked = [_cand("EURUSD", 50.0), _cand("GBPUSD", 45.0)]
    prior = {"symbol": "GBPUSD", "last_score": 45.0, "locked": False}
    slot = _slot_for(ranked, prior=prior)
    # New top (50) does not beat current (45) by the 15-point switch margin.
    assert slot["symbol"] == "GBPUSD"
    assert slot["switch_cause"] == "sticky"
    assert slot["switched"] is False


def test_candidate_replaces_current_only_when_score_beats_switch_margin():
    ranked = [_cand("EURUSD", 70.0), _cand("GBPUSD", 50.0)]
    prior = {"symbol": "GBPUSD", "last_score": 50.0, "locked": False}
    slot = _slot_for(ranked, prior=prior)
    # 70 >= 50 + 15 -> replace.
    assert slot["symbol"] == "EURUSD"
    assert slot["switch_cause"] == "switch_margin"
    assert slot["switched"] is True
    assert slot["previous_symbol"] == "GBPUSD"


def test_stale_current_symbol_can_be_replaced():
    ranked = [_cand("EURUSD", 56.0), _cand("GBPUSD", 60.0, fresh="stale")]
    prior = {"symbol": "GBPUSD", "last_score": 60.0, "locked": False}
    slot = _slot_for(ranked, prior=prior)
    # GBPUSD is the higher score but its data is stale, so the fresh winner wins.
    assert slot["symbol"] == "EURUSD"
    assert slot["switch_cause"] == "current_stale"


def test_missing_current_symbol_is_replaced():
    ranked = [_cand("EURUSD", 40.0)]
    prior = {"symbol": "GBPUSD", "last_score": 60.0, "locked": False}
    slot = _slot_for(ranked, prior=prior)
    assert slot["symbol"] == "EURUSD"
    assert slot["switch_cause"] == "current_missing"


def test_locked_symbol_never_auto_replaces():
    ranked = [_cand("EURUSD", 95.0), _cand("GBPUSD", 40.0)]
    prior = {"symbol": "GBPUSD", "last_score": 40.0, "locked": True}
    slot = _slot_for(ranked, prior=prior)
    # Even a massively stronger challenger cannot displace a locked symbol.
    assert slot["symbol"] == "GBPUSD"
    assert slot["switch_cause"] == "locked"
    assert slot["locked"] is True


def test_weak_symbol_replaced_after_ttl_even_without_margin():
    ranked = [_cand("EURUSD", 50.0), _cand("GBPUSD", 40.0)]
    # below_since far in the past (epoch 0) so the 900s weak TTL has elapsed.
    prior = {"symbol": "GBPUSD", "last_score": 40.0, "locked": False, "below_since": 0.0}
    slot = _slot_for(ranked, prior=prior, time_now=10_000.0)
    assert slot["symbol"] == "EURUSD"
    assert slot["switch_cause"] == "weak_ttl"


def test_no_candidate_above_scout_returns_weak_scout_or_no_hot_setup():
    # Fresh but weak -> WEAK_SCOUT, prime + AI review disabled.
    fresh_weak = _slot_for([_cand("BTCUSDT", 40.0, asset_type="crypto")], group="crypto", prior=None)
    assert fresh_weak["status"] == "WEAK_SCOUT"
    assert fresh_weak["eligible_for_prime"] is False
    assert fresh_weak["ai_review_enabled"] is False
    assert fresh_weak["reason"] == "market active but no candidate above scout threshold"

    # No price data -> NO_HOT_SETUP.
    no_data = _slot_for(
        [_cand("BTCUSDT", 40.0, asset_type="crypto", price=None)],
        group="crypto",
        prior=None,
    )
    assert no_data["status"] == "NO_HOT_SETUP"

    # Empty group -> NO_HOT_SETUP, no symbol.
    empty = _slot_for([], group="crypto", prior=None)
    assert empty["status"] == "NO_HOT_SETUP"
    assert empty["symbol"] is None


def test_crypto_anchor_preferred_unless_altcoin_significantly_stronger():
    # Altcoin only marginally ahead -> keep the anchor.
    ranked_close = [
        _cand("FOOUSDT", 60.0, asset_type="crypto"),
        _cand("BTCUSDT", 55.0, asset_type="crypto"),
    ]
    slot = _slot_for(ranked_close, group="crypto", prior=None)
    assert slot["symbol"] == "BTCUSDT"

    # Altcoin beats the best anchor by more than the switch margin -> altcoin wins.
    ranked_strong = [
        _cand("FOOUSDT", 80.0, asset_type="crypto"),
        _cand("BTCUSDT", 55.0, asset_type="crypto"),
    ]
    slot_strong = _slot_for(ranked_strong, group="crypto", prior=None)
    assert slot_strong["symbol"] == "FOOUSDT"


def test_manual_select_overrides_sticky_and_lock_round_trips_via_public_api():
    pairs = [
        _pair("BTCUSDT", "BTC/USDT", "crypto", source="binance"),
        _pair("ETHUSDT", "ETH/USDT", "crypto", source="binance"),
    ]
    live_prices = {
        "BTC/USDT": {"price": 68_000.0, "ts": 1_000.0, "change_pct": 3.2},
        "ETH/USDT": {"price": 3_500.0, "ts": 1_000.0, "change_pct": 0.4},
    }

    def fetch_candles(_pair, _tf, _limit):
        return []

    first = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["crypto"],
        timeframe="H1",
        time_now=1_000.0,
    )
    assert first["groups"]["crypto"]["winner"]["symbol"] == "BTCUSDT"
    slots = first["selectedSlots"]

    # Manually select ETHUSDT; it should stick even though BTC ranks higher.
    second = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["crypto"],
        timeframe="H1",
        time_now=1_010.0,
        selected_slots=slots,
        actions={"crypto": {"select": "ETHUSDT", "lock": True}},
    )
    assert second["groups"]["crypto"]["winner"]["symbol"] == "ETHUSDT"
    assert second["selectedSlots"]["crypto"]["locked"] is True

    # On the next plain refresh the locked ETH selection holds.
    third = build_engine_b_hotlist(
        pairs_universe=pairs,
        live_prices=live_prices,
        candle_fetch_fn=fetch_candles,
        config={},
        groups_override=["crypto"],
        timeframe="H1",
        time_now=1_020.0,
        selected_slots=second["selectedSlots"],
    )
    assert third["groups"]["crypto"]["winner"]["symbol"] == "ETHUSDT"
    assert "candidate" not in third["selectedSlots"]["crypto"]
