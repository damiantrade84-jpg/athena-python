import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from intermarket import (
    apply_confirmation_to_score,
    build_aligned_return_frame,
    build_public_matrix_payload,
    build_scan_snapshot,
    build_symbol_context,
    classify_relationship_regime,
    compute_relationship_matrix,
    discover_active_universe,
    prepare_series_store,
    resolve_us10y_real_yield_proxy,
    score_confirmation,
)


def _bars_from_returns(returns, start=None, hours=4, base=100.0):
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = float(base)
    bars = []
    for i, ret in enumerate(returns):
        open_price = price
        price = open_price * math.exp(float(ret))
        high = max(open_price, price) * 1.002
        low = min(open_price, price) * 0.998
        bars.append(
            {
                "time": (start + timedelta(hours=hours * i)).isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": price,
                "vol": 1000.0,
            }
        )
    return bars


def test_build_aligned_return_frame_aligns_inner_overlap():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars_a = _bars_from_returns([0.01] * 150, start=base)
    bars_b = _bars_from_returns([0.02] * 140, start=base + timedelta(hours=40))

    aligned = build_aligned_return_frame(
        {"A": bars_a, "B": bars_b},
        min_overlap_bars=100,
    )

    assert aligned["ok"] is True
    assert len(aligned["frame"]) >= 100
    assert list(aligned["frame"].columns) == ["A", "B"]


def test_build_aligned_return_frame_rejects_lagged_candles():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars_a = _bars_from_returns([0.01] * 140, start=base)
    bars_b = _bars_from_returns([0.01] * 140, start=base + timedelta(hours=12))

    aligned = build_aligned_return_frame(
        {"A": bars_a, "B": bars_b},
        min_overlap_bars=50,
    )

    assert aligned["ok"] is False
    assert aligned["reason"] == "lagged_candles"


def test_classify_relationship_regime_covers_broken_and_flipped():
    broken = classify_relationship_regime(
        {"correlation": -0.78, "stability": 0.20, "flippedRecently": False}
    )
    flipped = classify_relationship_regime(
        {"correlation": 0.71, "stability": 0.82, "flippedRecently": True}
    )

    assert broken["label"] == "broken / unstable"
    assert broken["broken"] is True
    assert flipped["label"] == "flipped recently"
    assert flipped["flippedRecently"] is True


def test_compute_relationship_matrix_marks_inverse_pair():
    returns_a = [0.01, -0.01] * 120
    returns_b = [-x for x in returns_a]
    universe = discover_active_universe(
        [
            {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "enabled": True},
            {"display": "USD/CHF", "symbol": "USDCHF", "type": "forex", "enabled": True},
        ]
    )
    store = {
        "EUR/USD": {
            "pair": universe["byCanonical"]["EUR/USD"],
            "close": pd.Series(
                [100 * math.exp(sum(returns_a[: i + 1])) for i in range(len(returns_a))],
                index=pd.to_datetime([b["time"] for b in _bars_from_returns(returns_a)], utc=True),
            ),
            "returns": pd.Series(
                returns_a,
                index=pd.to_datetime([b["time"] for b in _bars_from_returns(returns_a)], utc=True),
            ),
            "meta": {"latestTs": _bars_from_returns(returns_a)[-1]["time"], "stepSeconds": 14400},
        },
        "USD/CHF": {
            "pair": universe["byCanonical"]["USD/CHF"],
            "close": pd.Series(
                [100 * math.exp(sum(returns_b[: i + 1])) for i in range(len(returns_b))],
                index=pd.to_datetime([b["time"] for b in _bars_from_returns(returns_b)], utc=True),
            ),
            "returns": pd.Series(
                returns_b,
                index=pd.to_datetime([b["time"] for b in _bars_from_returns(returns_b)], utc=True),
            ),
            "meta": {"latestTs": _bars_from_returns(returns_b)[-1]["time"], "stepSeconds": 14400},
        },
    }

    matrix = compute_relationship_matrix(universe, store)
    rel = matrix["relationLookup"][("EUR/USD", "USD/CHF")]

    assert rel["regime"]["relation"] == "inverse"
    assert rel["current"]["correlation"] < 0


