"""Routing coverage for the 2026-07-28 ATFX portfolio additions.

Scope is deliberately narrow: score-group membership, timeframe-policy
resolution, suffix canonicalization and the special-instrument pins added for
the 249 new symbols. It also locks the pre-existing book so a future edit to the
shared taxonomy tables cannot move an older pair without a test failing.

Nothing here boots the Athena runtime — timeframe_policy, engine_a_groups and
style_resolver are all import-safe.
"""
from __future__ import annotations

import pytest
import yaml

from engine_a_groups import (
    CASH_EQUITY_EUROPE_TICKERS,
    resolve_cash_equity_region,
    resolve_score_group_by_type,
)
from style_resolver import resolve_auto_style
from timeframe_policy import (
    LiquidityClass,
    PolicySource,
    SpeedClass,
    SpeedState,
    Timeframe,
    TIMEFRAME_LADDER,
    canonical_symbol,
    describe_symbol_policy,
    resolve_timeframe_policy,
)

# ── The additions, exactly as commit 274ad604 declares them ──────────────────

G10_BROAD_CROSSES = [
    "AUD/CAD", "CAD/CHF", "EUR/CAD", "EUR/NZD", "GBP/CAD",
    "GBP/CHF", "GBP/NZD", "NZD/CAD", "NZD/CHF",
]
JPY_CROSSES = ["CAD/JPY", "CHF/JPY", "NZD/JPY"]
SCANDI_USD = ["USD/NOK", "USD/SEK"]
CEE_EM = ["EUR/HUF", "EUR/PLN", "USD/CZK", "USD/HUF", "USD/PLN"]
ZAR_CROSSES = ["EUR/ZAR", "GBP/ZAR"]
MANAGED_ASIA = ["USD/CNH", "USD/HKD"]
MANAGED_EUR_PROXY = ["USD/DKK"]

NEW_FOREX = (
    G10_BROAD_CROSSES + JPY_CROSSES + SCANDI_USD + CEE_EM
    + ZAR_CROSSES + MANAGED_ASIA + MANAGED_EUR_PROXY
)
NEW_METALS = ["XAU/ZAR"]
NEW_INDICES = ["China A50", "Spain 35", "France 40", "Italy 40"]


def _fx(display):
    return {"display": display, "symbol": display.replace("/", "") + "=X", "type": "forex"}


def _index(display, broker):
    return {"display": display, "symbol": broker, "type": "index"}


INDEX_BROKER = {
    "China A50": "CHI50", "Spain 35": "ESP35",
    "France 40": "FRA40", "Italy 40": "IT40",
}


def _share_tickers():
    """The ATFX share list is machine-local config; skip if absent."""
    try:
        cfg = yaml.safe_load(open("config.local.yaml", encoding="utf-8")) or {}
    except FileNotFoundError:
        pytest.skip("config.local.yaml not present")
    tickers = cfg.get("MT5_ATFX_SHARE_TICKERS")
    if not tickers:
        pytest.skip("MT5_ATFX_SHARE_TICKERS not configured")
    return [str(t).strip() for t in tickers]


# ── Mapping completeness and counts ──────────────────────────────────────────

def test_addition_counts_match_the_declared_portfolio():
    tickers = _share_tickers()
    assert len(NEW_FOREX) == 24
    assert len(NEW_METALS) == 1
    assert len(NEW_INDICES) == 4
    assert len(tickers) == 220
    assert len(NEW_FOREX) + len(NEW_METALS) + len(NEW_INDICES) + len(tickers) == 249


def test_every_addition_resolves_without_falling_through():
    pairs = (
        [_fx(d) for d in NEW_FOREX]
        + [{"display": "XAU/ZAR", "symbol": "XAUZAR=X", "type": "commodity"}]
        + [_index(d, INDEX_BROKER[d]) for d in NEW_INDICES]
        + [{"display": t, "symbol": t, "type": "stock"} for t in _share_tickers()]
    )
    assert len(pairs) == 249
    for pair in pairs:
        group = resolve_score_group_by_type(pair)
        assert group != "unknown", pair["display"]
        policy = resolve_timeframe_policy(
            pair["display"], pair["type"], group,
            resolve_auto_style("auto", pair, score_group=group,
                               asset_type=pair["type"]),
        )
        assert policy.policy_source is not PolicySource.SAFE_FALLBACK, pair["display"]
        assert not policy.diagnostics.safe_fallback, pair["display"]
        assert not policy.diagnostics.config_conflict, pair["display"]


