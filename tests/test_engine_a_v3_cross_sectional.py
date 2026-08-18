"""Focused tests for Engine A v3 explicit cross-sectional ranking."""

from __future__ import annotations

from pathlib import Path

from engine_a_v3.cross_sectional import (
    CONFIG_KEY,
    RANKING_FIELD,
    apply_cross_sectional_ranking,
    backtest_ranking_blocks_trade,
    evaluate_backtest_ranking,
    rank_cross_sectional_cohort,
    ranking_enabled,
    resolve_cross_sectional_config,
)


def _cfg(**overrides) -> dict:
    block = {
        "ENABLED": True,
        "METHOD": "top_n",
        "TOP_N": 2,
        "PERCENTILE": 0.20,
        "GROUP_BY": "score_group",
        "CUSTOM_GROUPS": {},
        "MIN_SCORE_FLOOR": 0.0,
        "MIN_GROUP_SIZE": 1,
        "REQUIRE_TRADE_DECISION": True,
        "REQUIRE_QUALIFIED": True,
        "APPLY_IN_LIVE_SCAN": True,
        "APPLY_IN_BACKTEST": True,
        "TIE_BREAKERS": ["conviction", "direction_confidence", "location_quality"],
    }
    block.update(overrides)
    return {CONFIG_KEY: block}


def _trade(**overrides) -> dict:
    signal = {
        "engine": "ENGINE_A_V3",
        "contractVersion": "3.1.0",
        "decision": "TRADE",
        "qualified": True,
        "engineATradeEnabled": True,
        "trade": True,
        "executable": True,
        "execution_permitted": True,
        "signalTier": "trade",
        "setupLabel": "TRADE",
        "executionScope": "DEMO_ONLY",
        "direction": "LONG",
        "confluenceScore": 2.2,
        "conviction": 0.70,
        "scoreNorm": 0.70,
        "maxScore": 3.0,
        "scoreGroup": "forex_majors",
        "type": "forex",
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "rejectionReasons": [],
        "factorDiagnostics": {
            "components": {
                "trend": {"signal": 0.8, "quality": 0.7},
                "location": {"signal": 0.2, "quality": 0.6},
            }
        },
        "scanDiagnostics": [],
    }
    signal.update(overrides)
    return signal


def test_disabled_mode_is_identity_and_adds_no_fields() -> None:
    first = _trade(display="EUR/USD", pair="EUR/USD", confluenceScore=2.4)
    second = _trade(display="GBP/USD", pair="GBP/USD", confluenceScore=2.1)
    before = [dict(first), dict(second)]
    report = apply_cross_sectional_ranking(
        [first, second],
        surface="live_scan",
        config=_cfg(ENABLED=False),
    )
    assert report["applied"] is False
    assert ranking_enabled(_cfg(ENABLED=False)) is False
    assert first == before[0]
    assert second == before[1]
    assert RANKING_FIELD not in first
    assert RANKING_FIELD not in second


def test_top_n_keeps_strongest_and_demotes_the_rest() -> None:
    rows = [
        _trade(display="EUR/USD", pair="EUR/USD", confluenceScore=2.50, conviction=0.80),
        _trade(display="GBP/USD", pair="GBP/USD", confluenceScore=2.30, conviction=0.75),
        _trade(display="USD/JPY", pair="USD/JPY", confluenceScore=2.10, conviction=0.70),
        _trade(display="AUD/USD", pair="AUD/USD", confluenceScore=1.95, conviction=0.65),
    ]
    report = apply_cross_sectional_ranking(rows, surface="live_scan", config=_cfg(TOP_N=2))
    assert report["applied"] is True
    assert report["accepted"] == 2
    assert report["demoted"] == 2
    assert rows[0]["decision"] == "TRADE"
    assert rows[1]["decision"] == "TRADE"
    assert rows[2]["decision"] == "WATCH"
    assert rows[3]["decision"] == "WATCH"
    assert rows[0][RANKING_FIELD]["rank"] == 1
    assert rows[1][RANKING_FIELD]["rank"] == 2
    assert rows[2][RANKING_FIELD]["accepted"] is False
    assert "cross_sectional_rank_below_cutoff" in rows[2]["rejectionReasons"]
    assert rows[2]["engineATradeEnabled"] is False
    assert rows[2]["setupLabel"] == "WATCH"
    assert rows[0]["factorDiagnostics"][RANKING_FIELD]["accepted"] is True


def test_percentile_keeps_top_fraction() -> None:
    rows = [
        _trade(display=f"P{i}", pair=f"P{i}", confluenceScore=2.0 + i * 0.1)
        for i in range(5)
    ]
    apply_cross_sectional_ranking(
        rows,
        surface="live_scan",
        config=_cfg(METHOD="percentile", PERCENTILE=0.20),
    )
    accepted = [row for row in rows if row["decision"] == "TRADE"]
    assert len(accepted) == 1
    assert accepted[0]["pair"] == "P4"


def test_empty_cohort_is_a_noop() -> None:
    report = apply_cross_sectional_ranking([], surface="live_scan", config=_cfg())
    assert report["applied"] is True
    assert report["considered"] == 0
    assert rank_cross_sectional_cohort([], surface="live_scan", config=_cfg()) == []


