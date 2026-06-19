"""Unit tests for the Engine A direction/entry/exit forensic helpers (research).

Covers the pure, deterministic pieces of the forensic ledger: setup tagging,
price-only weight masking, the individual indicator-vote decoders, and the
runner's Pearson. The heavy statistics (cluster bootstrap, Spearman) are already
covered in test_engine_a_ablation_paired.py.

Run: pytest tests/test_engine_a_direction_audit.py -q
"""

from __future__ import annotations

import os

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from athena_research.engine_a_ablation.direction_audit import (  # noqa: E402
    _di_sig, _macd_sig, _price_only_weights, _rsi_sig, _setup_id,
)
from athena_research.engine_a_ablation.run_direction_audit import _pearson  # noqa: E402
from athena_research.engine_a_ablation.shadow_scorer import PRICE_FACTORS  # noqa: E402


def test_price_only_weights_zero_subsystems():
    w = _price_only_weights("forex")
    for f in PRICE_FACTORS:
        assert w[f] > 0
    for s in ("carry", "sentiment", "intermarket", "macro", "microstructure"):
        assert w.get(s, 0.0) == 0.0


def test_setup_id_is_deterministic_and_partitioned():
    # mean_reversion style always range_mean_reversion regardless of regime
    assert _setup_id("mean_reversion", "trending", 0.5, "LONG") == "range_mean_reversion"
    # trending + location favouring the trade -> pullback; against -> continuation
    assert _setup_id("trend", "trending", 0.4, "LONG") == "trend_pullback"
    assert _setup_id("trend", "trending", -0.4, "LONG") == "momentum_continuation"
    # for a SHORT, a negative location signal favours the trade (aligned>0)
    assert _setup_id("trend", "trending", -0.4, "SHORT") == "trend_pullback"
    assert _setup_id("trend", "ranging", -0.4, "LONG") == "range_fade"
    assert _setup_id("trend", "ranging", 0.4, "LONG") == "range_breakout"


def test_rsi_sig_orientation_and_saturation():
    assert _rsi_sig({"rsi": 50}) == 0.0
    assert _rsi_sig({"rsi": 80}) == 1.0          # saturates at +1
    assert _rsi_sig({"rsi": 20}) == -1.0
    assert _rsi_sig({}) is None


def test_macd_sig_sign_and_strengthening():
    # positive hist, rising -> full strength +1
    assert _macd_sig({"macdHist": 0.5, "macdHistPrev": 0.2}) == 1.0
    # positive hist, weakening -> damped (0.6)
    assert _macd_sig({"macdHist": 0.2, "macdHistPrev": 0.5}) == 0.6
    # negative hist, no prev -> 0.8 damp
    assert _macd_sig({"macdHist": -1.0}) == -0.8
    assert _macd_sig({}) is None


def test_di_sig_directional():
    assert _di_sig({"plusDI": 30, "minusDI": 10}) == 0.5
    assert _di_sig({"plusDI": 10, "minusDI": 30}) == -0.5
    assert _di_sig({"plusDI": 0, "minusDI": 0}) is None


def test_pearson_basic():
    assert _pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0
    assert _pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0
    # None pairs are dropped; <3 valid -> None
    assert _pearson([1, None, 3], [None, 2, 3]) is None
