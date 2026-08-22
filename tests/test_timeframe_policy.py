from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from athena_app.services.market_state import candle_timestamp_epoch, split_market_state
from market_structure import engine_b_candles_for_tf, resolve_engine_b_tfs
from timeframe_policy import (
    _GROUP_OVERRIDES,
    _SYMBOL_OVERRIDES,
    ExecutionMode,
    LiquidityClass,
    M5Policy,
    M5Role,
    PolicyConfigurationError,
    PolicyMode,
    PolicySource,
    SessionCalendarSource,
    SpeedClass,
    SpeedState,
    TIMEFRAME_LADDER,
    Timeframe,
    apply_authoritative_policy_result,
    apply_speed_hysteresis,
    attach_timeframe_policy_payload,
    calculate_speed_state,
    classify_liquidity,
    canonical_symbol,
    derive_warmup_bars,
    get_trigger_record,
    parse_m5_matrix_language,
    reconcile_symbol_universe,
    resolve_session_calendar,
    resolve_timeframe_policy,
    speed_state_from_policy_payload,
    speed_class_for_percentile,
    speed_transition_candidate,
    timeframe_policy_execution_block_reason,
    validate_timeframe_role_order,
)

#: TF_POLICY_SESSION_CALENDARS config block for the four cash-equity
#: calendars, built the same way config.py's default does. Tests pass this
#: explicitly via ``config=`` rather than depending on the real CONFIG import,
#: so a change to the live config.yaml/config.py defaults cannot silently
#: change what these tests exercise.
_CASH_EQUITY_SESSION_CALENDARS_CONFIG = {
    "TF_POLICY_SESSION_CALENDARS": {
        "mt5": {
            "calendar:NYSE_NASDAQ": {
                "sessionCalendarId": "NYSE_NASDAQ",
                "providerSessionTimezone": "America/New_York",
            },
            "calendar:XETRA": {
                "sessionCalendarId": "XETRA",
                "providerSessionTimezone": "Europe/Berlin",
            },
            "calendar:EURONEXT_PARIS": {
                "sessionCalendarId": "EURONEXT_PARIS",
                "providerSessionTimezone": "Europe/Paris",
            },
            "calendar:HKEX": {
                "sessionCalendarId": "HKEX",
                "providerSessionTimezone": "Asia/Hong_Kong",
            },
        },
    },
}


def _registered_pairs() -> list[dict]:
    """Read source literals without importing side-effectful athena.py."""
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "athena.py").read_text(encoding="utf-8"))
    wanted = {
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    }
    pairs: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in wanted:
            pairs.extend(ast.literal_eval(node.value))
    return pairs


def test_all_conditional_m5_groups_and_overrides_keep_m15_authority() -> None:
    conditional_groups = {
        name: template
        for name, template in _GROUP_OVERRIDES.items()
        if template.m5_policy == M5Policy.CONDITIONAL
    }
    assert conditional_groups
    for name, template in conditional_groups.items():
        assert template.trigger == Timeframe.M15, name
        assert template.execution == Timeframe.M15, name
        assert template.m5_role == M5Role.REFINEMENT, name

    conditional_symbols = {
        symbol: patch
        for symbol, patch in _SYMBOL_OVERRIDES.items()
        if patch.get("m5_policy") == M5Policy.CONDITIONAL
    }
    assert conditional_symbols
    for symbol, patch in conditional_symbols.items():
        # Role TFs are no longer pinned on symbol patches; resolve instead.
        policy = resolve_timeframe_policy(symbol, "forex", None, "intraday")
        # Asset type is irrelevant when the symbol alias maps to a canonical pin.
        assert policy.trigger_tf == Timeframe.M15, symbol
        assert policy.m5_policy == M5Policy.CONDITIONAL, symbol


@pytest.mark.parametrize("engine_id", ("engine_a", "engine_b"))
@pytest.mark.parametrize("style", ("intraday", "swing"))
def test_registered_universe_never_uses_m5_as_authoritative_trigger(
    engine_id: str,
    style: str,
) -> None:
    for pair in _registered_pairs():
        display = str(pair.get("display") or pair.get("symbol") or "")
        policy = resolve_timeframe_policy(
            display,
            str(pair.get("type") or ""),
            pair.get("score_group"),
            style,
            engine_id=engine_id,
        )
        assert policy.trigger_tf != Timeframe.M5, (display, engine_id, style)
        assert policy.execution_tf != Timeframe.M5, (display, engine_id, style)


def test_every_source_enabled_registered_symbol_resolves_once() -> None:
    result = reconcile_symbol_universe(_registered_pairs())
    expected = [pair for pair in _registered_pairs() if pair.get("enabled", True)]

    assert len(result["rows"]) == len(expected)
    assert result["duplicate_canonical_symbols"] == []
    assert result["aliases_mapping_to_multiple_groups"] == {}
    assert result["unsafe_symbols"] == []
    assert all(row["policy_source"] != PolicySource.SAFE_FALLBACK.value for row in result["rows"])


def test_asx_200_aliases_share_canonical_engine_b_policy() -> None:
    assert canonical_symbol("ASX 200") == "AUS200"
    assert canonical_symbol("^AXJO") == "AUS200"
    assert canonical_symbol("ASX200") == "AUS200"
    assert canonical_symbol("AUS200.s") == "AUS200"
    left = resolve_timeframe_policy(
        "ASX 200", "index", "asian_indices", "intraday", engine_id="engine_b"
    )
    right = resolve_timeframe_policy(
        "^AXJO", "index", "asian_indices", "intraday", engine_id="engine_b"
    )
    assert left.structure_tf == right.structure_tf
    assert left.trigger_tf == right.trigger_tf
    assert left.payload()["timeframePolicyHash"] == right.payload()["timeframePolicyHash"]


def test_aliases_resolve_to_same_canonical_policy() -> None:
    left = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")
    right = resolve_timeframe_policy("EURUSD=X", "forex", "forex_majors", "intraday")
    crypto_old = resolve_timeframe_policy("MATICUSDT", "crypto", "crypto_alt_majors", "intraday")

    assert canonical_symbol("MT5:EURUSD") == "EURUSD"
    assert left.profile == right.profile
    assert left.execution_tf == right.execution_tf
    assert left.policy_key == right.policy_key
    assert left.payload()["timeframePolicyHash"] == right.payload()["timeframePolicyHash"]
    assert crypto_old.diagnostics.canonical_symbol == "POLUSDT"


def test_unknown_symbol_resolution_is_visible_and_fail_closed_when_asset_unknown() -> None:
    asset_default = resolve_timeframe_policy("NEWCOIN", "crypto", None, "intraday")
    fallback = resolve_timeframe_policy("UNKNOWN", "other", None, "intraday")

    assert asset_default.policy_source == PolicySource.ASSET_STYLE_DEFAULT
    assert any("symbol_not_in_alias_registry" in msg for msg in asset_default.diagnostics.messages)
    assert fallback.policy_source == PolicySource.SAFE_FALLBACK
    assert fallback.diagnostics.safe_fallback is True
    assert fallback.m5_role == M5Role.DISABLED


def test_required_baseline_matrix() -> None:
    # Intraday ladders: balanced intermediate setup (H1) for liquid groups;
    # session equities/indices use H1 structure + M30 setup.
    cases = {
        # (asset, group, structure, setup, trigger, m5_role)
        "EUR/USD": ("forex", "forex_majors", "H4", "H1", "M15", M5Role.DISABLED),
        "GBP/USD": ("forex", "forex_majors", "H4", "H1", "M15", M5Role.REFINEMENT),
        "EUR/GBP": ("forex", "forex_crosses", "H4", "H1", "M15", M5Role.DISABLED),
        "XAU/USD": ("commodity", "precious_trackers", "H4", "H1", "M15", M5Role.REFINEMENT),
        # pgm_metals aliases to thin_metals_base_softs: M30 trigger to avoid
        # spread-cost erosion.
        "XPT/USD": ("commodity", "pgm_metals", "H4", "H1", "M30", M5Role.DISABLED),
        "WTI Oil": ("commodity", "energy_oil", "H4", "H1", "M15", M5Role.REFINEMENT),
        "S&P 500": ("index", "us_indices_trackers", "H1", "M30", "M15", M5Role.DISABLED),
        "AAPL": ("stock", "us_stock_single", "H1", "M30", "M15", M5Role.REFINEMENT),
        "BTC/USDT": ("crypto", "crypto_btc", "H4", "H1", "M15", M5Role.REFINEMENT),
        "SOL/USDT": ("crypto", "crypto_alt_majors", "H4", "H1", "M15", M5Role.DISABLED),
    }
    for symbol, (asset, group, structure, setup, trigger, m5_role) in cases.items():
        policy = resolve_timeframe_policy(symbol, asset, group, "intraday")
        assert policy.regime_tf == Timeframe.D1
        assert policy.bias_tf == Timeframe.H4
        assert policy.structure_tf == Timeframe(structure), symbol
        assert policy.setup_tf == Timeframe(setup), symbol
        assert policy.trigger_tf == Timeframe(trigger), symbol
        assert policy.execution_tf == Timeframe.M15
        assert policy.m5_role == m5_role
        assert policy.execution_mode == ExecutionMode.LIVE_QUOTE

    conditional = resolve_timeframe_policy("GBP/USD", "forex", "forex_majors", "intraday")
    assert conditional.m5_policy == M5Policy.CONDITIONAL
    assert conditional.diagnostics.m15_confirmation_required_for_m5 is True
    assert conditional.execution_prerequisite_tf == Timeframe.M15
    payload = conditional.payload()
    assert payload["executionMode"] == ExecutionMode.LIVE_QUOTE.value
    assert payload["m5Policy"] == M5Policy.CONDITIONAL.value
    # Backward-compat fields remain emitted alongside the v4 fields.
    assert payload["executionTf"] == "M15"
    assert payload["m5Role"] == M5Role.REFINEMENT.value

    standard = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")
    assert standard.m5_policy == M5Policy.DISABLED
    assert standard.payload()["m5Policy"] == M5Policy.DISABLED.value

    oil = resolve_timeframe_policy("SpotBrent", "commodity", "energy_oil", "intraday")
    assert oil.diagnostics.m15_confirmation_required_for_m5 is True


def test_role_override_disabled_or_absent_preserves_universal_ladder(monkeypatch) -> None:
    from config import CONFIG

    for block in (None, {"ENABLED": False, "BY_GROUP": {}}, {"ENABLED": False, "BY_GROUP": {"forex_majors_standard": {"swing": {"structure": "D1"}}}}):
        if block is None:
            monkeypatch.delitem(CONFIG, "ENGINE_TF_ROLE_OVERRIDES", raising=False)
        else:
            monkeypatch.setitem(CONFIG, "ENGINE_TF_ROLE_OVERRIDES", block)
        for style in ("intraday", "swing"):
            policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", style)
            assert policy.regime_tf == Timeframe.D1
            assert policy.bias_tf == Timeframe.H4
            assert policy.structure_tf == Timeframe.H4
            assert policy.setup_tf == Timeframe.H1
            assert policy.trigger_tf == Timeframe.M15
            assert policy.diagnostics.role_override_applied is False
            assert policy.diagnostics.role_override_patched_roles == ()


