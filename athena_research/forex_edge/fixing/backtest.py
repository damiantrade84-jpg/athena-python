from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import numpy as np
import pandas as pd

from athena_research.forex_edge.fixing.calendar import resolve_fixing_utc
from athena_research.forex_edge.fixing.costs import stressed_trade_return
from athena_research.forex_edge.fixing.windows import FixingWindow, fixing_window


_PRICE_COLUMNS = (
    "bid_open",
    "bid_high",
    "bid_low",
    "bid_close",
    "ask_open",
    "ask_high",
    "ask_low",
    "ask_close",
)


def _prepare_bars(bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = {"timestamp", "symbol", *_PRICE_COLUMNS}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError("NO_EXECUTABLE_QUOTE")
    work = bars[bars["symbol"].eq(symbol)].copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    for column in _PRICE_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    if work[list(_PRICE_COLUMNS)].isna().any().any():
        raise ValueError("NO_EXECUTABLE_QUOTE")
    if (~np.isfinite(work[list(_PRICE_COLUMNS)].to_numpy(dtype=float))).any():
        raise ValueError("NO_EXECUTABLE_QUOTE")
    if (work[list(_PRICE_COLUMNS)] <= 0).any().any():
        raise ValueError("NO_EXECUTABLE_QUOTE")
    crossed = (
        (work["ask_open"] < work["bid_open"])
        | (work["ask_close"] < work["bid_close"])
        | (work["ask_high"] < work["bid_high"])
        | (work["ask_low"] < work["bid_low"])
    )
    if crossed.any():
        raise ValueError("CROSSED_QUOTE")
    duplicated = work[work.duplicated("timestamp", keep=False)]
    if not duplicated.empty:
        for _, group in duplicated.groupby("timestamp"):
            if len(group[list(_PRICE_COLUMNS)].drop_duplicates()) > 1:
                raise ValueError("DUPLICATE_CONFLICT")
        work = work.drop_duplicates("timestamp", keep="first")
    return work.set_index("timestamp").sort_index()


def _row_at(frame: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series:
    ts = pd.Timestamp(timestamp).tz_convert("UTC")
    if ts not in frame.index:
        raise ValueError("NO_EXECUTABLE_QUOTE")
    return frame.loc[ts]


def _mid_close(row: pd.Series) -> float:
    return float((row["bid_close"] + row["ask_close"]) / 2.0)


def run_fixing_event(
    bars: pd.DataFrame,
    *,
    symbol: str,
    window: FixingWindow,
    roundtrip_commission_bps: float,
    cost_multiplier: float,
) -> dict[str, object] | None:
    work = _prepare_bars(bars, symbol)
    observation = _row_at(work, window.observation_start)
    signal = _row_at(work, window.signal_bar_end)
    entry = _row_at(work, window.entry_bar_end)
    exit_row = _row_at(work, window.exit_bar_end)

    move = _mid_close(signal) - _mid_close(observation)
    if move == 0:
        return None
    direction = 1 if move > 0 else -1
    if window.direction_mode == "reversal":
        direction *= -1
    entry_price = float(entry["ask_open"] if direction > 0 else entry["bid_open"])
    exit_price = float(exit_row["bid_close"] if direction > 0 else exit_row["ask_close"])
    gross_mid, spread_cost, net_return = stressed_trade_return(
        direction=direction,
        entry_bid=float(entry["bid_open"]),
        entry_ask=float(entry["ask_open"]),
        exit_bid=float(exit_row["bid_close"]),
        exit_ask=float(exit_row["ask_close"]),
        commission_bps=roundtrip_commission_bps,
        cost_multiplier=cost_multiplier,
    )
    return {
        "symbol": symbol,
        "fixing_time": window.fixing_time,
        "observation_start": window.observation_start,
        "signal_bar_end": window.signal_bar_end,
        "entry_bar_end": window.entry_bar_end,
        "exit_bar_end": window.exit_bar_end,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_mid_return": gross_mid,
        "observed_spread_cost": spread_cost,
        "commission_bps": roundtrip_commission_bps,
        "net_return": net_return,
    }


def run_fixing_backtest(
    bars: pd.DataFrame,
    *,
    symbol: str,
    event_dates: Iterable[pd.Timestamp],
    timezone_name: str,
    local_time: str,
    mode: Literal["pre_continuation", "post_reversal"],
    roundtrip_commission_bps: float,
    cost_multiplier: float,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for event_date in event_dates:
        fixing = resolve_fixing_utc(
            event_date,
            timezone_name=timezone_name,
            local_time=local_time,
        )
        event = run_fixing_event(
            bars,
            symbol=symbol,
            window=fixing_window(fixing, mode=mode),
            roundtrip_commission_bps=roundtrip_commission_bps,
            cost_multiplier=cost_multiplier,
        )
        if event is not None:
            rows.append(event)
    return pd.DataFrame(rows)
