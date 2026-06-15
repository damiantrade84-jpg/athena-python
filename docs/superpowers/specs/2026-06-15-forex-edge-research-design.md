# Forex Edge Research Design

**Date:** 2026-06-15

**Status:** Approved design, pending implementation plan

## 1. Objective

Build two isolated, reproducible forex research tracks:

1. a monthly cross-sectional currency portfolio study using momentum, real
   exchange-rate value, and a clearly labeled policy-rate carry proxy; and
2. an intraday fixing-window study using executable bid and ask prices.

The first delivery includes read-only data ingestion, data-quality reporting,
one pre-registered bounded backtest for each track, validation artifacts, and
Markdown/JSON reports.

This project does not change Engine A, Engine B, Engine C, Engine D, ASE,
production scoring, thresholds, exits, risk controls, broker integration, or
execution behavior.

## 2. Safety Classification

This is high-risk trading research, but not an execution feature.

Required safety properties:

- Research-only package and CLI.
- No order placement, broker mutation, promotion, shadow, demo, or live
  commands.
- No imports from production execution or risk modules.
- No imports from Engine A scoring or ASE model, gate, registry, promotion,
  shadow, or execution modules.
- Read-only external network operations.
- Credentials remain outside the repository and are never printed, persisted
  in reports, or committed.
- Missing, stale, malformed, delayed, or unverifiable data fails closed.
- A positive result is labeled `RESEARCH_CANDIDATE`, not production-ready.

## 3. System Boundary

Create a standalone package:

```text
athena_research/forex_edge/
  __init__.py
  config.py
  models.py
  universe.py
  store.py
  quality.py
  reporting.py
  validation.py
  sources/
    __init__.py
    bis.py
    cftc.py
    fred.py
    dukascopy.py
  portfolio/
    __init__.py
    signals.py
    construction.py
    costs.py
    backtest.py
  fixing/
    __init__.py
    calendar.py
    windows.py
    costs.py
    backtest.py
```

Create a separate entry point:

```text
forex_edge_cli.py
```

The CLI exposes research operations only:

```text
forex_edge_cli.py ingest-bis
forex_edge_cli.py ingest-cftc
forex_edge_cli.py ingest-fred
forex_edge_cli.py import-dukascopy
forex_edge_cli.py quality-report
forex_edge_cli.py run-portfolio
forex_edge_cli.py run-fixing
forex_edge_cli.py run-both
```

There are no `promote`, `execute`, `trade`, `order`, `demo`, `shadow`, or
broker-account commands.

## 4. Universe And Coverage

The research universe is the 21 forex pairs currently declared in
`athena_ase/universe.py`:

```text
EURUSD GBPUSD USDJPY AUDUSD AUDCHF AUDNZD NZDUSD
EURGBP USDCAD USDCHF EURJPY GBPJPY AUDJPY EURAUD
GBPAUD USDZAR EURCHF USDMXN USDSGD USDBRL USDINR
```

The research package copies these symbols into its own immutable universe
definition at implementation time. It must not import ASE at runtime.

A repository contract test, outside the research package, compares the frozen
list against the current ASE universe. Drift fails that test and requires an
explicit research-universe revision. Runtime reports use the frozen universe
hash and do not import ASE.

Target coverage:

- Daily portfolio data: 2006-01-01 onward.
- M5 bid/ask fixing data: 2015-01-01 onward.
- Quality reporting: all 21 pairs.
- Initial fixing backtest: EURUSD, GBPUSD, and USDJPY.

Pair or currency absence creates an explicit eligibility record. It never
causes silent removal from denominators or reports.

## 5. Data Sources

### 5.1 BIS

Use the official BIS Data Portal SDMX interface for real effective exchange
rate series and related metadata.

Purpose:

- monthly REER value input;
- series labels, units, frequency, and update metadata.

The adapter must:

- accept explicit series identifiers from research configuration;
- preserve raw response bytes;
- record request URL without credentials;
- record retrieval time and response hash;
- normalize observation date, value, unit, and frequency;
- reject ambiguous units or duplicate observations;
- preserve missing observations rather than interpolate them.

BIS states that monthly EER data are released around mid-month. For the first
study, an observation for month `M` is usable no earlier than the final
calendar day of month `M+1`, unless an earlier exact historical release date is
present in the source metadata. This is a conservative availability lag.

