"""Signal causality — future bars must not change past decisions."""

from __future__ import annotations

import numpy as np

from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.instruments import Instrument
from athena_ase.signals.engine import iter_candidates


def _write_close(store, symbol, tf, rows):
    sid = f"EODHD:{symbol}:{tf}:close"
    store.register_series(sid, "EODHD")
    store.append_rows(sid, rows, source="EODHD", auto_register=False)
    for field in ("high", "low"):
        fsid = f"EODHD:{symbol}:{tf}:{field}"
        store.register_series(fsid, "EODHD")
        store.append_rows(
            fsid,
            [build_row(fsid, r["value_time"], r["available_time"], r["value"] * 1.001 if field == "high" else r["value"] * 0.999) for r in rows],
            source="EODHD",
            auto_register=False,
        )


def test_future_bar_does_not_change_prior_candidates(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    sym = "EURUSD"
    tf = "H1"
    sid = f"EODHD:{sym}:{tf}:close"
    base_rows = []
    for i in range(300):
        vt = i * 3_600_000
        av = vt + 90_000
        base_rows.append(build_row(sid, vt, av, 1.08 + i * 0.0001))
    _write_close(store, sym, tf, base_rows)
    inst = (Instrument(sym, "EUR/USD", "forex", "major"),)
    end = base_rows[-1]["available_time"]
    before = list(iter_candidates(store, inst, horizon="intraday", start_ms=0, end_ms=end))

    future_vt = (300) * 3_600_000
    future = build_row(sid, future_vt, future_vt + 90_000, 999.0)
    _write_close(store, sym, tf, base_rows + [future])
    after = list(iter_candidates(store, inst, horizon="intraday", start_ms=0, end_ms=end))
    assert len(before) == len(after)
    for a, b in zip(before, after):
        assert a.decision_time_ms == b.decision_time_ms
        assert a.direction == b.direction
        assert a.signals == b.signals
