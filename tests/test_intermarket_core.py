import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from intermarket import (
    build_aligned_return_frame,
    build_public_matrix_payload,
    build_scan_snapshot,
    build_symbol_context,
    classify_relationship_regime,
    compute_relationship_matrix,
    discover_active_universe,
    prepare_series_store,
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