No verified historical-vintage archive for BIS REER was identified during
design. The adapter therefore records `revision_history_verified=false`.
Value-only and blended results using this source carry
`NON_PROMOTABLE_REVISION_RISK`, even when their numerical results are
positive.

### 5.2 CFTC

Use official CFTC historical compressed Commitments of Traders datasets.

Purpose:

- weekly non-commercial positioning;
- carry-crowding and risk-state reporting.

COT is a crowding diagnostic only in the first bounded study. It does not
change direction, weight, eligibility, or exposure. A frozen COT-based risk
scaler requires a separate future design and trial registration.

The adapter must:

- use an explicit contract-to-currency mapping;
- preserve report date and publication availability time;
- apply the existing conservative availability convention: report-week
  Tuesday becomes usable no earlier than the following Monday 00:00 UTC;
- reject duplicate contract/report-date rows after normalization;
- expose missing contract mappings as `MISSING_COT_MAPPING`.

### 5.3 FRED And ALFRED

Use official FRED/ALFRED APIs for policy and short-rate series.

Purpose:

- daily H.10 bilateral spot exchange rates;
- policy-rate carry proxy;
- optional macro metadata used only in quality and regime reports.

Requirements:

- API key supplied through environment configuration;
- no key in command output, manifests, URLs stored on disk, exceptions, or
  reports;
- ALFRED real-time periods used where available;
- each observation stores observation date, real-time start, real-time end,
  available time, value, unit, and series identifier;
- each spot series stores its quotation orientation explicitly;
- percent units are converted explicitly to decimal rates at the signal
  boundary, not during raw ingestion;
- series without verified vintage or publication timing are marked
  `UNVERIFIED_AVAILABILITY` and are ineligible for point-in-time testing.

Federal Reserve H.10 bilateral rates are the frozen daily spot source for the
portfolio study. The Federal Reserve states that the previous business week's
daily rates are released on Monday at 16:15 America/New_York, or the following
business day when Monday is a Federal holiday. The normalized
`available_time` follows that release calendar. FRED observation dates alone
must not be treated as same-day availability.

The carry signal built from these rates is always marked `proxy_only`.

### 5.4 Dukascopy

Use files exported through the official Dukascopy Historical Data Export
facility. The first implementation does not automate undocumented endpoints.

Supported imports:

- CSV or delimited tick/bid-ask exports with explicit schema selection;
- M5 bid and ask bars when both executable sides are present;
- tick data normalized to M5 bid and ask bars by the importer.

The importer must:

- require the source file path and declared timezone;
- require explicit column mapping when headers do not match a known schema;
- reject midpoint-only files for the fixing backtest;
- reject nonpositive prices, crossed quotes, duplicate timestamps with
  conflicting values, and timestamps that cannot be localized unambiguously;
- retain raw file hashes and normalized partition hashes;
- never modify the source file.

## 6. Research Data Store

Use a research-only, immutable, partitioned Parquet store. Default location:

```text
%LOCALAPPDATA%/Athena/research/forex_edge/
```

An environment or CLI option may override the root.

Layout:

```text
raw/<source>/<dataset>/<retrieval_id>/
normalized/<dataset>/<symbol_or_series>/<year>/data.parquet
manifests/<dataset>/<version>.json
runs/<run_id>/
```

Raw and normalized datasets are append-only by version. Re-ingesting changed
source data creates a new manifest version; it does not overwrite the prior
version.

Every dataset manifest records:

- schema version;
- source and official source URL;
- retrieval timestamp;
- requested date range;
- actual date range;
- source file or response hashes;
- normalized partition hashes;
- timezone;
- units and conversion policy;
- row counts;
- duplicate and missing counts;
- revision/vintage policy;
- availability-time policy;
- license or usage note;
- configuration hash;
- code commit and dirty-tree metadata;
- eligibility status and reason codes.

Research runs pin exact dataset manifest hashes. A run cannot read "latest"
implicitly after it has started.

## 7. Shared Data Models

Use explicit typed records for:

- `DatasetManifest`
- `EligibilityResult`
- `QualityIssue`
- `CurrencyObservation`
- `BidAskBar`
- `FactorObservation`
- `PortfolioPosition`
- `BacktestResult`
- `ValidationResult`

`EligibilityResult` contains:

```text
eligible: bool
status: ELIGIBLE | INELIGIBLE | BLOCKED_DATA
reason_codes: ordered list
details: structured mapping
```

Initial reason-code vocabulary:

