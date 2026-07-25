"""ASE horizon and barrier constants (v2.1 frozen defaults)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ``intraday_h4`` is an offline experiment only.  It is deliberately absent
# from the live scan defaults until its separately-trained artifacts pass
# review and are explicitly promoted.
Horizon = Literal["intraday", "intraday_h4", "swing"]


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
    # Duration-equivalent to the H1 intraday model: do not turn the existing
    # 16-hour trade into a 64-hour trade merely by changing the bar timeframe.
    "intraday_h4": HorizonConfig(
        horizon="intraday_h4",
        tf="H4",
        max_hold_bars=4,
        sigma_bar_span=8,
        sigma_long_span=64,
        tsmom_lookbacks=(6, 18, 42),
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


def is_intraday(horizon: Horizon | str) -> bool:
    """Whether a horizon has intraday signal semantics (including H4 trial)."""
    return str(horizon) in {"intraday", "intraday_h4"}


def horizon_bar_ms(horizon: Horizon | str) -> int:
    """Duration of one bar for labeling and embargo calculations."""
    return {"intraday": 3_600_000, "intraday_h4": 14_400_000, "swing": 86_400_000}.get(
        str(horizon), 3_600_000
    )

K_SL = 1.0
K_TP = 1.0