def test_role_override_applies_per_group_and_style(monkeypatch) -> None:
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {
                # NAS100 resolves to equity_index_fast via symbol taxonomy.
                "equity_index_fast": {
                    "intraday": {"structure": "H1", "setup": "M30", "trigger": "M15"},
                },
                "forex_majors_standard": {
                    "swing": {
                        "regime": "D1",
                        "bias": "D1",
                        "structure": "D1",
                        "setup": "H4",
                        "trigger": "H1",
                    },
                },
            },
        },
    )
    intraday = resolve_timeframe_policy("NAS100", "index", "us_indices_trackers", "intraday")
    assert intraday.structure_tf == Timeframe.H1
    assert intraday.setup_tf == Timeframe.M30
    assert intraday.trigger_tf == Timeframe.M15
    assert intraday.regime_tf == Timeframe.D1
    assert intraday.bias_tf == Timeframe.H4
    assert intraday.diagnostics.role_override_applied is True
    assert "structure" in intraday.diagnostics.role_override_patched_roles
    assert any("ROLE_OVERRIDE" in message for message in intraday.diagnostics.messages)

    swing = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "swing")
    assert swing.regime_tf == Timeframe.D1
    assert swing.bias_tf == Timeframe.D1
    assert swing.structure_tf == Timeframe.D1
    assert swing.setup_tf == Timeframe.H4
    assert swing.trigger_tf == Timeframe.H1
    assert swing.execution_mode == ExecutionMode.LIVE_QUOTE
    assert swing.diagnostics.role_override_applied is True
    # The advisory execution context follows an overridden trigger. Left behind
    # on the template default (M15) it made execution_tf != trigger_tf, so
    # evaluate_execution_timeframe took the non-shared branch and re-tested a
    # faster forming bar against an already-confirmed trigger.
    assert swing.execution_tf == Timeframe.H1
    assert "execution" in swing.diagnostics.role_override_patched_roles
    assert intraday.execution_tf == Timeframe.M15

    # A group/style without a configured row keeps the universal ladder.
    standard = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")
    assert standard.regime_tf == Timeframe.D1
    assert standard.bias_tf == Timeframe.H4
    assert standard.structure_tf == Timeframe.H4
    assert standard.setup_tf == Timeframe.H1
    assert standard.trigger_tf == Timeframe.M15
    assert standard.diagnostics.role_override_applied is False


def test_role_override_engine_d_never_affected(monkeypatch) -> None:
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {
                "crypto_majors_fast": {
                    "scalp": {"structure": "D1", "setup": "H4", "trigger": "H1"},
                },
            },
        },
    )
    policy = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "scalp", engine_id="engine_d")
    assert policy.regime_tf == Timeframe.H1
    assert policy.structure_tf == Timeframe.M15
    assert policy.setup_tf == Timeframe.M5
    assert policy.trigger_tf == Timeframe.M1
    assert policy.diagnostics.role_override_applied is False


def test_role_override_does_not_mutate_config_conflict_fallback(monkeypatch) -> None:
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {
                "forex_majors_standard": {
                    "swing": {
                        "regime": "D1",
                        "bias": "D1",
                        "structure": "D1",
                        "setup": "H4",
                        "trigger": "H1",
                    }
                }
            },
        },
    )
    policy = resolve_timeframe_policy(
        "EUR/USD",
        "forex",
        "forex_majors",
        "swing",
        authoritative_group="forex_crosses",
    )

    assert policy.policy_source == PolicySource.CONFIG_CONFLICT
    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.H4
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M15
    assert policy.diagnostics.config_conflict is True
    assert policy.diagnostics.role_override_applied is False
    assert policy.diagnostics.role_override_patched_roles == ()


@pytest.mark.parametrize(
    "block",
    (
        {"ENABLED": "false", "BY_GROUP": {}},
        {"ENABLED": True, "BY_GROUP": []},
        {
            "ENABLED": True,
            "BY_GROUP": {"forex_majors_standard": {"intraday": []}},
        },
        {
            "ENABLED": True,
            "BY_GROUP": {
                "forex_majors_standard": {"intraday": {"execution": "M15"}}
            },
        },
    ),
)
def test_role_override_malformed_config_fails_closed(monkeypatch, block) -> None:
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_TF_ROLE_OVERRIDES", block)
    with pytest.raises(PolicyConfigurationError):
        resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")


@pytest.mark.parametrize(
    "row",
    (
        # Reversed ladder: setup slower than structure.
        {"structure": "H1", "setup": "H4"},
        # Trigger slower than setup.
        {"structure": "H4", "setup": "M30", "trigger": "H1"},
        # Structure slower than bias is forbidden (D1 bias requires D1 structure).
        {"bias": "H4", "structure": "D1"},
    ),
)
def test_role_override_invalid_ladder_fails_closed(monkeypatch, row) -> None:
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {"ENABLED": True, "BY_GROUP": {"forex_majors_standard": {"intraday": row}}},
    )
    with pytest.raises(PolicyConfigurationError):
        resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")


def test_role_override_unknown_timeframe_fails_closed(monkeypatch) -> None:
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {"forex_majors_standard": {"intraday": {"trigger": "M90"}}},
        },
    )
    with pytest.raises(PolicyConfigurationError):
        resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")


def test_corrected_symbol_specific_profiles() -> None:
    # Intraday ladder per symbol: the universal H4/H1/M15 holds for most
    # groups; the enabled override matrix rewrites structure/setup/trigger for
    # session-bound equities and thin/spread-expensive groups. Profile identity
    # and m5_role are asserted alongside.
    cases = {
        # (asset, group, profile, m5_role, structure, setup, trigger)
        "USD/JPY": ("forex", "forex_majors", "FOREX_MAJORS_FAST", M5Role.REFINEMENT, "H4", "H1", "M15"),
        "USD/CAD": ("forex", "forex_majors", "FOREX_MAJORS_STANDARD", M5Role.DISABLED, "H4", "H1", "M15"),
        "AUD/NZD": ("forex", "forex_crosses", "FOREX_CROSSES_BROAD", M5Role.DISABLED, "H4", "H1", "M15"),
        "GBP/JPY": ("forex", "forex_crosses", "GBPJPY_FAST_CROSS_CONDITIONAL", M5Role.REFINEMENT, "H4", "H1", "M15"),
        "EUR/JPY": ("forex", "forex_crosses", "FOREX_CROSSES_LIQUID", M5Role.DISABLED, "H4", "H1", "M15"),
        "AUD/JPY": ("forex", "forex_crosses", "FOREX_CROSSES_LIQUID", M5Role.DISABLED, "H4", "H1", "M15"),
        "EUR/CHF": ("forex", "forex_crosses", "FOREX_CROSSES_BROAD", M5Role.DISABLED, "H4", "H1", "M15"),
        "XAG/USD": ("commodity", "precious_trackers", "LIQUID_METALS", M5Role.DISABLED, "H4", "H1", "M15"),
        "XPT/USD": ("commodity", "pgm_metals", "THIN_METALS_BASE_SOFTS", M5Role.DISABLED, "H4", "H1", "M30"),
        "XPD/USD": ("commodity", "pgm_metals", "THIN_METALS_BASE_SOFTS", M5Role.DISABLED, "H4", "H1", "M30"),
        "Natural Gas": ("commodity", "nat_gas", "NAT_GAS_NO_M5", M5Role.DISABLED, "H4", "H1", "M30"),
        "NAS100": ("index", "us_indices_trackers", "EQUITY_INDEX_FAST", M5Role.REFINEMENT, "H1", "M30", "M15"),
        "US30": ("index", "us_indices_trackers", "EQUITY_INDEX_FAST", M5Role.REFINEMENT, "H1", "M30", "M15"),
        "GER40": ("index", "eu_indices", "EQUITY_INDEX_FAST", M5Role.REFINEMENT, "H1", "M30", "M15"),
        "JPN225": ("index", "asian_indices", "EQUITY_INDEX_STANDARD", M5Role.DISABLED, "H1", "M30", "M15"),
        "US500": ("index", "us_indices_trackers", "EQUITY_INDEX_STANDARD", M5Role.DISABLED, "H1", "M30", "M15"),
        "UK100": ("index", "eu_indices", "EQUITY_INDEX_STANDARD", M5Role.DISABLED, "H1", "M30", "M15"),
        "AAPL": ("stock", "us_stock_single", "US_STOCK_SINGLE", M5Role.REFINEMENT, "H1", "M30", "M15"),
        "SPY": ("etf", "us_etfs", "US_STOCK_SINGLE", M5Role.REFINEMENT, "H1", "M30", "M15"),
        "BTC/USDT": ("crypto", "crypto_btc", "CRYPTO_MAJORS_FAST", M5Role.REFINEMENT, "H4", "H1", "M15"),
        "ETH/USDT": ("crypto", "crypto_eth", "CRYPTO_MAJORS_FAST", M5Role.REFINEMENT, "H4", "H1", "M15"),
        "BNB/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_ALT_MAJORS", M5Role.DISABLED, "H4", "H1", "M15"),
        "SOL/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_ALT_MAJORS", M5Role.DISABLED, "H4", "H1", "M15"),
        "XRP/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_ALT_MAJORS", M5Role.DISABLED, "H4", "H1", "M15"),
        "DOGE/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_OTHER_THIN", M5Role.DISABLED, "H4", "H1", "M30"),
        "ADA/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_ALT_MAJORS", M5Role.DISABLED, "H4", "H1", "M15"),
        "LINK/USDT": ("crypto", "crypto_alt_majors", "CRYPTO_ALT_MAJORS", M5Role.DISABLED, "H4", "H1", "M15"),
    }
    for symbol, (asset, group, profile, m5_role, structure, setup, trigger) in cases.items():
        policy = resolve_timeframe_policy(symbol, asset, group, "intraday")
        assert policy.profile == profile, symbol
        assert policy.regime_tf == Timeframe.D1, symbol
        assert policy.bias_tf == Timeframe.H4, symbol
        assert policy.structure_tf == Timeframe(structure), symbol
        assert policy.setup_tf == Timeframe(setup), symbol
        assert policy.trigger_tf == Timeframe(trigger), symbol
        assert policy.execution_tf == Timeframe.M15, symbol
        assert policy.m5_role == m5_role, symbol


def test_symbol_overrides_are_partial_role_patches_with_diagnostics() -> None:
    # Symbol overrides pin m5_policy/profile only; the group Tuning Lab role
    # override is still applied independently.
    gbpjpy = resolve_timeframe_policy("GBP/JPY", "forex", "forex_crosses", "intraday")
    assert gbpjpy.policy_source == PolicySource.SYMBOL_OVERRIDE
    assert gbpjpy.diagnostics.symbol_override_applied is True
    assert gbpjpy.diagnostics.symbol_override_patched_roles == ("m5_policy",)
    assert gbpjpy.regime_tf == Timeframe.D1
    assert gbpjpy.bias_tf == Timeframe.H4
    assert gbpjpy.structure_tf == Timeframe.H4
    assert gbpjpy.setup_tf == Timeframe.H1
    assert gbpjpy.trigger_tf == Timeframe.M15
    assert gbpjpy.m5_policy == M5Policy.CONDITIONAL

    eurusd = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday")
    assert eurusd.diagnostics.symbol_override_applied is True
    assert set(eurusd.diagnostics.symbol_override_patched_roles) == {"m5_policy"}

    # A symbol without an override resolves from the group template directly.
    pol = resolve_timeframe_policy("POL/USDT", "crypto", "crypto_alt_majors", "intraday")
    assert pol.policy_source == PolicySource.SCORE_GROUP_OVERRIDE
    assert pol.diagnostics.symbol_override_applied is False
    assert pol.diagnostics.symbol_override_patched_roles == ()
    assert pol.profile == "CRYPTO_ALT_MAJORS"


