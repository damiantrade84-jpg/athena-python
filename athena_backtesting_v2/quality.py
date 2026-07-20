from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .constants import TIMEFRAME_SECONDS


@dataclass(frozen=True)
class QualityReport:
    state: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    duplicate_count: int
    gap_count: int
    provider_no_quote_count: int
    malformed_count: int
    nonpositive_count: int
    misaligned_count: int
    stale_start: bool
    stale_end: bool
    limitations: tuple[str, ...]
    observations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "rowCount": self.row_count,
            "firstTimestamp": self.first_timestamp,
            "lastTimestamp": self.last_timestamp,
            "duplicateCount": self.duplicate_count,
            "gapCount": self.gap_count,
            "providerNoQuoteCount": self.provider_no_quote_count,
            "malformedCount": self.malformed_count,
            "nonpositiveCount": self.nonpositive_count,
            "misalignedCount": self.misaligned_count,
            "staleStart": self.stale_start,
            "staleEnd": self.stale_end,
            "limitations": list(self.limitations),
            "observations": list(self.observations),
        }


def _timestamp_series(frame: pd.DataFrame) -> pd.Series:
    for column in ("time", "datetime", "open_time", "timestamp"):
        if column in frame.columns:
            raw = frame[column]
            if pd.api.types.is_numeric_dtype(raw):
                numeric = pd.to_numeric(raw, errors="coerce")
                unit = "ms" if float(numeric.dropna().max() or 0) > 1e12 else "s"
                return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
            return pd.to_datetime(raw, utc=True, errors="coerce")
    return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")


def _missing_market_bars(
    previous: pd.Timestamp,
    current: pd.Timestamp,
    *,
    seconds: int,
    timeframe: str,
    asset_type: str,
    provider: str | None,
) -> int:
    total_slots = max(0, int(round((current - previous).total_seconds() / seconds)) - 1)
    if total_slots == 0:
        return 0
    asset = str(asset_type or "").lower()
    if asset == "crypto":
        return total_slots
    if asset == "forex":
        def _fx_session_open(timestamp: pd.Timestamp) -> bool:
            if str(provider or "").lower() == "mt5":
                # The production MT5 broker emits a UTC 24/5 series: the last
                # H1 bar is Friday 23:00 UTC and the next is Monday 00:00 UTC.
                # Applying a New York Sunday reopen invents three missing bars
                # per weekend, so certification must use the frozen provider
                # session rather than a generic FX calendar.
                trading_date = timestamp.tz_convert(timezone.utc).date()
                return trading_date.weekday() < 5 and (trading_date.month, trading_date.day) not in {(1, 1), (12, 25)}
            local = timestamp.tz_convert(ZoneInfo("America/New_York"))
            # The FX trading date rolls at 17:00 New York.  Use that date for
            # both weekend and full-market holiday closures so D1 and intraday
            # bars share one calendar contract.
            trading_date = local.date()
            if local.hour >= 17:
                trading_date += timedelta(days=1)
            if trading_date.weekday() >= 5:
                return False
            return (trading_date.month, trading_date.day) not in {(1, 1), (12, 25)}

        return sum(
            1
            for slot in range(1, total_slots + 1)
            if _fx_session_open(previous + pd.Timedelta(seconds=seconds * slot))
        )
    if timeframe == "D1":
        return sum(
            1
            for slot in range(1, total_slots + 1)
            if (previous + pd.Timedelta(seconds=seconds * slot)).dayofweek < 5
        )
    # Exchange sessions differ by venue. Within-session holes are certified as
    # gaps; overnight/session boundaries are admitted without inventing hours.
    if previous.date() == current.date():
        return total_slots
    cursor = previous.normalize() + pd.Timedelta(days=1)
    skipped_sessions = 0
    while cursor < current.normalize():
        if cursor.dayofweek < 5:
            skipped_sessions += 1
        cursor += pd.Timedelta(days=1)
    return skipped_sessions


