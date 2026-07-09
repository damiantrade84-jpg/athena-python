from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from athena_research.commodity_data_audit.costs import (
    STRESS_MULTIPLIERS,
    modelled_costs_for_instrument,
    stress_spread_points,
)
from athena_research.commodity_data_audit.manifest import build_acquisition_manifest
from athena_research.commodity_data_audit.quality import audit_candle_series, candles_to_frame
from athena_research.commodity_data_audit.registry import (
    INDEPENDENT_CLUSTER_MIN,
    build_coverage_matrix,
    independent_cluster_count,
    load_commodity_pairs,
    recommended_acquisition_batch,
    resolve_provider_aliases,
)
from mt5_executor import mt5_map_symbol
from athena_research.commodity_data_audit.mt5_probe import (
    EMPIRICAL_BLOCKED_MT5_UNAVAILABLE,
    EMPIRICAL_BLOCKED_NOT_PROBED,
    EMPIRICAL_BLOCKED_SYMBOL_UNAVAILABLE,
    EMPIRICAL_CLEAR_ON_PROBE,
    Mt5ProbeOutcome,
    REASON_MT5_UNAVAILABLE,
    evaluate_mt5_probe,
    empirical_gate_status,
)


def _pair(display: str) -> dict:
    for pair in load_commodity_pairs():
        if pair.get("display") == display:
            return pair
    raise KeyError(display)


def test_load_commodity_pairs_matches_enabled_universe():
    pairs = load_commodity_pairs()
    displays = {pair["display"] for pair in pairs}
    assert "XAU/USD" in displays
    assert "WTI Oil" in displays
    assert "Gasoline" in displays
    assert all(pair.get("type") == "commodity" for pair in pairs)
    assert all(pair.get("enabled") is True for pair in pairs)


def test_mt5_alias_resolution_for_core_liquids():
    assert mt5_map_symbol("XAU/USD") == "XAUUSD"
    assert mt5_map_symbol("WTI Oil") == "SpotCrude"
    assert mt5_map_symbol("Brent Oil") == "SpotBrent"
    assert mt5_map_symbol("Nat Gas") == "NatGas"
    assert mt5_map_symbol("Copper") == "Copper"
    assert mt5_map_symbol("XPT/USD") == "XPTUSD"
    assert mt5_map_symbol("XPD/USD") == "XPDUSD"


def test_provider_alias_resolution_covers_eodhd_and_cot():
    aliases = resolve_provider_aliases(_pair("XAU/USD"))
    assert aliases.mt5_symbol == "XAUUSD"
    assert aliases.eodhd_ticker == "XAUUSD.FOREX"
    assert aliases.cot_futures_proxy == "XAU"

    wti = resolve_provider_aliases(_pair("WTI Oil"))
    assert wti.mt5_symbol == "SpotCrude"
    assert wti.eodhd_volume_ticker == "WTICOUSD.FOREX"
    assert wti.cot_futures_proxy == "OIL"

    copper = resolve_provider_aliases(_pair("Copper"))
    assert copper.eodhd_volume_ticker == "XCUUSD.FOREX"

    cocoa = resolve_provider_aliases(_pair("Cocoa"))
    assert cocoa.yfinance_symbol == "CC=F"


def test_timeframe_mapping_in_acquisition_batch():
    batch = recommended_acquisition_batch()
    assert batch["timeframes"] == ["H4", "H1", "D1"]
    assert batch["limits"]["H4"] == 4400
    assert batch["min_bars"]["H4"] >= 500


def test_independent_cluster_count_meets_no_code_minimum():
    assert independent_cluster_count() >= INDEPENDENT_CLUSTER_MIN


def test_coverage_matrix_tags_contract_and_volume_types():
    row = next(r for r in build_coverage_matrix() if r["canonical_instrument"] == "XAU/USD")
    assert row["instrument_kind"] == "spot_cfd"
    assert row["volume_kind"] == "tick_volume"
    assert row["mt5_native_h4"] is True
    assert row["independent_cluster"] is True


