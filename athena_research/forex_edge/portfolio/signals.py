from __future__ import annotations

import pandas as pd

from athena_research.forex_edge.sources.fred import percent_to_decimal


def _decision_utc(decision_time: pd.Timestamp) -> pd.Timestamp:
    decision = pd.Timestamp(decision_time)
    return (
        decision.tz_localize("UTC")
        if decision.tzinfo is None
        else decision.tz_convert("UTC")
    )


def _available(frame: pd.DataFrame, decision_time: pd.Timestamp) -> pd.DataFrame:
    decision = _decision_utc(decision_time)
    work = frame.copy()
    work["available_time"] = pd.to_datetime(
        work["available_time"],
        utc=True,
        errors="raise",
    )
    return work[work["available_time"] <= decision].copy()


def carry_proxy_scores(
    rates: pd.DataFrame,
    decision_time: pd.Timestamp,
) -> pd.Series:
    usable = _available(rates, decision_time)
    usable = usable[
        usable["availability_verified"].eq(True)
    ].dropna(subset=["value", "unit"])
    latest = usable.sort_values("timestamp").groupby("currency").tail(1)
    values = {
        str(row["currency"]): percent_to_decimal(
            float(row["value"]),
            str(row["unit"]),
        )
        for _, row in latest.iterrows()
    }
    return pd.Series(values, dtype=float).sort_index()


def momentum_12_1_scores(
    currency_returns: pd.DataFrame,
    decision_time: pd.Timestamp,
) -> pd.Series:
    usable = _available(currency_returns, decision_time)
    values: dict[str, float] = {}
    decision = _decision_utc(decision_time)
    for currency, group in usable.sort_values("timestamp").groupby("currency"):
        history = group[group["timestamp"] < decision].tail(12)
        window = history.iloc[:-1]
        if len(window) == 11 and window["return"].notna().all():
            values[str(currency)] = float((1.0 + window["return"]).prod() - 1.0)
    return pd.Series(values, dtype=float).sort_index()


def reer_value_5y_scores(
    reer: pd.DataFrame,
    decision_time: pd.Timestamp,
) -> pd.Series:
    usable = _available(reer, decision_time)
    values: dict[str, float] = {}
    for currency, group in usable.sort_values("timestamp").groupby("currency"):
        history = group.dropna(subset=["value"]).tail(61)
        if len(history) != 61:
            continue
        current = float(history.iloc[-1]["value"])
        trailing_mean = float(history.iloc[:-1]["value"].mean())
        if trailing_mean > 0:
            values[str(currency)] = -(current / trailing_mean - 1.0)
    return pd.Series(values, dtype=float).sort_index()


def centered_rank_scores(values: pd.Series) -> pd.Series:
    clean = values.dropna().astype(float)
    if len(clean) < 2:
        return pd.Series(dtype=float)
    ranks = clean.rank(method="average")
    return ((ranks - 1.0) / (len(clean) - 1.0) * 2.0 - 1.0).sort_index()


def blend_rank_scores(parts: dict[str, pd.Series]) -> pd.Series:
    if not parts:
        return pd.Series(dtype=float)
    joined = pd.concat(parts, axis=1, join="inner").dropna(how="any")
    return joined.mean(axis=1).sort_index()