@pytest.mark.parametrize("displays,expected_group", [
    (G10_BROAD_CROSSES, "forex_crosses"),
    (JPY_CROSSES, "forex_crosses"),
    (SCANDI_USD, "forex_crosses"),
    (MANAGED_EUR_PROXY, "forex_crosses"),
    (["USD/CNH"], "forex_crosses"),
    (CEE_EM, "forex_exotics"),
    (ZAR_CROSSES, "forex_exotics"),
    (["USD/HKD"], "forex_exotics"),
])
def test_new_forex_score_groups(displays, expected_group):
    for display in displays:
        assert resolve_score_group_by_type(_fx(display)) == expected_group, display


def test_new_indices_join_their_regional_groups():
    assert resolve_score_group_by_type(_index("China A50", "CHI50")) == "asian_indices"
    for display in ("Spain 35", "France 40", "Italy 40"):
        assert resolve_score_group_by_type(
            _index(display, INDEX_BROKER[display])) == "eu_indices"


def test_share_region_counts():
    tickers = _share_tickers()
    regions = [resolve_cash_equity_region(t) for t in tickers]
    assert regions.count("CASH_EQUITY_HONG_KONG") == 46
    assert regions.count("CASH_EQUITY_EUROPE") == 32
    # 142, not the plan's 141: SPCX is a US listing. Its new-listing handling is
    # a history guard, not a region, and that guard does not exist yet.
    assert regions.count("CASH_EQUITY_US") == 142
    assert len(CASH_EQUITY_EUROPE_TICKERS) == 32
    assert "SPCX" in tickers


def test_no_ticker_lands_in_two_regions():
    tickers = _share_tickers()
    assert len({t.upper() for t in tickers}) == len(tickers)
    for ticker in tickers:
        assert resolve_cash_equity_region(ticker) in {
            "CASH_EQUITY_US", "CASH_EQUITY_EUROPE", "CASH_EQUITY_HONG_KONG",
        }


def test_brkb_keeps_its_broker_casing_and_identity():
    tickers = _share_tickers()
    assert "BRKb" in tickers
    assert "BRKB" not in tickers
    assert resolve_cash_equity_region("BRKb") == "CASH_EQUITY_US"
    assert resolve_cash_equity_region("brkb") == "CASH_EQUITY_US"
    assert resolve_cash_equity_region("#BRKb") == "CASH_EQUITY_US"


# ── Ladders ──────────────────────────────────────────────────────────────────

def _ladder(display, asset_type, style="intraday", engine_id="engine_a"):
    pair = {"display": display, "type": asset_type,
            "symbol": display.replace("/", "")}
    policy = resolve_timeframe_policy(
        display, asset_type, resolve_score_group_by_type(pair), style,
        engine_id=engine_id,
    )
    return (policy.regime_tf, policy.bias_tf, policy.structure_tf,
            policy.setup_tf, policy.trigger_tf)


D1, H4, H1, M30, M15, M5 = (Timeframe.D1, Timeframe.H4, Timeframe.H1,
                            Timeframe.M30, Timeframe.M15, Timeframe.M5)
# Universal Engine A/B ladder for every group and pair.
UNIVERSAL = (D1, H4, H4, H1, M15)


def test_g10_broad_crosses_use_universal_ladder():
    for display in G10_BROAD_CROSSES + MANAGED_EUR_PROXY:
        assert _ladder(display, "forex") == UNIVERSAL, display


def test_jpy_and_scandi_crosses_use_universal_ladder():
    for display in JPY_CROSSES + SCANDI_USD + ["USD/CNH"]:
        assert _ladder(display, "forex") == UNIVERSAL, display


def test_cee_crosses_use_universal_ladder():
    for display in CEE_EM:
        assert _ladder(display, "forex") == UNIVERSAL, display


def test_zar_crosses_and_usdhkd_use_universal_ladder():
    for display in ZAR_CROSSES + ["USD/HKD"]:
        assert _ladder(display, "forex") == UNIVERSAL, display


def test_china_a50_uses_universal_ladder():
    assert _ladder("China A50", "index") == UNIVERSAL


def test_xauzar_and_xauusd_share_universal_ladder():
    assert _ladder("XAU/ZAR", "commodity") == UNIVERSAL
    assert _ladder("XAU/USD", "commodity") == UNIVERSAL


# ── Special-instrument rules ─────────────────────────────────────────────────