def test_deprecated_group_names_normalize_to_v4_taxonomy(caplog) -> None:
    import logging

    import engine_a_groups

    # Warnings are emitted once per deprecated name per process; reset the
    # dedupe set so this test exercises the warning deterministically.
    engine_a_groups._deprecation_logged.discard("forex_crosses")
    with caplog.at_level(logging.WARNING, logger="engine_a_groups"):
        policy = resolve_timeframe_policy("EUR/GBP", "forex", "forex_crosses", "intraday")
    assert policy.diagnostics.score_group == "forex_crosses_broad"
    assert any(
        "forex_crosses" in record.message and "deprecated" in record.message
        for record in caplog.records
    )
    # The same deprecated name warns only once per process.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="engine_a_groups"):
        resolve_timeframe_policy("EUR/GBP", "forex", "forex_crosses", "intraday")
    assert not caplog.records
    # New group names resolve directly without any deprecation warning.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="engine_a_groups"):
        direct = resolve_timeframe_policy(
            "EUR/GBP", "forex", "forex_crosses_broad", "intraday"
        )
    assert direct.diagnostics.score_group == "forex_crosses_broad"
    assert not caplog.records


def test_timeframe_policy_group_taxonomy_routing() -> None:
    from engine_a_groups import (
        ENGINE_A_TIMEFRAME_POLICY_GROUPS,
        resolve_timeframe_policy_group,
    )

    assert len(ENGINE_A_TIMEFRAME_POLICY_GROUPS) == 18
    cases = {
        ("EUR/USD", "forex"): "forex_majors_standard",
        ("GBP/USD", "forex"): "forex_majors_fast",
        ("USD/JPY", "forex"): "forex_majors_fast",
        ("EUR/GBP", "forex"): "forex_crosses_broad",
        ("USD/SGD", "forex"): "forex_crosses_broad",
        ("EUR/JPY", "forex"): "forex_crosses_liquid",
        ("GBP/JPY", "forex"): "forex_crosses_liquid",
        ("USD/ZAR", "forex"): "forex_exotics_liquid",
        ("USD/BRL", "forex"): "forex_exotics_restricted",
        ("XAU/USD", "commodity"): "liquid_metals",
        ("XPT/USD", "commodity"): "thin_metals_base_softs",
        ("WTI OIL", "commodity"): "energy_oil",
        ("NAT GAS", "commodity"): "nat_gas",
        ("NASDAQ-100", "index"): "equity_index_fast",
        ("S&P 500", "index"): "equity_index_standard",
        ("AAPL", "stock"): "us_stock_single",
        ("SPY", "etf"): "us_stock_single",
        ("TLT", "etf_bond"): "bond_tlt_smallcap_em_etf",
        ("IWM", "etf"): "bond_tlt_smallcap_em_etf",
        ("BTC/USDT", "crypto"): "crypto_majors_fast",
        ("SOL/USDT", "crypto"): "crypto_alt_majors",
        ("XRP/USDT", "crypto"): "crypto_alt_majors",
        ("DOGE/USDT", "crypto"): "crypto_other_thin",
    }
    for (display, ptype), group in cases.items():
        assert resolve_timeframe_policy_group({"display": display, "type": ptype}) == group


def test_policy_keys_are_scoped_by_engine_and_style() -> None:
    engine_a = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a")
    engine_b = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_b")
    engine_d = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "scalp", engine_id="engine_d")

    assert len({engine_a.policy_key, engine_b.policy_key, engine_d.policy_key}) == 3


def test_speed_class_boundaries_and_hysteresis() -> None:
    assert speed_class_for_percentile(0) == SpeedClass.SLOW
    assert speed_class_for_percentile(35) == SpeedClass.SLOW
    assert speed_class_for_percentile(36) == SpeedClass.NORMAL
    assert speed_class_for_percentile(75) == SpeedClass.FAST
    assert speed_class_for_percentile(91.999) == SpeedClass.FAST
    assert speed_class_for_percentile(92) == SpeedClass.EXTREME
    assert speed_class_for_percentile(100) == SpeedClass.EXTREME

    first, streak = apply_speed_hysteresis(SpeedClass.NORMAL, SpeedClass.FAST, 0)
    second, streak = apply_speed_hysteresis(first, SpeedClass.FAST, streak)
    assert first == SpeedClass.NORMAL
    assert second == SpeedClass.FAST
    assert streak == 0

    cfg = SpeedState().thresholds
    assert speed_transition_candidate(SpeedClass.SLOW, 44.9, cfg) == SpeedClass.SLOW
    assert speed_transition_candidate(SpeedClass.SLOW, 45, cfg) == SpeedClass.NORMAL
    assert speed_transition_candidate(SpeedClass.NORMAL, 35, cfg) == SpeedClass.SLOW
    assert speed_transition_candidate(SpeedClass.NORMAL, 74.9, cfg) == SpeedClass.NORMAL
    assert speed_transition_candidate(SpeedClass.NORMAL, 75, cfg) == SpeedClass.FAST
    assert speed_transition_candidate(SpeedClass.FAST, 65, cfg) == SpeedClass.NORMAL
    assert speed_transition_candidate(SpeedClass.FAST, 92, cfg) == SpeedClass.EXTREME
    assert speed_transition_candidate(SpeedClass.EXTREME, 88, cfg) == SpeedClass.FAST
    pending, age = apply_speed_hysteresis(SpeedClass.SLOW, SpeedClass.EXTREME, 0)
    transitioned, _ = apply_speed_hysteresis(pending, SpeedClass.EXTREME, age)
    assert pending == SpeedClass.SLOW
    assert transitioned == SpeedClass.NORMAL


def test_extreme_thin_market_blocks_m5_eligibility_without_rewriting_roles() -> None:
    state = SpeedState(
        live_speed_class=SpeedClass.EXTREME,
        speed_percentile=95.0,
        history_ready=True,
        liquidity_class=LiquidityClass.THIN,
        thin_liquidity=True,
        m5_quality_acceptable=False,
    )
    policy = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "intraday", state)

    # THIN demotes trigger M15→M30 (cost trap) and blocks M5 eligibility.
    # Regime/bias/structure/setup stay on the liquid crypto ladder.
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M30
    assert policy.execution_tf == Timeframe.M15
    assert policy.m5_role == M5Role.REFINEMENT
    assert policy.m5_policy == M5Policy.CONDITIONAL
    assert policy.diagnostics.m5_liquidity_blocked is True
    assert policy.diagnostics.adaptation_applied is True
    assert any("m5_liquidity_blocked" in message for message in policy.diagnostics.messages)


def test_speed_adaptation_moves_setup_and_trigger_only() -> None:
    slow = SpeedState(
        live_speed_class=SpeedClass.SLOW,
        speed_percentile=20.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )
    fast = SpeedState(
        live_speed_class=SpeedClass.FAST,
        speed_percentile=80.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )

    slow_policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", slow)
    fast_policy = resolve_timeframe_policy("EUR/USD", "forex", "forex_majors", "intraday", fast)
    fast_majors = resolve_timeframe_policy("USD/JPY", "forex", "forex_majors", "intraday", fast)
    slow_majors = resolve_timeframe_policy("USD/JPY", "forex", "forex_majors", "intraday", slow)

    # Structure/setup fixed; SLOW demotes trigger M15→M30 only.
    for policy in (fast_policy, fast_majors):
        assert policy.regime_tf == Timeframe.D1
        assert policy.bias_tf == Timeframe.H4
        assert policy.structure_tf == Timeframe.H4
        assert policy.setup_tf == Timeframe.H1
        assert policy.trigger_tf == Timeframe.M15
        assert policy.execution_tf == Timeframe.M15
        assert policy.diagnostics.adaptation_applied is False
    for policy in (slow_policy, slow_majors):
        assert policy.regime_tf == Timeframe.D1
        assert policy.structure_tf == Timeframe.H4
        assert policy.setup_tf == Timeframe.H1
        assert policy.trigger_tf == Timeframe.M30
        assert policy.diagnostics.adaptation_applied is True
    # Conditional-M5 profiles keep M5 as refinement-only.
    assert fast_majors.m5_role == M5Role.REFINEMENT
    assert fast_majors.m5_policy == M5Policy.CONDITIONAL
    assert slow_majors.m5_policy == M5Policy.CONDITIONAL


def test_speed_state_uses_confirmed_volatility_velocity_volume_and_quote_quality() -> None:
    def bars(count: int, step: float) -> list[dict]:
        return [
            {
                "open": 100.0 + index * step,
                "high": 100.8 + index * step,
                "low": 99.4 + index * step,
                "close": 100.2 + index * step,
                "vol": 1000 + index * 10,
            }
            for index in range(count)
        ]

    state = calculate_speed_state(
        bars(240, 0.2),
        bars(240, 0.08),
        spread=0.02,
        quote_age_sec=1.0,
        current_session="london_ny",
        gap_status="normal",
        scheduled_event=False,
        provider_market_state="open",
        last_closed_h1_open_time=123,
    )

    assert state.speed_percentile is not None
    assert state.price_velocity_percentile is not None
    assert state.relative_volume is not None
    assert state.live_speed_class is not None
    assert state.live_speed_class != SpeedClass.UNAVAILABLE
    assert state.liquidity_class in {LiquidityClass.DEEP, LiquidityClass.NORMAL}
    assert state.m5_quality_acceptable is True


def test_market_state_separates_mt5_forming_and_bybit_confirm_flags() -> None:
    now = datetime(2026, 7, 13, 10, 7, tzinfo=timezone.utc).timestamp()
    mt5 = split_market_state(
        [
            {"time": "2026-07-13T09:55:00", "close": 1.0},
            {"time": "2026-07-13T10:05:00", "close": 1.1},
        ],
        "M5",
        "EUR/USD",
        time_now=now,
        provider="mt5",
        provider_symbol="EURUSD",
    )
    bybit = split_market_state(
        [
            {"time": "2026-07-13T10:00:00Z", "close": 100.0, "confirm": True},
            {"time": "2026-07-13T09:55:00Z", "close": 101.0, "confirm": False},
        ],
        "M5",
        "BTC/USDT",
        time_now=now,
        provider="bybit",
        provider_symbol="BTCUSDT",
    )

    assert len(mt5["confirmed"]) == 1
    assert mt5["forming"]["close"] == 1.1
    assert mt5["provider_symbol"] == "EURUSD"
    assert len(bybit["confirmed"]) == 1
    assert bybit["forming"]["confirm"] is False


def test_naive_provider_timestamp_is_normalized_as_utc() -> None:
    expected = int(datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc).timestamp())
    assert candle_timestamp_epoch({"time": "2026-07-13T10:00:00"}) == expected


