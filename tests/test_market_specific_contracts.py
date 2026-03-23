"""Static and unit checks for market-specific TP/SL behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from indicators import calc_levels


ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"


@pytest.mark.parametrize(
    ("pair_type", "style"),
    [
        ("forex", "scalp"),
        ("forex", "intraday"),
        ("forex", "swing"),
        ("crypto", "scalp"),
        ("crypto", "intraday"),
        ("crypto", "swing"),
        ("stock", "swing"),
        ("commodity", "swing"),
        ("index", "swing"),
    ],
)
def test_calc_levels_keeps_tp1_faster_than_tp2_for_all_asset_groups(pair_type, style):
    price = 100.0
    atr = 2.0
    levels = calc_levels(price, atr, "LONG", pair_type, regime_state=1, style=style)

    assert levels["sl"] < price
    assert price < levels["tp1"] < levels["tp2"]
    assert levels["rr1"] < levels["rr2"]


def test_calc_levels_are_asset_class_specific():
    price = 100.0
    atr = 2.0

    forex = calc_levels(price, atr, "LONG", "forex", regime_state=1, style="swing")
    crypto = calc_levels(price, atr, "LONG", "crypto", regime_state=1, style="swing")
    commodity = calc_levels(
        price, atr, "LONG", "commodity", regime_state=1, style="intraday"
    )
    index_levels = calc_levels(
        price, atr, "LONG", "index", regime_state=1, style="intraday"
    )

    assert abs(price - forex["sl"]) != abs(price - crypto["sl"])
    assert abs(price - commodity["sl"]) != abs(price - index_levels["sl"])


def test_athena_source_disables_forex_factor_fallback():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert "skipping pair to avoid factor-engine fallback" in src
    assert "falling back to calc_confluence" not in src


def test_athena_source_keeps_engine_b_scalp_as_distinct_profile():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert 'resolved == "scalp"' not in src
    assert "Scalp promoted to intraday" not in src


def test_athena_source_uses_class_specific_engine_b_overlay_gate():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert 'CONFIG["MIN_CONFLUENCE_CLASS"].get(' in src
