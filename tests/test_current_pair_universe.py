from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"
CONFIG_PATH = ROOT / "config.yaml"


def _literal_assignment(name: str):
    tree = ast.parse(ATHENA_PATH.read_text(encoding="utf-8"), filename=str(ATHENA_PATH))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found")


def _by_display(list_name: str) -> dict[str, dict]:
    return {pair["display"]: pair for pair in _literal_assignment(list_name)}


def test_original_user_list_missing_members_are_in_tool_pair_universe():
    indices = _by_display("INDEX_PAIRS")
    stocks = _by_display("US_STOCK_PAIRS")
    etfs = _by_display("ETF_PAIRS")

    assert indices["US2000"] == {
        "symbol": "US2000",
        "type": "index",
        "display": "US2000",
        "source": "mt5",
        "enabled": True,
    }

    # Originally added as GOOGL (Class A). Renamed to GOOG (Class C) 2026-07-30:
    # ATFX only lists "#GOOG" ("Google-Alphabet Inc Class C(CFD)USA") — there is
    # no #GOOGL on this broker, so the pair now matches what's actually tradeable.
    assert stocks["GOOG"] == {
        "symbol": "GOOG.US",
        "type": "stock",
        "display": "GOOG",
        "source": "mt5",
        "enabled": True,
    }
    assert "GOOGL" not in stocks

    assert stocks["AVGO"] == {
        "symbol": "AVGO.US",
        "type": "stock",
        "display": "AVGO",
        "source": "mt5",
        "enabled": True,
    }

    assert etfs["TQQQ"] == {
        "symbol": "TQQQ.US",
        "type": "etf",
        "display": "TQQQ",
        "source": "mt5",
        "enabled": False,
    }
    assert etfs["SQQQ"] == {
        "symbol": "SQQQ.US",
        "type": "etf",
        "display": "SQQQ",
        "source": "mt5",
        "enabled": False,
    }


def test_new_us_symbols_have_supporting_tool_config_mappings():
    config_text = CONFIG_PATH.read_text(encoding="utf-8")

    for symbol in ("GOOG", "AVGO", "TQQQ", "SQQQ"):
        assert f'"{symbol}": "US"' in config_text

    # GOOGL (Class A) is not tradeable on ATFX; see the GOOG rename note above.
    assert '"GOOGL": "US"' not in config_text
    assert "TQQQ: etf" in config_text
    assert "SQQQ: etf" in config_text


def test_mt5_symbol_map_knows_requested_tool_additions(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(mt5_executor.CONFIG, "MT5_SYMBOL_OVERRIDES", {})
    assert mt5_executor.mt5_map_symbol("US2000") == "US2000"
    assert mt5_executor.mt5_map_symbol("GOOGL") == "GOOGL.US"
    assert mt5_executor.mt5_map_symbol("AVGO") == "AVGO.US"
    assert mt5_executor.mt5_map_symbol("TQQQ") == "TQQQ.US"
    assert mt5_executor.mt5_map_symbol("SQQQ") == "SQQQ.US"
    assert "GOOG" not in mt5_executor._MT5_SYMBOL_MAP
