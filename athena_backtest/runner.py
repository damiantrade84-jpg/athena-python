"""Single-pair and universe orchestration for the rebuilt backtester."""

from __future__ import annotations

import time

from athena_backtest.data.store import ParquetCandleStore


def run_pair(
    pair: dict,
    *,
    engine: str,
    horizon: str,
    store: ParquetCandleStore | None = None,
    n_trials_hint: int = 1,
) -> dict:
    store = store or ParquetCandleStore()
    engine = engine.upper()
    started = time.perf_counter()
    if engine == "A":
        from athena_backtest.engines.engine_a import run_engine_a_backtest

        result = run_engine_a_backtest(pair, horizon=horizon, store=store)
    elif engine == "B":
        from athena_backtest.engines.engine_b import run_engine_b_backtest

        result = run_engine_b_backtest(pair, style=horizon, store=store)
    else:
        raise ValueError(f"unsupported engine: {engine!r}")
    result["wallTimeSec"] = round(time.perf_counter() - started, 3)

    # Additive Stage 6 disclosure: honest OOS/validation metrics over the
    # produced trade list. Never alters which trades were generated.
    trades = result.get("trades")
    if isinstance(trades, list) and trades and "error" not in result:
        try:
            from athena_backtest.metrics import summarize_trades
            from athena_backtest.validation import validation_block

            n_trials = int(n_trials_hint) if n_trials_hint else 1
            result["metricsV3"] = summarize_trades(trades, n_trials=n_trials)
            result["validation"] = validation_block(trades, n_trials=n_trials)
        except Exception as exc:  # disclosure must not break the run result
            result["validationError"] = type(exc).__name__
    return result
