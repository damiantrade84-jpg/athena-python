# Athena Scoring Diagnostics Report — Measured History

Source database: `audit.db`

Tables used:
- `audit_log`
- `backtest_results`
- `shadow_signals`

Measurement date:
- 2026-03-26

## Read This First

This report uses stored history, not design-time formulas.

Important limits:
- There is no single table that stores every rejected scan candidate for every engine.
- Because of that, a true end-to-end "scan pass rate" is only available where the database has both accepted and rejected observations.
- `audit_log` also contains legacy rows from older scoring versions, so current-era calculations are filtered where possible.
- For Engine C, `shadow_signals` stores accepted consensus rows only, not rejected candidates.

Interpretation:
- "Measured distribution" = what actually got written to history
- "Measured threshold-hit rate" = within the persisted sample only
- "True scan pass rate" = only reported when the denominator is trustworthy; otherwise marked unavailable

## Data Inventory

- `audit_log`: 832 rows
- `backtest_results`: 309 rows
- `shadow_signals`: 399 rows

## Engine A

### Measured history available

Backtest engine rows:
- `factor_scoring`: 163 runs across 65 pairs
- `forex_scoring`: 44 runs across 17 pairs

Current-era persisted signal sample:
- non-forex rows with `max_score = 3.0`: 24
- forex rows with `max_score = 1.0`: 19

Data-quality note:
- Forex `audit_log` also contains legacy rows on an older 0–3 style scale, so only `max_score = 1.0` rows are treated as current-era forex.

### Measured score distribution

Current-era persisted Engine A sample:

| Slice | N | P25 | Median | P75 |
|---|---:|---:|---:|---:|
| Non-forex current-era sample | 24 | 0.8700 | 1.1450 | 1.3900 |
| Forex current-era sample | 19 | 1.0000 | 1.0000 | 1.0000 |

### Measured threshold-hit rate

This is threshold-hit rate inside the persisted current-era sample, not full scan pass rate.

| Asset class | N | Threshold-hit N | Threshold-hit % |
|---|---:|---:|---:|
| crypto | 13 | 4 | 30.8% |
| commodity | 2 | 0 | 0.0% |
| stock | 2 | 1 | 50.0% |
| index | 7 | 0 | 0.0% |
| forex | 19 | 19 | 100.0% |

These numbers are not suitable as global live pass rates because the stored sample is already filtered and small for several asset classes.

### Backtest outcome summary

| Engine A branch | Runs | Pairs | Avg trades/run | Avg win rate | Trade-weighted win rate | Avg eval_threshold |
|---|---:|---:|---:|---:|---:|---:|
| factor_scoring | 163 | 65 | 23.75 | 44.85% | 48.48% | 0.76 |
| forex_scoring | 44 | 17 | 42.68 | 39.28% | 36.37% | 0.59 |

### True scan pass rate

Unavailable from current storage.

Reason:
- rejected Engine A scan candidates are not stored in a uniform denominator table
- persisted `audit_log` rows are a filtered subset, not the full scan stream

## Engine B

### Measured history available

Backtest engine rows:
- `naked_engine`: 102 runs across 17 pairs

Style extraction from `backtest_results.notes`:
- intraday: 62 runs
- scalp: 20 runs
- swing: 20 runs

### Measured score distribution

Direct raw score distribution for all Engine B scans is not stored centrally.

What is available:
- backtest run-level trade counts
- backtest run-level win rates
- configured `eval_threshold` captured per run

### Backtest outcome summary

Overall:

| Runs | Pairs | Avg trades/run | Avg win rate | Trade-weighted win rate | Avg eval_threshold | Min eval_threshold | Max eval_threshold |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 102 | 17 | 114.00 | 38.95% | 38.00% | 3.01 | 1.50 | 4.00 |

By style:

| Style | Runs | Avg trades/run | Min trades | Max trades | Avg win rate | Trade-weighted win rate | Avg eval_threshold |
|---|---:|---:|---:|---:|---:|---:|---:|
| intraday | 62 | 78.73 | 19 | 230 | 38.55% | 37.70% | 3.24 |
| scalp | 20 | 212.15 | 8 | 1988 | 49.73% | 42.96% | 3.00 |
| swing | 20 | 125.20 | 17 | 245 | 29.41% | 30.17% | 2.33 |

### Measured threshold-hit rate

Unavailable as a true pass rate.

Reason:
- the database stores backtest run summaries, not every accepted and rejected Engine B setup
- there is no reliable per-candidate denominator in `audit.db` for Engine B

## Engine C

### Measured history available

`shadow_signals` contains 399 consensus rows.

Important scope note:
- these are accepted consensus signals only
- current table contents are all `verdict = ALIGNED`
- rejected or conflicting consensus attempts are not stored here

### Measured conviction distribution

| N | Avg conviction | Min | P25 | Median | P75 | Max |
|---:|---:|---:|---:|---:|---:|---:|
| 399 | 0.6513 | 0.3634 | 0.5500 | 0.6300 | 0.7655 | 1.0000 |

Tier distribution:

| Tier band | N | Share |
|---|---:|---:|
| HIGH (`>=0.70`) | 168 | 42.1% |
| MEDIUM (`0.50–0.69`) | 150 | 37.6% |
| LOW (`0.35–0.49`) | 81 | 20.3% |

Measured RR distribution on accepted shadow signals:

| Avg RR | P25 | Median | P75 |
|---:|---:|---:|---:|
| 10.79 | 3.77 | 7.32 | 11.91 |

### Measured threshold-hit rate

Within `shadow_signals`, threshold-hit rate is trivially 100% because only accepted consensus rows are stored.

That is not a true Engine C scan pass rate.

### True scan pass rate

Unavailable from current storage.

Reason:
- rejected Engine C candidates are not stored alongside accepted ones
- `shadow_signals` is an accepted-signal ledger, not a full denominator table

## Scalp

### Measured history available

`audit_log` rows with `style = 'scalp'`: 56

Closed scalp trades:
- 32

Grade mix in stored scalp rows:
- `EXECUTED`: 32
- `MANUAL-ERR`: 17
- `A+`: 5
- `B`: 2

Data-quality note:
- this is execution/audit history, not a full scalp scan candidate log
- many rejected scalp setups never enter `audit_log`

### Measured score distribution

Stored scalp audit rows:

| N | Avg score | Min | Max |
|---:|---:|---:|---:|
| 56 | 2.2295 | 0.4000 | 6.0000 |

This stored score field is mixed execution history and is not a clean scalp-candidate score distribution.

### Measured closed-trade outcomes

Closed scalp trades:

| N | Win N (`pnl > 0`) | Win % | Positive R N | Avg R | Min R | Max R | Avg PnL |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 32 | 16 | 50.0% | 16 | 0.2781 | -1.6100 | 2.0600 | 21.4603 |

### Measured threshold-hit rate

Unavailable as a true scan pass rate.

Reason:
- stored scalp rows are execution/audit records, not every generated scalp candidate
- there is no full candidate-denominator table for rejected scalp setups

## Bottom Line

What is measured cleanly right now:
- Engine A backtest run outcomes
- Engine B backtest run outcomes
- Engine C accepted consensus conviction/tier distribution
- Scalp closed-trade outcomes from audit history

What is not measurable cleanly right now:
- true live scan pass rate for Engine A
- true live scan pass rate for Engine B
- true live scan pass rate for Engine C
- true live scan pass rate for Scalp

Why not:
- the current database does not persist a complete accepted+rejected candidate stream for every engine

If you want the next step, I can add lightweight telemetry so every engine writes:
- candidate generated
- threshold passed/failed
- rejection reason
- final tradeable yes/no

That would let us produce true measured pass-rate dashboards instead of inferred or accepted-only distributions.
