"""ASE equity book matches the ATFX 50 US names plus curated non-US shares."""

from __future__ import annotations

from pathlib import Path

import yaml

from athena_ase.universe import UNIVERSE, instrument_by_symbol


_ROOT = Path(__file__).resolve().parents[1]
_US_50 = (
    "NVDA", "TSLA", "PLTR", "AMD", "COIN", "AAPL", "MSFT", "AMZN", "META", "GOOG",
    "AVGO", "NFLX", "TSM", "ORCL", "QCOM", "INTC", "CRM", "ADBE", "SNOW", "RBLX",
    "UBER", "SHOP", "DASH", "BA", "GME", "AMC", "AI", "MRNA", "TWLO", "DOCU",
    "ZM", "PYPL", "JPM", "GS", "BAC", "WFC", "V", "XOM", "CVX", "GE", "CAT",
    "NRG", "BABA", "NKE", "GM", "C", "LLY", "IBM", "DIS", "UAL",
)
_NON_US = (
    "SAP", "SIE", "IFX", "DB1", "BMW",
    "AIR", "LVMH", "TTEF", "AXAF", "BNPP",
    "HK0700", "HK9988", "HK1810", "HK0005", "HK0388",
)


def test_config_ws_us_book_is_exactly_50():
    cfg = yaml.safe_load((_ROOT / "config.yaml").read_text(encoding="utf-8"))
    ws = [str(s).strip().upper() for s in cfg["EODHD_WS_US_SYMBOLS"]]
    assert ws == list(_US_50)
    assert len(ws) == 50
    assert len(set(ws)) == 50


def test_ase_lists_all_50_us_stocks_and_curated_non_us():
    us_missing = [sym for sym in _US_50 if instrument_by_symbol(sym) is None]
    intl_missing = [sym for sym in _NON_US if instrument_by_symbol(sym) is None]
    assert us_missing == []
    assert intl_missing == []
    goog = instrument_by_symbol("GOOG")
    assert goog is not None
    assert goog.symbol == "GOOG"
    assert goog.display == "GOOG"
    assert instrument_by_symbol("GOOGL") is goog


def test_ase_us_equity_symbols_are_unique():
    us = [inst.symbol for inst in UNIVERSE if inst.family == "equity" and inst.subclass == "us"]
    assert len(us) == len(set(us))
    assert set(_US_50).issubset(set(us))
    assert set(_NON_US).issubset(set(us))
    assert set(us) == set(_US_50) | set(_NON_US)
    assert instrument_by_symbol("NPN.JO") is None
    assert instrument_by_symbol("USDINR") is None