```text
MISSING_SERIES
MISSING_PAIR
MISSING_CURRENCY
MISSING_COT_MAPPING
INSUFFICIENT_HISTORY
INSUFFICIENT_UNIVERSE_BREADTH
STALE_DATA
UNVERIFIED_AVAILABILITY
AMBIGUOUS_UNIT
AMBIGUOUS_TIMEZONE
DUPLICATE_CONFLICT
NONPOSITIVE_PRICE
CROSSED_QUOTE
MIDPOINT_ONLY
EXCESSIVE_GAPS
NO_EXECUTABLE_QUOTE
PROXY_CARRY_ONLY
UNVERIFIED_REVISION_HISTORY
PROXY_TRANSACTION_COSTS
PBO_UNAVAILABLE
```

No reason code is inferred from free-form text.

## 8. Quality Gates

### 8.1 Daily Portfolio Data

A monthly decision date is eligible only when:

- all values used have `available_time <= decision_time`;
- required lookbacks are complete for that currency;
- rate and REER units are known;
- no value is stale beyond its configured frequency tolerance;
- the eligible currency count satisfies the frozen breadth rule;
- currency-leg construction can produce both a long and a short basket.

Initial breadth rule:

- at least 12 eligible currencies;
- exactly four currencies on each side before final exposure caps.

The quality report still covers all 21 pairs even when fewer currencies are
eligible for a given study date.

### 8.2 M5 Bid/Ask Data

A fixing event is eligible only when:

- the required pre-entry, entry, and exit bars exist;
- bid and ask are positive and `ask >= bid`;
- the event contains executable entry and exit quotes;
- no required interval has an unresolved duplicate conflict;
- the fixing calendar resolves to one UTC timestamp;
- the observed spread is finite and below the pre-registered data-error cap.

The data-error cap identifies malformed observations. It is not a strategy
optimization parameter and must be declared in configuration before a run.

## 9. Portfolio Study

### 9.1 Decision Schedule

- Frequency: monthly.
- Decision time: final eligible daily observation of each calendar month.
- Position application: next eligible daily observation after the decision.
- Holding period: until the next monthly rebalance.

Signal calculation uses only observations available by the decision time.

### 9.2 Currency Representation

Pair data is converted to currency-level observations before ranking.

For pair `BASE/QUOTE`:

- a positive base-currency score implies long base and short quote;
- a pair return is decomposed into equal and opposite currency-leg exposure;
- duplicate economic exposure across crosses is aggregated by currency before
  portfolio normalization.

Daily currency returns are derived from the frozen canonical USD pair for each
non-USD currency. Quotation orientation is normalized so a positive return
always means that currency appreciated against USD.

The backtest operates on currency-leg weights. For cost and turnover
accounting, each non-USD currency weight maps only to its canonical USD pair;
USD is the residual leg. Crosses are covered by ingestion and quality reports
but are not introduced as redundant execution routes in the first portfolio
study.

### 9.3 Frozen Signals

#### Policy-Rate Carry Proxy

For currency `c` at decision time `t`:

```text
carry_proxy(c, t) = latest verified decimal short rate available at t
```

Currencies are ranked by that rate. The factor is labeled `proxy_only`.

This is not investable carry because it omits historical forward points,
cross-currency basis, broker financing, and realized rollover.

#### Momentum

For each currency:

```text
momentum_12_1 = cumulative currency return from month t-12 through t-2
```

The latest month is skipped. The currency return series is reconstructed from
available pair returns without using future data.

#### REER Value

For each currency:

```text
value_5y = negative percentage deviation of current REER
           from its trailing 60-month mean
```

A lower REER relative to its trailing mean produces a higher value score.
The trailing mean contains only observations available at the decision time.

### 9.4 Ranking And Blend

For each signal independently:

- rank eligible currencies cross-sectionally;
- transform ranks to centered scores in `[-1, 1]`;
- require at least 12 eligible currencies;
- long the top four and short the bottom four;
- equal weight within each side before volatility and exposure controls.

The only blended configuration is:

```text
blend_score = mean(carry_rank_score, momentum_rank_score, value_rank_score)
```

The blend requires all three component scores for a currency. Missing factor
values are not replaced with zero.

### 9.5 Exposure And Volatility Controls

- Gross currency exposure before volatility scaling: 1.0, split equally
  between long and short sides.
