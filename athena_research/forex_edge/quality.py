from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from athena_research.forex_edge.models import (
    EligibilityResult,
    EligibilityStatus,
    InvalidResearchInputError,
    ReasonCode,
)


_BAR_COLUMNS = (
    "timestamp",
    "bid_open",
    "bid_high",
    "bid_low",
    "bid_close",
    "ask_open",
    "ask_high",
    "ask_low",
    "ask_close",
)

_PRICE_COLUMNS = _BAR_COLUMNS[1:]


def _normalize_utc_timestamp(value: object, field_name: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidResearchInputError(
            f"{field_name} must be a valid UTC timestamp"
        ) from exc
    if pd.isna(timestamp):
        raise InvalidResearchInputError(
            f"{field_name} must be a valid UTC timestamp"
        )
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _normalize_utc_series(values: pd.Series, field_name: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, utc=True, errors="raise")
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidResearchInputError(
            f"{field_name} must contain valid UTC timestamps"
        ) from exc
    if parsed.isna().any():
        raise InvalidResearchInputError(
            f"{field_name} must contain valid UTC timestamps"
        )
    return parsed


def _verified_flags(values: pd.Series) -> pd.Series:
    checked: list[bool] = []
    for value in values.tolist():
        if isinstance(value, (bool, np.bool_)):
            checked.append(bool(value))
            continue
        raise InvalidResearchInputError(
            "availability_verified must contain boolean flags"
        )
    return pd.Series(checked, index=values.index, dtype=bool)


def _json_rows(rows: pd.Index | pd.Series | list[int]) -> list[int]:
    return [int(value) for value in list(rows)]


def macro_asof(frame: pd.DataFrame, decision_time: pd.Timestamp) -> pd.Series:
    required = {"available_time", "value", "availability_verified"}
    if not isinstance(frame, pd.DataFrame):
        raise InvalidResearchInputError("frame must be a DataFrame")

    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{ReasonCode.MISSING_SERIES.value}:{missing}")

    work = frame.copy().reset_index(drop=True)
    work["available_time"] = _normalize_utc_series(
        work["available_time"],
        "available_time",
    )
    work["availability_verified"] = _verified_flags(
        work["availability_verified"]
    )
    if "timestamp" in work.columns:
        work["timestamp"] = _normalize_utc_series(work["timestamp"], "timestamp")

    decision = _normalize_utc_timestamp(decision_time, "decision_time")
    eligible = work[work["available_time"] <= decision]
    if eligible.empty:
        raise ValueError(ReasonCode.MISSING_SERIES.value)

    if not eligible["availability_verified"].all():
        raise ValueError(ReasonCode.UNVERIFIED_AVAILABILITY.value)

    selected = eligible.sort_values("available_time", kind="stable").iloc[-1]
    return selected


def _price_matrix(prices: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    raw = prices.to_numpy(dtype=object)
    numeric = prices.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    return raw, numeric


def _nonpositive_price_rows(prices: pd.DataFrame) -> pd.Series:
    raw, numeric = _price_matrix(prices)
    bool_mask = np.vectorize(
        lambda value: isinstance(value, (bool, np.bool_)),
        otypes=[bool],
    )(raw)
    invalid = bool_mask | ~np.isfinite(numeric) | (numeric <= 0)
    return pd.Series(invalid.any(axis=1), index=prices.index)


def _crossed_quote_rows(prices: pd.DataFrame) -> pd.Series:
    _, numeric = _price_matrix(prices)
    valid = np.isfinite(numeric).all(axis=1) & (numeric > 0).all(axis=1)
    bid = numeric[:, :4]
    ask = numeric[:, 4:]
    cross = (
        (ask[:, 0] < bid[:, 0])
        | (ask[:, 1] < bid[:, 1])
        | (ask[:, 2] < bid[:, 2])
        | (ask[:, 3] < bid[:, 3])
    )
    bid_high = bid[:, 1]
    bid_low = bid[:, 2]
    bid_open = bid[:, 0]
    bid_close = bid[:, 3]
    ask_high = ask[:, 1]
    ask_low = ask[:, 2]
    ask_open = ask[:, 0]
    ask_close = ask[:, 3]
    bid_consistent = (
        (bid_high >= bid_open)
        & (bid_high >= bid_close)
        & (bid_high >= bid_low)
        & (bid_low <= bid_open)
        & (bid_low <= bid_close)
        & (bid_low <= bid_high)
    )
    ask_consistent = (
        (ask_high >= ask_open)
        & (ask_high >= ask_close)
        & (ask_high >= ask_low)
        & (ask_low <= ask_open)
        & (ask_low <= ask_close)
        & (ask_low <= ask_high)
    )
    return pd.Series(
        valid & (~bid_consistent | ~ask_consistent | cross),
        index=prices.index,
    )


def _duplicate_conflict_rows(
    work: pd.DataFrame,
    prices: pd.DataFrame,
) -> list[str]:
    conflicts: list[str] = []
    for timestamp, group in work.groupby("timestamp", sort=True):
        group_prices = prices.loc[group.index]
        if len(group_prices.drop_duplicates()) > 1:
            conflicts.append(pd.Timestamp(timestamp).isoformat())
    return conflicts


def validate_bid_ask_bars(frame: pd.DataFrame) -> EligibilityResult:
    if not isinstance(frame, pd.DataFrame):
        raise InvalidResearchInputError("frame must be a DataFrame")

    required = set(_BAR_COLUMNS)
    missing = sorted(required.difference(frame.columns))
    if missing:
        return EligibilityResult(
            eligible=False,
            status=EligibilityStatus.INELIGIBLE,
            reason_codes=(ReasonCode.MIDPOINT_ONLY,),
            details={"missing_columns": missing},
        )

    work = frame.copy().reset_index(drop=True)
    work["timestamp"] = _normalize_utc_series(work["timestamp"], "timestamp")

    prices = work[list(_PRICE_COLUMNS)]
    normalized_prices = prices.apply(pd.to_numeric, errors="coerce")
    reasons: list[ReasonCode] = []
    details: dict[str, Any] = {}

    nonpositive_rows = _nonpositive_price_rows(prices)
    if nonpositive_rows.any():
        reasons.append(ReasonCode.NONPOSITIVE_PRICE)
        details["nonpositive_price_rows"] = _json_rows(
            work.index[nonpositive_rows]
        )

    crossed_rows = _crossed_quote_rows(prices)
    if crossed_rows.any():
        reasons.append(ReasonCode.CROSSED_QUOTE)
        details["crossed_rows"] = _json_rows(work.index[crossed_rows])

    conflicts = _duplicate_conflict_rows(work, normalized_prices)
    if conflicts:
        reasons.append(ReasonCode.DUPLICATE_CONFLICT)
        details["duplicate_conflicts"] = conflicts

    ordered = tuple(dict.fromkeys(reasons))
    return EligibilityResult(
        eligible=not ordered,
        status=(
            EligibilityStatus.ELIGIBLE
            if not ordered
            else EligibilityStatus.INELIGIBLE
        ),
        reason_codes=ordered,
        details=details,
    )