def test_engine_b_enforced_intraday_policy_uses_new_timeframes() -> None:
    equity = resolve_timeframe_policy("MSFT", "stock", "us_stock_single", "intraday")
    engine_b_intraday = resolve_engine_b_tfs(
        "forex", "intraday", symbol="EUR/USD", score_group="forex_majors"
    )
    engine_b_swing = resolve_engine_b_tfs(
        "forex", "swing", symbol="EUR/USD", score_group="forex_majors"
    )
    engine_b_thin_swing = resolve_timeframe_policy(
        "USD/BRL", "forex", "forex_exotics_restricted", "swing", engine_id="engine_b"
    )
    engine_b_fast = resolve_timeframe_policy(
        "GBP/USD", "forex", "forex_majors", "intraday", engine_id="engine_b"
    )
    engine_b_cross = resolve_timeframe_policy(
        "EUR/GBP", "forex", "forex_crosses", "intraday", engine_id="engine_b"
    )
    engine_b_exotic_liquid = resolve_timeframe_policy(
        "USD/ZAR", "forex", "forex_exotics_liquid", "intraday", engine_id="engine_b"
    )
    engine_b_exotic_restricted = resolve_timeframe_policy(
        "USD/BRL", "forex", "forex_exotics_restricted", "intraday", engine_id="engine_b"
    )
    engine_b_scalp = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "scalp", engine_id="engine_b"
    )
    engine_d = resolve_timeframe_policy("BTC/USDT", "crypto", "crypto_btc", "engine_d")

    # Session equities: H1 structure / M30 setup / M15 trigger.
    # Liquid FX: H4 structure / H1 setup / M15 trigger.
    assert equity.structure_tf == Timeframe.H1
    assert equity.setup_tf == Timeframe.M30
    assert equity.trigger_tf == Timeframe.M15
    assert engine_b_intraday["bias"] == "H4"
    assert engine_b_intraday["struct"] == "H4"
    assert engine_b_intraday["zone"] == "H4"
    assert engine_b_intraday["zone"] == engine_b_intraday["struct"]
    assert engine_b_intraday["setup"] == "H1"
    assert engine_b_intraday["trigger"] == "M15"
    assert engine_b_intraday["execution"] == "M15"
    assert engine_b_intraday["atr"] == "H4"
    assert engine_b_swing["struct"] == "D1"
    assert engine_b_swing["zone"] == "D1"
    assert engine_b_swing["bias"] == "D1"
    assert engine_b_swing["setup"] == "H4"
    assert engine_b_swing["trigger"] == "H1"
    assert engine_b_swing["atr"] == "D1"
    assert engine_b_thin_swing.structure_tf == Timeframe.D1
    assert engine_b_thin_swing.setup_tf == Timeframe.H4
    assert engine_b_thin_swing.trigger_tf == Timeframe.H1
    assert engine_b_fast.execution_tf == Timeframe.M15
    assert engine_b_fast.trigger_tf == Timeframe.M15
    assert engine_b_fast.m5_role == M5Role.REFINEMENT
    assert engine_b_fast.m5_policy == M5Policy.CONDITIONAL
    assert engine_b_fast.execution_prerequisite_tf == Timeframe.M15
    assert engine_b_cross.structure_tf == Timeframe.H4
    assert engine_b_cross.trigger_tf == Timeframe.M15
    assert engine_b_cross.m5_role == M5Role.DISABLED
    assert engine_b_cross.m5_policy == M5Policy.DISABLED
    for exotic in (engine_b_exotic_liquid, engine_b_exotic_restricted):
        assert exotic.structure_tf == Timeframe.H4
        assert exotic.setup_tf == Timeframe.H1
        assert exotic.trigger_tf == Timeframe.M30
        assert exotic.execution_tf == Timeframe.M15
        assert exotic.m5_policy == M5Policy.DISABLED
    # Engine B scalp retains universal H4 structure; M5 is refinement only.
    assert engine_b_scalp.regime_tf == Timeframe.D1
    assert engine_b_scalp.bias_tf == Timeframe.H4
    assert engine_b_scalp.structure_tf == Timeframe.H4
    assert engine_b_scalp.setup_tf == Timeframe.H1
    assert engine_b_scalp.trigger_tf == Timeframe.M15
    assert engine_b_scalp.execution_tf == Timeframe.M15
    assert engine_b_scalp.m5_policy == M5Policy.CONDITIONAL
    assert engine_b_scalp.execution_mode == ExecutionMode.LIVE_QUOTE
    # Engine D native: H1 context, M15 confirmed structure/bias, M5 setup, M1
    # trigger; execution is live-quote based.
    assert engine_d.regime_tf == Timeframe.H1
    assert engine_d.bias_tf == Timeframe.M15
    assert engine_d.structure_tf == Timeframe.M15
    assert engine_d.setup_tf == Timeframe.M5
    assert engine_d.trigger_tf == Timeframe.M1
    assert engine_d.execution_tf == Timeframe.M1
    assert engine_d.execution_mode == ExecutionMode.LIVE_QUOTE
    ox_alpha = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="ox_alpha"
    )
    assert ox_alpha.engine_id == "ox_alpha"
    assert ox_alpha.regime_tf == Timeframe.H1
    assert ox_alpha.bias_tf == Timeframe.H1
    assert ox_alpha.structure_tf == Timeframe.H1
    assert ox_alpha.setup_tf == Timeframe.M15
    assert ox_alpha.trigger_tf == Timeframe.M15
    assert ox_alpha.execution_tf == Timeframe.M15
    assert ox_alpha.execution_mode == ExecutionMode.LIVE_QUOTE
    assert ox_alpha.m5_policy == M5Policy.DISABLED
    assert {tf.value for tf in ox_alpha.required_closed_tfs} == {"H1", "M15"}

    eng_a_intra = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    eng_a_swing = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "swing", engine_id="engine_a"
    )
    for eng_a in (eng_a_intra,):
        assert eng_a.regime_tf == Timeframe.D1
        assert eng_a.bias_tf == Timeframe.H4
        assert eng_a.structure_tf == Timeframe.H4
        assert eng_a.setup_tf == Timeframe.H1
        assert eng_a.trigger_tf == Timeframe.M15
        assert eng_a.execution_tf == Timeframe.M15
    assert eng_a_swing.regime_tf == Timeframe.D1
    assert eng_a_swing.bias_tf == Timeframe.D1
    assert eng_a_swing.structure_tf == Timeframe.D1
    assert eng_a_swing.setup_tf == Timeframe.H4
    assert eng_a_swing.trigger_tf == Timeframe.H1
    assert eng_a_swing.execution_tf == Timeframe.M15
    assert eng_a_swing.profile.startswith("ENGINE_A_SWING_")


def test_engine_a_broad_thin_groups_use_fixed_h4_h1_m15_ladder() -> None:
    slow = SpeedState(
        live_speed_class=SpeedClass.SLOW,
        speed_percentile=20.0,
        history_ready=True,
        liquidity_class=LiquidityClass.NORMAL,
        thin_liquidity=False,
        m5_quality_acceptable=True,
    )
    groups = (
        # Liquid broad: SLOW demotes M15→M30; setup stays H1.
        ("EUR/GBP", "forex", "forex_crosses_broad", "M30", "H1"),
        ("USD/ZAR", "forex", "forex_exotics_liquid", "M30", "H1"),
        ("USD/BRL", "forex", "forex_exotics_restricted", "M30", "H1"),
        ("XPT/USD", "commodity", "thin_metals_base_softs", "M30", "H1"),
        # Thin crypto baseline trigger is M30.
        ("DOGE/USDT", "crypto", "crypto_other_thin", "M30", "H1"),
    )
    for symbol, asset_type, score_group, intraday_trigger, setup_tf in groups:
        intraday = resolve_timeframe_policy(
            symbol, asset_type, score_group, "intraday", slow, engine_id="engine_a"
        )
        assert intraday.regime_tf == Timeframe.D1
        assert intraday.bias_tf == Timeframe.H4
        assert intraday.structure_tf == Timeframe.H4
        assert intraday.setup_tf == Timeframe(setup_tf)
        assert intraday.trigger_tf == Timeframe(intraday_trigger)
        assert intraday.execution_tf == Timeframe.M15
        assert intraday.m5_policy == M5Policy.DISABLED

        swing = resolve_timeframe_policy(
            symbol, asset_type, score_group, "swing", slow, engine_id="engine_a"
        )
        assert swing.regime_tf == Timeframe.D1
        assert swing.bias_tf == Timeframe.D1
        assert swing.structure_tf == Timeframe.D1
        assert swing.setup_tf == Timeframe.H4
        assert swing.trigger_tf == Timeframe.H1
        assert swing.execution_tf == Timeframe.M15
        assert swing.m5_policy == M5Policy.DISABLED

        # Scalp has no override row; SLOW demotes M15→M30 on the universal ladder.
        scalp = resolve_timeframe_policy(
            symbol, asset_type, score_group, "scalp", slow, engine_id="engine_a"
        )
        assert scalp.structure_tf == Timeframe.H4
        assert scalp.setup_tf == Timeframe.H1
        assert scalp.trigger_tf == Timeframe.M30

    auto_exotic = resolve_timeframe_policy(
        "USD/ZAR", "forex", "forex_exotics_liquid", "auto", slow, engine_id="engine_a"
    )
    assert auto_exotic.style == "intraday"
    assert auto_exotic.bias_tf == Timeframe.H4
    assert auto_exotic.trigger_tf == Timeframe.M30


def test_auto_policy_preserves_legacy_intraday_default() -> None:
    major = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "auto", engine_id="engine_a"
    )
    exotic_a = resolve_timeframe_policy(
        "USD/ZAR", "forex", "forex_exotics", "auto", engine_id="engine_a"
    )
    exotic_b = resolve_timeframe_policy(
        "USD/ZAR", "forex", "forex_exotics", "auto", engine_id="engine_b"
    )
    equity = resolve_timeframe_policy(
        "MSFT", "stock", "us_stock_single", "auto", engine_id="engine_a"
    )

    assert major.style == "intraday"
    assert exotic_a.style == "intraday"
    assert exotic_b.style == "intraday"
    assert equity.style == "intraday"


def test_engine_a_payload_attachment_does_not_change_score_or_direction(
    monkeypatch,
) -> None:
    monkeypatch.setitem(
        __import__("config", fromlist=["CONFIG"]).CONFIG,
        "ENGINE_A_ENTRY_TIMING_GATES_ENABLED",
        True,
    )
    signal = {
        "score": 2.15,
        "confluenceScore": 2.15,
        "direction": "LONG",
        "price": 1.1,
        "atr": 0.01,
    }
    before = {key: signal[key] for key in ("score", "confluenceScore", "direction")}
    states = {
        tf: {"confirmed": [{"time": f"2026-07-13T0{index}:00:00Z"}]}
        for index, tf in enumerate(("D1", "H4", "H1", "M30", "M15"), start=1)
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        market_states=states,
    )

    assert {key: signal[key] for key in before} == before
    assert signal["entryReadiness"] == "PENDING"
    assert signal["triggerCandleClosed"] is True


def test_confirmed_engine_b_trigger_sets_ready_without_changing_structure_result() -> None:
    signal = {"trigger_ok": True, "structural_verdict": "CLEAR"}
    states = {
        tf: {"confirmed": [{"time": "2026-07-13T10:00:00Z"}], "stale": False}
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        engine="engine_b",
        market_states=states,
    )

    assert signal["structural_verdict"] == "CLEAR"
    assert signal["entryReadiness"] == "READY"
    assert signal["triggerConfirmed"] is True