- Target net currency exposure: 0.0.
- Maximum absolute single-currency weight: 0.25.
- Annualized portfolio volatility target: 10%.
- Volatility estimate: trailing 63 eligible daily returns.
- Maximum gross leverage after scaling: 2.0.
- If volatility is unavailable or nonpositive, the date is ineligible.
- Scaling uses prior returns only and is recomputed at each rebalance.

These values are frozen for the first bounded study.

### 9.6 Portfolio Costs

Costs are charged at every rebalance on changed executable pair notional.

The first bounded portfolio study uses one frozen proxy cost schedule copied
from the repository's current research cost assumptions at design time:

```text
major pairs:   1.2 bps spread + 0.6 bps commission = 1.8 bps round trip
other pairs:   3.5 bps spread + 0.6 bps commission = 4.1 bps round trip
```

The seven major pairs are EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD, and
USDCHF. Every other pair in the frozen universe uses the second row.

There is no separately invented slippage estimate in the 1.0x baseline.
Instead, the complete proxy transaction cost is stressed at 1.5x and 2.0x.
All portfolio results carry `PROXY_TRANSACTION_COSTS`. Observed Dukascopy
spreads are reported separately for overlapping dates and are not mixed into
the frozen baseline.

The cost model records:

- spread;
- configured commission;
- slippage;
- turnover;
- financing treatment.

Policy-rate carry tests do not pretend that financing is measured. All
carry-only and blended results receive evidence flag:

```text
NON_PROMOTABLE_PROXY_CARRY
```

Value-only and blended results using non-vintage BIS data receive evidence
flag:

```text
NON_PROMOTABLE_REVISION_RISK
```

Momentum-only results remain research-only but do not inherit those two
specific evidence flags.

## 10. Fixing-Window Study

### 10.1 Initial Pairs

The bounded fixing backtest runs only:

```text
EURUSD
GBPUSD
USDJPY
```

All 21 pairs appear in the data-quality report.

### 10.2 Fixing Calendars

Frozen fixing anchors:

- London: 16:00 `Europe/London`.
- Tokyo: 09:55 `Asia/Tokyo`.

Calendars use IANA timezone rules. The resolved UTC timestamp is stored with
each event.

Weekends and dates with unresolved or missing required quotes are ineligible.
No nearest-bar substitution is allowed outside the exact frozen interval.

### 10.3 Frozen Windows

Normalized M5 bars represent half-open intervals `[start, end)` and are keyed
by `end`. A signal uses a completed bar close. Entry uses the executable open
of the immediately following M5 bar. This sequencing is mandatory even when
both bars share the same boundary timestamp.

Pre-fix continuation:

- observation start: fixing time minus 30 minutes;
- signal time: fixing time minus 15 minutes;
- entry: executable open of the next M5 bar after the signal bar;
- exit: executable close of the M5 bar ending at fixing time;
- direction: sign of mid-price change from observation start through signal
  time.

Post-fix reversal:

- observation start: fixing time minus 15 minutes;
- signal time: fixing time;
- entry: executable open of the next M5 bar after the signal bar;
- exit: executable close of the M5 bar ending at fixing time plus 30 minutes;
- direction: opposite the sign of executable mid-price change from
  observation start through signal time.

If the observation move is exactly zero, no trade is recorded.

These are the only first-delivery fixing configurations. The implementation
does not search alternative windows.

### 10.4 Executable Pricing

- Long entry uses ask.
- Long exit uses bid.
- Short entry uses bid.
- Short exit uses ask.
- A signal computed from an M5 close may enter only at the next M5 interval's
  executable open. It cannot enter on the same bar.
- Spread is therefore embedded directly in trade P&L.
- Round-trip commission is the frozen 0.6 bps major-pair proxy.
- Cost stress scales the observed spread impact plus commission to 1.5x and
  2.0x; no hidden slippage parameter is fitted.
- Mid prices may determine the observation direction but never entry or exit
  P&L.

No stop loss, take profit, grid, martingale, averaging down, overlapping
position netting, model training, or threshold optimization is included.

## 11. Validation Design

### 11.1 Chronological Splits

Portfolio:

- development: 2006-01-01 through 2018-12-31;
- final holdout: 2019-01-01 onward.

Fixing:

- development: 2015-01-01 through 2020-12-31;
- final holdout: 2021-01-01 onward.

If source coverage starts later, the run is `BLOCKED_DATA`; split dates do not
move automatically.

The holdout is reported once per immutable data/config/code manifest
combination. Repeated execution of the same manifest is reproducibility, not a
new trial.

### 11.2 Walk-Forward

Development-period diagnostics use expanding annual folds.

