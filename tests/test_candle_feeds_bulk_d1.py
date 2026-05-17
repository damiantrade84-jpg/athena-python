from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import candle_feeds


def test_bulk_d1_skip_exchanges_defaults_to_comm(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "CONFIG",
        {**config.CONFIG, "CANDLE_BUILDER_BULK_D1_SKIP_EXCHANGES": ["COMM"]},
    )
    assert candle_feeds.bulk_d1_skip_exchanges() == frozenset({"COMM"})


def test_bulk_d1_skip_exchanges_uppercases_and_deduplicates(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "CONFIG",
        {**config.CONFIG, "CANDLE_BUILDER_BULK_D1_SKIP_EXCHANGES": ["comm", "COMM", " Comm "]},
    )
    assert candle_feeds.bulk_d1_skip_exchanges() == frozenset({"COMM"})