def test_engine_b_entry_ok_catalyst_sets_ready_when_raw_trigger_ok_false() -> None:
    """Flexible Engine B can pass via sweep/CHoCH/breakout with trigger_ok=False."""
    signal = {
        "trigger_ok": False,
        "entry_ok": True,
        "structural_verdict": "CLEAR",
        "direction": "LONG",
        "current_price": 1.10,
    }
    states = {
        tf: {"confirmed": [{"time": "2026-07-13T10:00:00Z"}], "stale": False}
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    # Shared trigger/execution TF (M15): forming must align with direction.
    states["M15"]["forming"] = {
        "time": "2026-07-13T10:15:00Z",
        "open": 1.095,
        "high": 1.102,
        "low": 1.094,
        "close": 1.101,
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        engine="engine_b",
        market_states=states,
    )

    assert signal["entryReadiness"] == "READY"
    assert signal["triggerConfirmed"] is True


def test_engine_b_trigger_false_without_entry_ok_stays_pending(monkeypatch) -> None:
    monkeypatch.setitem(
        __import__("config", fromlist=["CONFIG"]).CONFIG,
        "ENGINE_A_ENTRY_TIMING_GATES_ENABLED",
        True,
    )
    signal = {"trigger_ok": False, "entry_ok": False, "structural_verdict": "CLEAR"}
    states = {
        tf: {"confirmed": [{"time": "2026-07-13T10:00:00Z"}], "stale": False}
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        engine="engine_b",
        market_states=states,
    )

    assert signal["entryReadiness"] == "PENDING"
    assert signal["entryReadinessReason"] == "confirmed trigger condition not met"
    assert signal["triggerConfirmed"] is False


def test_engine_b_trigger_false_ready_when_entry_timing_gates_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setitem(
        __import__("config", fromlist=["CONFIG"]).CONFIG,
        "ENGINE_A_ENTRY_TIMING_GATES_ENABLED",
        False,
    )
    signal = {"trigger_ok": False, "entry_ok": False, "structural_verdict": "CLEAR"}
    states = {
        tf: {"confirmed": [{"time": "2026-07-13T10:00:00Z"}], "stale": False}
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        engine="engine_b",
        market_states=states,
    )

    assert signal["entryReadiness"] == "READY"
    assert "entry_timing_gates_disabled" in str(signal.get("entryReadinessReason") or "")
    assert signal["triggerConfirmed"] is False


def test_shared_trigger_execution_tf_is_satisfied_by_the_confirmed_trigger() -> None:
    # The forming bar points the other way, but execution and trigger share the
    # M15 rung, so the confirmed trigger carries the timing evidence.  Re-testing
    # the next unconfirmed bar of the same timeframe would re-decide a confirmed
    # entry on an intrabar coin flip.
    signal = {
        "score": 2.7,
        "direction": "LONG",
        "trigger_ok": True,
        "current_price": 1.095,
    }
    states = {
        tf: {
            "confirmed": [
                {
                    "time": "2026-07-13T10:00:00Z",
                    "open": 1.09,
                    "high": 1.105,
                    "low": 1.085,
                    "close": 1.10,
                }
            ],
            "stale": False,
        }
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    states["M15"]["forming"] = {
        "time": "2026-07-13T10:15:00Z",
        "open": 1.10,
        "high": 1.101,
        "low": 1.094,
        "close": 1.096,
    }

    attach_timeframe_policy_payload(
        signal,
        {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        engine="engine_b",
        market_states=states,
    )

    assert signal["triggerTf"] == signal["executionTf"] == "M15"
    assert signal["executionTimingStatus"] == "SHARED_TRIGGER_CONFIRMED"
    assert signal["executionTimingSource"] == "confirmed_trigger"
    assert signal["entryReadiness"] == "READY"
    assert signal["score"] == 2.7
    assert signal["direction"] == "LONG"


def test_role_override_trigger_is_not_overturned_by_a_faster_forming_bar(
    monkeypatch,
) -> None:
    """A Tuning Lab trigger override must move execution with it.

    Regression for the 2026-08-10 GC=F row: the override set trigger=M30 but
    execution stayed on the template default M15, so the live M15 move was
    compared against an already-confirmed M30 trigger and returned OPPOSED.
    The signal was decision=TRADE, qualified=True, score 1.1285 over a 1.08
    threshold, and was demoted to watchlist by entryReadiness=PENDING.
    """
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {
                # precious_trackers resolves here via
                # engine_a_groups.TIMEFRAME_POLICY_GROUP_ALIASES.
                "liquid_metals": {"intraday": {"trigger": "M30"}},
            },
        },
    )
    signal = {
        "score": 1.1285,
        "direction": "LONG",
        "trigger_ok": True,
        "current_price": 4391.6,
    }
    states = {
        tf: {
            "confirmed": [
                {
                    "time": "2026-08-10T08:00:00Z",
                    "open": 4380.0,
                    "high": 4395.0,
                    "low": 4378.0,
                    "close": 4392.0,
                }
            ],
            "stale": False,
        }
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    # M15 is falling while the confirmed M30 trigger is long.
    states["M15"]["forming"] = {
        "time": "2026-08-10T08:30:00Z",
        "open": 4392.0,
        "high": 4392.5,
        "low": 4380.0,
        "close": 4381.0,
    }

    attach_timeframe_policy_payload(
        signal,
        {"display": "XAU/USD", "type": "commodity", "score_group": "precious_trackers"},
        "intraday",
        engine="engine_a",
        market_states=states,
    )

    assert signal["triggerTf"] == "M30"
    assert signal["executionTf"] == "M30"
    assert signal["entryReadiness"] == "READY", signal.get("entryReadinessReason")
    assert signal["executionTimingStatus"] == "SHARED_TRIGGER_CONFIRMED"
    # Timing must never rewrite the thesis.
    assert signal["score"] == 1.1285
    assert signal["direction"] == "LONG"


def test_mt5_execution_timing_uses_bid_candle_close_not_quote_midpoint() -> None:
    signal = {
        "direction": "SHORT",
        "trigger_ok": True,
        # ATFX-style 20-point spread: midpoint is above the M15 open even
        # though the broker's bid candle has moved lower.
        "current_price": 0.81615,
    }
    states = {
        tf: {
            "confirmed": [
                {
                    "time": "2026-07-23T10:00:00Z",
                    "open": 0.81610,
                    "high": 0.81630,
                    "low": 0.81600,
                    "close": 0.81613,
                }
            ],
            "stale": False,
        }
        for tf in ("D1", "H4", "H1", "M30", "M15")
    }
    states["M15"]["forming"] = {
        "time": "2026-07-23T10:15:00Z",
        "open": 0.81613,
        "high": 0.81618,
        "low": 0.81604,
        "close": 0.81605,
    }

    # SLOW demotes trigger M15→M30; execution advisory stays M15.
    attach_timeframe_policy_payload(
        signal,
        {
            "display": "USD/CHF",
            "type": "forex",
            "source": "mt5",
            "score_group": "forex_majors",
        },
        "intraday",
        engine="engine_b",
        market_states=states,
        speed_state=SpeedState(
            live_speed_class=SpeedClass.SLOW,
            speed_percentile=10.0,
            history_ready=True,
            liquidity_class=LiquidityClass.NORMAL,
        ),
    )

    assert signal["triggerTf"] == "M30"
    assert signal["executionTf"] == "M15"


def test_shared_m15_trigger_execution_clears_entry_without_rescoring_engine_a_or_b() -> None:
    # Universal ladder: EUR/GBP uses M15 trigger/execution. Both engines take
    # the confirmed M15 trigger as timing evidence; neither is rescored by it.
    # Missing execution-TF data still fails closed.
    required_states = {
        tf: {
            "confirmed": [
                {
                    "time": "2026-07-13T10:00:00Z",
                    "open": 1.10,
                    "high": 1.11,
                    "low": 1.09,
                    "close": 1.105,
                }
            ],
            "stale": False,
        }
        for tf in ("D1", "H4", "H1", "M15")
    }

    for engine in ("engine_a", "engine_b"):
        forming_against = {
            "score": 2.4,
            "direction": "SHORT",
            "trigger_ok": True,
            "current_price": 1.106,
        }
        states = {
            **required_states,
            "M15": {
                "confirmed": [
                    {
                        "time": "2026-07-13T10:00:00Z",
                        "open": 1.10,
                        "high": 1.11,
                        "low": 1.09,
                        "close": 1.105,
                    }
                ],
                "forming": {
                    "time": "2026-07-13T10:15:00Z",
                    "open": 1.10,
                    "high": 1.107,
                    "low": 1.094,
                    "close": 1.106,
                },
                "stale": False,
            },
        }
        attach_timeframe_policy_payload(
            forming_against,
            {
                "display": "EUR/GBP",
                "type": "forex",
                "score_group": "forex_crosses",
            },
            "intraday",
            engine=engine,
            market_states=states,
        )

        assert forming_against["triggerTf"] == "M15"
        assert forming_against["executionTf"] == "M15"
        assert forming_against["executionTfActual"] == "M15"
        assert forming_against["executionTfConsumed"] is True
        assert forming_against["executionTimingStatus"] == "SHARED_TRIGGER_CONFIRMED"
        assert forming_against["entryReadiness"] == "READY"
        assert forming_against["score"] == 2.4
        assert forming_against["direction"] == "SHORT"
        assert timeframe_policy_execution_block_reason(
            forming_against,
            {
                "TF_POLICY_MODE": "enforced_demo",
                "TF_POLICY_DEMO_AUTOTRADE_ENABLED": True,
            },
        ) is None

        forming_with = {
            "score": 2.4,
            "direction": "SHORT",
            "trigger_ok": True,
            "current_price": 1.095,
        }
        attach_timeframe_policy_payload(
            forming_with,
            {
                "display": "EUR/GBP",
                "type": "forex",
                "score_group": "forex_crosses",
            },
            "intraday",
            engine=engine,
            market_states=states,
        )

        assert forming_with["executionTimingStatus"] == "SHARED_TRIGGER_CONFIRMED"
        assert forming_with["entryReadiness"] == "READY"
        assert forming_with["score"] == 2.4
        assert forming_with["direction"] == "SHORT"

        unavailable = {
            "score": 2.4,
            "direction": "SHORT",
            "trigger_ok": True,
        }
        states_without_m15 = {
            tf: state for tf, state in required_states.items() if tf != "M15"
        }
        attach_timeframe_policy_payload(
            unavailable,
            {
                "display": "EUR/GBP",
                "type": "forex",
                "score_group": "forex_crosses",
            },
            "intraday",
            engine=engine,
            market_states=states_without_m15,
        )

        assert unavailable["executionTfActual"] is None
        assert unavailable["executionTimingStatus"] == "UNAVAILABLE"
        assert unavailable["entryReadiness"] == "UNAVAILABLE"
        assert unavailable["score"] == 2.4
        assert unavailable["direction"] == "SHORT"
        assert timeframe_policy_execution_block_reason(
            unavailable,
            {
                "TF_POLICY_MODE": "enforced_demo",
                "TF_POLICY_DEMO_AUTOTRADE_ENABLED": True,
            },
        ) == "TF_POLICY_ENTRY_READINESS_UNAVAILABLE"


def test_policy_payload_rebuild_tolerates_v3_and_v4_payloads() -> None:
    # v4 payload: declarative conditional M5.
    rebuilt_v4 = speed_state_from_policy_payload(
        {
            "currentSpeedClass": "FAST",
            "liquidityClass": "NORMAL",
            "speedPercentile": 80.0,
            "executionTf": "M5",
            "m5Role": "refinement",
            "m5Policy": "conditional",
        }
    )
    assert rebuilt_v4 is not None
    assert rebuilt_v4.m5_quality_acceptable is True
    policy = resolve_timeframe_policy(
        "USD/JPY", "forex", "forex_majors", "intraday", rebuilt_v4
    )
    assert policy.trigger_tf == Timeframe.M15
    assert policy.execution_tf == Timeframe.M15
    assert policy.m5_role == M5Role.REFINEMENT
    assert policy.m5_policy == M5Policy.CONDITIONAL

    # v3 payload: speed-promoted M5 execution stamps still parse, but the
    # removed promotion is never re-armed — resolution stays on the template.
    rebuilt_v3 = speed_state_from_policy_payload(
        {
            "currentSpeedClass": "FAST",
            "liquidityClass": "NORMAL",
            "speedPercentile": 80.0,
            "executionTf": "M5",
            "m5Role": "execution",
        }
    )
    assert rebuilt_v3 is not None
    assert rebuilt_v3.m5_quality_acceptable is True
    policy_v3 = resolve_timeframe_policy(
        "USD/JPY", "forex", "forex_majors", "intraday", rebuilt_v3
    )
    assert policy_v3.m5_role == M5Role.REFINEMENT
    assert policy_v3.m5_policy == M5Policy.CONDITIONAL
    assert policy_v3.payload()["timeframePolicyHash"] == policy.payload()["timeframePolicyHash"]


def test_missing_lower_tf_preserves_payload_and_execution_has_no_higher_tf_fallback() -> None:
    signal = {"score": 2.0, "direction": "SHORT"}
    attach_timeframe_policy_payload(
        signal,
        {"display": "GBP/USD", "type": "forex", "score_group": "forex_majors"},
        "intraday",
        market_states={"D1": {"confirmed": [{}]}, "H4": {"confirmed": [{}]}, "H1": {"confirmed": [{}]}},
    )

    assert signal["direction"] == "SHORT"
    assert signal["entryReadiness"] == "UNAVAILABLE"
    assert "M15" in signal["entryReadinessReason"]
    assert engine_b_candles_for_tf("M15", [1], [2], [3], extra_by_tf={}) == []


def test_payload_contract_and_warmup_derivation() -> None:
    signal: dict = {"atr": 12.5}
    policy = attach_timeframe_policy_payload(
        signal,
        {"display": "AAPL", "type": "stock", "score_group": "us_stock_single"},
        "intraday",
        market_states={},
    )
    required = {
        "timeframePolicyVersion",
        "timeframeProfile",
        "regimeTf",
        "biasTf",
        "structureTf",
        "setupTf",
        "triggerTf",
        "executionTf",
        "m5Role",
        "executionMode",
        "m5Policy",
        "baselineSpeedClass",
        "liveSpeedClass",
        "currentSpeedClass",
        "candidateSpeedClass",
        "candidateAgeH1Bars",
        "transitionPending",
        "lastSpeedTransitionUtc",
        "speedPercentile",
        "policySource",
        "signalId",
        "engineId",
        "style",
        "policyKey",
        "entryDriftAtr",
        "entryDriftAtrTimeframe",
        "riskAtr",
        "riskAtrTimeframe",
        "executionMoveAtr",
        "executionMoveAtrTimeframe",
        "sessionCalendarId",
        "sessionCalendarSource",
        "providerSessionTimezone",
        "sessionState",
        "confirmedBarTimes",
        "formingBarTime",
        "entryReadiness",
        "entryReadinessReason",
        "atrValue",
        "atrTimeframe",
        "structureAgeBars",
        "quoteAgeSec",
    }
    assert required <= signal.keys()
    assert signal["timeframePolicyHash"]
    # AAPL session ladder: H1 structure / M30 setup / M15 trigger.
    assert policy.diagnostics.config_conflict is False
    assert policy.structure_tf == Timeframe.H1
    assert policy.setup_tf == Timeframe.M30
    assert policy.trigger_tf == Timeframe.M15
    assert derive_warmup_bars(ema_periods=(21, 50, 200), safety_margin=20) == 220


def test_policy_identity_is_canonical_engine_and_style_scoped() -> None:
    intraday = resolve_timeframe_policy(
        "EURUSD=X", "forex", "forex_majors", "intraday", engine_id="engine_a"
    )
    swing = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "swing", engine_id="engine_a"
    )
    engine_b = resolve_timeframe_policy(
        "MT5:EURUSD", "forex", "forex_majors", "intraday", engine_id="engine_b"
    )

    assert intraday.policy_key == "EURUSD:engine_a:intraday"
    assert len({intraday.policy_key, swing.policy_key, engine_b.policy_key}) == 3


def test_m5_matrix_language_maps_only_approved_authority() -> None:
    assert [role.value for role in M5Role] == [
        "execution",
        "refinement",
        "advisory",
        "disabled",
    ]
    assert parse_m5_matrix_language("M5") == (M5Role.EXECUTION, None)
    assert parse_m5_matrix_language("M5 after M15 confirmation") == (
        M5Role.EXECUTION,
        Timeframe.M15,
    )
    assert parse_m5_matrix_language("M5 refinement optional")[0] == M5Role.REFINEMENT
    assert parse_m5_matrix_language("M5 refinement only")[0] == M5Role.REFINEMENT
    assert parse_m5_matrix_language("M5 advisory")[0] == M5Role.ADVISORY
    assert parse_m5_matrix_language("no M5 authority")[0] == M5Role.DISABLED


def test_role_order_and_ladder_are_canonical_and_reversals_are_rejected() -> None:
    assert TIMEFRAME_LADDER == (
        Timeframe.D1,
        Timeframe.H4,
        Timeframe.H1,
        Timeframe.M30,
        Timeframe.M15,
        Timeframe.M5,
        Timeframe.M1,
    )
    try:
        validate_timeframe_role_order(
            Timeframe.D1,
            Timeframe.H4,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.M5,
            Timeframe.M5,
        )
    except ValueError as exc:
        assert "reverses" in str(exc)
    else:
        raise AssertionError("reversed policy was accepted")

    # Regime..trigger stay strict under LIVE_QUOTE, but the advisory execution
    # TF is exempt from ordering relative to the trigger.
    validate_timeframe_role_order(
        Timeframe.H1,
        Timeframe.M15,
        Timeframe.M15,
        Timeframe.M5,
        Timeframe.M1,
        Timeframe.M1,
        execution_mode=ExecutionMode.LIVE_QUOTE,
    )
    try:
        validate_timeframe_role_order(
            Timeframe.D1,
            Timeframe.H4,
            Timeframe.H1,
            Timeframe.M30,
            Timeframe.M15,
            Timeframe.M30,
            execution_mode=ExecutionMode.NEXT_AVAILABLE_QUOTE,
        )
    except ValueError as exc:
        assert "reverses" in str(exc)
    else:
        raise AssertionError("timeframe-based execution behind trigger was accepted")


def test_m1_roles_are_rejected_outside_scalp_templates(monkeypatch) -> None:
    import timeframe_policy

    # Engine B scalp keeps the universal M15 trigger; only Engine D is M1-native.
    scalp_b = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "scalp", engine_id="engine_b"
    )
    assert scalp_b.trigger_tf == Timeframe.M15
    native = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "scalp", engine_id="engine_d"
    )
    assert native.trigger_tf == Timeframe.M1

    # A (hypothetical) non-scalp template that resolves M1 fails closed.
    #
    # The shipped ENGINE_TF_ROLE_OVERRIDES matrix rewrites forex_majors_standard
    # trigger to M15 for both styles, so with it enabled the M1 patch below never
    # reaches the guard at all — the resolved ladder is a clean H4/M15 with no M1
    # in any role. Disable it here so this test exercises the invariant it names
    # rather than an artefact of the override matrix.
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG, "ENGINE_TF_ROLE_OVERRIDES", {"ENABLED": False, "BY_GROUP": {}}
    )
    monkeypatch.setitem(
        timeframe_policy._SYMBOL_OVERRIDES,
        "EURUSD",
        {"trigger": Timeframe.M1, "profile": "BROKEN_M1_PATCH"},
    )
    for engine_id, style in (
        ("engine_a", "intraday"),
        ("engine_b", "intraday"),
        ("engine_a", "swing"),
        ("engine_b", "swing"),
    ):
        try:
            resolve_timeframe_policy(
                "EUR/USD", "forex", "forex_majors", style, engine_id=engine_id
            )
        except PolicyConfigurationError as exc:
            assert "M1" in str(exc)
        else:
            raise AssertionError(f"M1 accepted for {engine_id}/{style}")

    # The guard covers the execution role too, so an M1 stranded there — the
    # state the role-override path used to leave behind — still fails closed.
    monkeypatch.setitem(
        timeframe_policy._SYMBOL_OVERRIDES,
        "EURUSD",
        {"execution": Timeframe.M1, "profile": "BROKEN_M1_EXECUTION_PATCH"},
    )
    try:
        resolve_timeframe_policy(
            "EUR/USD", "forex", "forex_majors", "intraday", engine_id="engine_a"
        )
    except PolicyConfigurationError as exc:
        assert "M1" in str(exc)
    else:
        raise AssertionError("M1 execution role accepted outside scalp templates")


