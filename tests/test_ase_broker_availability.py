"""ASE live-scan universe follows the configured MT5 broker availability list."""

from __future__ import annotations

from athena_ase.broker_availability import (
    filter_broker_tradable,
    instrument_blocked_by_broker,
)
from athena_ase.runtime import scan as scan_module
from athena_ase.universe import UNIVERSE, instrument_by_symbol


_ATFX_CFG = {
    "MT5_DISABLED_PAIRS": ["EUR/USD", "XAU/USD"],
    "MT5_ENABLED_PAIRS": ["GLD"],
    "MT5_DISABLE_COMMODITIES": False,
}


def test_inactive_old_broker_names_are_off_the_catalog():
    for symbol in ("USDINR", "USDBRL", "USDHKD", "COPPER", "SPY", "NPN.JO"):
        assert instrument_by_symbol(symbol) is None


def test_filter_blocks_a_listed_name_when_local_broker_disables_it():
    eurusd = instrument_by_symbol("EURUSD")
    btc = instrument_by_symbol("BTCUSDT")
    assert eurusd is not None and btc is not None
    assert instrument_blocked_by_broker(eurusd, _ATFX_CFG) is True
    assert instrument_blocked_by_broker(btc, _ATFX_CFG) is False
    tradable = {inst.symbol for inst in filter_broker_tradable(UNIVERSE, _ATFX_CFG)}
    assert "EURUSD" not in tradable
    assert "GC=F" not in tradable
    assert "AUDCAD" in tradable
    assert "BTCUSDT" in tradable
    assert "GLD" in tradable


def test_commodity_kill_switch_still_honours_enabled_override():
    cfg = {
        "MT5_DISABLED_PAIRS": [],
        "MT5_ENABLED_PAIRS": ["XAU/USD"],
        "MT5_DISABLE_COMMODITIES": True,
    }
    xau = instrument_by_symbol("XAU/USD")
    wti = instrument_by_symbol("CL=F")
    assert xau is not None and wti is not None
    assert instrument_blocked_by_broker(xau, cfg) is False
    assert instrument_blocked_by_broker(wti, cfg) is True


def test_atfx_crosses_are_in_catalog():
    assert instrument_by_symbol("AUDCAD") is not None
    assert instrument_by_symbol("EURCAD") is not None
    assert instrument_by_symbol("USDCNH") is not None
    assert instrument_by_symbol("XAU/ZAR") is not None
    assert instrument_by_symbol("France 40") is not None


def test_scan_omits_disabled_symbol_even_when_requested(tmp_path, monkeypatch):
    monkeypatch.setattr("athena_ase.broker_availability._cfg", lambda: _ATFX_CFG)
    result = scan_module.run_ase_scan(
        symbols=["EURUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(tmp_path / "ptis"),
        ingest_sources=(),
    )
    assert result["instrumentCount"] == 0
    assert result["signalCount"] == 0
    assert result["brokerFilter"]["excluded"] == ["EURUSD"]


def test_scan_keeps_allowed_forex_when_a_peer_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr("athena_ase.broker_availability._cfg", lambda: _ATFX_CFG)
    result = scan_module.run_ase_scan(
        symbols=["EURUSD", "GBPUSD"],
        horizon="intraday",
        write_journal=False,
        ptis_root=str(tmp_path / "ptis"),
        ingest_sources=(),
    )
    scored = {row["instrument"] for row in result["signals"]}
    assert scored == {"GBPUSD"}
    assert result["brokerFilter"]["excluded"] == ["EURUSD"]
    assert result["instrumentCount"] == 1