def _trigger_is_not_faster_than(display, asset_type, floor):
    limit = TIMEFRAME_LADDER.index(floor)
    for engine_id in ("engine_a", "engine_b"):
        for style in ("intraday", "swing"):
            trigger = _ladder(display, asset_type, style, engine_id)[4]
            assert TIMEFRAME_LADDER.index(trigger) <= limit, (
                f"{display} {engine_id}/{style} trigger={trigger}")


def test_usdhkd_uses_m15_trigger_on_any_engine_or_style():
    for engine_id in ("engine_a", "engine_b"):
        for style in ("intraday", "swing"):
            assert _ladder("USD/HKD", "forex", style, engine_id) == UNIVERSAL


def test_usdcnh_never_reaches_m5():
    _trigger_is_not_faster_than("USD/CNH", "forex", Timeframe.M15)
    for style in ("intraday", "swing"):
        pair = {"display": "USD/CNH", "type": "forex", "symbol": "USDCNH"}
        policy = resolve_timeframe_policy(
            "USD/CNH", "forex", resolve_score_group_by_type(pair), style)
        assert policy.m5_policy.value == "disabled"


def test_usddkk_is_not_a_liquid_usd_major():
    pair = _fx("USD/DKK")
    assert resolve_score_group_by_type(pair) != "forex_majors"
    # Roles are universal; group/profile identity still differ from majors.
    assert resolve_score_group_by_type(pair) == "forex_crosses"


def test_zar_crosses_and_usdzar_share_universal_trigger():
    assert _ladder("USD/ZAR", "forex")[4] is M15
    for display in ZAR_CROSSES:
        assert _ladder(display, "forex")[4] is M15, display


def test_fra40_receives_universal_ladder_with_m5_disabled():
    assert _ladder("France 40", "index") == UNIVERSAL
    pair = _index("France 40", "FRA40")
    policy = resolve_timeframe_policy(
        "France 40", "index", resolve_score_group_by_type(pair), "intraday")
    assert policy.m5_policy.value == "disabled"


# ── Precedence ───────────────────────────────────────────────────────────────

def test_symbol_override_beats_score_group():
    # CAD/JPY and EUR/CAD share the forex_crosses group; only CAD/JPY is pinned.
    assert resolve_score_group_by_type(_fx("CAD/JPY")) == \
        resolve_score_group_by_type(_fx("EUR/CAD"))
    pinned = resolve_timeframe_policy("CAD/JPY", "forex", "forex_crosses", "intraday")
    inherited = resolve_timeframe_policy("EUR/CAD", "forex", "forex_crosses", "intraday")
    assert pinned.policy_source is PolicySource.SYMBOL_OVERRIDE
    assert inherited.policy_source is PolicySource.SCORE_GROUP_OVERRIDE
    # Roles are universal; profile identity still differs via the pin.
    assert pinned.structure_tf is H4 and inherited.structure_tf is H4
    assert pinned.profile == "FOREX_CROSSES_LIQUID"
    assert inherited.profile == "FOREX_CROSSES_BROAD"


def test_score_group_beats_asset_default():
    grouped = resolve_timeframe_policy("EUR/CAD", "forex", "forex_exotics", "intraday")
    defaulted = resolve_timeframe_policy("EUR/CAD", "forex", None, "intraday")
    assert grouped.policy_source is PolicySource.SCORE_GROUP_OVERRIDE
    assert defaulted.policy_source is PolicySource.ASSET_STYLE_DEFAULT
    assert grouped.baseline_speed_class is SpeedClass.SLOW
    assert defaulted.baseline_speed_class is not SpeedClass.SLOW


def test_style_overlay_cannot_unlock_a_disabled_m5():
    for display in ["USD/HKD"] + ZAR_CROSSES:
        for engine_id in ("engine_a", "engine_b"):
            for style in ("intraday", "swing"):
                pair = _fx(display)
                policy = resolve_timeframe_policy(
                    display, "forex", resolve_score_group_by_type(pair), style,
                    engine_id=engine_id)
                assert policy.m5_policy.value == "disabled", (display, engine_id, style)


# ── Suffix normalization ─────────────────────────────────────────────────────

@pytest.mark.parametrize("broker,plain", [
    ("CADJPY.s", "CAD/JPY"),
    ("USDHKD.s", "USD/HKD"),
    ("CHI50.s", "China A50"),
    ("EURUSD.s", "EUR/USD"),
])
def test_broker_suffix_resolves_to_the_same_canonical_symbol(broker, plain):
    assert canonical_symbol(broker) is not None
    assert canonical_symbol(broker) == canonical_symbol(plain)