def test_cold_start_keeps_baseline_and_reports_unavailable() -> None:
    bars = [{"high": 101, "low": 99, "close": 100, "vol": 1000}] * 40
    state = calculate_speed_state(
        bars,
        bars,
        spread=0.1,
        quote_age_sec=1,
        relative_volume=1.0,
        provider_market_state="open",
        current_session="open",
        gap_status="normal",
        scheduled_event=False,
        last_closed_h1_open_time=1,
    )
    policy = resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", "intraday", state
    )

    assert state.live_speed_class == SpeedClass.UNAVAILABLE
    assert state.history_ready is False
    assert state.adaptation_reason == "INSUFFICIENT_HISTORY"
    assert policy.execution_tf == Timeframe.M15
    assert policy.diagnostics.adaptation_applied is False


def test_hard_liquidity_failure_does_not_reclassify_persistent_speed() -> None:
    def bars(count: int) -> list[dict]:
        return [
            {
                "high": 101 + index * 0.01,
                "low": 99 + index * 0.01,
                "close": 100 + index * 0.01,
                "vol": 1000 + index,
            }
            for index in range(count)
        ]

    previous = SpeedState(
        live_speed_class=SpeedClass.FAST,
        history_ready=True,
        last_closed_h1_open_time=1,
    )
    state = calculate_speed_state(
        bars(240),
        bars(240),
        spread=None,
        quote_age_sec=None,
        current_session="open",
        gap_status="normal",
        scheduled_event=False,
        previous=previous,
        last_closed_h1_open_time=2,
    )

    assert state.live_speed_class == SpeedClass.FAST
    assert state.liquidity_class == LiquidityClass.UNAVAILABLE
    assert state.m5_quality_acceptable is False


def test_empty_market_state_does_not_force_thin_liquidity() -> None:
    """MT5 ticks often omit market_state; that must not stamp every major THIN."""

    def bars(count: int) -> list[dict]:
        return [
            {
                "high": 101 + index * 0.01,
                "low": 99 + index * 0.01,
                "close": 100 + index * 0.01,
                "vol": 1000 + index,
            }
            for index in range(count)
        ]

    state = calculate_speed_state(
        bars(240),
        bars(240),
        spread=0.0001,
        quote_age_sec=1,
        relative_volume=1.2,
        current_session="london",
        gap_status="normal",
        scheduled_event=False,
        provider_market_state="",
        last_closed_h1_open_time=1,
    )
    assert state.liquidity_class != LiquidityClass.THIN


def test_unavailable_liquidity_does_not_demote_forex_major_trigger() -> None:
    policy = resolve_timeframe_policy(
        "GBP/USD",
        "forex",
        "forex_majors",
        "intraday",
        SpeedState(
            live_speed_class=SpeedClass.FAST,
            history_ready=True,
            liquidity_class=LiquidityClass.UNAVAILABLE,
        ),
        engine_id="engine_a",
    )
    assert policy.structure_tf == Timeframe.H4
    assert policy.setup_tf == Timeframe.H1
    assert policy.trigger_tf == Timeframe.M15
    assert policy.diagnostics.adaptation_applied is False


def test_liquidity_is_separate_from_speed_and_blocks_m5_eligibility() -> None:
    liquidity = classify_liquidity(
        quote_age_sec=1,
        spread_trigger_atr=0.25,
        relative_volume=2.0,
        provider_market_state="open",
    )
    assert liquidity == LiquidityClass.THIN
    state = SpeedState(
        live_speed_class=SpeedClass.FAST,
        history_ready=True,
        liquidity_class=liquidity,
        m5_quality_acceptable=False,
    )
    policy = resolve_timeframe_policy(
        "BTC/USDT", "crypto", "crypto_btc", "intraday", state
    )
    # THIN demotes trigger only (M15→M30); M5 eligibility remains blocked.
    assert policy.trigger_tf == Timeframe.M30
    assert policy.execution_tf == Timeframe.M15
    assert policy.m5_policy == M5Policy.CONDITIONAL
    assert policy.diagnostics.m15_confirmation_required_for_m5 is True
    assert policy.diagnostics.m5_liquidity_blocked is True


