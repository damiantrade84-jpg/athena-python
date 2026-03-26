# Athena Scoring Diagnostics Report

Generated from current code/config in:
- `factor_scoring.py`
- `forex_scoring.py`
- `scoring.py`
- `market_structure.py`
- `engine_c.py`
- `scalp_engine.py`
- `config.yaml`

Notes:
- "Expected pass rate" below is a code-derived design estimate, not a measured live/backtest statistic.
- Where the engine applies structural/checklist gates in addition to score thresholds, the real pass rate will be lower than the score-band estimate.

## Engine A

Engine A has two live scoring branches:
- Non-forex: factor engine in `factor_scoring.py`
- Forex: dedicated rules engine in `forex_scoring.py`

### A1. Non-Forex Factor Engine

Score distribution:
- `final_score` range: `0.0` to theoretical `3.0`
- Practical formula: `abs(dir_score) * quality_mult * directional_confidence_multiplier`
- `dir_score`: weighted directional average, bounded by indicator clamp range and factor aggregation
- `quality_mult`: `0.6` to `1.0`
- `directional_confidence_multiplier`: `0.0` to `<1.0`, logistic around the directional threshold

Threshold gates:
- Static scan gate:
  - crypto: `1.50`
  - commodity: `1.65`
  - stock: `1.85`
  - index: `1.85`
- Optional subgroup gates override class gate when present:
  - crypto_btc / crypto_eth: `1.60`
  - commodity range: `1.55` to `1.75`
  - index range: `1.75` to `1.95`
  - stock range: `1.75` to `1.95`
- Directional gate inside factor engine:
  - global `FACTOR_MIN_DIRECTIONAL = 0.25`
  - crypto `FACTOR_MIN_DIRECTIONAL_CRYPTO = 0.15`
  - effective threshold softens slightly when optional directional feeds are missing
- Quantile gate:
  - enabled for non-crypto when sample count `>= 5`
  - top fraction target: `20%`
  - effective scan threshold becomes `max(static_threshold, percentile_cut)`

Expected pass rate:
- Static threshold only, uniform score-band estimate:
  - crypto `>=1.50/3.0`: about `50%`
  - commodity `>=1.65/3.0`: about `45%`
  - stock/index `>=1.85/3.0`: about `38%`
- With scan quantile enabled, non-crypto scan pass rate is designed to compress toward about `20%` of candidates per scan when enough names are present.
- Real pass rate is lower because signals can still fail the directional gate or have no active directional factors.

Maximum theoretical score:
- UI/API max score: `3.0`
- This is the declared cap used by Engine A non-forex signals.

Normalization bounds:
- Indicator normalization clamp: typically `[-3.0, +3.0]`
- Nondirectional normalization inside final-score formula: `nondir_score / 3.0`, clamped to `[0, 1]`
- Rolling normalization lookback:
  - crypto: `300`
  - commodity: `300`
  - stock: `350`
  - index: `350`
- UI `confluencePct` is not a straight `score/maxScore` mapping:
  - it anchors the active scan threshold to `67%`
  - formula in `athena.py`: `(score / threshold) * 67`, clamped to `[0, 100]`

### A2. Forex Engine

Score distribution:
- `final_score` range: `0.0` to `1.0`
- Final score is capped at `1.0`
- Components are rules-based and already expressed on a `0.0` to `1.0` scale

Threshold gates:
- Live scan threshold: `MIN_FOREX_CONFLUENCE = 0.70`
- Class threshold mirrors this: `MIN_CONFLUENCE_CLASS.forex = 0.70`
- Trend gate must pass first:
  - D1/H4 EMA alignment
  - ADX minimum from `FOREX_ENGINE.trend_gate_adx_min` defaulting to `20.0`
- Session gate:
  - London and NY for all forex pairs
  - Asian session only for selected JPY/AUD/NZD crosses

Expected pass rate:
- Threshold-only, uniform score-band estimate: `>=0.70/1.0` implies about `30%`
- Real pass rate is lower because the trend gate and session gate are hard prerequisites.

Maximum theoretical score:
- `1.0`

Normalization bounds:
- Final score clamp: `[0.0, 1.0]`
- Robust MAD z-score helper clamp for normalized internals: `[-3.0, +3.0]`
- Engine output stays on a `0.0` to `1.0` scale

## Engine B

Engine B is the naked structure engine. Its score is a checklist count, not a z-score.

Score distribution:
- Raw `score` is the count of passed confirmations
- `max_possible` depends on style:
  - scalp/intraday: base 5 checks + up to 2 bonus rows = max `7`
  - swing: base 6 checks + up to 2 bonus rows = max `8`
- `pct = score / max_possible * 100`

Threshold gates:
- Two gates apply:
  - raw score must be at or above the style/regime gate
  - boolean `passed` checklist must also be `True`
- Style thresholds from current `config.yaml`:
  - scalp: `1.5`
  - intraday: `2.0`
  - swing: `2.5`
- Regime multipliers used live:
  - TRENDING: `0.85`
  - RANGING: `1.20`
  - HIGH_VOLATILITY: `1.30`
  - LOW_VOLATILITY: `1.00`
