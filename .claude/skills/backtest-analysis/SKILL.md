---
name: backtest-analysis
description: >
  Backtest running, debugging, and interpretation for Athena. Use this skill whenever
  the user asks to run a backtest, analyze backtest results, debug Engine A or C backtest
  reliability, investigate timeout rates, compare live vs backtest parity, or evaluate
  whether a strategy has edge. Also trigger for "why is the hit rate low", "check Engine A
  results", "Engine C timeouts", "backtest_runner.py issues", or any question about
  whether a scanner/engine result is statistically meaningful.
---

# Athena Backtest Analysis

## Key Files

- `backtest_runner.py` — main entry point, orchestrates engine-specific runs
- `backtest.py` — legacy backtest harness
- `backtest_candle_cache.py` — OHLCV cache (candle_cache.db)
- `run_backtest.py` — CLI runner
- `research_validation.py` — strategy validation layer

## Before Interpreting Results

Always check:
1. **Candle source:** Was the same feed used for both signal generation and fill simulation?
2. **Forming-bar lookahead:** Was the signal scored on a closed bar or a forming bar?
3. **Timeframe key:** Does the engine output's timeframe key match what execution.py expects? (Known bug: Engine A/C pipeline mismatch for swing-style forex — D1 vs H4 keys)
4. **Score threshold:** Was `AUTO_TRADE_MIN_SCORE` (the dead config key) confused with the active threshold? Check the actual gate being tested.
5. **Signal count vs trade count:** Low trades with many signals = fill logic bug or RR filter too aggressive

## Engine A Backtest Checklist

- Hit rate near 50%: likely noise, not edge — confirm with chi-squared test (>200 samples minimum)
- combinedConviction cap: Engine A-only signals structurally cap below auto-trade gate — verify this is not the cause of 0 auto-trades
- Structure-first entry model: Engine B structural confirmation required before Engine A score gate triggers
- Forming-bar lookahead: verify signal is scored on `iloc[-2]` (closed), not `iloc[-1]` (forming)

## Engine C Backtest Checklist

- Timeout rate > 10%: check `_monitor_fill_index` bisect call — type mismatch between float price and list of dicts (known fixed bug — verify fix is in place)
- Trust verdict distribution: if trust_neither > 40%, likely signal quality issue upstream in A or B

## Statistical Validity Gates

- Minimum 200 closed trades for directional hit-rate conclusions
- Minimum 500 trades for Sharpe/expectancy claims
- Always separate long vs short hit rates — aggregated can mask directional bias
- Report: trades, win_rate, avg_r, expectancy, max_drawdown_r, profit_factor

## Debugging Workflow

1. Run backtest with `--verbose` or `--debug` flag
2. Check for `SKIP` / `NO_FILL` / `TIMEOUT` in logs — count and categorize
3. Dump first 10 signal records: confirm field presence and types
4. Verify candle alignment: signal bar close timestamp vs fill bar open timestamp
5. For Engine A: dump `factor_score_detail` for 5 sample signals — confirm normalization
