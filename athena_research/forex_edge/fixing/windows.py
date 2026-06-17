from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class FixingWindow:
    fixing_time: pd.Timestamp
    observation_start: pd.Timestamp
    signal_bar_end: pd.Timestamp
    entry_bar_end: pd.Timestamp
    exit_bar_end: pd.Timestamp
    direction_mode: Literal["continuation", "reversal"]


def fixing_window(
    fixing_time: pd.Timestamp,
    *,
    mode: Literal["pre_continuation", "post_reversal"],
) -> FixingWindow:
    fixing = pd.Timestamp(fixing_time).tz_convert("UTC")
    if mode == "pre_continuation":
        signal = fixing - pd.Timedelta(minutes=15)
        return FixingWindow(
            fixing,
            fixing - pd.Timedelta(minutes=30),
            signal,
            signal + pd.Timedelta(minutes=5),
            fixing,
            "continuation",
        )
    if mode == "post_reversal":
        signal = fixing
        return FixingWindow(
            fixing,
            fixing - pd.Timedelta(minutes=15),
            signal,
            signal + pd.Timedelta(minutes=5),
            fixing + pd.Timedelta(minutes=30),
            "reversal",
        )
    raise ValueError(f"Unknown fixing mode: {mode}")
