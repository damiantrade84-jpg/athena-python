from __future__ import annotations

import numpy as np
import pandas as pd


CANONICAL_PAIR = {
    "EUR": ("EURUSD", 1.0),
    "GBP": ("GBPUSD", 1.0),
    "JPY": ("USDJPY", -1.0),
    "AUD": ("AUDUSD", 1.0),
    "NZD": ("NZDUSD", 1.0),
    "CAD": ("USDCAD", -1.0),
    "CHF": ("USDCHF", -1.0),
    "ZAR": ("USDZAR", -1.0),
    "MXN": ("USDMXN", -1.0),
    "SGD": ("USDSGD", -1.0),
    "BRL": ("USDBRL", -1.0),
    "INR": ("USDINR", -1.0),
}


def build_currency_weights(
    scores: pd.Series,
    *,
    top_n: int,
    min_currencies: int,
) -> pd.Series:
    clean = scores.dropna().astype(float).sort_values()
    if len(clean) < min_currencies:
        raise ValueError("INSUFFICIENT_UNIVERSE_BREADTH")
    short = clean.head(top_n).index
    long = clean.tail(top_n).index
    weights = pd.Series(0.0, index=clean.index)
    weights.loc[long] = 0.5 / top_n
    weights.loc[short] = -0.5 / top_n
    if weights.abs().max() > 0.25 or abs(float(weights.sum())) > 1e-12:
        raise ValueError("INVALID_EXPOSURE")
    return weights[weights.ne(0)].sort_index()


def map_currency_weights_to_pairs(weights: pd.Series) -> pd.Series:
    pair_weights: dict[str, float] = {}
    for currency, weight in weights.items():
        if currency == "USD":
            continue
        pair, orientation = CANONICAL_PAIR[str(currency)]
        pair_weights[pair] = pair_weights.get(pair, 0.0) + float(weight) * orientation
    return pd.Series(pair_weights, dtype=float).sort_index()


def scale_weights_to_vol(
    weights: pd.Series,
    prior_portfolio_returns: pd.Series,
    *,
    target_vol: float,
    lookback: int,
    max_gross: float,
) -> pd.Series:
    history = prior_portfolio_returns.dropna().tail(lookback)
    if len(history) != lookback:
        raise ValueError("INSUFFICIENT_HISTORY")
    annualized = float(history.std(ddof=1) * np.sqrt(252.0))
    if not np.isfinite(annualized) or annualized <= 0:
        raise ValueError("INVALID_VOLATILITY")
    gross = float(weights.abs().sum())
    if gross <= 0:
        raise ValueError("INVALID_EXPOSURE")
    multiplier = min(target_vol / annualized, max_gross / gross)
    return weights * multiplier