def test_score_confirmation_handles_inverse_macro_logic_and_lead_lag():
    raw_context = {
        "target": "EUR/USD",
        "drivers": [
            {
                "driver": "DXY",
                "driverAssetClass": "macro",
                "sourceType": "both",
                "priorRelation": "inverse",
                "effectivePriorRelation": "inverse",
                "summary": {
                    "regime": {"relation": "inverse", "label": "strongly inverse"},
                    "current": {
                        "correlation": -0.82,
                        "stability": 0.91,
                        "signPersistence": 0.88,
                        "volAdjustedScore": -0.70,
                        "driverRecentChangePct": -1.4,
                        "targetRecentChangePct": 1.1,
                        "window": 50,
                        "flippedRecently": False,
                        "lastBarContradiction": False,
                        "leadLag": {"leader": "driver", "evidence": "strong"},
                    },
                },
            }
        ],
        "unavailablePriors": [],
    }

    result = score_confirmation(
        raw_context,
        "LONG",
        {"display": "EUR/USD", "type": "forex"},
        config={
            "INTERMARKET_CONFIRMATION": {
                "enabled": True,
                "engine_a_enabled": True,
                "lead_lag_enabled": True,
            }
        },
    )

    assert result["verdict"] == "supportive"
    assert result["score"] > 0
    assert result["activeWindow"] == 50
    assert result["topSupporting"][0]["driver"] == "DXY"


def test_xau_long_dxy_down_inverse_relation_is_supportive():
    raw_context = {
        "target": "XAU/USD",
        "drivers": [
            {
                "driver": "DXY",
                "driverAssetClass": "macro",
                "sourceType": "both",
                "priorRelation": "inverse",
                "effectivePriorRelation": "inverse",
                "summary": {
                    "regime": {"relation": "inverse", "label": "strongly inverse"},
                    "current": {
                        "correlation": -0.82,
                        "stability": 0.91,
                        "signPersistence": 0.88,
                        "volAdjustedScore": -0.70,
                        "driverRecentChangePct": -1.4,
                        "targetRecentChangePct": 1.1,
                        "window": 50,
                        "flippedRecently": False,
                    },
                },
            }
        ],
    }

    result = score_confirmation(
        raw_context,
        "LONG",
        {"display": "XAU/USD", "type": "commodity"},
        config={"INTERMARKET_CONFIRMATION": {"enabled": True}},
    )

    assert result["verdict"] == "supportive"
    assert result["score"] > 0
    assert result["topSupporting"][0]["driver"] == "DXY"


def test_xau_long_dxy_up_inverse_relation_is_contradictory():
    raw_context = {
        "target": "XAU/USD",
        "drivers": [
            {
                "driver": "DXY",
                "driverAssetClass": "macro",
                "sourceType": "both",
                "priorRelation": "inverse",
                "effectivePriorRelation": "inverse",
                "summary": {
                    "regime": {"relation": "inverse", "label": "strongly inverse"},
                    "current": {
                        "correlation": -0.82,
                        "stability": 0.91,
                        "signPersistence": 0.88,
                        "volAdjustedScore": -0.70,
                        "driverRecentChangePct": 1.4,
                        "targetRecentChangePct": -1.1,
                        "window": 50,
                        "flippedRecently": False,
                    },
                },
            }
        ],
    }

    result = score_confirmation(
        raw_context,
        "LONG",
        {"display": "XAU/USD", "type": "commodity"},
        config={"INTERMARKET_CONFIRMATION": {"enabled": True}},
    )

    assert result["verdict"] == "contradictory"
    assert result["score"] < 0
    assert result["topContradictory"][0]["driver"] == "DXY"


def test_us10y_real_yield_proxy_resolver_returns_mocked_daily_series(monkeypatch):
    import intermarket

    intermarket._REAL_YIELD_CACHE["series"] = None
    intermarket._REAL_YIELD_CACHE["fetched_at"] = 0.0
    intermarket._REAL_YIELD_FAIL_UNTIL = 0.0

    def fake_fetch(_series_id, *, blocking=False):
        return [
            ("2026-01-01", 1.80),
            ("2026-01-02", 1.82),
            ("2026-01-03", 1.78),
            ("2026-01-04", 1.75),
        ]

    monkeypatch.setattr("intermarket._fetch_fred_daily_series", fake_fetch)

    proxy = resolve_us10y_real_yield_proxy(blocking=True)

    assert proxy is not None
    assert proxy["label"] == "US10Y_REAL_YIELD_PROXY"
    assert proxy["meta"]["source"] == "FRED:DFII10"
    assert proxy["meta"]["latestDate"] == "2026-01-04"
    assert proxy["meta"]["validBars"] == 4
    assert proxy["meta"]["status"] == "ok"
    assert proxy["returns"].index[-1].strftime("%Y-%m-%d") == "2026-01-04"


