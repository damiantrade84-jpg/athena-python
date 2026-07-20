"""Tests for TF/entry path audit trade diagnostics."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import athena_research.tf_entry_path_audit.diagnostic_v3 as diagnostic_v3
from engine_a_v3.contract import PriceZone

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


def _timed_bars(timeframe: str, count: int, *, price: float = 100.0) -> list[dict]:
    step = {"D1": timedelta(days=1), "H4": timedelta(hours=4), "H1": timedelta(hours=1)}[
        timeframe
    ]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "time": (start + index * step).isoformat(),
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "vol": 1_000.0,
        }
        for index in range(count)
    ]


def test_v3_diagnostic_entry_starts_after_h4_decision_bar_closes():
    entry_rows = _timed_bars("H1", 8)
    start_epoch = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())

    assert diagnostic_v3._find_entry_index(entry_rows, start_epoch + 4 * 60 * 60) == 5


def test_v3_diagnostic_skips_entry_bar_that_does_not_touch_zone(monkeypatch):
    signal = SimpleNamespace(
        decision="TRADE",
        qualified=True,
        entryZone=PriceZone(low=90.0, high=91.0),
        direction="LONG",
        sl=89.0,
        tp1=93.0,
        tp2=94.0,
        exitPolicy="SINGLE_TP1",
        decisionTime="2025-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        diagnostic_v3,
        "evaluate_engine_a_v3",
        lambda *_args, **_kwargs: signal,
    )
    candles = {
        "D1": _timed_bars("D1", 100),
        "H4": _timed_bars("H4", 100),
        "H1": _timed_bars("H1", 500),
    }

    result = diagnostic_v3.run_diagnostic_v3_backtest(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        signal_tf="H4",
        entry_tf="H1",
        exit_tf="H1",
        execution_tf="H1",
        horizon="intraday",
        candles=candles,
    )

    assert result["trades"] == []
