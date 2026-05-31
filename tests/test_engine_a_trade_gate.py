from engine_a_trade_gate import (
    annotate_engine_a_trade_eligibility,
    engine_a_trade_enabled,
    resolve_engine_a_trade_eligibility,
)


def _cfg(**overrides):
    base = {
        "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": True,
        "ENGINE_A_TRADE_ENABLED_DEFAULT": False,
        "ENGINE_A_TRADE_ENABLED_BY_CLASS": {
            "forex": False,
            "commodity": False,
            "index": False,
            "stock": False,
            "crypto": False,
        },
        "ENGINE_A_TRADE_ENABLED_BY_SCORE_GROUP": {},
        "ENGINE_A_TRADE_ENABLED_OVERRIDES": {},
    }
    base.update(overrides)
    return base


def test_forex_major_is_research_only_by_class_gate():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}

    detail = resolve_engine_a_trade_eligibility(pair, config=_cfg())

    assert detail["enabled"] is False
    assert detail["research_only"] is True
    assert detail["source"] == "class:forex"
    assert "research-only" in detail["reason"]


def test_display_override_can_enable_specific_evidence_qualified_pair():
    pair = {"display": "XAU/USD", "symbol": "XAUUSD=X", "type": "commodity"}
    cfg = _cfg(
        ENGINE_A_TRADE_ENABLED_OVERRIDES={
            "XAU/USD": True,
        }
    )

    detail = resolve_engine_a_trade_eligibility(pair, config=cfg)

    assert detail["enabled"] is True
    assert detail["research_only"] is False
    assert detail["source"] == "override:XAU/USD"
    assert engine_a_trade_enabled(pair, config=cfg) is True


def test_score_group_override_precedes_asset_class():
    pair = {
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "scoreGroup": "crypto_btc",
    }
    cfg = _cfg(
        ENGINE_A_TRADE_ENABLED_BY_CLASS={"crypto": False},
        ENGINE_A_TRADE_ENABLED_BY_SCORE_GROUP={"crypto_btc": True},
    )

    detail = resolve_engine_a_trade_eligibility(pair, config=cfg)

    assert detail["enabled"] is True
    assert detail["source"] == "score_group:crypto_btc"


def test_master_flag_off_preserves_legacy_trade_eligibility():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    cfg = _cfg(ENGINE_A_TRADE_ELIGIBILITY_ENABLED=False)

    detail = resolve_engine_a_trade_eligibility(pair, config=cfg)

    assert detail["enabled"] is True
    assert detail["research_only"] is False
    assert detail["source"] == "master_disabled"


def test_annotation_marks_disabled_signal_without_changing_score_or_direction():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    signal = {
        "direction": "LONG",
        "confluenceScore": 2.4,
        "warnings": [],
        "trade": True,
        "executable": True,
    }

    annotated = annotate_engine_a_trade_eligibility(signal, pair, config=_cfg())

    assert annotated is signal
    assert signal["direction"] == "LONG"
    assert signal["confluenceScore"] == 2.4
    assert signal["engineATradeEnabled"] is False
    assert signal["engineATradeGate"]["enabled"] is False
    assert signal["trade"] is False
    assert signal["executable"] is False
    assert any("ENGINE_A_RESEARCH_ONLY" in w for w in signal["warnings"])