def test_daily_macro_proxy_alignment_uses_daily_target_returns_without_h4_forward_fill(monkeypatch):
    import intermarket

    intermarket._REAL_YIELD_CACHE["series"] = None
    intermarket._REAL_YIELD_CACHE["fetched_at"] = 0.0
    intermarket._REAL_YIELD_FAIL_UNTIL = 0.0

    target_returns = [0.01, -0.005, 0.006, -0.004, 0.007, -0.003] * 7
    h4_target = _bars_from_returns(target_returns, start=datetime(2026, 1, 1, tzinfo=timezone.utc), hours=4)
    real_yield_rows = [
        ("2026-01-01", 1.80),
        ("2026-01-02", 1.78),
        ("2026-01-03", 1.76),
        ("2026-01-04", 1.74),
        ("2026-01-05", 1.72),
        ("2026-01-06", 1.70),
        ("2026-01-07", 1.68),
    ]

    monkeypatch.setattr(
        "intermarket._fetch_fred_daily_series",
        lambda *_a, **_k: real_yield_rows,
    )
    universe = discover_active_universe(
        [{"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity", "enabled": True}]
    )
    store = {
        "XAU/USD": {
            "pair": universe["byCanonical"]["XAU/USD"],
            "label": "XAU/USD",
            "close": pd.Series(
                [bar["close"] for bar in h4_target],
                index=pd.to_datetime([bar["time"] for bar in h4_target], utc=True),
            ),
            "returns": pd.Series(
                target_returns,
                index=pd.to_datetime([bar["time"] for bar in h4_target], utc=True),
            ),
            "meta": {"latestTs": h4_target[-1]["time"], "stepSeconds": 14400},
            "assetClass": "commodity",
            "sourceType": "tradable",
        },
    }
    snapshot = {
        "universe": universe,
        "seriesStore": store,
        "relationLookup": {},
        "builtAt": "2026-01-05T00:00:00+00:00",
    }

    ctx = build_symbol_context(
        {"display": "XAU/USD", "type": "commodity"},
        snapshot,
        config={
            "INTERMARKET_CONFIRMATION": {
                "enabled": True,
                "windows": [2, 3],
                "min_overlap_bars": 3,
                "macro_priors": [
                    {"pair": "XAU/USD", "driver": "US10Y_REAL_YIELD_PROXY", "relation": "inverse"}
                ],
            }
        },
    )

    driver = ctx["drivers"][0]
    overlap = driver["summary"]["current"]["overlapBars"]
    assert driver["driver"] == "US10Y_REAL_YIELD_PROXY"
    assert overlap <= len(real_yield_rows) - 1
    assert overlap < len(h4_target) - 1


def test_insufficient_real_yield_overlap_marks_unavailable_prior(monkeypatch):
    import intermarket

    intermarket._REAL_YIELD_CACHE["series"] = None
    intermarket._REAL_YIELD_CACHE["fetched_at"] = 0.0
    intermarket._REAL_YIELD_FAIL_UNTIL = 0.0

    monkeypatch.setattr(
        "intermarket._fetch_fred_daily_series",
        lambda *_a, **_k: [("2026-01-01", 1.80), ("2026-01-02", 1.78)],
    )
    h4_target = _bars_from_returns([0.01] * 24, start=datetime(2026, 1, 1, tzinfo=timezone.utc), hours=4)
    universe = discover_active_universe(
        [{"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity", "enabled": True}]
    )
    store = {
        "XAU/USD": {
            "pair": universe["byCanonical"]["XAU/USD"],
            "label": "XAU/USD",
            "close": pd.Series(
                [bar["close"] for bar in h4_target],
                index=pd.to_datetime([bar["time"] for bar in h4_target], utc=True),
            ),
            "returns": pd.Series(
                [0.01] * 24,
                index=pd.to_datetime([bar["time"] for bar in h4_target], utc=True),
            ),
            "meta": {"latestTs": h4_target[-1]["time"], "stepSeconds": 14400},
            "assetClass": "commodity",
            "sourceType": "tradable",
        },
    }

    ctx = build_symbol_context(
        {"display": "XAU/USD", "type": "commodity"},
        {"universe": universe, "seriesStore": store, "relationLookup": {}},
        config={
            "INTERMARKET_CONFIRMATION": {
                "enabled": True,
                "windows": [20, 50, 90],
                "min_overlap_bars": 20,
                "macro_priors": [
                    {"pair": "XAU/USD", "driver": "US10Y_REAL_YIELD_PROXY", "relation": "inverse"}
                ],
            }
        },
    )

    assert ctx["drivers"] == []
    assert ctx["unavailablePriors"][0]["driver"] == "US10Y_REAL_YIELD_PROXY"
    assert ctx["unavailablePriors"][0]["reason"] == "insufficient_overlap"