For each fold:

- all normalization, volatility, ranking eligibility, and cost estimates use
  prior data only;
- no learned value crosses the fold boundary;
- the frozen signal and window definitions do not change.

### 11.3 Trial Registry

Every attempted result is registered before metrics are compared.

Initial portfolio configurations:

```text
carry_proxy
momentum_12_1
reer_value_5y
equal_weight_three_factor_blend
```

Initial fixing configurations:

```text
London pre-fix continuation
London post-fix reversal
Tokyo pre-fix continuation
Tokyo post-fix reversal
```

Each fixing configuration is evaluated for each of the three initial pairs.

Cost stress variants at 1.0x, 1.5x, and 2.0x are sensitivity evaluations and
are included in the total trial registry used by DSR/PBO.

### 11.4 Metrics

Report:

- observation and trade count;
- arithmetic and compounded return;
- annualized volatility;
- Sharpe;
- Sortino;
- maximum drawdown;
- Calmar where defined;
- win rate and expectancy for event studies;
- turnover;
- gross and net exposure;
- maximum currency, pair, year, and event contribution;
- annual and regime results;
- cost totals;
- results at 1.0x, 1.5x, and 2.0x costs;
- stationary or block-bootstrap lower bound;
- DSR using the full registered trial count;
- CSCV PBO where mathematically available.

Undefined metrics are emitted as `null` with a reason code. They are never
converted to zero.

### 11.5 Result Status

Result classification uses separate fields so data completion, statistical
evidence, and input limitations cannot overwrite one another.

Possible `study_status` values:

```text
BLOCKED_DATA
COMPLETED_NO_EDGE
RESEARCH_CANDIDATE
```

`RESEARCH_CANDIDATE` means only that the frozen study completed and met its
pre-registered research criteria. It does not authorize execution.

`evidence_flags` is an ordered list that may contain:

```text
NON_PROMOTABLE_PROXY_CARRY
NON_PROMOTABLE_REVISION_RISK
PROXY_TRANSACTION_COSTS
PBO_UNAVAILABLE
```

`production_eligible` is always `false` in the first delivery.

First-delivery candidate criteria:

- positive final-holdout net expectancy or return;
- positive 95% block-bootstrap lower bound;
- positive result at 2.0x costs;
- DSR z-score greater than 1.645 using the repository's approximate
  deflated-Sharpe implementation;
- PBO below 0.50 when available;
- no single currency, pair, year, or event contributes more than 40% of total
  positive P&L;
- at least three non-overlapping positive holdout years;
- no quality-gate failure.

If PBO is mathematically unavailable, the result cannot be
`RESEARCH_CANDIDATE`; it remains `COMPLETED_NO_EDGE` with
`PBO_UNAVAILABLE`.

Carry-only, value-only, and blended results may satisfy the numerical criteria
and receive `RESEARCH_CANDIDATE`, but their evidence flags and
`production_eligible=false` remain unchanged.

## 12. Reproducibility

Each run writes:

```text
runs/<run_id>/
  run_manifest.json
  eligibility.json
  quality.json
  trials.jsonl
  metrics.json
  equity_or_event_returns.parquet
  report.md
```

The run manifest records:

- code commit, branch, dirty status, and diff hash;
- Python and package versions;
- effective configuration and hash;
- exact dataset manifest identifiers and hashes;
- frozen universe hash;
- trial registry hash;
- study split dates;
- timezone database information where available;
- random seeds;
- command arguments with secrets redacted.

Repeated runs over identical inputs must reproduce deterministic normalized
data, signals, positions, event returns, and metrics. Bootstrap procedures use
fixed seeds recorded in the manifest.

## 13. CLI Failure Behavior

All commands return structured JSON summaries to stdout.

Exit codes:

```text
0 = operation completed
2 = completed but study found no qualifying edge
3 = blocked by data eligibility or quality
4 = invalid configuration or input schema
5 = network or provider failure
```

Provider failures do not fall back to another provider unless the effective
configuration explicitly names and prioritizes that provider. Substitution is
recorded in the dataset manifest and is forbidden for a pinned run.

Exceptions and logs redact environment values and URL query parameters that
may contain credentials.

## 14. Configuration

Add a dedicated research configuration file:

```text
configs/forex_edge_research.yaml
```

It contains:

- frozen universe;
- source series and contract mappings;
- date targets;
- file schema mappings;
- staleness and gap tolerances;
- portfolio signal definitions;
- exposure and volatility limits;
- fixing calendars and windows;
- cost estimates;
- split dates;
- bootstrap and CSCV seeds.

