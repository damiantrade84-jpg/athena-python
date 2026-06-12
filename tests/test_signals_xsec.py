"""Cross-sectional momentum tests."""

from __future__ import annotations

import numpy as np

from athena_ase.instruments import Instrument
from athena_ase.signals.common import BarSeries
from athena_ase.signals.xsec import compute_xsec


def _series(values: list[float], sigma: float = 0.01) -> BarSeries:
    n = len(values)
    vt = np.arange(n, dtype=np.int64) * 3_600_000
    av = vt + 90_000
    close_log = np.log(np.array(values, dtype=float))
    sig = np.full(n, sigma)
    return BarSeries(vt, av, close_log, close_log, close_log, sig, sig)


def test_xsec_percentile_long_short():
    inst = Instrument("AAA", "AAA", "equity", "us")
    family = {}
    for i, sym in enumerate("ABCDEFGHIJ"):
        vals = [1.0 + j * 0.01 for j in range(80)]
        if sym == "A":
            vals = [1.0 + j * 0.05 for j in range(80)]
        family[sym] = _series(vals)
    inst_top = Instrument("A", "A", "equity", "us")
    t = family["A"].available_time[-1]
    top = compute_xsec(inst_top, family, int(t), "swing")
    assert top.direction == 1
    assert top.pct >= 0.8

    inst_low = Instrument("J", "J", "equity", "us")
    low = compute_xsec(inst_low, family, int(t), "swing")
    assert low.direction == -1
    assert low.pct <= 0.2


def test_xsec_universe_too_small_disabled():
    inst = Instrument("A", "A", "equity", "us")
    family = {f"S{i}": _series([1.0] * 70) for i in range(5)}
    res = compute_xsec(inst, family, 1_000_000_000, "swing")
    assert res.disabled is True
    assert res.direction == 0
