"""ASE live labels use Athena display names, not Yahoo futures tickers."""

from __future__ import annotations

from athena_ase.contracts import error_signal, resolve_instrument_display
from athena_ase.universe import instrument_by_symbol


def test_commodity_catalog_keeps_yahoo_ids_and_athena_displays():
    gold = instrument_by_symbol("GC=F")
    brent = instrument_by_symbol("BZ=F")
    silver = instrument_by_symbol("SI=F")
    wti = instrument_by_symbol("CL=F")
    assert gold is not None and gold.symbol == "GC=F" and gold.display == "XAU/USD"
    assert brent is not None and brent.symbol == "BZ=F" and brent.display == "Brent Oil"
    assert silver is not None and silver.symbol == "SI=F" and silver.display == "XAG/USD"
    assert wti is not None and wti.symbol == "CL=F" and wti.display == "WTI Oil"
    assert instrument_by_symbol("XAU/USD") is gold
    assert instrument_by_symbol("Brent Oil") is brent


def test_yahoo_ids_resolve_to_athena_display_names():
    assert resolve_instrument_display("GC=F") == "XAU/USD"
    assert resolve_instrument_display("BZ=F") == "Brent Oil"
    assert resolve_instrument_display("SI=F") == "XAG/USD"
    assert resolve_instrument_display("CL=F") == "WTI Oil"
    assert resolve_instrument_display("PL=F") == "XPT/USD"
    assert resolve_instrument_display("PA=F") == "XPD/USD"
    assert resolve_instrument_display("^GSPC") == "S&P 500"
    assert resolve_instrument_display("EURUSD") == "EUR/USD"


def test_signal_payload_exposes_display_for_yahoo_commodity_ids():
    sig = error_signal(
        instrument="GC=F",
        family="commodity",
        horizon="intraday",
        reason="test",
    )
    payload = sig.to_dict()
    assert payload["instrument"] == "GC=F"
    assert payload["display"] == "XAU/USD"
    assert sig.display == "XAU/USD"


def test_executed_trade_row_backfills_display_for_legacy_yahoo_ids():
    from athena_app.api.routes_ase import _executed_trade_row

    row = _executed_trade_row({"instrument": "BZ=F", "direction": "SHORT"})
    assert row["instrument"] == "BZ=F"
    assert row["display"] == "Brent Oil"
