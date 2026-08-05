"""Parity proof for the enrich_fvg_lifecycle suffix-extreme rewrite.

The O(FVGs x window) per-FVG min/max scans (and per-FVG tail slice copies)
were replaced with one O(n) suffix-min-low / suffix-max-high pass. min/max
over the identical set is bit-exact; the old `_number(..., top/bottom)`
defaults reduce to the same clamped 0.0 as the new +/-inf sentinels (case
analysis in the implementation comment). This file keeps the pre-change
implementation verbatim as the reference and asserts identical outputs.

Documented boundary: NaN highs/lows (absent from the parquet store, which
writes clean float64) map to the sentinels in the new version while the old
version propagated NaN through Python's order-dependent min/max. Every other
input class — unparseable values, empty post regions, out-of-range indices —
is covered below and must match exactly.
"""

from __future__ import annotations

import random

from engine_b_imbalance import _number, enrich_fvg_lifecycle


# ── pre-change implementation, verbatim (reference) ──────────────────────────
def _reference_enrich_fvg_lifecycle(
    fvgs: list[dict],
    candles: list[dict],
    *,
    atr: float = 0.0,
    timeframe: str | None = None,
    config: dict | None = None,
) -> list[dict]:
    cfg = config if isinstance(config, dict) else {}
    atr_value = max(0.0, _number(atr))
    min_body_atr = max(0.0, _number(cfg.get("BAG_MIN_DISPLACEMENT_BODY_ATR"), 0.8))
    min_body_ratio = max(0.0, min(1.0, _number(cfg.get("BAG_MIN_BODY_RANGE_RATIO"), 0.65)))
    min_gap_atr = max(0.0, _number(cfg.get("FVG_MIN_SIZE_ATR"), 0.05))
    breakout_lookback = max(3, int(_number(cfg.get("BAG_BREAKOUT_LOOKBACK_BARS"), 8)))
    followthrough_bars = max(1, int(_number(cfg.get("BAG_MIN_FOLLOWTHROUGH_BARS"), 2)))
    continuation_atr = max(0.0, _number(cfg.get("BAG_MIN_CONTINUATION_ATR"), 0.5))
    max_fill = max(0.0, min(1.0, _number(cfg.get("BAG_MAX_FILL_FRACTION"), 0.25)))

    enriched: list[dict] = []
    for source in fvgs:
        item = dict(source)
        direction = str(item.get("type") or "").lower()
        top = _number(item.get("top"))
        bottom = _number(item.get("bottom"))
        size = max(0.0, top - bottom)
        middle_index = int(_number(item.get("bar_index"), -1))
        third_index = middle_index + 1
        post = candles[third_index + 1 :] if 0 <= third_index < len(candles) else []

        fill_fraction = 0.0
        if size > 0 and post:
            if direction == "bullish":
                deepest = min(_number(c.get("low"), top) for c in post)
                fill_fraction = (top - deepest) / size
            elif direction == "bearish":
                deepest = max(_number(c.get("high"), bottom) for c in post)
                fill_fraction = (deepest - bottom) / size
        fill_fraction = max(0.0, min(1.0, fill_fraction))
        ce = bottom + size * 0.5
        if fill_fraction <= 0:
            fill_state = "unfilled"
        elif fill_fraction < 0.5:
            fill_state = "partial"
        elif fill_fraction < 1.0:
            fill_state = "ce_touched"
        else:
            fill_state = "full"

        displacement_body_atr = 0.0
        displacement_body_ratio = 0.0
        structure_break = False
        if 0 <= middle_index < len(candles):
            middle = candles[middle_index]
            open_ = _number(middle.get("open"))
            close = _number(middle.get("close"))
            high = _number(middle.get("high"))
            low = _number(middle.get("low"))
            body = abs(close - open_)
            range_ = max(high - low, 0.0)
            displacement_body_atr = body / atr_value if atr_value > 0 else 0.0
            displacement_body_ratio = body / range_ if range_ > 0 else 0.0
            prior = candles[max(0, middle_index - breakout_lookback) : middle_index]
            if len(prior) >= 3:
                if direction == "bullish":
                    structure_break = close > max(_number(c.get("high")) for c in prior)
                elif direction == "bearish":
                    structure_break = close < min(_number(c.get("low")) for c in prior)

        gap_size_atr = size / atr_value if atr_value > 0 else 0.0
        displacement_ok = (
            atr_value > 0
            and displacement_body_atr >= min_body_atr
            and displacement_body_ratio >= min_body_ratio
            and gap_size_atr >= min_gap_atr
        )
        latest_close = _number(candles[-1].get("close")) if candles else 0.0
        continuation = (
            (latest_close - top) / atr_value
            if direction == "bullish" and atr_value > 0
            else (bottom - latest_close) / atr_value
            if direction == "bearish" and atr_value > 0
            else 0.0
        )
        followthrough = len(post) >= followthrough_bars and continuation >= continuation_atr

        if not displacement_ok:
            bag_state = "not_bag"
            bag_reason = "insufficient_displacement"
        elif not structure_break:
            bag_state = "not_bag"
            bag_reason = "no_structure_break"
        elif fill_fraction >= 0.5:
            bag_state = "invalidated"
            bag_reason = "consequent_encroachment_reached"
        elif followthrough and fill_fraction <= max_fill:
            bag_state = "confirmed"
            bag_reason = "structure_break_displacement_followthrough"
        else:
            bag_state = "candidate"
            bag_reason = "awaiting_followthrough"

        item.update(
            {
                "timeframe": str(timeframe or item.get("timeframe") or "").upper() or None,
                "ce": round(ce, 8),
                "fill_fraction": round(fill_fraction, 4),
                "fill_pct": round(fill_fraction * 100.0, 2),
                "fill_state": fill_state,
                "mitigated": fill_fraction >= 0.5,
                "fully_mitigated": fill_fraction >= 1.0,
                "gap_size_atr": round(gap_size_atr, 4),
                "displacement_body_atr": round(displacement_body_atr, 4),
                "displacement_body_ratio": round(displacement_body_ratio, 4),
                "structure_break": structure_break,
                "continuation_atr": round(continuation, 4),
                "bag_state": bag_state,
                "bag_reason": bag_reason,
            }
        )
        enriched.append(item)
    return enriched