def test_alias_group_conflict_sets_config_error_and_disables_policy_autotrade() -> None:
    signal = {"score": 2.0, "direction": "LONG"}
    policy = attach_timeframe_policy_payload(
        signal,
        {
            "display": "EURUSD=X",
            "type": "forex",
            "score_group": "forex_exotics",
        },
        "intraday",
        config={"TF_POLICY_MODE": "enforced", "TF_POLICY_AUTOTRADE_ENABLED": True},
    )

    assert policy.policy_source == PolicySource.CONFIG_CONFLICT
    assert signal["entryReadiness"] == "CONFIG_ERROR"
    assert signal["timeframePolicyAutotradeEnabled"] is False
    assert timeframe_policy_execution_block_reason(
        signal, {"TF_POLICY_MODE": "shadow", "TF_POLICY_AUTOTRADE_ENABLED": False}
    ) == "TF_POLICY_CONFIG_CONFLICT"


def test_fresh_scan_unavailable_speed_state_round_trips_policy_hash() -> None:
    """Fresh Engine B scans resolve with a real (possibly UNAVAILABLE) SpeedState.

    The execute-time refresh pins speed/liquidity from the stamped payload via
    speed_state_from_policy_payload; both passes must produce the same policy
    hash.  In v4, THIN/UNAVAILABLE liquidity never rewrites role timeframes —
    it is recorded as m5_liquidity_blocked for the eligibility layer — so a
    baseline (None-speed) resolution can no longer diverge from the pinned
    refresh either.
    """
    scan_state = SpeedState()  # fail-closed default: UNAVAILABLE speed/liquidity
    scan = resolve_timeframe_policy(
        "BTCUSDT", "crypto", None, "intraday", scan_state, engine_id="engine_b"
    )
    stamped = scan.payload()

    assert scan.diagnostics.m5_liquidity_blocked is True
    # UNAVAILABLE liquidity demotes M15→M30; M5 remains conditional refinement.
    assert scan.trigger_tf == Timeframe.M30
    assert scan.m5_policy == M5Policy.CONDITIONAL

    pinned = speed_state_from_policy_payload(stamped)
    assert pinned is not None
    refreshed = resolve_timeframe_policy(
        "BTCUSDT", "crypto", None, "intraday", pinned, engine_id="engine_b"
    )
    assert refreshed.payload()["timeframePolicyHash"] == stamped["timeframePolicyHash"]

    # None speed_state does not demote (no THIN/SLOW evidence) — hashes may
    # differ from a scan that saw UNAVAILABLE liquidity. Pin path must match.
    legacy = resolve_timeframe_policy(
        "BTCUSDT", "crypto", None, "intraday", None, engine_id="engine_b"
    )
    assert legacy.trigger_tf == Timeframe.M15
    assert refreshed.trigger_tf == Timeframe.M30


def test_reconciliation_rejects_aliases_assigned_to_conflicting_groups() -> None:
    pairs = [
        {
            "display": "EUR/USD",
            "symbol": "EURUSD=X",
            "type": "forex",
            "score_group": "forex_majors",
        },
        {
            "display": "EURUSD=X",
            "symbol": "EURUSD",
            "type": "forex",
            "score_group": "forex_exotics",
        },
    ]
    try:
        reconcile_symbol_universe(pairs)
    except PolicyConfigurationError as exc:
        assert "EURUSD" in str(exc)
    else:
        raise AssertionError("conflicting alias groups were accepted")


def test_session_calendar_resolution_is_provider_first_and_timezone_aware() -> None:
    resolved = resolve_session_calendar(
        provider_metadata={
            "sessionCalendarId": "broker-x-aapl",
            "timezone": "America/New_York",
            "state": "open",
        },
        underlying_exchange_calendar={
            "sessionCalendarId": "XNYS",
            "timezone": "America/New_York",
        },
    )
    assert resolved.source == SessionCalendarSource.PROVIDER_METADATA
    assert resolved.calendar_id == "broker-x-aapl"
    unavailable = resolve_session_calendar(
        provider_calendar={"sessionCalendarId": "bad", "timezone": "Not/AZone"}
    )
    assert unavailable.source == SessionCalendarSource.SESSION_UNAVAILABLE


def test_enforced_policy_requires_separate_autotrade_promotion() -> None:
    signal = {"policySource": PolicySource.SYMBOL_OVERRIDE.value}
    assert timeframe_policy_execution_block_reason(
        signal, {"TF_POLICY_MODE": PolicyMode.SHADOW.value, "TF_POLICY_AUTOTRADE_ENABLED": False}
    ) is None
    assert timeframe_policy_execution_block_reason(
        signal, {"TF_POLICY_MODE": PolicyMode.ENFORCED.value, "TF_POLICY_AUTOTRADE_ENABLED": False}
    ) == "TF_POLICY_DEMO_AUTOTRADE_DISABLED"


def test_policy_readiness_gate_does_not_capture_unstamped_engine_d() -> None:
    assert timeframe_policy_execution_block_reason(
        {"engine": "scalp", "direction": "LONG"},
        {
            "TF_POLICY_MODE": "enforced_demo",
            "TF_POLICY_DEMO_AUTOTRADE_ENABLED": True,
        },
        {"accountId": "demo-42", "demo": True},
    ) is None


def test_enforced_demo_promotes_policy_score_direction_and_locks_real_account() -> None:
    signal = {
        "confluenceScore": 5.9,
        "score": 5.9,
        "direction": "SHORT",
        "maxScore": 10,
        "entryReadiness": "READY",
    }
    cfg = {
        "TF_POLICY_MODE": "enforced_demo",
        "TF_POLICY_DEMO_AUTOTRADE_ENABLED": True,
        "TF_POLICY_REAL_AUTOTRADE_ENABLED": False,
    }

    apply_authoritative_policy_result(
        signal,
        policy_score=7.2,
        policy_direction="LONG",
        config=cfg,
    )

    assert signal["legacyScore"] == 5.9
    assert signal["legacyDirection"] == "SHORT"
    assert signal["policyScore"] == 7.2
    assert signal["policyDirection"] == "LONG"
    assert signal["confluenceScore"] == 7.2
    assert signal["direction"] == "LONG"
    assert signal["authoritativeScoreSource"] == "POLICY"
    assert timeframe_policy_execution_block_reason(
        signal, cfg, {"accountId": "demo-42", "exchange": "Bybit", "demo": True}
    ) is None
    assert timeframe_policy_execution_block_reason(
        signal, cfg, {"accountId": "real-42", "exchange": "Bybit", "demo": False}
    ) == "TF_POLICY_REAL_ACCOUNT_LOCKED"
    assert signal["timeframePolicyExecution"]["accountEnvironment"] == "real"


# --- Phase 6/7: conditional M5 eligibility + trigger lifecycle stamping ---


def _closed_states(tfs=("D1", "H4", "H1", "M30", "M15", "M5")) -> dict:
    return {
        tf: {"confirmed": [{"time": "2026-07-13T10:00:00Z"}], "stale": False}
        for tf in tfs
    }


