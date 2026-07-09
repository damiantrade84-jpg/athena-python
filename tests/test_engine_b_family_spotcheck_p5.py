"""Phase 5: one-pair-per-family Engine B paper/synthetic spot-check."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from market_structure import NakedEngine
from zone_registry import reset_zone_registry


FAMILY_PAIRS = {
    "forex": {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "score_group": "forex_majors",
    },
    "crypto": {
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "score_group": "crypto_btc",
    },
    "commodity": {
        "display": "XAU/USD",
        "symbol": "XAUUSD",
        "type": "commodity",
        "score_group": "precious_trackers",
    },
    "index": {
        "display": "S&P 500",
        "symbol": "US500",
        "type": "index",
        "score_group": "us_indices_trackers",
    },
    "equity": {
        "display": "AAPL",
        "symbol": "AAPL.US",
        "type": "stock",
        "score_group": "us_stock_single",
    },
}


def _candles(*, base: float = 100.0, slope: float = 0.08) -> dict[str, list[dict]]:
    start = datetime(2025, 3, 3, 8, 0, tzinfo=timezone.utc)
    frames: dict[str, list[dict]] = {}
    for tf, step, count in (
        ("D1", timedelta(days=1), 120),
        ("H4", timedelta(hours=4), 200),
        ("H1", timedelta(hours=1), 240),
    ):
        rows = []
        for idx in range(count):
            close = base + slope * idx
            rows.append(
                {
                    "time": (start + step * idx).isoformat(),
                    "open": close - 0.15,
                    "high": close + 0.6,
                    "low": close - 0.6,
                    "close": close,
                    "vol": 2_000 + idx * 3,
                }
            )
        frames[tf] = rows
    return frames


def test_family_spotcheck_engine_b_no_crash():
    reset_zone_registry()
    engine = NakedEngine()
    summary: dict[str, dict] = {}
    for family, pair in FAMILY_PAIRS.items():
        base = 50_000.0 if family == "crypto" else 100.0
        frames = _candles(base=base, slope=0.1 if family != "crypto" else 25.0)
        price = float(frames["H1"][-1]["close"])
        atr = max(base * 0.01, 1.0)
        engine.set_registry_context(pair["symbol"])
        pre = engine.precompute_structure_data(
            frames["D1"],
            frames["H4"],
            frames["H1"],
            price,
            atr,
            asset_type=pair["type"],
            pair=pair,
            style="intraday",
        )
        assert isinstance(pre, dict)
        assert pre.get("_error") is None
        res = engine.analyze_structure_direction(pre, price, "LONG")
        assert isinstance(res, dict)
        conf = engine.calculate_confidence(
            res,
            current_price=price,
            direction="LONG",
            entry_candles=frames["H1"],
            style_profile={
                "style": "intraday",
                "min_score": 4.0,
                "min_rr": 1.0,
                "fallback_rr": 2.0,
                "min_room_atr": 0.35,
                "require_macro_align": False,
                "score_group": pair["score_group"],
            },
        )
        assert "structure_ok" in conf
        assert "room_ok" in conf
        assert "passed" in conf
        zone_tf = res.get("zone_tf") or pre.get("_zone_tf") or pre.get("zone_tf")
        if zone_tf:
            assert str(zone_tf).upper() in {"H1", "H4", "D1"}
        # TRADE-quality rows must clear tightened structure/room when they pass.
        if conf.get("passed"):
            assert conf.get("structure_ok") is True
            assert conf.get("space_gate_ok") is True or conf.get("room_ok") is True
        summary[family] = {
            "structure_ok": conf.get("structure_ok"),
            "passed": conf.get("passed"),
            "score": conf.get("score"),
            "zone_tf": zone_tf,
        }
    assert set(summary) == set(FAMILY_PAIRS)


def test_family_spotcheck_edge_config_active():
    assert config.CONFIG.get("ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF") is True
    assert config.CONFIG.get("ENGINE_B_ROOM_NO_WALL_REQUIRE_MTF") is True
    ft = config.CONFIG.get("ENGINE_B_FOLLOW_THROUGH") or {}
    assert ft.get("ENABLED") is True
    assert ft.get("BLOCK_ENTRY_ON_TRAP") is True
    assert float(
        (config.CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS") or {}).get("LOW_VOLATILITY", 0)
    ) == 1.0