@pytest.mark.parametrize("broker,plain", [
    ("AUDCAD.s", "AUDCAD"), ("XAUZAR.s", "XAUZAR"), ("FRA40.s", "FRA40"),
])
def test_unaliased_broker_suffix_does_not_split_identity(broker, plain):
    # These have no alias entry; both forms must agree rather than one of them
    # silently resolving to a different canonical identity.
    assert canonical_symbol(broker) == canonical_symbol(plain)


def test_suffix_strip_does_not_truncate_dot_us_symbols():
    assert canonical_symbol("SPY.US") == canonical_symbol("SPY") == "SPY"


# ── Speed adaptation ─────────────────────────────────────────────────────────

def _speed_state(speed_class):
    return SpeedState(
        live_speed_class=speed_class,
        liquidity_class=LiquidityClass.NORMAL,
        history_ready=True,
    )


@pytest.mark.parametrize("display,asset_type", [
    ("USD/HKD", "forex"), ("USD/CNH", "forex"), ("EUR/ZAR", "forex"),
    ("GBP/ZAR", "forex"), ("XAU/ZAR", "commodity"), ("China A50", "index"),
    ("EUR/HUF", "forex"), ("USD/DKK", "forex"),
])
@pytest.mark.parametrize("speed_class", [SpeedClass.FAST, SpeedClass.NORMAL,
                                         SpeedClass.SLOW])
def test_speed_adaptation_never_moves_a_trigger_faster(display, asset_type,
                                                       speed_class):
    """v4 adaptation is monotonically slower; nothing may promote these symbols.

    resolve_timeframe_policy only steps setup/trigger via _adjacent(faster=False)
    and never touches execution. This locks that contract for the new additions
    so a future 'speed ceiling' cannot be assumed to exist where it does not.
    """
    pair = {"display": display, "type": asset_type,
            "symbol": display.replace("/", "")}
    group = resolve_score_group_by_type(pair)
    baseline = resolve_timeframe_policy(display, asset_type, group, "intraday")
    adapted = resolve_timeframe_policy(
        display, asset_type, group, "intraday", speed_state=_speed_state(speed_class))
    for role in ("setup_tf", "trigger_tf", "execution_tf"):
        base_tf = TIMEFRAME_LADDER.index(getattr(baseline, role))
        new_tf = TIMEFRAME_LADDER.index(getattr(adapted, role))
        assert new_tf <= base_tf, (display, speed_class, role)
    assert adapted.execution_tf is baseline.execution_tf


# ── Diagnostics ──────────────────────────────────────────────────────────────

def test_describe_symbol_policy_reports_the_required_fields():
    described = describe_symbol_policy(
        {"display": "USD/HKD", "symbol": "USDHKD=X", "type": "forex"})
    assert described["canonicalSymbol"] == "USDHKD"
    assert described["profileResolutionSource"] == "SYMBOL_OVERRIDE"
    assert described["scoreGroup"] == "forex_exotics"
    assert described["structureTf"] == "H4"
    assert described["setupTf"] == "H1"
    assert described["triggerTf"] == "M15"
    assert described["executionMode"] == "live_quote"
    assert described["m5Policy"] == "disabled"
    assert "M5" in set(described["disabledTfs"])
    assert described["allowedRefinementTfs"] == []
    assert described["symbolOverrideApplied"] is True
    for field_name in (
        "inputSymbol", "assetClass", "regionalGroup", "selectedProfile",
        "regimeTf", "biasTf", "setupTf", "requiredClosedTfs",
        "baselineSpeedClass", "policyKey", "policyVersion",
    ):
        assert field_name in described


def test_describe_symbol_policy_reports_share_regions():
    for ticker, region in (("HK0700", "CASH_EQUITY_HONG_KONG"),
                           ("LVMH", "CASH_EQUITY_EUROPE"),
                           ("BRKb", "CASH_EQUITY_US")):
        described = describe_symbol_policy(
            {"display": ticker, "symbol": ticker, "type": "stock"})
        assert described["regionalGroup"] == region


# ── Auto-style routing ───────────────────────────────────────────────────────
# Auto sends commodity/index/stock to swing, and swing replaces the group ladder
# with a uniform D1/D1/H4/H1/H1. The instruments below opt out per symbol so the
# ladders they were added with are the ones a live scan actually runs.

