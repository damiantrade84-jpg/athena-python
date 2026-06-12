"""Mean reversion signal tests."""

from __future__ import annotations

import numpy as np

from athena_ase.instruments import Instrument
from athena_ase.signals.meanrev import compute_meanrev


def test_meanrev_z_thresholds():
    inst = Instrument("EURUSD", "EUR/USD", "forex", "major")
    close = np.zeros(30)
    close[19:29] = 0.0
    close[29] = 0.5
    res = compute_meanrev(inst, close, 29, tsmom_blend=0.0)
    assert res.direction == -1
    assert res.raw_strength > 0

    close2 = close.copy()
    close2[29] = -0.5
    res2 = compute_meanrev(inst, close2, 29, tsmom_blend=0.0)
    assert res2.direction == 1


def test_meanrev_trend_suppression():
    inst = Instrument("EURUSD", "EUR/USD", "forex", "major")
    close = np.zeros(30)
    close[19:29] = 0.0
    close[29] = 0.5
    suppressed = compute_meanrev(inst, close, 29, tsmom_blend=2.0)
    assert suppressed.direction == 0

    close[29] = -0.5
    suppressed2 = compute_meanrev(inst, close, 29, tsmom_blend=-2.0)
    assert suppressed2.direction == 0