def test_single_pair_group_accepted_when_above_floor() -> None:
    signal = _trade(confluenceScore=2.2)
    apply_cross_sectional_ranking([signal], surface="live_scan", config=_cfg(TOP_N=3))
    assert signal["decision"] == "TRADE"
    assert signal[RANKING_FIELD]["rank"] == 1
    assert signal[RANKING_FIELD]["eligibleCount"] == 1
    assert signal[RANKING_FIELD]["accepted"] is True


def test_below_min_score_floor_is_rejected_even_if_only_name() -> None:
    signal = _trade(confluenceScore=1.4)
    apply_cross_sectional_ranking(
        [signal],
        surface="live_scan",
        config=_cfg(MIN_SCORE_FLOOR=1.8, TOP_N=3),
    )
    assert signal["decision"] == "WATCH"
    assert signal[RANKING_FIELD]["reason"] == "cross_sectional_below_min_score_floor"
    assert signal[RANKING_FIELD]["accepted"] is False


def test_ties_break_on_conviction_then_pair_name() -> None:
    high = _trade(
        display="EUR/USD",
        pair="EUR/USD",
        confluenceScore=2.20,
        conviction=0.90,
    )
    low = _trade(
        display="GBP/USD",
        pair="GBP/USD",
        confluenceScore=2.20,
        conviction=0.40,
    )
    apply_cross_sectional_ranking([low, high], surface="live_scan", config=_cfg(TOP_N=1))
    assert high["decision"] == "TRADE"
    assert low["decision"] == "WATCH"
    assert high[RANKING_FIELD]["rank"] == 1
    assert low[RANKING_FIELD]["rank"] == 2


def test_watch_is_never_upgraded() -> None:
    watch = _trade(decision="WATCH", qualified=False, confluenceScore=2.9, setupLabel="WATCH")
    trade = _trade(display="GBP/USD", pair="GBP/USD", confluenceScore=2.1)
    apply_cross_sectional_ranking(
        [watch, trade],
        surface="live_scan",
        config=_cfg(TOP_N=1, REQUIRE_TRADE_DECISION=True),
    )
    assert watch["decision"] == "WATCH"
    assert trade["decision"] == "TRADE"
    assert watch[RANKING_FIELD]["eligible"] is False
    assert watch[RANKING_FIELD]["reason"] == "not_trade_decision"


def test_grouping_by_score_group_is_independent() -> None:
    majors = [
        _trade(display="EUR/USD", pair="EUR/USD", scoreGroup="forex_majors", confluenceScore=2.4),
        _trade(display="GBP/USD", pair="GBP/USD", scoreGroup="forex_majors", confluenceScore=2.1),
    ]
    crypto = [
        _trade(
            display="BTC/USDT",
            pair="BTC/USDT",
            scoreGroup="crypto_btc",
            type="crypto",
            confluenceScore=2.0,
        ),
        _trade(
            display="ETH/USDT",
            pair="ETH/USDT",
            scoreGroup="crypto_btc",
            type="crypto",
            confluenceScore=1.8,
        ),
    ]
    rows = majors + crypto
    apply_cross_sectional_ranking(rows, surface="live_scan", config=_cfg(TOP_N=1))
    assert majors[0]["decision"] == "TRADE"
    assert majors[1]["decision"] == "WATCH"
    assert crypto[0]["decision"] == "TRADE"
    assert crypto[1]["decision"] == "WATCH"


def test_custom_group_map_and_asset_type_grouping() -> None:
    eurusd = _trade(display="EUR/USD", pair="EUR/USD", confluenceScore=2.4)
    btc = _trade(
        display="BTC/USDT",
        pair="BTC/USDT",
        type="crypto",
        scoreGroup="crypto_btc",
        confluenceScore=2.3,
    )
    apply_cross_sectional_ranking(
        [eurusd, btc],
        surface="live_scan",
        config=_cfg(
            TOP_N=1,
            GROUP_BY="custom",
            CUSTOM_GROUPS={"EUR/USD": "g10", "BTC/USDT": "g10"},
        ),
    )
    assert eurusd["decision"] == "TRADE"
    assert btc["decision"] == "WATCH"
    assert eurusd[RANKING_FIELD]["groupKey"] == "g10"

    usd = _trade(display="AAPL", pair="AAPL", type="stock", scoreGroup="us_stock_single", confluenceScore=2.2)
    eurusd2 = _trade(display="EUR/USD", pair="EUR/USD", confluenceScore=2.0)
    apply_cross_sectional_ranking(
        [usd, eurusd2],
        surface="live_scan",
        config=_cfg(TOP_N=1, GROUP_BY="asset_type"),
    )
    assert usd[RANKING_FIELD]["groupKey"] == "stock"
    assert eurusd2[RANKING_FIELD]["groupKey"] == "forex"
    assert usd["decision"] == "TRADE"
    assert eurusd2["decision"] == "TRADE"


