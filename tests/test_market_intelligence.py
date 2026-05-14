from __future__ import annotations

import market_intelligence


def test_market_intelligence_unknown_symbol_is_stable(monkeypatch):
    monkeypatch.setattr(market_intelligence, "_event_context", lambda *a, **k: ([], {"status": "unavailable", "timestamp": None, "source": "test"}, ["events off"]))
    monkeypatch.setattr(market_intelligence, "_cot_context", lambda *a, **k: ({}, {"status": "unavailable", "timestamp": None, "source": "test"}, ["cot off"]))
    monkeypatch.setattr(market_intelligence, "_funding_context", lambda *a, **k: ({}, {"status": "unavailable", "timestamp": None, "source": "test"}, []))

    out = market_intelligence.get_market_intelligence(None, None, force_refresh=True)

    assert out["schema_version"] == "market_intelligence.v1"
    assert out["freshness_status"] == "unavailable"
    assert "symbol" in out["missing_fields"]
    assert isinstance(out["warnings"], list)


def test_market_intelligence_cache_and_force_refresh(monkeypatch):
    calls = {"n": 0}

    def fake_pair(symbol, asset_type):
        calls["n"] += 1
        return {"symbol": symbol, "asset_type": asset_type, "warnings": [], "freshness_status": "partial"}

    monkeypatch.setattr(market_intelligence, "build_pair_context", fake_pair)
    monkeypatch.setattr(market_intelligence, "_event_context", lambda *a, **k: ([], {"status": "partial", "timestamp": "t", "source": "test"}, []))
    monkeypatch.setattr(market_intelligence, "_cot_context", lambda *a, **k: ({}, {"status": "unavailable", "timestamp": None, "source": "test"}, []))
    monkeypatch.setattr(market_intelligence, "_funding_context", lambda *a, **k: ({}, {"status": "unavailable", "timestamp": None, "source": "test"}, []))

    first = market_intelligence.get_market_intelligence("EUR/USD", "forex", force_refresh=True)
    second = market_intelligence.get_market_intelligence("EUR/USD", "forex")
    third = market_intelligence.get_market_intelligence("EUR/USD", "forex", force_refresh=True)

    assert first["generated_at"] == second["generated_at"]
    assert third["generated_at"]
    assert calls["n"] == 2


def test_market_intelligence_combines_sources_pair_context_and_warnings(monkeypatch):
    monkeypatch.setattr(
        market_intelligence,
        "_event_context",
        lambda *a, **k: ([{"name": "FOMC"}], {"status": "fresh", "timestamp": "t1", "source": "events"}, []),
    )
    monkeypatch.setattr(
        market_intelligence,
        "_cot_context",
        lambda *a, **k: ({"z": 1.4}, {"status": "partial", "timestamp": "t2", "source": "cot"}, []),
    )
    monkeypatch.setattr(
        market_intelligence,
        "_funding_context",
        lambda *a, **k: ({"binance": 0.01}, {"status": "partial", "timestamp": "t3", "source": "funding"}, []),
    )
    monkeypatch.setattr(
        market_intelligence,
        "build_pair_context",
        lambda symbol, asset_type: {
            "symbol": symbol,
            "asset_type": asset_type,
            "freshness_status": "partial",
            "warnings": ["pair warning"],
        },
    )

    out = market_intelligence.get_market_intelligence("BTC/USDT", "crypto", force_refresh=True)

    assert out["freshness_status"] == "partial"
    assert out["macro_regime"]["calendar_within_72h"] == [{"name": "FOMC"}]
    assert out["positioning_extremes"]["cot"] == {"z": 1.4}
    assert out["positioning_extremes"]["funding"] == {"binance": 0.01}
    assert out["pair_context"]["symbol"] == "BTC/USDT"
    assert out["warnings"] == ["pair warning"]
