---
name: backtest-analysis
description: >
  Explicit backtest-analysis tasks for Athena only. Use when the user asks to run,
  debug, interpret, or compare backtest results, or asks whether a backtest result is
  statistically meaningful.
---

# Athena Backtest Analysis

## Scope

Use this skill only for explicit backtest-analysis tasks. Do not use it for normal Engine A/B/C/D fixes unless the user asks to run or interpret backtests.

Backtest findings are diagnostic and must not be directly applied to live thresholds without live-specific gating review.

Historical notes about Engine A requiring Engine B confirmation, Engine A/C timeframe mismatches, or old structure-first experiments must be treated as historical until re-verified in current code and config.

## Key Files

- `backtest_runner.py` - main entry point, orchestrates engine-specific runs.
- `backtest.py` - legacy backtest harness.
- `backtest_candle_cache.py` - OHLCV cache (`candle_cache.db`).
- `run_backtest.py` - CLI runner.
- `research_validation.py` - strategy validation layer.

## Before Interpreting Results

Always check candle source, forming-bar lookahead, timeframe key, active score threshold, and signal count versus trade count.

## Statistical Validity Gates

- Minimum 200 closed trades for directional hit-rate conclusions.
- Minimum 500 trades for Sharpe/expectancy claims.
- Separate long and short hit rates; aggregated rates can mask directional bias.
- Report trades, win_rate, avg_r, expectancy, max_drawdown_r, and profit_factor.

## Debugging Workflow

1. Run the smallest relevant backtest with `--verbose` or `--debug` when available.
2. Check `SKIP`, `NO_FILL`, and `TIMEOUT` logs; count and categorize.
3. Dump representative signal records to confirm field presence and types.
4. Verify candle alignment: signal bar close timestamp versus fill bar open timestamp.
5. For Engine A, inspect representative `factor_score_detail` output and confirm normalization.