def test_intermarket_delta_is_bounded_and_reports_no_legacy_double_adjustment():
    raw_context = {
        "enabled": True,
        "target": "XAU/USD",
        "drivers": [
            {
                "driver": "DXY",
                "driverAssetClass": "macro",
                "sourceType": "both",
                "priorRelation": "inverse",
                "effectivePriorRelation": "inverse",
                "summary": {
                    "regime": {"relation": "inverse", "label": "strongly inverse"},
                    "current": {
                        "correlation": -0.95,
                        "stability": 1.0,
                        "signPersistence": 1.0,
                        "volAdjustedScore": -1.0,
                        "driverRecentChangePct": -5.0,
                        "targetRecentChangePct": 5.0,
                        "window": 50,
                        "flippedRecently": False,
                    },
                },
            }
        ],
        "divergence": True,
        "divergence_score": 1.0,
    }

    adjusted = apply_confirmation_to_score(
        2.0,
        "LONG",
        {"display": "XAU/USD", "type": "commodity"},
        raw_context,
        max_score=3.0,
        config={
            "INTERMARKET_CONFIRMATION": {
                "enabled": True,
                "engine_a_enabled": True,
                "engine_a_score_cap": 0.18,
            }
        },
    )

    assert adjusted["confirmation"]["engineADelta"] <= 0.18
    assert adjusted["adjusted_score"] == pytest.approx(2.18)


def test_build_public_matrix_payload_filters_asset_classes():
    all_pairs = [
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "enabled": True},
        {"display": "WTI Oil", "symbol": "USOIL", "type": "commodity", "enabled": True},
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "enabled": True},
    ]
    candles = {
        "EUR/USD": _bars_from_returns([0.01] * 160),
        "WTI Oil": _bars_from_returns([0.01] * 160),
        "BTC/USDT": _bars_from_returns([-0.01] * 160),
    }
    universe = discover_active_universe(all_pairs)

    def _fetch(pair, _tf, _limit):
        return candles.get(pair["display"])

    snapshot = build_scan_snapshot(
        all_pairs,
        disabled_pairs=set(),
        etf_pairs=[],
        fetch_candles=_fetch,
        config={"INTERMARKET_CONFIRMATION": {"enabled": True}},
        preloaded_h4_candles=candles,
        force=True,
    )

    payload = build_public_matrix_payload(snapshot, asset_class_filter="forex:commodity")

    assert payload["success"] is True
    assert payload["relationships"]
    assert all(
        {
            row["targetAssetClass"],
            row["driverAssetClass"],
        }
        == {"forex", "commodity"}
        for row in payload["relationships"]
    )


def test_v3_signal_intermarket_attachment_pattern():
    """Mirror analyze_pair helper: confirmation attaches to v3-shaped signal dict."""
    from intermarket import apply_confirmation_to_score, build_symbol_context

    raw_context = {
        "target": "XAU/USD",
        "drivers": [
            {
                "driver": "DXY",
                "driverAssetClass": "macro",
                "sourceType": "both",
                "priorRelation": "inverse",
                "effectivePriorRelation": "inverse",
                "summary": {
                    "regime": {"relation": "inverse", "label": "strongly inverse"},
                    "current": {
                        "correlation": -0.82,
                        "stability": 0.91,
                        "signPersistence": 0.88,
                        "volAdjustedScore": -0.70,
                        "driverRecentChangePct": -1.4,
                        "targetRecentChangePct": 1.1,
                        "window": 50,
                        "flippedRecently": False,
                    },
                },
            }
        ],
        "unavailablePriors": [],
    }
    snapshot = {
        "universe": {"byCanonical": {"XAU/USD": {"display": "XAU/USD", "type": "commodity"}}},
        "seriesStore": {},
        "relationLookup": {},
    }
    pair = {"display": "XAU/USD", "type": "commodity"}
    ctx = build_symbol_context(
        pair,
        snapshot,
        config={
            "INTERMARKET_CONFIRMATION": {
                "enabled": True,
                "engine_a_enabled": True,
                "macro_priors": [{"pair": "XAU/USD", "driver": "DXY", "relation": "inverse"}],
            }
        },
    )
    # Use raw_context directly when matrix empty (same as rich driver path)
    ctx = raw_context
    signal = {
        "confluenceScore": 2.0,
        "score": 2.0,
        "maxScore": 3.0,
        "direction": "LONG",
    }
    result = apply_confirmation_to_score(
        signal["confluenceScore"],
        signal["direction"],
        pair,
        ctx,
        max_score=signal["maxScore"],
        config={"INTERMARKET_CONFIRMATION": {"enabled": True, "engine_a_enabled": True}},
    )
    signal["intermarketConfirmation"] = result["confirmation"]
    signal["intermarketEngineADelta"] = result["confirmation"]["engineADelta"]
    signal["confluenceScore"] = result["adjusted_score"]
    signal["score"] = result["adjusted_score"]

    assert signal["intermarketConfirmation"]["verdict"] == "supportive"
    assert signal["intermarketEngineADelta"] > 0
    assert signal["confluenceScore"] > 2.0
