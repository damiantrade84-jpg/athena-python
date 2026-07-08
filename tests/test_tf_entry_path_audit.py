"""Tests for TF/entry path audit trade diagnostics."""

from athena_research.tf_entry_path_audit.trade_path import (
    EntryQualityClass,
    IntrabarExitClass,
    classify_entry_quality,
    classify_intrabar_exit,
    compute_trade_path,
    enrich_trade_record,
)


def _bars(seq):
    return [{"open": o, "high": h, "low": l, "close": c} for o, h, l, c in seq]


def test_intrabar_both_touched_conservative():
    window = _bars([(100, 105, 95, 102)])
    cls, amb = classify_intrabar_exit(
        window,
        direction="LONG",
        entry=100.0,
        sl=95.0,
        tp=105.0,
        outcome="SL",
    )
    assert cls == IntrabarExitClass.BOTH_TOUCHED_SAME_CANDLE
    assert amb is True


def test_clean_entry_classification():
    window = _bars([
        (100, 101, 99.5, 100.5),
        (100.5, 102, 100, 101.5),
        (101.5, 103, 101, 102),
    ])
    path = compute_trade_path(window, direction="LONG", entry=100.0, sl=98.0, tp=106.0)
    cls = classify_entry_quality(path, final_r=1.0, outcome="TP1", intrabar_class=IntrabarExitClass.TP_ONLY)
    assert cls in (EntryQualityClass.CLEAN_ENTRY, EntryQualityClass.UNCLASSIFIED)


def test_early_adverse_classification():
    window = _bars([
        (100, 100.2, 98.5, 99),
        (99, 99.5, 97.5, 98),
    ])
    path = compute_trade_path(window, direction="LONG", entry=100.0, sl=97.0, tp=106.0)
    cls = classify_entry_quality(path, final_r=-1.0, outcome="SL", intrabar_class=IntrabarExitClass.SL_ONLY)
    assert cls == EntryQualityClass.EARLY_ADVERSE


def test_enrich_trade_record_fields():
    trade = {
        "direction": "LONG",
        "entry": 100.0,
        "sl": 98.0,
        "tp": 104.0,
        "outcome": "SL",
        "resultR": -1.0,
        "max_favorable_excursion_r": 0.1,
        "max_adverse_excursion_r": -1.0,
    }
    rec = enrich_trade_record(
        trade,
        engine="ENGINE_B",
        symbol="EUR/USD",
        signal_tf="H4",
        entry_tf="H1",
        exit_tf="H1",
        execution_tf="H1",
        indicator_profile="bar_native",
    )
    assert rec["engine"] == "ENGINE_B"
    assert rec["signal_timeframe"] == "H4"
    assert rec["entry_quality_class"] in {c.value for c in EntryQualityClass}