def normalize_and_validate(
    candles: list[dict[str, Any]],
    *,
    timeframe: str,
    asset_type: str,
    provider: str | None = None,
    requested_start: datetime,
    requested_end: datetime,
    as_of: datetime | None = None,
) -> tuple[pd.DataFrame, QualityReport]:
    tf = str(timeframe).upper()
    seconds = TIMEFRAME_SECONDS.get(tf)
    if seconds is None:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    frame = pd.DataFrame(candles)
    for column in ("open", "high", "low", "close"):
        if column not in frame.columns:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "volume" not in frame.columns:
        frame["volume"] = frame.get("vol", 0.0)
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    frame["time"] = _timestamp_series(frame)

    malformed_mask = frame[["time", "open", "high", "low", "close"]].isna().any(axis=1)
    malformed_count = int(malformed_mask.sum())
    finite_mask = np.isfinite(frame[["open", "high", "low", "close"]]).all(axis=1)
    nonpositive_mask = (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
    geometry_mask = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
    )
    invalid_mask = malformed_mask | ~finite_mask | nonpositive_mask | geometry_mask
    nonpositive_count = int(nonpositive_mask.sum())

    duplicate_count = int(frame["time"].duplicated(keep=False).sum())
    frame = frame.loc[~invalid_mask].sort_values("time", kind="mergesort").reset_index(drop=True)
    frame = frame.loc[~frame["time"].duplicated(keep="first")].reset_index(drop=True)

    gap_count = 0
    provider_no_quote_count = 0
    misaligned_count = 0
    if not frame.empty:
        epoch_seconds = (frame["time"].astype("int64") // 1_000_000_000).to_numpy()
        # D1 opens can be exchange-local and are normalized by providers; only
        # enforce grid alignment below daily resolution.
        if tf != "D1":
            misaligned_count = int(np.sum(epoch_seconds % seconds != epoch_seconds[0] % seconds))
        differences = frame["time"].diff()
        for index in range(1, len(frame)):
            delta = differences.iloc[index].total_seconds()
            if delta > seconds * 1.5:
                missing_slots = _missing_market_bars(
                    frame["time"].iloc[index - 1],
                    frame["time"].iloc[index],
                    seconds=seconds,
                    timeframe=tf,
                    asset_type=asset_type,
                    provider=provider,
                )
                if str(provider or "").lower() == "mt5":
                    # MT5 OHLC bars are quote/tick-generated: the broker does
                    # not emit an empty bar for any asset when its feed is
                    # closed or no quote arrives. Preserve those absent grid
                    # slots as visible provider observations instead of
                    # misclassifying them as corrupt history. Coverage,
                    # malformed rows, duplicates and alignment still fail
                    # closed independently.
                    provider_no_quote_count += missing_slots
                else:
                    gap_count += missing_slots

    first = frame["time"].iloc[0].isoformat() if not frame.empty else None
    last = frame["time"].iloc[-1].isoformat() if not frame.empty else None
    start_utc = requested_start.astimezone(timezone.utc)
    requested_end_utc = requested_end.astimezone(timezone.utc)
    as_of_utc = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # A date-only request ending today represents "through now", not future
    # hours later in the UTC day.  Comparing against 23:59 falsely rejected
    # otherwise current crypto intraday series.
    end_utc = min(requested_end_utc, as_of_utc)
    stale_tolerance = seconds * (3 if str(asset_type).lower() == "crypto" else 5)
    start_tolerance = max(stale_tolerance, 3 * 86_400 if str(asset_type).lower() != "crypto" else stale_tolerance)
    stale_start = True
    stale_end = True
    if not frame.empty:
        stale_start = (frame["time"].iloc[0].to_pydatetime() - start_utc).total_seconds() > start_tolerance
        stale_end = (end_utc - frame["time"].iloc[-1].to_pydatetime()).total_seconds() > stale_tolerance
        if str(asset_type).lower() != "crypto" and end_utc.weekday() >= 5:
            stale_end = False

    limitations: list[str] = []
    if malformed_count:
        limitations.append(f"malformed_rows:{malformed_count}")
    if duplicate_count:
        limitations.append(f"duplicate_rows:{duplicate_count}")
    if gap_count:
        limitations.append(f"missing_bars:{gap_count}")
    if misaligned_count:
        limitations.append(f"misaligned_bars:{misaligned_count}")
    if stale_start:
        limitations.append("requested_start_not_covered")
    if stale_end:
        limitations.append("requested_end_not_covered")
    if len(frame) < 50:
        limitations.append("insufficient_history")
    observations: list[str] = []
    if provider_no_quote_count:
        observations.append(f"provider_no_quote_bars:{provider_no_quote_count}")
    state = "PASS" if not limitations else "FAIL"
    return frame[["time", "open", "high", "low", "close", "volume"]], QualityReport(
        state=state,
        row_count=len(frame),
        first_timestamp=first,
        last_timestamp=last,
        duplicate_count=duplicate_count,
        gap_count=gap_count,
        provider_no_quote_count=provider_no_quote_count,
        malformed_count=malformed_count,
        nonpositive_count=nonpositive_count,
        misaligned_count=misaligned_count,
        stale_start=stale_start,
        stale_end=stale_end,
        limitations=tuple(limitations),
        observations=tuple(observations),
    )
