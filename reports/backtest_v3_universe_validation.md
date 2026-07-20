# Backtest v3 — Universe Validation Run (Stage 10)

Run date: 2026-07-20 (UTC). Driver: 3-process pool over all 116 enabled pairs,
Engine A then Engine B, style `intraday`, full stored history per symbol,
every result persisted to `backtest_runs_v3` (audit.db). Commits under test:
`4d4812ef` (rebuild, stages 1–8) + `be7113ef` (legacy archival, stage 9).

## Timing

| Phase | Symbols | Wall time | Median / symbol | Max / symbol |
|---|---|---|---|---|
| Engine A | 116 | 62.1 min | 23.4 s | 698.8 s (yfinance-sourced indices) |
| Engine B | 116 | 58.9 min | 82.4 s | 424.4 s |

Old system reference: a single Engine A symbol took ~8 min (479 s) before the
rebuild and Engine B exceeded 60–75 min per symbol; a universe run of this
shape was not practically runnable at all. Single-symbol targets from the
plan: Engine A < 60 s (achieved: 28.6 s EUR/USD, byte-identical metrics to
the pre-optimization scorer output) and Engine B < 5 min (achieved: 132 s
BTCUSDT at the default 180-day bounded replay).

## Engine A results (116/116 evaluated, 0 errors)

- Verdicts: 31 HOLDOUT_POSITIVE / 85 HOLDOUT_NEGATIVE (temporal 30% holdout,
  purged; verdict floor n≥30).
- 19/116 symbols have positive total R over full history.
- Top by SQN: AMD 2.23 (512 trades, deflatedSqn −0.19), Cattle 1.80 (160),
  PLTR 1.75 (346), EEM 1.55 (409), INTC 1.20 (484).
- Every deflated SQN is ≤ 0 once the 116-trial multiple-testing correction is
  applied — no symbol survives selection-bias adjustment on this run. This is
  the honest read the old validator never produced.
- Bottom: NVDA −18.0 SQN (1067 trades), EUR/GBP −8.5, USD/SGD −7.2.

## Engine B results (116/116 evaluated, 0 errors, 113 zero-trade)

Only BTC/USDT, EUR/USD, and USD/CHF produced trades (all HOLDOUT_NEGATIVE,
SQN −4.1 to −4.9). The other 113 zero-trade results are a **data-coverage
gap, not a gate finding**: Engine B intraday requires stored M30/M15 trigger
bars and only BTCUSDT (Binance) plus the MT5 majors with intraday depth were
topped up during Stage 5. The store fails closed on missing timeframes — it
never substitutes another TF. Follow-up to make the Engine B leaderboard
meaningful: run the ETL M30/M15 top-up across the crypto + MT5 universe
(`python -m athena_backtest.cli` etl refresh), then rerun Engine B.

## Comparability disclosures (attached to every result)

- `unreplayed`: live_quote_gates, microstructure_context, dxy_h4_closes,
  zone_registry_persistence, runner_trail_and_timed_exit_variants,
  spread_and_session_speed_inputs.
- `tfWindowBars` D1/H4/H1=1000, M30/M15=500 — identical to live
  `scan_candle_limits()`; the backtest evaluates exactly the data depth live
  sees (this is a parity improvement over full-history prefixes).
- Engine B `replayWindowBounded`: default 180 days
  (`ENGINE_B_BT_MAX_REPLAY_DAYS`, per asset type), disclosed with
  `replayStartIndex`.
- `sameBarPolicy`: ADVERSE_SL_FIRST; exit sim granularity = iteration TF.
- Validation type: `rolling_oos_replay_no_refit` (replay, not re-fitting —
  labeled honestly); deflated SQN uses trialCount = 116 (whole universe).

## Persistence / UI

- 232 rows in `backtest_runs_v3` (116 A + 116 B); `backtest_trades_v3` holds
  per-trade rows. Legacy `backtest_results` untouched; `*_v2` tables remain
  exclusively owned by `athena_backtesting_v2`.
- `/api/backtest-history`, `/api/backtest-history/<pair>`, `/api/backtest-best`
  verified rendering (200, legacy field aliases run_date/pair/trades/expectancy
  preserved for panel code).

## Interpretation

The rebuild's job was speed + honesty, not edge discovery. On that basis:
Engine A shows a small cohort of positive-holdout symbols (equities/commodity
tilt) but nothing that survives deflation at universe trial count; Engine B
(where data exists) is negative after realistic costs, consistent with the
2026-06-24 "no edge" assessment. Recommended next research steps: Engine B
intraday data top-up + rerun, then per-score-group calibration on the Engine A
HOLDOUT_POSITIVE cohort with the trial registry tracking every variant.