- Effective min score examples:
  - scalp: `1.28` / `1.80` / `1.95` / `1.50`
  - intraday: `1.70` / `2.40` / `2.60` / `2.00`
  - swing: `2.12` / `3.00` / `3.25` / `2.50`
- Checklist pass requires, at minimum:
  - structure ok
  - location ok
  - trigger or valid breakout catalyst
  - RR ok
  - macro ok when required

Expected pass rate:
- Score-only uniform estimate by style/regime:
  - scalp on max `7`: about `72%` to `82%`
  - intraday on max `7`: about `63%` to `76%`
  - swing on max `8`: about `59%` to `74%`
- Real pass rate is materially lower because `passed=True` is a second hard gate and RR/location/trigger failures are common.

Maximum theoretical score:
- scalp/intraday: `7`
- swing: `8`

Normalization bounds:
- Raw score: discrete `0` to `7/8`
- Percent score: `[0, 100]`
- No z-score normalization is used in Engine B itself

## Engine C

Engine C is the consensus layer combining Engine A and Engine B.

Score distribution:
- Output conviction range: `0.0` to `1.0`
- A and B are both normalized to `0.0` to `1.0` before combination
- Consensus conviction is capped at `1.0`

Threshold gates:
- Tier thresholds:
  - HIGH: `>= 0.70`
  - MEDIUM: `>= 0.50`
  - LOW: `>= 0.35`
  - SKIP: `< 0.35`
- Additional route-specific gates:
  - A-only conviction = `A_norm * 0.60`
  - B-only conviction = `B_norm * ENGINE_C_B_ONLY_MULT` with current multiplier `0.65`
  - Conflict override requires:
    - `B_norm >= 0.70`
    - `A_norm <= 0.45`
    - penalty multiplier `0.85`
  - Final RR floor:
    - aligned path tries to keep resolved RR `>= 1.0`
    - B-only / override path is skipped if resolved RR `< 1.0`

Expected pass rate:
- On the raw conviction band alone:
  - HIGH: top `30%`
  - MEDIUM: next `20%`
  - LOW: next `15%`
  - SKIP: bottom `35%`
- Route-specific threshold equivalents:
  - A-only needs `A_norm >= 0.583`
  - B-only needs `B_norm >= 0.538` before RR filtering
  - B conflict override minimum implied conviction is `0.70 * 0.85 = 0.595`
- Real pass rate is lower because Engine C also needs signal availability, direction agreement or valid override, and acceptable RR.

Maximum theoretical score:
- Conviction max: `1.0`
- Vision modifiers can boost conviction, but it is clamped back to `1.0`

Normalization bounds:
- Engine A input normalized to `[0, 1]`
- Engine B input normalized to `[0, 1]`
- Consensus conviction normalized to `[0, 1]`

## Scalp

This is the dedicated M15/M5 scalp engine.

Score distribution:
- Raw AI quality score range: `0` to `100`, then clamped
- Exposed `confluenceScore` = `ai_score / 100.0`
- Exposed `maxScore` = `1.0`
- Practical additive components:
  - zone quality: up to `40`
  - session quality: up to `20`
  - trigger quality: up to `20`
  - momentum quality: up to `20`
  - spread adjustment: `+5` to `-5`
- Unclamped additive max is `105`, but exposed score is clamped to `100`

Threshold gates:
- Hard pre-score filters:
  - UTC session gate: London `07:00–16:00`, New York `13:00–21:00`
  - spread gate
  - higher-timeframe bias gate when `WITH_TREND_ONLY = true`
  - valid M15 zone
  - valid M5 trigger
  - valid momentum confirmation
- Auto-execute grade gate:
  - `MIN_GRADE_AUTO_EXECUTE = B`
  - score bands:
    - A: `80–100`
    - B: `60–79`
    - C: `40–59`
    - D: `<40`

Expected pass rate:
- There is no separate numeric scan threshold after a scalp setup is generated; score mainly ranks and grades the already-filtered setup.
- Auto-execute grade-band estimate:
  - B-or-better (`>=60`): about `41%` of the 0–100 band
  - A-only (`>=80`): about `21%` of the 0–100 band
- Real scan pass rate is much lower because session/spread/zone/trigger/momentum/trend filters happen before the score exists.

Maximum theoretical score:
- Exposed max score: `100`
- Exposed normalized max: `1.0`

Normalization bounds:
- Raw AI score clamp: `[0, 100]`
- Normalized signal score: `[0.0, 1.0]`

## Quick Comparison

| Engine | Raw score range | Normalized output range | Primary live threshold | Max theoretical score |
|---|---:|---:|---:|---:|
| Engine A non-forex | `0.0–3.0` | UI percent is threshold-anchored, not direct max scaling | `1.50–1.85` class/group dependent | `3.0` |
| Engine A forex | `0.0–1.0` | `0.0–1.0` | `0.70` | `1.0` |
| Engine B | `0–7` or `0–8` | `0–100%` | style/regime `1.28–3.25` plus checklist pass | `7` / `8` |
| Engine C | `0.0–1.0` conviction | `0.0–1.0` | `0.35` LOW tier | `1.0` |
| Scalp | `0–100` AI quality | `0.0–1.0` | no scan score gate; auto-exec `>=60` (`B`) | `100` / `1.0` |