INTRADAY_OPT_IN = [
    ("EUR/HUF", "forex"), ("EUR/PLN", "forex"), ("USD/CZK", "forex"),
    ("USD/HUF", "forex"), ("USD/PLN", "forex"),
    ("XAU/ZAR", "commodity"), ("China A50", "index"),
    ("Spain 35", "index"), ("France 40", "index"), ("Italy 40", "index"),
]

STILL_SWING = [
    # Restricted / pegged: their symbol override pins an H1 trigger under both
    # styles, so swing costs no resolution and keeps the stricter thresholds.
    ("EUR/ZAR", "forex"), ("GBP/ZAR", "forex"), ("USD/HKD", "forex"),
    # Pre-existing pairs sharing those groups — must not move.
    ("USD/ZAR", "forex"), ("USD/MXN", "forex"), ("Gasoline", "commodity"),
    ("DAX 40", "index"), ("UK100", "index"), ("Nikkei 225", "index"),
    ("ASX 200", "index"), ("Hang Seng", "index"), ("AAPL", "stock"),
    ("XAU/USD", "commodity"), ("TLT", "etf_bond"),
]


@pytest.mark.parametrize("display,asset_type", INTRADAY_OPT_IN)
def test_opted_in_symbols_resolve_to_intraday(display, asset_type):
    pair = {"display": display, "type": asset_type,
            "symbol": display.replace("/", "")}
    group = resolve_score_group_by_type(pair)
    assert resolve_auto_style("auto", pair, score_group=group,
                              asset_type=asset_type) == "intraday", display


@pytest.mark.parametrize("display,asset_type", STILL_SWING)
def test_everything_else_keeps_its_existing_auto_style(display, asset_type):
    pair = {"display": display, "type": asset_type,
            "symbol": display.replace("/", "")}
    group = resolve_score_group_by_type(pair)
    assert resolve_auto_style("auto", pair, score_group=group,
                              asset_type=asset_type) == "swing", display


@pytest.mark.parametrize("display,asset_type", [
    ("XAU/ZAR", "commodity"),
    ("China A50", "index"),
    ("France 40", "index"),
    ("Spain 35", "index"),
    ("Italy 40", "index"),
    ("EUR/HUF", "forex"),
])
def test_auto_scan_now_runs_the_universal_ladder(display, asset_type):
    """The ladder a live auto scan resolves, not just the explicit-intraday one."""
    pair = {"display": display, "type": asset_type,
            "symbol": display.replace("/", "")}
    group = resolve_score_group_by_type(pair)
    style = resolve_auto_style("auto", pair, score_group=group,
                               asset_type=asset_type)
    assert _ladder(display, asset_type, style) == UNIVERSAL, display


def test_opt_in_matches_on_display_symbol_or_broker_suffix():
    for value in ("XAU/ZAR", "XAUZAR", "XAUZAR=X", "XAUZAR.s", "xauzar"):
        assert resolve_auto_style(
            "auto", {"display": value, "type": "commodity"}) == "intraday", value
    for value in ("China A50", "CHI50", "CHI50.s"):
        assert resolve_auto_style(
            "auto", {"display": value, "type": "index"}) == "intraday", value


def test_explicit_style_still_overrides_the_opt_in():
    pair = {"display": "XAU/ZAR", "type": "commodity", "symbol": "XAUZAR=X"}
    assert resolve_auto_style("swing", pair) == "swing"
    assert resolve_auto_style("scalp", pair) == "scalp"


def test_opt_in_does_not_leak_to_similar_symbols():
    # XAU/USD must not match the XAU/ZAR entry, and USD/HUF must not drag USD/HKD.
    assert resolve_auto_style(
        "auto", {"display": "XAU/USD", "type": "commodity"}) == "swing"
    assert resolve_auto_style(
        "auto", {"display": "USD/HKD", "type": "forex"},
        score_group="forex_exotics") == "swing"


# ── Cash-equity share CFDs ───────────────────────────────────────────────────

