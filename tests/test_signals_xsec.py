"""xsec signal tests."""

from __future__ import annotations

import numpy as np
import pytest

from athena_ase.instruments import Instrument
from athena_ase.signals.common import BarSeries
from athena_ase.signals.xsec import compute_xsec


def _series(n: int = 80, *, drift: float = 0.0) -> BarSeries:
    t = np.arange(n, dtype="i8") * 86_400_000 + 1_700_000_000_000
    close = np.cumsum(np.full(n, drift)) + 4.0
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


def _universe(n_symbols: int = 12) -> dict[str, BarSeries]:
    out: dict[str, BarSeries] = {}
    for i in range(n_symbols):
        out[f"SYM{i}"] = _series(drift=0.001 * (i - n_symbols // 2))
    return out


def test_xsec_single_pass_percentile_mode():
    inst = Instrument("SYM5", "SYM5", "equity", "us", "SYM5.US", "SPY")
    universe = _universe()
    universe["SYM5"] = _series(drift=0.05)
    decision = int(universe["SYM5"].available_time[-1])
    res = compute_xsec(inst, universe, decision, "swing")
    assert res.direction in (-1, 0, 1)
    assert 0.0 <= res.raw_strength <= 1.0


def test_xsec_continuous_mode_uses_z_strength(monkeypatch):
    monkeypatch.setitem(__import__("config").CONFIG, "ASE_XSEC_CONTINUOUS", True)
    inst = Instrument("SYM5", "SYM5", "equity", "us", "SYM5.US", "SPY")
    universe = _universe()
    universe["SYM5"] = _series(drift=0.2)
    decision = int(universe["SYM5"].available_time[-1])
    res = compute_xsec(inst, universe, decision, "swing")
    assert res.direction == 1
    assert res.raw_strength > 0.0


def test_commodity_xsec_is_research_gated_and_uses_three_asset_sleeve(monkeypatch):
    cfg = __import__("config").CONFIG
    monkeypatch.setitem(cfg, "ASE_XSEC", {"COMMODITY_ENABLED": False})
    inst = Instrument("GC=F", "XAU/USD", "commodity", "cfd", "", "USDX")
    universe = {
        "GC=F": _series(drift=0.03),
        "XAUZAR": _series(drift=0.01),
        "SI=F": _series(drift=-0.01),
        "CL=F": _series(drift=0.20),
    }
    decision = int(universe["GC=F"].available_time[-1])

    assert compute_xsec(inst, universe, decision, "swing").disabled is True

    monkeypatch.setitem(cfg, "ASE_XSEC", {"COMMODITY_ENABLED": True})
    result = compute_xsec(inst, universe, decision, "swing")
    assert result.disabled is False
    assert result.direction == 1
    assert result.dispersion > 0.0
