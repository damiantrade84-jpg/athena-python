"""Phase 2 — triangular consistency labels (shadow only)."""

from __future__ import annotations

import math
import tempfile

from athena_ase.context.triangular import (
    CONSISTENT,
    CONTRADICTORY,
    INSUFFICIENT_DATA,
    cross_legs,
    triangular_label,
)
from athena_ase.data.ingest.common import append_ptis_rows
from athena_ase.data.ptis import PTISStore, build_row

_DAY = 86_400_000


def _ingest_trend(store: PTISStore, symbol: str, *, n: int = 320, drift: float = 0.002):
    for field in ("close", "high", "low"):
        sid = f"MT5:{symbol}:D1:{field}"
        rows = []
        for i in range(n):
            price = math.exp(drift * i)
            bump = 1.0005 if field == "high" else 0.9995 if field == "low" else 1.0
            t = i * _DAY
            rows.append(build_row(sid, t, t + 1, price * bump))
        append_ptis_rows(store, sid, "MT5", rows)


def test_consistent_and_contradictory_labels():
    store = PTISStore(tempfile.mkdtemp(prefix="ase_tri_"))
    _ingest_trend(store, "GBPUSD", drift=0.002)   # GBP strengthening vs USD
    _ingest_trend(store, "USDJPY", drift=0.002)   # JPY weakening vs USD
    t = 319 * _DAY

    long_lbl = triangular_label(store, "GBPJPY", 1, t, "swing")
    assert long_lbl is not None and long_lbl["label"] == CONSISTENT

    short_lbl = triangular_label(store, "GBPJPY", -1, t, "swing")
    assert short_lbl is not None and short_lbl["label"] == CONTRADICTORY


def test_missing_leg_is_insufficient():
    store = PTISStore(tempfile.mkdtemp(prefix="ase_tri_missing_"))
    _ingest_trend(store, "GBPUSD", drift=0.002)  # no USDJPY data
    lbl = triangular_label(store, "GBPJPY", 1, 319 * _DAY, "swing")
    assert lbl is not None and lbl["label"] == INSUFFICIENT_DATA


def test_usd_pairs_have_no_triangular_label():
    assert cross_legs("EURUSD") is None
    assert cross_legs("GBPJPY") is not None