def test_share_cfds_use_the_cash_equity_ladder_not_us_stock_single():
    """The 219 ATFX shares share the universal ladder with M5 off.

    us_stock_single carries conditional M5 refinement calibrated on named US
    singles. These share CFDs have no trade history in Athena, so they route to
    their own template (M5 disabled) instead of inheriting that promotion.
    """
    for ticker in ("BABA", "LVMH", "HK0700", "BRKb", "AA"):
        pair = {"display": ticker, "symbol": ticker, "type": "stock"}
        group = resolve_score_group_by_type(pair)
        assert group == "stock_other", ticker
        style = resolve_auto_style("auto", pair, score_group=group, asset_type="stock")
        assert style == "intraday", ticker
        policy = resolve_timeframe_policy(ticker, "stock", group, style)
        assert policy.profile == "CASH_EQUITY_STANDARD_DYNAMIC", ticker
        assert (policy.regime_tf, policy.bias_tf, policy.structure_tf,
                policy.setup_tf, policy.trigger_tf) == UNIVERSAL, ticker
        assert policy.m5_policy.value == "disabled", ticker


def test_named_us_singles_keep_us_stock_single():
    """AAPL and friends must not be dragged onto the share-CFD template."""
    for ticker, symbol in (("AAPL", "AAPL.US"), ("MSFT", "MSFT.US"), ("TSLA", "TSLA.US")):
        pair = {"display": ticker, "symbol": symbol, "type": "stock"}
        assert resolve_score_group_by_type(pair) == "us_stock_single", ticker
        assert resolve_auto_style(
            "auto", pair, score_group="us_stock_single", asset_type="stock") == "swing", ticker


def test_etfs_are_not_swept_into_the_share_cfd_group():
    for display, ptype, expected in (
        ("TLT", "etf_bond", "bond_tlt"), ("IWM", "etf", "smallcap_em_etf"),
        ("EEM", "etf", "smallcap_em_etf"), ("GLD", "etf", "precious_trackers"),
        ("SPY", "stock", "us_indices_trackers"),
    ):
        pair = {"display": display, "symbol": display, "type": ptype}
        assert resolve_score_group_by_type(pair) == expected, display


# ── Non-regression on the pre-existing book ──────────────────────────────────

PRE_EXISTING = {
    ("EUR/USD", "forex"): ("forex_majors", UNIVERSAL),
    ("GBP/USD", "forex"): ("forex_majors", UNIVERSAL),
    ("USD/JPY", "forex"): ("forex_majors", UNIVERSAL),
    ("EUR/GBP", "forex"): ("forex_crosses", UNIVERSAL),
    ("EUR/JPY", "forex"): ("forex_crosses", UNIVERSAL),
    ("AUD/JPY", "forex"): ("forex_crosses", UNIVERSAL),
    ("GBP/JPY", "forex"): ("forex_crosses", UNIVERSAL),
    ("USD/SGD", "forex"): ("forex_crosses", UNIVERSAL),
    ("AUD/CHF", "forex"): ("forex_crosses", UNIVERSAL),
    ("USD/ZAR", "forex"): ("forex_exotics", UNIVERSAL),
    ("USD/MXN", "forex"): ("forex_exotics", UNIVERSAL),
    ("USD/BRL", "forex"): ("forex_exotics", UNIVERSAL),
    ("USD/INR", "forex"): ("forex_exotics", UNIVERSAL),
    ("XAU/USD", "commodity"): ("precious_trackers", UNIVERSAL),
    ("XAG/USD", "commodity"): ("precious_trackers", UNIVERSAL),
    ("WTI Oil", "commodity"): ("energy_oil", UNIVERSAL),
    ("Nat Gas", "commodity"): ("nat_gas", UNIVERSAL),
    ("DAX 40", "index"): ("eu_indices", UNIVERSAL),
    ("UK100", "index"): ("eu_indices", UNIVERSAL),
    ("Nikkei 225", "index"): ("asian_indices", UNIVERSAL),
    ("Hang Seng", "index"): ("asian_indices", UNIVERSAL),
    ("ASX 200", "index"): ("asian_indices", UNIVERSAL),
    ("NASDAQ-100", "index"): ("us_indices_trackers", UNIVERSAL),
    ("AAPL", "stock"): ("us_stock_single", UNIVERSAL),
    ("BTC/USDT", "crypto"): ("crypto_btc", UNIVERSAL),
    ("DOGE/USDT", "crypto"): ("crypto_doge", UNIVERSAL),
}


@pytest.mark.parametrize("key", sorted(PRE_EXISTING, key=str))
def test_pre_existing_symbols_match_the_authoritative_policy(key):
    display, asset_type = key
    expected_group, expected_ladder = PRE_EXISTING[key]
    pair = {"display": display, "type": asset_type,
            "symbol": display.replace("/", "")}
    assert resolve_score_group_by_type(pair) == expected_group, display
    assert _ladder(display, asset_type) == expected_ladder, display
