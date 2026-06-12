"""ASE horizon and barrier constants (v2.1 frozen defaults)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Horizon = Literal["intraday", "swing"]


@dataclass(frozen=True)
class HorizonConfig:
    horizon: Horizon
    tf: str
    max_hold_bars: int
    sigma_bar_span: int
    sigma_long_span: int
    tsmom_lookbacks: tuple[int, ...]
    xsec_lookback: int = 63


HORIZONS: dict[Horizon, HorizonConfig] = {
    "intraday": HorizonConfig(
        horizon="intraday",
        tf="H1",
        max_hold_bars=16,
        sigma_bar_span=32,
        sigma_long_span=256,
        tsmom_lookbacks=(24, 72, 168),
    ),
    "swing": HorizonConfig(
        horizon="swing",
        tf="D1",
        max_hold_bars=10,
        sigma_bar_span=21,
        sigma_long_span=126,
        tsmom_lookbacks=(21, 63, 126, 252),
    ),
}

K_SL = 1.0
K_TP = 1.0
