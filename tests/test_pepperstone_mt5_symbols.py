from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from mt5_executor import mt5_map_symbol


_ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"
_ATHENA_SPEC = importlib.util.spec_from_file_location("athena_main", _ATHENA_PATH)
assert _ATHENA_SPEC and _ATHENA_SPEC.loader
_ATHENA_MOD = importlib.util.module_from_spec(_ATHENA_SPEC)
_ATHENA_SPEC.loader.exec_module(_ATHENA_MOD)

ALL_PAIRS = _ATHENA_MOD.ALL_PAIRS
_TELEGRAM_BOT_PATH = Path(__file__).resolve().parents[1] / "telegram_bot.py"
_STATIC_INDEX_PATH = Path(__file__).resolve().parents[1] / "static" / "index.html"
_PAIR_BROWSER_PANEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "static"
    / "react-app"
    / "app"
    / "src"
    / "components"
    / "panels"
    / "PairBrowserPanel.tsx"
)


_EXPECTED_NEW_SYMBOLS = {
    "AUD/CHF": {"symbol": "AUDCHF=X", "type": "forex", "mt5": "AUDCHF"},
    "AUD/NZD": {"symbol": "AUDNZD=X", "type": "forex", "mt5": "AUDNZD"},
    "USD/BRL": {"symbol": "USDBRL=X", "type": "forex", "mt5": "USDBRL"},
    "USD/INR": {"symbol": "USDINR=X", "type": "forex", "mt5": "USDINR"},
    "Aluminium": {"symbol": "Aluminium", "type": "commodity", "mt5": "Aluminium"},
    "Lead": {"symbol": "Lead", "type": "commodity", "mt5": "Lead"},
    "Nickel": {"symbol": "Nickel", "type": "commodity", "mt5": "Nickel"},
    "Zinc": {"symbol": "Zinc", "type": "commodity", "mt5": "Zinc"},
    "Gasoline": {"symbol": "Gasoline", "type": "commodity", "mt5": "Gasoline"},
    "Cattle": {"symbol": "Cattle", "type": "commodity", "mt5": "Cattle"},
    "Cocoa": {"symbol": "Cocoa", "type": "commodity", "mt5": "Cocoa"},
    "Coffee": {"symbol": "Coffee", "type": "commodity", "mt5": "Coffee"},
    "Corn": {"symbol": "Corn", "type": "commodity", "mt5": "Corn"},
    "Cotton": {"symbol": "Cotton", "type": "commodity", "mt5": "Cotton"},
    "Soybeans": {"symbol": "Soybeans", "type": "commodity", "mt5": "Soybeans"},
    "Sugar": {"symbol": "Sugar", "type": "commodity", "mt5": "Sugar"},
    "Wheat": {"symbol": "Wheat", "type": "commodity", "mt5": "Wheat"},
    "EURX": {"symbol": "EURX", "type": "index", "mt5": "EURX"},
    "JPYX": {"symbol": "JPYX", "type": "index", "mt5": "JPYX"},
    "USDX": {"symbol": "USDX", "type": "index", "mt5": "USDX"},
    "DIA": {"symbol": "DIA.US", "type": "etf", "mt5": "DIA.US"},
    "GDX": {"symbol": "GDX.US", "type": "etf", "mt5": "GDX.US"},
    "SOXX": {"symbol": "SOXX.US", "type": "etf", "mt5": "SOXX.US"},
}


def test_new_pepperstone_symbols_are_retained_in_mt5_universe():
    by_display = {pair["display"]: pair for pair in ALL_PAIRS}

    for display, expected in _EXPECTED_NEW_SYMBOLS.items():
        pair = by_display.get(display)
        assert pair is not None, f"{display} missing from ALL_PAIRS"
        assert pair["symbol"] == expected["symbol"]
        assert pair["type"] == expected["type"]
        assert pair["source"] == "mt5"
        assert "enabled" in pair


@pytest.mark.parametrize(
    ("display", "mt5_symbol"),
    [(display, values["mt5"]) for display, values in _EXPECTED_NEW_SYMBOLS.items()],
)
def test_mt5_map_symbol_knows_new_pepperstone_symbols(
    display: str, mt5_symbol: str, monkeypatch
):
    import mt5_executor

    monkeypatch.setitem(mt5_executor.CONFIG, "MT5_SYMBOL_OVERRIDES", {})
    assert mt5_map_symbol(display) == mt5_symbol


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("AAPL", "AAPL"),
        ("GLD", "GLD"),
        ("SLV", "SLV"),
    ],
)
def test_yfinance_symbol_for_us_stocks_and_etfs_strips_us_suffix(display: str, expected: str):
    pair = next(p for p in ALL_PAIRS if p["display"] == display)
    assert _ATHENA_MOD._yfinance_symbol_for_pair(pair) == expected


def test_yfinance_symbol_for_cocoa_uses_yahoo_futures_ticker():
    pair = next(p for p in ALL_PAIRS if p["display"] == "Cocoa")
    assert _ATHENA_MOD._yfinance_symbol_for_pair(pair) == "CC=F"


def test_fetch_mt5_fails_closed_without_yahoo_when_symbol_missing(monkeypatch):
    import mt5_executor

    pair = next(p for p in ALL_PAIRS if p["display"] == "ASX 200")
    called = {"fallback": False}

    class _FakeMT5:
        TIMEFRAME_M1 = 1
        TIMEFRAME_M5 = 5
        TIMEFRAME_M15 = 15
        TIMEFRAME_M30 = 30
        TIMEFRAME_H1 = 60
        TIMEFRAME_H4 = 240
        TIMEFRAME_D1 = 1440

        def symbol_select(self, _symbol, _enable):
            return False

    def _forbidden_fallback(*_args, **_kwargs):
        called["fallback"] = True
        return (
            [
                {
                    "time": "2026-04-09T08:00:00+00:00",
                    "open": 8800.0,
                    "high": 8810.0,
                    "low": 8790.0,
                    "close": 8805.0,
                    "vol": 1000.0,
                }
            ],
            "yfinance",
        )

    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(_ATHENA_MOD, "_fetch_fallback_candles", _forbidden_fallback)

    out = _ATHENA_MOD.fetch_mt5(pair, "H4", 10)

    assert out["error"] is True
    assert out["detail"] == "symbol not found in MT5"
    assert "candles" not in out
    assert called["fallback"] is False


def _load_literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found in {path}")


def test_currency_indices_are_present_on_operator_surfaces():
    pair_aliases = _load_literal_assignment(_TELEGRAM_BOT_PATH, "_PAIR_ALIASES")

    assert pair_aliases["eurx"] == "EURX"
    assert pair_aliases["usdx"] == "USDX"
    assert pair_aliases["jpyx"] == "JPYX"
    assert pair_aliases["jpx"] == "JPYX"

    pair_browser_source = _PAIR_BROWSER_PANEL_PATH.read_text(encoding="utf-8")
    assert "EURX: 'PEPPERSTONE:EURX'" in pair_browser_source
    assert "JPYX: 'PEPPERSTONE:JPYX'" in pair_browser_source
    assert "USDX: 'PEPPERSTONE:USDX'" in pair_browser_source
    assert "tradingViewSymbolForLabel(selectedLabel)" in pair_browser_source
