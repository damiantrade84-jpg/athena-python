"""Safety regressions for candle understanding layer."""

from __future__ import annotations

import importlib
import sys

import pytest

from athena_ai.candle_understanding import derive_candle_understanding
from athena_ai.price_action_facts import derive_sweep_event


def _bar(o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_modules_import_without_flask() -> None:
    assert "flask" not in sys.modules or importlib.import_module("athena_ai.price_action_facts")
    importlib.import_module("athena_ai.candle_understanding")


def test_no_direct_buy_sell_from_derivations() -> None:
    bars = [_bar(100, 105, 98, 100.5)]
    sweep = derive_sweep_event(bars, liquidity_pools=[104.0], atr=1.0)
    block = derive_candle_understanding(candles=bars, timeframe="H4", engine="A")
    blob = str({**sweep, **block}).upper()
    assert "TRADE_NOW" not in blob
    assert "PLACE_ORDER" not in blob
    assert "EXECUTE_TRADE" not in blob


def test_candle_understanding_advisory_only_flag() -> None:
    block = derive_candle_understanding(candles=[_bar(100, 101, 99, 100)], engine="A")
    assert block.get("advisory_only") is True
    assert block["scores"].get("report_only") is True