Strategy constants are not read from `config.yaml`, because that is a
production configuration surface. The research package must not mutate either
configuration file.

## 15. Testing

Use TDD for implementation.

Create one focused test file for the first verification pass:

```text
tests/test_forex_edge_research.py
```

Synthetic fixtures cover:

- package import isolation;
- universe freeze and drift detection;
- point-in-time macro availability;
- H.10 weekly publication availability;
- percent-to-decimal rate conversion;
- BIS conservative lag and revision-risk flag;
- missing-data reason codes;
- no factor-value imputation;
- currency-leg exposure aggregation;
- canonical USD-pair return orientation;
- long/short neutrality and caps;
- prior-data-only volatility scaling;
- bid/ask executable P&L;
- no same-bar signal and entry;
- London DST and Tokyo calendar resolution;
- exact fixing windows;
- chronological holdout boundaries;
- full trial-count propagation to DSR/PBO;
- deterministic manifests and outputs;
- secret redaction;
- CLI nonzero exit codes for blocked data.

Network tests are not part of the default targeted pytest run. Provider
adapters are tested with frozen response fixtures and explicit parser tests.

Targeted verification command:

```text
py -m pytest tests/test_forex_edge_research.py -q
```

## 16. First-Delivery Artifacts

The first implementation is complete when it can:

1. ingest and version BIS, CFTC, and FRED/ALFRED fixture or live data through
   read-only adapters;
2. import and version user-provided Dukascopy bid/ask data;
3. produce a 21-pair quality and eligibility report;
4. run the four frozen portfolio configurations;
5. run the twelve frozen fixing configurations;
6. produce deterministic JSON, Parquet, and Markdown run artifacts;
7. distinguish `BLOCKED_DATA`, `COMPLETED_NO_EDGE`, and
   `RESEARCH_CANDIDATE`, while preserving independent evidence flags;
8. pass the focused synthetic test file;
9. contain no execution, promotion, broker mutation, or production scoring
   integration.

Live data acquisition may expose unavailable series or insufficient history.
That is a valid `BLOCKED_DATA` outcome, not a reason to relax the design.

## 17. Deferred Work

The following are explicitly outside the first delivery:

- paid forward-point or rollover data;
- production or demo execution;
- strategy promotion;
- UI integration;
- automated undocumented Dukascopy downloads;
- parameter grids or optimizer searches;
- machine-learning models;
- macro-announcement trading;
- institutional order flow;
- OTC option volume;
- latency arbitrage or market making;
- grid, martingale, or averaging-down strategies.

Historical forward or broker-rollover support may be added later through a
licensed provider interface. Until then, carry and blended results remain
non-promotable.

## 18. Source References

- BIS Data Portal tools and SDMX:
  https://data.bis.org/help/tools
- BIS EER coverage and publication schedule:
  https://www.bis.org/statistics/eer.htm
- CFTC historical compressed COT data:
  https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm
- FRED API:
  https://fred.stlouisfed.org/docs/api/fred/overview.html
- Federal Reserve H.10 history and release timing:
  https://www.federalreserve.gov/releases/h10/hist/
- Dukascopy Historical Data Export:
  https://www.dukascopy.com/swiss/english/marketwatch/historical/
- Research evidence review:
  `docs/FOREX_EDGE_RESEARCH_STUDY_2026-06-14.md`

## 19. Implementation Status (2026-06-15)

Implemented research-only surfaces:

- `athena_research/forex_edge/`
- `forex_edge_cli.py`
- `configs/forex_edge_research.yaml`
- `tests/test_forex_edge_research.py`

Focused verification command:

```text
C:\dev\athena-python\.venv\Scripts\python.exe -m pytest tests/test_forex_edge_research.py -q
```

Latest implementation-worktree result: 105 passed.

Current behavior:

- The CLI exposes only `ingest-bis`, `ingest-cftc`, `ingest-fred`,
  `import-dukascopy`, `quality-report`, `run-portfolio`, `run-fixing`, and
  `run-both`.
- Pinned manifest IDs are required for run commands.
- Run artifacts are deterministic and always record
  `production_eligible=false`.
- Synthetic tests do not claim empirical edge.
- Live official-source ingestion, user Dukascopy files, final holdout returns,
  and real `RESEARCH_CANDIDATE` status are not verified in this pass.