def test_engine_b_stub_is_not_mutated() -> None:
    stub = {"engine": "B", "engine_source": "ENGINE_B", "display": "EUR/USD", "confluenceScore": 0.0}
    v3 = _trade(confluenceScore=2.2)
    apply_cross_sectional_ranking(
        [({"display": "EUR/USD"}, stub), ({"display": "GBP/USD"}, v3)],
        surface="live_scan",
        config=_cfg(),
    )
    assert "crossSectionalRanking" not in stub
    assert v3[RANKING_FIELD]["accepted"] is True


def test_live_and_backtest_surface_flags() -> None:
    signal = _trade()
    live_off = apply_cross_sectional_ranking(
        [signal],
        surface="live_scan",
        config=_cfg(APPLY_IN_LIVE_SCAN=False, APPLY_IN_BACKTEST=True),
    )
    assert live_off["applied"] is False
    assert RANKING_FIELD not in signal

    blocked, diagnostic = backtest_ranking_blocks_trade(
        signal,
        {"display": "EUR/USD", "type": "forex"},
        config=_cfg(APPLY_IN_BACKTEST=False, APPLY_IN_LIVE_SCAN=True),
    )
    assert blocked is False
    assert diagnostic.get("applied") is False


def test_backtest_universe_of_one_respects_floor() -> None:
    signal = _trade(confluenceScore=1.2)
    blocked, diagnostic = backtest_ranking_blocks_trade(
        signal,
        {"display": "EUR/USD", "scoreGroup": "forex_majors", "type": "forex"},
        config=_cfg(MIN_SCORE_FLOOR=1.8, APPLY_IN_BACKTEST=True),
    )
    assert blocked is True
    assert diagnostic["reason"] == "cross_sectional_below_min_score_floor"
    # Frozen-style object path must not require mutation.
    assert signal["decision"] == "TRADE"

    ok = _trade(confluenceScore=2.2)
    blocked_ok, diagnostic_ok = backtest_ranking_blocks_trade(
        ok,
        {"display": "EUR/USD", "scoreGroup": "forex_majors", "type": "forex"},
        config=_cfg(MIN_SCORE_FLOOR=1.8, APPLY_IN_BACKTEST=True),
    )
    assert blocked_ok is False
    assert diagnostic_ok["accepted"] is True
    assert diagnostic_ok["rank"] == 1


def test_invalid_enabled_config_does_not_rank() -> None:
    cfg = resolve_cross_sectional_config({CONFIG_KEY: {"ENABLED": True, "METHOD": "not_a_method"}})
    assert cfg.enabled is True
    assert cfg.valid is False
    signal = _trade()
    report = apply_cross_sectional_ranking([signal], surface="live_scan", config={CONFIG_KEY: {"ENABLED": True, "METHOD": "not_a_method"}})
    assert report["applied"] is False
    assert signal["decision"] == "TRADE"


def test_pair_tuple_cohort_uses_pair_score_group() -> None:
    signal = _trade(scoreGroup=None, display="EUR/USD")
    pair = {"display": "EUR/USD", "score_group": "forex_majors", "type": "forex"}
    apply_cross_sectional_ranking([(pair, signal)], surface="live_scan", config=_cfg())
    assert signal[RANKING_FIELD]["groupKey"] == "forex_majors"


def test_scanner_and_backtest_call_the_batch_hook() -> None:
    root = Path(__file__).resolve().parents[1]
    scanner = (root / "scanner.py").read_text(encoding="utf-8")
    backtest = (root / "engine_a_v3" / "backtest.py").read_text(encoding="utf-8")
    replay = (root / "athena_backtesting_v2" / "replay.py").read_text(encoding="utf-8")
    evaluator = (root / "engine_a_v3" / "evaluator.py").read_text(encoding="utf-8")
    assert "apply_cross_sectional_ranking" in scanner
    assert 'surface="live_scan"' in scanner
    assert "backtest_ranking_blocks_trade" in backtest
    assert "backtest_ranking_blocks_trade" in replay
    assert "apply_cross_sectional_ranking" not in evaluator


def test_evaluate_backtest_ranking_disabled_is_not_applied() -> None:
    diagnostic = evaluate_backtest_ranking(_trade(), config=_cfg(ENABLED=False))
    assert diagnostic["applied"] is False


def test_min_group_size_skips_cutoff_but_keeps_floor() -> None:
    strong = _trade(display="EUR/USD", pair="EUR/USD", confluenceScore=2.4)
    weak = _trade(display="GBP/USD", pair="GBP/USD", confluenceScore=2.1)
    apply_cross_sectional_ranking(
        [strong, weak],
        surface="live_scan",
        config=_cfg(TOP_N=1, MIN_GROUP_SIZE=5),
    )
    assert strong["decision"] == "TRADE"
    assert weak["decision"] == "TRADE"
    assert strong[RANKING_FIELD]["reason"] == "group_below_min_size"
    assert weak[RANKING_FIELD]["reason"] == "group_below_min_size"


def test_checked_in_default_is_disabled() -> None:
    from config import CONFIG

    resolved = resolve_cross_sectional_config(CONFIG)
    assert resolved.enabled is False
    assert ranking_enabled(CONFIG) is False
