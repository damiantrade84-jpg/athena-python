from engine_b_imbalance import directional_fvg_context, enrich_fvg_lifecycle
from engine_b_quality import compute_confluence_subscores


def _bar(open_, high, low, close):
    return {"open": open_, "high": high, "low": low, "close": close}


def _bullish_bag_candles():
    return [
        _bar(100.0, 101.0, 99.0, 100.2),
        _bar(100.1, 101.0, 99.2, 100.3),
        _bar(100.2, 101.0, 99.4, 100.4),
        _bar(100.3, 101.0, 99.5, 100.5),
        _bar(100.4, 104.5, 100.2, 104.0),
        _bar(103.0, 105.0, 102.0, 104.5),
        _bar(104.0, 106.0, 103.0, 105.5),
        _bar(105.0, 107.0, 104.0, 106.5),
    ]


def test_bag_confirms_only_after_breakout_displacement_and_followthrough():
    candles = _bullish_bag_candles()
    [fvg] = enrich_fvg_lifecycle(
        [{"type": "bullish", "bottom": 101.0, "top": 102.0, "bar_index": 4}],
        candles,
        atr=2.0,
        timeframe="H4",
    )
    assert fvg["timeframe"] == "H4"
    assert fvg["structure_break"] is True
    assert fvg["fill_state"] == "unfilled"
    assert fvg["bag_state"] == "confirmed"


def test_bag_invalidates_at_consequent_encroachment():
    candles = _bullish_bag_candles()
    candles[6] = _bar(103.0, 105.0, 101.4, 104.0)
    [fvg] = enrich_fvg_lifecycle(
        [{"type": "bullish", "bottom": 101.0, "top": 102.0, "bar_index": 4}],
        candles,
        atr=2.0,
        timeframe="H4",
    )
    assert fvg["fill_fraction"] >= 0.5
    assert fvg["mitigated"] is True
    assert fvg["bag_state"] == "invalidated"


def test_directional_context_excludes_opposing_fvg_and_reads_trigger_reaction():
    bullish = {
        "type": "bullish", "bottom": 100.0, "top": 102.0, "ce": 101.0,
        "bag_state": "confirmed", "gap_size_atr": 1.0,
    }
    bearish = {
        "type": "bearish", "bottom": 100.0, "top": 102.0, "ce": 101.0,
        "bag_state": "confirmed", "gap_size_atr": 1.0,
    }
    context = directional_fvg_context(
        [bullish, bearish],
        direction="LONG",
        active_zone={"lower": 99.5, "upper": 101.5},
        trigger_candles=[_bar(100.5, 102.5, 100.0, 102.0)],
        atr=2.0,
        timeframe="H4",
    )
    assert context["aligned_active_count"] == 1
    assert context["opposing_active_count"] == 1
    assert context["overlap"] is True
    assert context["reaction_confirmed"] is True
    assert context["bag"] is bullish


def test_quality_separates_fvg_reaction_from_bag_continuation():
    bag = {
        "bag_state": "confirmed",
        "continuation_atr": 1.5,
        "gap_size_atr": 0.5,
        "displacement_body_atr": 1.2,
        "fill_fraction": 0.1,
    }
    result = compute_confluence_subscores(
        {
            "fvg_overlap": True,
            "fvg_context": {
                "direction": "LONG",
                "reaction_confirmed": True,
                "nearest": bag,
                "bag_state": "confirmed",
                "bag": bag,
            },
            "bos_volume_source_applicable": False,
        },
        "LONG",
        2.0,
        asset_type="forex",
    )
    assert result["fvg_confluence"] > 0.5
    assert result["bag_continuation"] > 0.75


def test_opposing_bag_cannot_earn_quality():
    result = compute_confluence_subscores(
        {
            "fvg_overlap": False,
            "fvg_context": {
                "direction": "SHORT",
                "bag_state": "confirmed",
                "bag": {"continuation_atr": 2.0},
            },
            "bos_volume_source_applicable": False,
        },
        "LONG",
        2.0,
        asset_type="forex",
    )
    assert result["fvg_confluence"] == 0.0
    assert result["bag_continuation"] == 0.0
