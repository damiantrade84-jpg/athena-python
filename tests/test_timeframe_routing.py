from ai_review.timeframe_routing import (
    normalize_asset_route_group,
    normalize_timeframe,
    resolve_timeframe_route,
)


def _comparison(**overrides):
    base = {
        "engineAProvided": True,
        "engineABiasValid": True,
        "engineAPassed": True,
        "chartConfirmsEngineADirection": True,
        "chartContradictsEntryTiming": True,
        "comparisonVerdict": "engine_a_direction_confirmed_entry_rejected",
        "finalDecision": "wait",
    }
    base.update(overrides)
    return base


def test_h4_crypto_entry_rejected_routes_to_h1_entry():
    route = resolve_timeframe_route(
        asset_group="crypto_btc",
        context_tf="H4",
        ai_review={"human_action": "wait"},
        verdict_comparison=_comparison(),
    )

    assert route["schemaVersion"] == "timeframe_route.v1"
    assert route["enabled"] is True
    assert route["engine"] == "A"
    assert route["assetGroup"] == "crypto"
    assert route["sourceGroup"] == "crypto_btc"
    assert route["contextTf"] == "H4"
    assert route["entryTf"] == "H1"
    assert route["executionTf"] == "M15"
    assert route["autoSelectTf"] == "H1"
    assert route["mode"] == "entry_wait"
    assert "monitor H1" in route["reason"]
    assert "execute" not in route["reason"].lower()
    assert "order" not in route["reason"].lower()


def test_h4_forex_wait_with_confirmed_direction_routes_to_h1_entry():
    route = resolve_timeframe_route(
        asset_group="forex_majors",
        context_tf="4H",
        ai_review={"human_action": "wait"},
        verdict_comparison=_comparison(chartContradictsEntryTiming=False, comparisonVerdict="engine_a_confirmed"),
    )

    assert route["assetGroup"] == "forex"
    assert route["contextTf"] == "H4"
    assert route["entryTf"] == "H1"
    assert route["executionTf"] == "M15"
    assert route["autoSelectTf"] == "H1"
    assert route["mode"] == "entry_wait"


def test_d1_stock_uses_stock_route():
    route = resolve_timeframe_route(
        asset_group="us_stock_single",
        context_tf="D",
        ai_review={"human_action": "take"},
        verdict_comparison=_comparison(
            chartContradictsEntryTiming=False,
            comparisonVerdict="engine_a_confirmed",
            finalDecision="trade",
        ),
    )

    assert route["assetGroup"] == "stocks"
    assert route["contextTf"] == "D1"
    assert route["entryTf"] == "H4"
    assert route["executionTf"] == "H1"
    assert route["autoSelectTf"] == "D1"
    assert route["mode"] == "context"


def test_h1_indices_route_to_m15_entry_and_execution():
    route = resolve_timeframe_route(
        asset_group="us_indices_trackers",
        context_tf="60",
        ai_review={"human_action": "wait"},
        verdict_comparison=_comparison(),
    )

    assert route["assetGroup"] == "indices"
    assert route["contextTf"] == "H1"
    assert route["entryTf"] == "M15"
    assert route["executionTf"] == "M15"
    assert route["autoSelectTf"] == "M15"


def test_unknown_group_uses_default_route():
    route = resolve_timeframe_route(
        asset_group="not_a_group",
        context_tf="H4",
        ai_review={"human_action": "take"},
        verdict_comparison=_comparison(
            chartConfirmsEngineADirection=False,
            chartContradictsEntryTiming=False,
            comparisonVerdict="unknown",
            finalDecision="trade",
        ),
    )

    assert route["assetGroup"] == "default"
    assert route["entryTf"] == "H1"
    assert route["executionTf"] == "M15"
    assert route["autoSelectTf"] == "H4"


def test_tf_aliases_normalize_to_backend_timeframes():
    assert normalize_timeframe("240") == "H4"
    assert normalize_timeframe("4H") == "H4"
    assert normalize_timeframe("60") == "H1"
    assert normalize_timeframe("1H") == "H1"
    assert normalize_timeframe("15") == "M15"
    assert normalize_timeframe("5") == "M5"
    assert normalize_timeframe("1") == "M1"
    assert normalize_timeframe("D") == "D1"
    assert normalize_timeframe("W") == "W1"


def test_asset_route_group_aliases_normalize_to_broad_groups():
    assert normalize_asset_route_group("crypto_eth") == "crypto"
    assert normalize_asset_route_group("forex_exotics") == "forex"
    assert normalize_asset_route_group("commodity") == "commodities"
    assert normalize_asset_route_group("indices") == "indices"
    assert normalize_asset_route_group("etf") == "stocks"