def _eurusd_pair() -> dict:
    return {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"}


def _gbpusd_pair() -> dict:
    return {"display": "GBP/USD", "type": "forex", "score_group": "forex_majors"}


def test_attach_stamps_m5_ineligible_with_policy_reason_when_policy_disabled() -> None:
    signal = {"score": 2.15, "direction": "LONG", "price": 1.1, "atr": 0.01}
    before = {key: signal[key] for key in ("score", "direction")}

    attach_timeframe_policy_payload(
        signal, _eurusd_pair(), "intraday", market_states=_closed_states()
    )

    assert signal["m5Policy"] == M5Policy.DISABLED.value
    assert signal["m5Eligible"] is False
    assert signal["m5EligibilityReasons"] == ["m5_policy_disabled"]
    # Eligibility is diagnostics-only: score/direction never mutate.
    assert {key: signal[key] for key in before} == before
    # No trigger inputs => no trigger fields (backward compatible).
    assert "triggerState" not in signal
    assert "triggerAge" not in signal
    assert "triggerExpiry" not in signal


def _m5_approved_config(score_group: str = "forex_majors") -> dict:
    """CONFIG marking one score group as having frozen out-of-sample evidence.

    The evidence gate runs before every per-bar condition, so tests that want
    to exercise those conditions must supply an approved group; otherwise the
    only reason reported is m5_validation_not_approved.
    """
    return {
        "M5_ELIGIBILITY": {
            "VALIDATION_STATUS_DEFAULT": "unvalidated",
            "VALIDATION_STATUS_BY_SCORE_GROUP": {score_group: "approved"},
        }
    }


def test_attach_stamps_m5_eligible_when_all_conditional_inputs_present() -> None:
    signal = {
        "score": 2.5,
        "direction": "LONG",
        "price": 1.3,
        "atr": 0.01,
        "timestamp": "2026-07-13T10:00:00Z",
        "setupZone": [1.29, 1.30],
        "triggerLevel": 1.301,
        "htfStructureValid": True,
        "setupArmed": True,
        "sessionEligible": True,
        "currentSpread": 0.0002,
        "m5SpreadThreshold": 0.0005,
        "distanceFromSetupAtr": 0.3,
        "m5MaxDisplacementAtr": 1.5,
        "m5EntryEvent": "sweep_reclaim",
        "rrAtCurrentPrice": 2.1,
        "m5MinRr": 1.5,
        "quoteInsideOpposingZone": False,
        "ltfDirectionAgrees": True,
    }

    policy = attach_timeframe_policy_payload(
        signal, _gbpusd_pair(), "intraday", market_states=_closed_states(),
        config=_m5_approved_config(),
    )

    assert policy.m5_policy == M5Policy.CONDITIONAL
    assert signal["m5Eligible"] is True
    assert signal["m5EligibilityReasons"] == ["entry_event:sweep_reclaim"]
    assert signal["score"] == 2.5
    assert signal["direction"] == "LONG"


def test_attach_m5_eligibility_fail_closed_on_missing_inputs() -> None:
    signal = {"score": 2.5, "direction": "LONG", "price": 1.3}

    attach_timeframe_policy_payload(
        signal, _gbpusd_pair(), "intraday", market_states=_closed_states(),
        config=_m5_approved_config(),
    )

    assert signal["m5Policy"] == M5Policy.CONDITIONAL.value
    assert signal["m5Eligible"] is False
    reasons = signal["m5EligibilityReasons"]
    assert "spread_missing" in reasons
    assert "displacement_missing" in reasons
    assert "structural_rr_missing" in reasons
    assert "no_m5_entry_event" in reasons
    assert "setup_not_armed" in reasons


def test_attach_stamps_trigger_lifecycle_with_defaults() -> None:
    signal = {
        "direction": "LONG",
        "price": 1.31,
        "timestamp": "2026-07-13T11:00:00Z",
        "setupZone": [1.29, 1.30],
        "triggerLevel": 1.301,
    }

    policy = attach_timeframe_policy_payload(
        signal, _gbpusd_pair(), "intraday", market_states=_closed_states()
    )

    assert signal["signalId"]
    assert signal["triggerState"] == "ARMED"
    assert signal["triggerAge"] == 0.0
    # Default TTL: whichever of 8 trigger bars (8 x M15 = 2h) or 4h first.
    assert policy.trigger_tf == Timeframe.M15
    assert signal["triggerExpiry"] == "2026-07-13T13:00:00+00:00"

    from timeframe_policy import get_trigger_record

    record = get_trigger_record(signal["signalId"])
    assert record is not None
    assert record.state.value == "ARMED"
    assert record.setup_zone == (1.29, 1.30)
    assert record.trigger_level == 1.301
    assert record.trigger_tf == "M15"


def test_attach_trigger_lifecycle_respects_config_ttl_override() -> None:
    signal = {
        "direction": "SHORT",
        "price": 1.31,
        "timestamp": "2026-07-13T12:00:00Z",
        "setupZone": [1.31, 1.32],
        "triggerLevel": 1.309,
    }

    attach_timeframe_policy_payload(
        signal,
        _gbpusd_pair(),
        "intraday",
        market_states=_closed_states(),
        config={"TRIGGER_LIFECYCLE": {"TTL_SECONDS": 600, "MAX_TRIGGER_BARS": 8}},
    )

    # min(600s, 8 x 300s) = 600s after the 12:00 arm time.
    assert signal["triggerExpiry"] == "2026-07-13T12:10:00+00:00"


def test_attach_trigger_lifecycle_expires_stale_record_on_restamp() -> None:
    pair = _gbpusd_pair()
    states = _closed_states()
    armed = {
        "direction": "LONG",
        "price": 1.31,
        "timestamp": "2026-07-13T13:00:00Z",
        "setupZone": [1.29, 1.30],
        "triggerLevel": 1.301,
    }
    attach_timeframe_policy_payload(
        armed, pair, "intraday", market_states=states,
        config=_m5_approved_config(),
    )
    assert armed["triggerState"] == "ARMED"

    # Same signal id re-stamped 5h later: past both TTL bounds (8 x M15 bars =
    # 2h, 4h wall clock), so the attach-time sweep expires the record.
    restamped = dict(armed)
    restamped["signalId"] = armed["signalId"]
    restamped["timestamp"] = "2026-07-13T18:00:00Z"
    attach_timeframe_policy_payload(
        restamped, pair, "intraday", market_states=states,
        config=_m5_approved_config(),
    )

    assert restamped["signalId"] == armed["signalId"]
    assert restamped["triggerState"] == "EXPIRED"
    assert restamped["triggerAge"] == 5 * 3600.0
    assert restamped["triggerExpiry"] == "2026-07-13T15:00:00+00:00"
    # The expired trigger feeds M5 eligibility fail-closed.
    assert "trigger_expired" in restamped["m5EligibilityReasons"]


def test_attach_omits_trigger_fields_when_required_inputs_absent() -> None:
    # Setup zone / trigger level / timestamp missing => nothing stamped.
    signal = {"direction": "LONG", "price": 1.1}
    attach_timeframe_policy_payload(
        signal, _eurusd_pair(), "intraday", market_states=_closed_states()
    )
    assert "triggerState" not in signal

    # Zone + level present but no usable timestamp => still nothing stamped.
    signal2 = {"direction": "LONG", "price": 1.1, "setupZone": [1.09, 1.10], "triggerLevel": 1.101}
    attach_timeframe_policy_payload(
        signal2, _eurusd_pair(), "intraday", market_states=_closed_states()
    )
    assert "triggerState" not in signal2


def test_attach_stamps_symbol_override_applied_flag() -> None:
    signal = {"direction": "LONG", "price": 1.1}
    policy = attach_timeframe_policy_payload(
        signal, _eurusd_pair(), "intraday", market_states=_closed_states()
    )
    assert signal["symbolOverrideApplied"] == policy.diagnostics.symbol_override_applied
    assert (
        signal["timeframePolicyDiagnostics"]["symbolOverrideApplied"]
        == signal["symbolOverrideApplied"]
    )


# --- Cash-equity exchange-session calendars (2026-07-28 ATFX additions) ---
#
# 2026-07-28 is a Tuesday, an ordinary trading day with no weekend/holiday
# ambiguity. UTC offsets below assume each zone's July (summer) offset:
# America/New_York EDT UTC-4, Europe/Berlin & Europe/Paris CEST UTC+2,
# Asia/Hong_Kong UTC+8 year-round (no DST). exchange_calendars has its own
# boundary-by-boundary tests; these confirm the wiring through
# attach_timeframe_policy_payload, not the calendar math itself.

def _hk0700_pair() -> dict:
    return {"display": "HK0700", "symbol": "HK0700", "type": "stock", "source": "mt5"}


def _sap_pair() -> dict:
    """XETRA-listed via CASH_EQUITY_XETRA_TICKERS, not Euronext."""
    return {"display": "SAP", "symbol": "SAP", "type": "stock", "source": "mt5"}


def _lvmh_pair() -> dict:
    """Euronext Paris-listed: in CASH_EQUITY_EUROPE_TICKERS but not XETRA."""
    return {"display": "LVMH", "symbol": "LVMH", "type": "stock", "source": "mt5"}


def _aapl_pair() -> dict:
    return {"display": "AAPL", "symbol": "AAPL.US", "type": "stock", "source": "mt5"}


def test_hk_share_resolves_hkex_calendar_and_live_lunch_break_phase() -> None:
    signal = {
        "direction": "LONG", "timestamp": "2026-07-28T04:30:00Z",  # 12:30 HKT
    }
    attach_timeframe_policy_payload(
        signal, _hk0700_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal["sessionCalendarId"] == "HKEX"
    assert signal["providerSessionTimezone"] == "Asia/Hong_Kong"
    assert signal["sessionState"] == "LUNCH_BREAK"


def test_hk_share_reports_open_during_continuous_session() -> None:
    signal = {"direction": "LONG", "timestamp": "2026-07-28T02:00:00Z"}  # 10:00 HKT
    attach_timeframe_policy_payload(
        signal, _hk0700_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal["sessionState"] == "OPEN"


def test_xetra_and_euronext_share_split_resolves_distinct_calendars() -> None:
    """SAP and LVMH are both CASH_EQUITY_EUROPE but on different exchanges."""
    sap_signal = {"direction": "LONG", "timestamp": "2026-07-28T08:00:00Z"}  # 10:00 CEST
    attach_timeframe_policy_payload(
        sap_signal, _sap_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert sap_signal["sessionCalendarId"] == "XETRA"
    assert sap_signal["providerSessionTimezone"] == "Europe/Berlin"
    assert sap_signal["sessionState"] == "OPEN"

    lvmh_signal = {"direction": "LONG", "timestamp": "2026-07-28T08:00:00Z"}
    attach_timeframe_policy_payload(
        lvmh_signal, _lvmh_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert lvmh_signal["sessionCalendarId"] == "EURONEXT_PARIS"
    assert lvmh_signal["providerSessionTimezone"] == "Europe/Paris"
    assert lvmh_signal["sessionState"] == "OPEN"


def test_us_share_resolves_nyse_nasdaq_calendar() -> None:
    signal = {"direction": "LONG", "timestamp": "2026-07-28T18:00:00Z"}  # 14:00 ET
    attach_timeframe_policy_payload(
        signal, _aapl_pair(), "swing",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal["sessionCalendarId"] == "NYSE_NASDAQ"
    assert signal["sessionState"] == "OPEN"


def test_forex_pair_is_unaffected_by_cash_equity_calendars() -> None:
    """The calendar config only matches type=='stock'; forex must resolve

    exactly as it did before session calendars existed: SESSION_UNAVAILABLE.
    """
    signal = {"direction": "LONG", "timestamp": "2026-07-28T14:00:00Z"}
    attach_timeframe_policy_payload(
        signal, _eurusd_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal.get("sessionCalendarId") is None
    assert signal["sessionState"] == "SESSION_UNAVAILABLE"


def test_missing_timestamp_leaves_session_state_at_config_default() -> None:
    """No decision timestamp -> no live phase computed; identity still resolves."""
    signal = {"direction": "LONG"}
    attach_timeframe_policy_payload(
        signal, _hk0700_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal["sessionCalendarId"] == "HKEX"
    assert signal["sessionState"] == "UNKNOWN"


def test_hk_trigger_armed_before_lunch_is_invalidated_when_restamped_during_lunch() -> None:
    """A trigger armed just before the HK lunch break must not carry through it."""
    pair = _hk0700_pair()
    signal_id = "hk-lunch-carry-test"

    armed = {
        "direction": "LONG", "timestamp": "2026-07-28T03:55:00Z",  # 11:55 HKT
        "signalId": signal_id, "setupZone": [100.0, 101.0], "triggerLevel": 101.5,
    }
    attach_timeframe_policy_payload(
        armed, pair, "intraday", config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert armed["sessionState"] == "OPEN"
    assert armed["triggerState"] == "ARMED"

    restamped = {
        "direction": "LONG", "timestamp": "2026-07-28T04:30:00Z",  # 12:30 HKT
        "signalId": signal_id, "setupZone": [100.0, 101.0], "triggerLevel": 101.5,
    }
    attach_timeframe_policy_payload(
        restamped, pair, "intraday", config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert restamped["sessionState"] == "LUNCH_BREAK"
    assert restamped["triggerState"] == "INVALIDATED"

    record = get_trigger_record(signal_id)
    assert record is not None
    assert record.invalidation_reason == "session_break:LUNCH_BREAK"


def test_hk_trigger_does_not_arm_fresh_during_lunch_break() -> None:
    """A brand-new signal_id first seen during the break must not arm at all."""
    signal = {
        "direction": "LONG", "timestamp": "2026-07-28T04:30:00Z",
        "signalId": "hk-lunch-fresh-test",
        "setupZone": [100.0, 101.0], "triggerLevel": 101.5,
    }
    attach_timeframe_policy_payload(
        signal, _hk0700_pair(), "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal["sessionState"] == "LUNCH_BREAK"
    assert "triggerState" not in signal
    assert get_trigger_record("hk-lunch-fresh-test") is None


def test_hk_trigger_can_rearm_after_lunch_reopens() -> None:
    """Once invalidated, a later signal for the same underlying setup can arm

    fresh with a new signal_id — the next scan's rebuilt setup/room/spread,
    not the stale pre-lunch one.
    """
    pair = _hk0700_pair()
    before = {
        "direction": "LONG", "timestamp": "2026-07-28T03:55:00Z",
        "signalId": "hk-reopen-before", "setupZone": [100.0, 101.0], "triggerLevel": 101.5,
    }
    attach_timeframe_policy_payload(
        before, pair, "intraday", config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert before["triggerState"] == "ARMED"

    after = {
        "direction": "LONG", "timestamp": "2026-07-28T05:05:00Z",  # 13:05 HKT
        "signalId": "hk-reopen-after", "setupZone": [100.2, 101.2], "triggerLevel": 101.7,
    }
    attach_timeframe_policy_payload(
        after, pair, "intraday", config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert after["sessionState"] == "OPEN"
    assert after["triggerState"] == "ARMED"


@pytest.mark.parametrize("ticker,calendar_id", [
    ("HK0001", "HKEX"), ("HK9988", "HKEX"),
    ("SAP", "XETRA"), ("BMW", "XETRA"),
    ("LVMH", "EURONEXT_PARIS"), ("BNPP", "EURONEXT_PARIS"),
    ("AAPL", "NYSE_NASDAQ"), ("BABA", "NYSE_NASDAQ"),
])
def test_share_calendar_resolution_matches_region(ticker, calendar_id) -> None:
    signal = {"direction": "LONG", "timestamp": "2026-07-28T10:00:00Z"}
    attach_timeframe_policy_payload(
        signal,
        {"display": ticker, "symbol": ticker, "type": "stock", "source": "mt5"},
        "intraday",
        config=_CASH_EQUITY_SESSION_CALENDARS_CONFIG,
    )
    assert signal["sessionCalendarId"] == calendar_id
