from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd


KNOWN_SCHEMAS = {
    "tick_bid_ask": {"time": "time", "bid": "bid", "ask": "ask"},
    "m5_bid_ask": {
        "time": "time",
        "bid_open": "bid_open",
        "bid_high": "bid_high",
        "bid_low": "bid_low",
        "bid_close": "bid_close",
        "ask_open": "ask_open",
        "ask_high": "ask_high",
        "ask_low": "ask_low",
        "ask_close": "ask_close",
    },
}

BAR_COLUMNS = (
    "timestamp",
    "symbol",
    "bid_open",
    "bid_high",
    "bid_low",
    "bid_close",
    "ask_open",
    "ask_high",
    "ask_low",
    "ask_close",
)


def _timezone(timezone_name: str) -> ZoneInfo:
    if not timezone_name:
        raise ValueError("AMBIGUOUS_TIMEZONE")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("AMBIGUOUS_TIMEZONE") from exc


def _mapping_for(
    schema: str,
    column_mapping: dict[str, str] | None,
) -> dict[str, str]:
    if "midpoint" in schema.lower():
        raise ValueError("MIDPOINT_ONLY")
    if column_mapping is not None:
        return dict(column_mapping)
    if schema not in KNOWN_SCHEMAS:
        raise ValueError(f"unknown Dukascopy schema: {schema}")
    return dict(KNOWN_SCHEMAS[schema])


def _utc_timestamp(raw: pd.Series, timezone_name: str) -> pd.Series:
    parsed = pd.to_datetime(raw, errors="raise")
    localized = parsed.dt.tz_localize(
        _timezone(timezone_name),
        ambiguous="raise",
        nonexistent="raise",
    )
    return localized.dt.tz_convert("UTC")


def _validate_duplicate_conflicts(frame: pd.DataFrame, columns: list[str]) -> None:
    duplicated = frame[frame.duplicated("timestamp", keep=False)]
    if duplicated.empty:
        return
    for _, group in duplicated.groupby("timestamp"):
        if len(group[columns].drop_duplicates()) > 1:
            raise ValueError("DUPLICATE_CONFLICT")


def _validate_bar_quotes(frame: pd.DataFrame) -> None:
    price_columns = [column for column in BAR_COLUMNS if column not in {"timestamp", "symbol"}]
    numeric = frame[price_columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or (~np.isfinite(numeric)).any().any():
        raise ValueError("NONPOSITIVE_PRICE")
    if (numeric <= 0).any().any():
        raise ValueError("NONPOSITIVE_PRICE")
    crossed = (
        (numeric["ask_open"] < numeric["bid_open"])
        | (numeric["ask_high"] < numeric["bid_high"])
        | (numeric["ask_low"] < numeric["bid_low"])
        | (numeric["ask_close"] < numeric["bid_close"])
    )
    if crossed.any():
        raise ValueError("CROSSED_QUOTE")


def _resample_ticks(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    work = frame.set_index("timestamp").sort_index()
    bid = work["bid"].resample("5min", label="right", closed="left").ohlc()
    ask = work["ask"].resample("5min", label="right", closed="left").ohlc()
    bars = pd.DataFrame(
        {
            "timestamp": bid.index,
            "symbol": symbol,
            "bid_open": bid["open"],
            "bid_high": bid["high"],
            "bid_low": bid["low"],
            "bid_close": bid["close"],
            "ask_open": ask["open"],
            "ask_high": ask["high"],
            "ask_low": ask["low"],
            "ask_close": ask["close"],
        }
    ).dropna(how="any")
    return bars.reset_index(drop=True)


def import_dukascopy(
    source_path: Path,
    *,
    symbol: str,
    timezone_name: str,
    schema: str,
    column_mapping: dict[str, str] | None = None,
    delimiter: str = ",",
) -> pd.DataFrame:
    _timezone(timezone_name)
    mapping = _mapping_for(schema, column_mapping)
    raw = pd.read_csv(source_path, delimiter=delimiter)
    missing = set(mapping.values()) - set(raw.columns)
    if missing:
        raise ValueError(f"MISSING_SERIES:{sorted(missing)}")
    frame = raw.rename(columns={source: target for target, source in mapping.items()})
    frame["timestamp"] = _utc_timestamp(frame["time"], timezone_name)
    target_columns = set(mapping)
    if {"time", "bid", "ask"}.issubset(target_columns):
        frame["bid"] = pd.to_numeric(frame["bid"], errors="coerce")
        frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")
        if frame[["bid", "ask"]].isna().any().any():
            raise ValueError("NONPOSITIVE_PRICE")
        if ((frame["bid"] <= 0) | (frame["ask"] <= 0)).any():
            raise ValueError("NONPOSITIVE_PRICE")
        if (frame["ask"] < frame["bid"]).any():
            raise ValueError("CROSSED_QUOTE")
        _validate_duplicate_conflicts(frame, ["bid", "ask"])
        bars = _resample_ticks(frame[["timestamp", "bid", "ask"]], symbol)
    elif set(BAR_COLUMNS) - {"timestamp", "symbol"} <= target_columns:
        columns = [column for column in BAR_COLUMNS if column not in {"timestamp", "symbol"}]
        frame = frame[["timestamp", *columns]].copy()
        _validate_duplicate_conflicts(frame, columns)
        frame["timestamp"] = frame["timestamp"] + pd.Timedelta(minutes=5)
        frame["symbol"] = symbol
        bars = frame[list(BAR_COLUMNS)].sort_values("timestamp").reset_index(drop=True)
    else:
        raise ValueError(f"unknown Dukascopy schema: {schema}")
    _validate_bar_quotes(bars)
    return bars[list(BAR_COLUMNS)].sort_values("timestamp").reset_index(drop=True)
