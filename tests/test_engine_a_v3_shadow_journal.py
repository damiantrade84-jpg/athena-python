from __future__ import annotations

import tempfile
from pathlib import Path

from engine_a_v3 import shadow_journal


def _candles():
    return {
        tf: [
            {"time": f"2026-01-0{d}T00:00:00", "open": 1.0, "high": 1.1,
             "low": 0.9, "close": 1.0, "vol": 1.0}
            for d in range(1, 4)
        ]
        for tf in ("D1", "H4", "H1")
    }


def _signal():
    return {
        "symbol": "EURUSD",
        "pair": "EUR/USD",
        "type": "forex",
        "scoreGroup": "forex_majors",
        "horizon": "swing",
        "lastConfirmedCandleTs": "2026-01-03T00:00:00",
        "decision": "TRADE",
        "qualified": True,
        "direction": "LONG",
        "confluenceScore": 2.4,
        "confluenceThreshold": 2.0,
        "rejectionReasons": (),
    }


def test_build_shadow_entry_none_without_confirmed_ts():
    assert shadow_journal.build_shadow_entry({}, _candles(), {}) is None


def test_build_shadow_entry_has_provenance_and_implementation_hash():
    entry = shadow_journal.build_shadow_entry({}, _candles(), _signal())
    assert entry is not None
    assert entry["symbol"] == "EURUSD"
    assert entry["decision"] == "TRADE"
    assert len(entry["datasetProvenanceSha256"]) == 64
    assert len(entry["implementationSha256"]) == 64
    assert entry["barCounts"] == {"D1": 3, "H4": 3, "H1": 3}


def test_record_shadow_entry_writes_jsonl_and_never_raises():
    root = Path(tempfile.mkdtemp())
    shadow_journal.record_shadow_entry({}, _candles(), _signal(), root=root)
    files = list(root.glob("*.jsonl"))
    assert len(files) == 1
    entries = shadow_journal.read_journal(files[0])
    assert len(entries) == 1
    assert entries[0]["symbol"] == "EURUSD"

    # Malformed signal must not raise — journaling is best-effort.
    shadow_journal.record_shadow_entry({}, {"bad": object()}, {"lastConfirmedCandleTs": "x"}, root=root)
    assert len(shadow_journal.read_journal(files[0])) == 1


def test_compare_entry_to_rescore_flags_drift_and_provenance_mismatch():
    entry = shadow_journal.build_shadow_entry({}, _candles(), _signal())
    assert entry is not None

    matching = {**_signal(), "_candles": _candles(), "implementationSha256": entry["implementationSha256"]}
    result = shadow_journal.compare_entry_to_rescore(entry, matching)
    assert result["provenanceMatch"] is True
    assert result["matched"] is True
    assert result["drift"] == {}

    drifted = {**matching, "direction": "SHORT"}
    result = shadow_journal.compare_entry_to_rescore(entry, drifted)
    assert result["matched"] is False
    assert "direction" in result["drift"]

    different_candles = _candles()
    different_candles["D1"][0]["close"] = 999.0
    mismatched = {**matching, "_candles": different_candles}
    result = shadow_journal.compare_entry_to_rescore(entry, mismatched)
    assert result["provenanceMatch"] is False
    assert result["matched"] is False
