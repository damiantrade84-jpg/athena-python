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
    "DIA": {"symbol": "DIA.US", "type": "stock", "mt5": "DIA.US"},
    "GDX": {"symbol": "GDX.US", "type": "stock", "mt5": "GDX.US"},
    "SOXX": {"symbol": "SOXX.US", "type": "stock", "mt5": "SOXX.US"},
}


def test_new_pepperstone_symbols_are_in_mt5_universe():
    by_display = {pair["display"]: pair for pair in ALL_PAIRS}

    for display, expected in _EXPECTED_NEW_SYMBOLS.items():
        pair = by_display.get(display)
        assert pair is not None, f"{display} missing from ALL_PAIRS"
        assert pair["symbol"] == expected["symbol"]
        assert pair["type"] == expected["type"]
        assert pair["source"] == "mt5"
        assert pair["enabled"] is True


@pytest.mark.parametrize(
    ("display", "mt5_symbol"),
    [(display, values["mt5"]) for display, values in _EXPECTED_NEW_SYMBOLS.items()],
)
def test_mt5_map_symbol_knows_new_pepperstone_symbols(display: str, mt5_symbol: str):
    assert mt5_map_symbol(display) == mt5_symbol


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

    html = _STATIC_INDEX_PATH.read_text(encoding="utf-8")
    assert '"EURX":"PEPPERSTONE:EURX"' in html
    assert '"JPYX":"PEPPERSTONE:JPYX"' in html
    assert '"USDX":"PEPPERSTONE:USDX"' in html
