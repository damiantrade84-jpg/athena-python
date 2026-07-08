"""ASE Layer-1 intraday family policy tests (F7)."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from athena_ase.instruments import Instrument
from athena_ase.signals.common import BarSeries
from athena_ase.signals import engine as engine_mod


def _bar_series(n: int = 300) -> BarSeries:
    t = np.arange(n, dtype="i8") * 3_600_000 + 1_700_000_000_000
    close = np.full(n, 4.605)
    sig = np.full(n, 0.01)
    return BarSeries(
        value_time=t,
        available_time=t,
        close_log=close,
        high_log=close + 0.001,
        low_log=close - 0.001,
        sigma_bar=sig,
        sigma_long=sig,
    )


def test_intraday_skips_us_equity_by_default(monkeypatch):
    monkeypatch.setattr(engine_mod, "load_bar_series", lambda *_a, **_k: _bar_series())
    monkeypatch.setattr(engine_mod, "compute_tsmom", lambda *_a, **_k: MagicMock(direction=1, raw_strength=0.8, blend=0.0))
    monkeypatch.setattr(engine_mod, "arbitrate", lambda *_a, **_k: MagicMock())
    store = MagicMock()
    inst = Instrument("AAPL", "AAPL", "equity", "us", "AAPL.US", "SPY")
    cands = list(
        engine_mod.iter_candidates(
            store,
            instruments=(inst,),
            horizon="intraday",
            start_ms=1_700_000_000_000,
            end_ms=1_700_100_000_000,
        )
    )
    assert cands == []


def test_swing_still_emits_for_us_equity(monkeypatch):
    emitted: list = []

    monkeypatch.setattr(engine_mod, "load_bar_series", lambda *_a, **_k: _bar_series())
    monkeypatch.setattr(engine_mod, "compute_tsmom", lambda *_a, **_k: MagicMock(direction=1, raw_strength=0.8, blend=0.0))
    monkeypatch.setattr(engine_mod, "compute_carry", lambda *_a, **_k: MagicMock(direction=0, raw_strength=0.0))
    monkeypatch.setattr(engine_mod, "compute_xsec", lambda *_a, **_k: MagicMock(direction=0, raw_strength=0.0, disabled=True))
    monkeypatch.setattr(engine_mod, "_family_bar_cache", lambda *_a, **_k: {})
    monkeypatch.setattr(
        engine_mod,
        "arbitrate",
        lambda *_a, **_k: emitted.append(1) or engine_mod.Candidate(
            instrument="AAPL",
            family="equity",
            horizon="swing",
            decision_time_ms=1,
            bar_index=1,
            direction=1,
        ),
    )
    store = MagicMock()
    inst = Instrument("AAPL", "AAPL", "equity", "us", "AAPL.US", "SPY")
    list(
        engine_mod.iter_candidates(
            store,
            instruments=(inst,),
            horizon="swing",
            start_ms=1_700_000_000_000,
            end_ms=1_700_100_000_000,
        )
    )
    assert len(emitted) >= 1