# ── fixtures ─────────────────────────────────────────────────────────────────
def _walk_candles(seed: int, n: int, start: float = 100.0) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    price = start
    for i in range(n):
        drift = rng.uniform(-1.2, 1.2)
        o = price
        c = price + drift
        h = max(o, c) + rng.uniform(0.0, 0.6)
        lo = min(o, c) - rng.uniform(0.0, 0.6)
        rows.append({"open": o, "high": h, "low": lo, "close": c, "vol": rng.uniform(50, 500)})
        price = c
    return rows


def _fvg(direction: str, bar_index: int, bottom: float, top: float) -> dict:
    return {"type": direction, "bar_index": bar_index, "bottom": bottom, "top": top}


def _assert_same(fvgs, candles, **kwargs):
    expected = _reference_enrich_fvg_lifecycle(fvgs, candles, **kwargs)
    actual = enrich_fvg_lifecycle(fvgs, candles, **kwargs)
    assert actual == expected


# ── tests ────────────────────────────────────────────────────────────────────
def test_suffix_extremes_parity_random_walks():
    for seed in range(15):
        candles = _walk_candles(seed, 400)
        rng = random.Random(1000 + seed)
        fvgs = []
        for _ in range(12):
            idx = rng.randrange(2, 398)
            base = candles[idx]["close"]
            gap = rng.uniform(0.1, 2.0)
            if rng.random() < 0.5:
                fvgs.append(_fvg("bullish", idx, base, base + gap))
            else:
                fvgs.append(_fvg("bearish", idx, base - gap, base))
        _assert_same(fvgs, candles, atr=0.8, timeframe="h1")
        _assert_same(fvgs, candles, atr=0.0)


def test_suffix_extremes_parity_edge_cases():
    candles = _walk_candles(42, 120)
    n = len(candles)
    fvgs = [
        _fvg("bullish", n - 2, 100.0, 101.0),   # third at last bar: empty post
        _fvg("bearish", n - 3, 100.0, 101.0),   # one-bar post
        _fvg("bullish", n, 100.0, 101.0),       # out of range
        _fvg("bullish", -1, 100.0, 101.0),      # negative index
        _fvg("bearish", 5, 100.0, 100.0),       # zero-size gap
        _fvg("bullish", 10, 1.0, 0.5),          # inverted gap (size clamped 0)
        _fvg("sideways", 10, 100.0, 101.0),     # unknown direction
        {"bar_index": 20},                      # missing type/top/bottom
        _fvg("bullish", 10, 300.0, 400.0),      # gap far above all post lows
        _fvg("bearish", 10, 0.5, 1.5),          # gap far below all post highs
    ]
    _assert_same(fvgs, candles, atr=0.8, timeframe="m15", config={"BAG_MIN_FOLLOWTHROUGH_BARS": 3})


def test_suffix_extremes_parity_unparseable_extremes():
    candles = _walk_candles(7, 80)
    # Unparseable values in the post region of the gap at bar_index 4.
    candles[10]["low"] = None
    candles[20]["high"] = "bad"
    candles[30]["low"] = {}
    fvgs = [
        _fvg("bullish", 4, candles[4]["close"], candles[4]["close"] + 1.0),
        _fvg("bearish", 4, candles[4]["close"] - 1.0, candles[4]["close"]),
    ]
    _assert_same(fvgs, candles, atr=0.8)


def test_suffix_extremes_parity_empty_inputs():
    assert enrich_fvg_lifecycle([], [], atr=1.0) == _reference_enrich_fvg_lifecycle([], [], atr=1.0)
    fvgs = [_fvg("bullish", 0, 1.0, 2.0)]
    _assert_same(fvgs, [], atr=1.0)
