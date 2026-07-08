"""Optional vectorbt acceleration layer (Part 3) — provisional only."""

from __future__ import annotations

from typing import Any

VECTORBT_ASSUMPTION_MISMATCHES = [
    "vectorbt uses close-to-close fills; Athena uses next-bar open + slippage",
    "vectorbt ignores scale-out TP1/runner BE logic in Engine B",
    "vectorbt does not apply ENGINE_B_BT_RUNNER_REQUIRES_BREAK",
    "vectorbt same-bar SL/TP uses configurable but simplified OHLC logic",
    "vectorbt ignores forex session gates and structure funnel",
]


def vectorbt_available() -> bool:
    try:
        import vectorbt  # noqa: F401

        return True
    except ImportError:
        return False


def run_provisional_sweep(
    ohlc_df,
    *,
    symbol: str,
    signal_tf: str,
    entry_tf: str,
    sl_atr_mult: float = 2.0,
    tp_atr_mult: float = 4.0,
) -> list[dict[str, Any]]:
    """Fast provisional parameter sweep — NOT final validation."""
    if not vectorbt_available():
        return []

    import numpy as np
    import pandas as pd

    try:
        import vectorbt as vbt
    except ImportError:
        return []

    close = ohlc_df["close"].astype(float)
    high = ohlc_df["high"].astype(float)
    low = ohlc_df["low"].astype(float)
    atr = (high - low).rolling(14).mean()
    entries = close > close.shift(1)
    exits = close < close.shift(1)
    sl_stop = (atr * sl_atr_mult / close).fillna(0.02)
    tp_stop = (atr * tp_atr_mult / close).fillna(0.04)

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        sl_stop=sl_stop,
        tp_stop=tp_stop,
        freq="1h" if entry_tf == "H1" else "4h",
    )
    trades = pf.trades.records_readable
    if trades is None or len(trades) == 0:
        return [{
            "symbol": symbol,
            "signal_tf": signal_tf,
            "entry_tf": entry_tf,
            "sl_atr_mult": sl_atr_mult,
            "tp_atr_mult": tp_atr_mult,
            "trade_count": 0,
            "avg_r": 0.0,
            "status": "provisional_no_trades",
            "assumption_mismatches": "|".join(VECTORBT_ASSUMPTION_MISMATCHES),
        }]

    returns = trades["Return"].astype(float) if "Return" in trades.columns else pd.Series([0.0])
    return [{
        "symbol": symbol,
        "signal_tf": signal_tf,
        "entry_tf": entry_tf,
        "sl_atr_mult": sl_atr_mult,
        "tp_atr_mult": tp_atr_mult,
        "trade_count": len(trades),
        "avg_r": round(float(returns.mean()), 4) if len(returns) else 0.0,
        "win_rate": round(float((returns > 0).mean()), 4) if len(returns) else 0.0,
        "status": "provisional",
        "assumption_mismatches": "|".join(VECTORBT_ASSUMPTION_MISMATCHES),
    }]
