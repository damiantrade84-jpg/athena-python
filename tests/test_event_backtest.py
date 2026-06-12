"""Layer 1 event backtest simulation tests."""

from __future__ import annotations

import math

import numpy as np
import pytest

from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.horizon import K_SL, K_TP
from athena_ase.instruments import Instrument
from athena_ase.signals.arbitrate import Candidate
from athena_ase.signals.common import BarSeries
from athena_research.ase.event_backtest import simulate_candidate


def _seed_ohlc(store, symbol, tf, bars):
    for field in ("close", "high", "low"):
        sid = f"EODHD:{symbol}:{tf}:{field}"
        store.register_series(sid, "EODHD")
        rows = []
        for vt, c, h, lo, av in bars:
            val = {"close": c, "high": h, "low": lo}[field]
            rows.append(build_row(sid, vt, av, val))
        store.append_rows(sid, rows, source="EODHD", auto_register=False)


def test_adverse_first_and_cost_math(tmp_path):
    store = PTISStore(tmp_path / "ptis")
    h = 16
    sig = 0.01
    r_unit = K_SL * sig * math.sqrt(h)
    p0 = math.log(100.0)
    upper = p0 + r_unit
    lower = p0 - r_unit
    bars = [(i * 3_600_000, math.exp(p0), math.exp(upper + 0.01), math.exp(lower - 0.01), i * 3_600_000 + 90_000) for i in range(5)]
    bars[1] = (3_600_000, math.exp(p0), math.exp(upper + 0.01), math.exp(lower - 0.01), 3_600_000 + 90_000)
    _seed_ohlc(store, "TST", "H1", bars)
    inst = Instrument("TST", "TST", "forex", "major", "TST.FOREX", "USDX")
    cand = Candidate(
        "TST", "forex", "intraday", bars[0][4], 0, 1, [], 1, False, sig, p0
    )
    out = simulate_candidate(cand, store, inst)
    assert out is not None
    assert out.exit_reason == "adverse_first"
    assert out.gross_R == pytest.approx(-1.0, abs=0.05)
    assert out.net_R == pytest.approx(out.gross_R - out.cost_R)


def test_vertical_exit_sign(tmp_path, monkeypatch):
    store = PTISStore(tmp_path / "ptis")
    sig = 0.02
    p0 = 0.0
    bars = []
    for i in range(20):
        vt = i * 3_600_000
        px = math.exp(p0 + i * 0.0001)
        bars.append((vt, px, px * 1.001, px * 0.999, vt + 90_000))
    _seed_ohlc(store, "FLAT", "H1", bars)
    inst = Instrument("FLAT", "FLAT", "forex", "major", "FLAT.FOREX", "USDX")
    cand = Candidate("FLAT", "forex", "intraday", bars[0][4], 0, 1, [], 1, False, sig, p0)
    out = simulate_candidate(cand, store, inst)
    assert out is not None
    assert out.exit_reason == "vertical"
    assert out.hold_bars == 16