def test_gasoline_kept_for_mt5_but_notes_eodhd_gap():
    row = next(r for r in build_coverage_matrix() if r["canonical_instrument"] == "Gasoline")
    assert row["rejection_reason"] is None
    assert row["eodhd_fallback_status"] == "mt5_only_no_eodhd_ticker"
    assert "no EODHD_COMMODITY_TICKERS entry" in row["eodhd_notes"]


def test_audit_candle_series_detects_duplicates_missing_and_future_bars():
    candles = [
        {"time": "2024-01-01T00:00:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 10},
        {"time": "2024-01-01T04:00:00+00:00", "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "vol": 12},
        {"time": "2024-01-01T04:00:00+00:00", "open": 1.6, "high": 2.6, "low": 1.1, "close": 2.1, "vol": 13},
        {"time": "2099-01-01T00:00:00+00:00", "open": 3, "high": 4, "low": 2, "close": 3.5, "vol": 1},
    ]
    report = audit_candle_series(
        candles,
        timeframe="H4",
        as_of=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert report.bar_count == 4
    assert report.duplicate_timestamps
    assert report.future_bar_count == 1
    assert report.missing_bar_estimate >= 0


def test_audit_candle_series_excludes_forming_bar_by_default():
    candles = [
        {"time": "2024-01-01T00:00:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 10},
        {"time": "2024-01-01T04:00:00+00:00", "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "vol": 12},
    ]
    report = audit_candle_series(
        candles,
        timeframe="H4",
        as_of=datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc),
        include_forming_bar=False,
    )
    assert report.bar_count == 2
    assert report.details["confirmed_bar_count"] == 1
    assert report.incomplete_bar_present is False


def test_manifest_content_hash_is_reproducible_for_same_inputs():
    first = build_acquisition_manifest(
        symbols=["XAU/USD", "WTI Oil"],
        timeframes=["H4", "D1"],
    )
    second = build_acquisition_manifest(
        symbols=["XAU/USD", "WTI Oil"],
        timeframes=["H4", "D1"],
    )
    assert first["content_hash"] == second["content_hash"]


def test_modelled_costs_include_stress_multipliers():
    costs = modelled_costs_for_instrument("WTI Oil")
    assert costs.spread_points == 8.0
    assert costs.stress_multipliers == STRESS_MULTIPLIERS
    assert stress_spread_points(costs.spread_points, 2.0) == 16.0


def test_candles_to_frame_preserves_timestamp_order():
    frame = candles_to_frame(
        [
            {"time": "2024-01-01T08:00:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 1},
            {"time": "2024-01-01T04:00:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 1},
        ]
    )
    assert list(frame.index.strftime("%H:%M")) == ["04:00", "08:00"]


def test_evaluate_mt5_probe_fails_closed_when_mt5_unavailable():
    outcome = evaluate_mt5_probe(
        {"error": "MT5 not connected"},
        expected_displays=["XAU/USD"],
    )
    assert outcome.ok is False
    assert outcome.empirical_gate_status == EMPIRICAL_BLOCKED_MT5_UNAVAILABLE
    assert outcome.reason_code == REASON_MT5_UNAVAILABLE


def test_evaluate_mt5_probe_fails_when_symbol_not_available():
    outcome = evaluate_mt5_probe(
        {"Gasoline": {"mt5_symbol": "Gasoline", "available": False}},
        expected_displays=["Gasoline"],
    )
    assert outcome.ok is False
    assert outcome.empirical_gate_status == EMPIRICAL_BLOCKED_SYMBOL_UNAVAILABLE
    assert outcome.unavailable_symbols == ("Gasoline",)


def test_evaluate_mt5_probe_clears_when_all_symbols_available():
    outcome = evaluate_mt5_probe(
        {"XAU/USD": {"mt5_symbol": "XAUUSD", "available": True}},
        expected_displays=["XAU/USD"],
    )
    assert outcome.ok is True
    assert outcome.empirical_gate_status == EMPIRICAL_CLEAR_ON_PROBE


def test_empirical_gate_blocked_until_probe_requested_and_succeeds():
    assert empirical_gate_status(probe_mt5_requested=False, probe_outcome=None) == EMPIRICAL_BLOCKED_NOT_PROBED
    blocked = Mt5ProbeOutcome(
        ok=False,
        empirical_gate_status=EMPIRICAL_BLOCKED_MT5_UNAVAILABLE,
        reason_code=REASON_MT5_UNAVAILABLE,
        probe={"error": "MT5 not connected"},
        unavailable_symbols=tuple(),
    )
    assert empirical_gate_status(probe_mt5_requested=True, probe_outcome=blocked) == EMPIRICAL_BLOCKED_MT5_UNAVAILABLE
    cleared = Mt5ProbeOutcome(
        ok=True,
        empirical_gate_status=EMPIRICAL_CLEAR_ON_PROBE,
        reason_code=None,
        probe={"XAU/USD": {"mt5_symbol": "XAUUSD", "available": True}},
        unavailable_symbols=tuple(),
    )
    assert empirical_gate_status(probe_mt5_requested=True, probe_outcome=cleared) == EMPIRICAL_CLEAR_ON_PROBE


def test_audit_cli_exits_nonzero_when_probe_mt5_unavailable(monkeypatch):
    import tools.audit_commodity_data_coverage as cli

    output = Path(__file__).resolve().parent / "_tmp_cli_probe_fail.json"
    blocked = Mt5ProbeOutcome(
        ok=False,
        empirical_gate_status=EMPIRICAL_BLOCKED_MT5_UNAVAILABLE,
        reason_code=REASON_MT5_UNAVAILABLE,
        probe={"error": "MT5 not connected"},
        unavailable_symbols=tuple(),
    )
    monkeypatch.setattr(cli, "run_mt5_symbol_probe", lambda _pairs: blocked)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_commodity_data_coverage.py",
            "--symbols",
            "XAU/USD",
            "--probe-mt5",
            "--output",
            str(output),
        ],
    )

    try:
        exit_code = cli.main()
        report = json.loads(output.read_text(encoding="utf-8"))
    finally:
        output.unlink(missing_ok=True)

    assert exit_code == cli.EXIT_PROBE_FAILED
    assert report["empirical_gate_status"] == EMPIRICAL_BLOCKED_MT5_UNAVAILABLE
    assert report["empirical_gate_reason"] == REASON_MT5_UNAVAILABLE
    assert report["mt5_symbol_probe"] == {"error": "MT5 not connected"}
    assert report["independent_cluster_count"] == 1


def test_audit_cli_succeeds_when_probe_mt5_clears(monkeypatch):
    import tools.audit_commodity_data_coverage as cli

    output = Path(__file__).resolve().parent / "_tmp_cli_probe_ok.json"
    cleared = Mt5ProbeOutcome(
        ok=True,
        empirical_gate_status=EMPIRICAL_CLEAR_ON_PROBE,
        reason_code=None,
        probe={"XAU/USD": {"mt5_symbol": "XAUUSD", "available": True}},
        unavailable_symbols=tuple(),
    )
    monkeypatch.setattr(cli, "run_mt5_symbol_probe", lambda _pairs: cleared)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_commodity_data_coverage.py",
            "--symbols",
            "XAU/USD",
            "--probe-mt5",
            "--output",
            str(output),
        ],
    )

    try:
        exit_code = cli.main()
        report = json.loads(output.read_text(encoding="utf-8"))
    finally:
        output.unlink(missing_ok=True)

    assert exit_code == cli.EXIT_OK
    assert report["empirical_gate_status"] == EMPIRICAL_CLEAR_ON_PROBE
    assert report["empirical_gate_reason"] is None
    assert "acquire_cmd" not in report
