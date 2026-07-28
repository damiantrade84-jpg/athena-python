from __future__ import annotations

import ast
from pathlib import Path

import mt5_executor


def test_mt5_symbol_overrides_precede_static_broker_map(monkeypatch):
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "MT5_SYMBOL_OVERRIDES",
        {
            "AUD/USD": "AUDUSD.s",
            "META": "#FB",
            "GOOGL": "#GOOG",
            "EEM": "#ETF-EEM",
        },
    )

    assert mt5_executor.mt5_map_symbol("AUD/USD") == "AUDUSD.s"
    assert mt5_executor.mt5_map_symbol("META") == "#FB"
    assert mt5_executor.mt5_map_symbol("GOOGL") == "#GOOG"
    assert mt5_executor.mt5_map_symbol("EEM") == "#ETF-EEM"
    assert mt5_executor.mt5_map_symbol("USD/INR") == "USDINR"


def test_effective_symbol_map_supports_position_reverse_mapping(monkeypatch):
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "MT5_SYMBOL_OVERRIDES",
        {"EUR/USD": "EURUSD.s", "META": "#FB"},
    )

    reverse = {
        broker_symbol: display
        for display, broker_symbol in mt5_executor._effective_mt5_symbol_map().items()
    }

    assert reverse["EURUSD.s"] == "EUR/USD"
    assert reverse["#FB"] == "META"


def test_atfx_share_catalog_maps_exact_broker_tickers(monkeypatch):
    monkeypatch.setitem(mt5_executor.CONFIG, "MT5_SYMBOL_OVERRIDES", {})
    monkeypatch.setitem(mt5_executor.CONFIG, "MT5_ATFX_SHARE_TICKERS", ["BRKb", "HK0001"])

    assert mt5_executor.mt5_map_symbol("BRKb") == "#BRKb"
    assert mt5_executor.mt5_map_symbol("HK0001") == "#HK0001"


def test_atfx_availability_disables_configured_pairs_and_commodities():
    athena_path = Path(__file__).resolve().parents[1] / "athena.py"
    tree = ast.parse(athena_path.read_text(encoding="utf-8"), filename=str(athena_path))
    function_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_apply_mt5_pair_availability"
    )
    isolated_module = ast.Module(body=[function_node], type_ignores=[])
    ast.fix_missing_locations(isolated_module)
    namespace: dict[str, object] = {}
    exec(compile(isolated_module, str(athena_path), "exec"), namespace)
    apply_availability = namespace["_apply_mt5_pair_availability"]

    pairs = [
        {"display": "EUR/USD", "type": "forex", "source": "mt5", "enabled": True},
        {"display": "USD/INR", "type": "forex", "source": "mt5", "enabled": True},
        {"display": "XAU/USD", "type": "commodity", "source": "mt5", "enabled": True},
        {
            "display": "GLD",
            "type": "etf",
            "source": "mt5",
            "enabled": False,
            "auto_enable": False,
        },
        {"display": "BTC/USDT", "type": "crypto", "source": "binance", "enabled": True},
    ]

    applied = apply_availability(
        pairs,
        {
            "MT5_DISABLE_COMMODITIES": True,
            "MT5_DISABLED_PAIRS": ["USD/INR"],
            "MT5_ENABLED_PAIRS": ["GLD"],
        },
    )
    by_display = {pair["display"]: pair for pair in pairs}

    assert applied == ["USD/INR", "XAU/USD"]
    assert by_display["EUR/USD"]["enabled"] is True
    assert by_display["USD/INR"]["enabled"] is False
    assert by_display["USD/INR"]["auto_enable"] is False
    assert by_display["XAU/USD"]["enabled"] is False
    assert by_display["GLD"]["enabled"] is True
    assert "auto_enable" not in by_display["GLD"]
    assert by_display["BTC/USDT"]["enabled"] is True
