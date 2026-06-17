# Forex Edge Research Study

**Study date:** 2026-06-14

**Scope:** Evidence review and research design for a forex-only engine. This
document does not authorize live trading, change Engine A, change ASE gates, or
change any execution or risk control.

## Executive conclusion

No durable forex swing edge is currently verified in Athena.

The current ASE forex swing report is positive on 40 selected evaluation
trades, but that result is not an untouched out-of-sample estimate:

- The decision threshold is selected on the same evaluation sample whose
  realized returns are then reported.
- The model is not retrained independently for each expanding fold.
- The result fails the current PROVISIONAL concentration and Brier-skill gates.
- The full Phase 1 forex swing candidate set is negative in every calendar year
  represented.

This does not prove that forex has no edge. It means the existing result does
not prove one.

The strongest evidence-backed direction for Athena is a separate,
portfolio-based FX research engine combining:

1. investable carry,
2. cross-sectional momentum,
3. real-exchange-rate value, and
4. explicit crash, crowding, concentration, and transaction-cost controls.

A second, independent research path is a liquid-major intraday
fixing/reversal engine. Athena's current H1 data cannot test that hypothesis;
it requires several years of five-minute or tick bid/ask data.

Publicly marketed bots are not accepted as evidence. No independently audited,
broker-verifiable bot performance record was identified in the primary-source
search used for this study. CFTC material warns that bot and AI claims do not
establish predictive ability and that most retail forex accounts lose money.

## 1. Verified state of Athena forex

### 1.1 Reported ASE result

The current training report states:

| Horizon | Selected trades | Expectancy | Win rate | DSR | Bootstrap LB |
|---|---:|---:|---:|---:|---:|
| intraday | 42 | +0.2443R | 66.67% | 5.1601 | +0.0826R |
| swing | 40 | +0.1378R | 62.50% | 3.6725 | +0.0773R |

Those numbers are real outputs, but their interpretation is limited by the
selection procedure described below.

### 1.2 Full Phase 1 swing evidence

The full forex swing candidate set contains 2,547 trades with mean
`-0.0716R`. Its annual means are:

| Year | Trades | Mean net R |
|---|---:|---:|
| 2023 | 457 | -0.1113 |
| 2024 | 853 | -0.0229 |
| 2025 | 856 | -0.1038 |
| 2026 through data end | 381 | -0.0606 |

Every represented calendar year is negative.

Signal-level results are also negative. The intervals below use a
10,000-resample IID bootstrap with random seed 42:

| Signal set | Trades | Mean net R | Bootstrap 95% interval |
|---|---:|---:|---:|
| carry | 1,116 | -0.0860 | [-0.1340, -0.0387] |
| TSMOM | 617 | -0.0882 | [-0.1528, -0.0233] |
| carry plus TSMOM | 814 | -0.0392 | [-0.0954, +0.0172] |

USDJPY has the highest pair mean, `+0.0929R` over 150 candidates, but its
IID bootstrap interval is `[-0.0305, +0.2180]`, and one represented year is
negative. It is a testable hypothesis, not a verified edge.

### 1.3 Why the selected 40-trade result is not proof

In `athena_research/ase/train.py`:

- `train_family_horizon()` creates an 80/20 train/evaluation split.
- `select_expected_net_r_threshold()` searches thresholds on the evaluation
  data and maximizes realized evaluation expectancy, subject to a minimum
  selected-trade count.
- Bootstrap and DSR are then computed on the selected trades from that same
  evaluation sample.
- The expanding-fold diagnostic reuses the already-trained final model rather
  than fitting a new model and threshold inside each fold.

This creates evaluation-set selection bias. A valid estimate requires nested
selection: threshold and hyperparameter selection inside development data,
followed by one untouched holdout.

The current forex manifests also fail two PROVISIONAL checks:

| Horizon | OOS trades | Instruments | Max profit share | Brier skill | Nonnegative folds |
|---|---:|---:|---:|---:|---:|
| intraday | 42 | 10 | 0.4668 | -0.0006 | 4/4 |
| swing | 40 | 12 | 0.4767 | -0.0291 | 4/4 |

The configured maximum concentration is `0.40`, and Brier skill must be at
least zero. Both horizons fail both conditions.

### 1.4 Confirmed data and feature limitations

- Forex Phase 1 swing decisions cover only approximately 2023-06-14 through
  2026-06-05. That is too short for a strong claim about carry, value, or
  multi-regime momentum.
- Forex volume z-score is 100% missing in both current datasets.
- COT fields are 100% missing in both current datasets.
- Benchmark beta is 56.4% missing for swing and 93.2% missing for intraday.
- `athena_research/ase/train.py` does not pass `cot_asset` into
  `FeatureBuildContext`, although the feature builder can load COT data.
- The local COT cache has weekly history for major currencies, so this is a
  wiring gap rather than total data absence.
- The local policy-rate cache has incomplete currency coverage and stale CHF
  short-rate data.

### 1.5 Confirmed carry accounting defects

`athena_ase/signals/carry.py` consumes FRED policy rates reported in percent,
then divides the rate differential directly by decimal volatility. For
example, official FRED metadata reports DFF and ECBDFR in percent. The signal
therefore needs an explicit percent-to-decimal conversion before it can be
interpreted as a return divided by volatility.

In the current swing dataset, 94.17% of nonzero carry strengths are saturated
at 1.0. A read-only diagnostic applying `/100` reduced saturation but did not
produce an edge: the Phase 1 swing mean changed from `-0.0716R` to
approximately `-0.1233R`.

`athena_ase/labels/triple_barrier.py` charges spread, commission, and slippage,
but the active cost path defaults overnight swap to zero. A swing carry test
without realized rollover or forward points does not measure investable carry.

## 2. What published evidence supports

### 2.1 Carry

Currency carry is one of the best documented FX return factors: borrow or sell
low-yield currencies and buy high-yield currencies. Published evidence also
shows material crash and crowding risk.

The important implementation detail is that academic carry is generally a
diversified currency portfolio based on forward discounts or investable rate
differentials. It is not a collection of isolated pair entries based only on
central-bank headline rates.

Evidence:

- [Carry Trade and Momentum in Currency Markets, NBER](https://www.nber.org/system/files/working_papers/w16942/w16942.pdf)
- [Currency Carry Trades, NBER](https://www.nber.org/system/files/working_papers/w16491/w16491.pdf)
- [The Risks of Currency Carry Trades, NBER](https://www.nber.org/system/files/working_papers/w20433/w20433.pdf)
- [Carry off, carry on, BIS, August 2024](https://www.bis.org/publ/bisbull90.pdf)

### 2.2 Cross-sectional momentum

Currency momentum is also documented, but turnover and transaction costs are
decisive. Published strategies rank a currency universe and hold diversified
winner and loser baskets. Athena currently calculates time-series momentum
independently per pair and explicitly excludes forex from its cross-sectional
signal.

Evidence:

- [Currency Momentum Strategies, BIS](https://www.bis.org/publ/work366.pdf)
- [Carry Trade and Momentum in Currency Markets, NBER](https://www.nber.org/system/files/working_papers/w16942/w16942.pdf)

### 2.3 Value

Long-horizon real-exchange-rate value has published cross-sectional predictive
evidence and is economically distinct from carry and momentum. Robust versions
need point-in-time inflation, productivity, external-balance, and related macro
data, often with long estimation windows.

Athena does not currently implement a forex value signal.

Evidence:

- [Currency Value, Review of Financial Studies](https://academic.oup.com/rfs/article-abstract/30/2/416/2669968)
- [Boosting Carry Trades with Equilibrium Exchange Rate Models, ECB](https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp2731~4db9534c80.en.pdf)

### 2.4 Combining factors

Carry, momentum, and value can diversify one another. This is more defensible
than relying on one model-selected subset, provided each component survives
costs and untouched out-of-sample validation independently.

Recent working-paper evidence reports stronger results after expanding the
currency universe and hedging geographic risks, but it also finds that G10
carry performance has flattened in recent decades. This is research evidence,
not a promise that the effect remains tradable through Athena's broker and
cost structure.

Evidence:

- [The Anatomy of Currency Strategies, NBER working paper](https://www.nber.org/system/files/working_papers/w32900/revisions/w32900.rev0.pdf)
- [Style Investing in the Foreign Exchange Market, BIS](https://www.bis.org/publ/bppdf/bispap58j.pdf)

### 2.5 COT as a crowding control

CFTC non-commercial positioning can help measure crowding in currencies such
as JPY and CHF. The evidence supports using it as a risk scaler or veto during
crowded carry conditions, not assuming that it is an independent directional
alpha signal.

Evidence:

- [Carry trades and risk-off episodes, BIS, 2026](https://www.bis.org/publ/bisbull124.pdf)

### 2.6 Intraday fixing and reversal effects

Research using long samples of high-frequency FX data reports recurring price
patterns around major fixing windows and predictable time-of-day variation.
The London/New York overlap generally has tighter spreads and greater activity.
Many simple intraday patterns disappear after costs, so this is a narrow,
data-intensive hypothesis rather than a generic session filter.

Evidence:

- [Intraday Patterns in Foreign Exchange Markets, NBER](https://www.nber.org/system/files/working_papers/w12413/w12413.pdf)
- [Intraday Patterns in FX Returns and Order Flow, SNB](https://www.snb.ch/public/asset/it/www-snb-ch/publications/research/working-papers/2011/working_paper_2011_04/publications0_it/working_paper_2011_04.n.pdf)
- [Currency Fixings and Returns](https://sites.insead.edu/facultyresearch/research/file.cfm?fid=66802)

Athena's current H1 ASE ingest cannot validate a five-minute fixing strategy.

## 3. What is not currently implementable

The following published effects require data or infrastructure Athena does not
currently possess:

| Effect | Missing requirement |
|---|---|
| institutional order-flow alpha | dealer or customer order-flow feed |
| OTC option-volume alpha | timely OTC option volume and positioning |
| EM bond-flow prediction | timely, point-in-time portfolio-flow data |
| macro-announcement surprise trading | actual-versus-consensus feed plus minute bid/ask |
| latency arbitrage or market making | colocated low-latency infrastructure and order-book data |

Sources:

- [FX Order Flow and Predictability, NBER](https://www.nber.org/system/files/working_papers/w27199/w27199.pdf)
- [Order Flow and Exchange Rate Dynamics, BIS](https://www.bis.org/publ/work405.pdf)
- [FX Option Volume, Bank of England](https://www.bankofengland.co.uk/-/media/boe/files/working-paper/2022/fx-option-volume.pdf)
- [Bond Flows and Exchange Rates, BIS](https://www.bis.org/publ/work1042.pdf)
- [Macroeconomic News and FX, Federal Reserve](https://www.federalreserve.gov/pubs/ifdp/2004/823/ifdp823.htm)
- [High-frequency Trading in FX, BIS](https://www.bis.org/publ/mktc05.pdf)

## 4. Why trading-bot marketing is not evidence

A claimed return, screenshot, backtest, or short broker statement is not enough
to establish an edge. At minimum, useful evidence would require:

- a complete independently verified return series,
- stated leverage and maximum drawdown,
- deposits and withdrawals separated from trading P&L,
- broker and account verification,
- bid/ask, commission, rollover, and slippage treatment,
- survival and account-selection disclosure,
- enough history to cover multiple volatility and rate regimes, and
- an untouched forward period.

The primary-source search for this study did not identify a bot with that
evidence. This is not proof that none exists. CFTC guidance specifically warns
against guaranteed-return and AI/bot claims and states that about two out of
three retail FX traders lose money each quarter.

Regulatory sources:

- [CFTC Customer Advisory: AI Trading Bots](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/AITradingBots.html)
- [CFTC: Forex Frauds](https://www.cftc.gov/LearnAndProtect/forexfrauds)
- [CFTC: Eight Things You Should Know Before Trading Forex](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/reduce_risk_of_forex_fraud.htm)

## 5. Recommended research architecture

### 5.1 Track A: FX Portfolio Engine

Build this first under a separate research namespace, not inside Engine A and
not as a modification of the current ASE classifier.

Proposed research modules:

```text
athena_research/fx_portfolio/
  data.py
  signals.py
  portfolio.py
  costs.py
  backtest.py
  validation.py
```

Required signals:

1. **Carry:** one-, three-, and twelve-month forward discounts where available,
   or broker rollover normalized to annual return. Central-bank policy rates
   may be a fallback research proxy but must not be treated as executable
   carry.
2. **Momentum:** cross-sectional 1-, 3-, 6-, and 12-month total returns, with
   the most recent week or month optionally skipped and tested as a
   pre-registered variant.
3. **Value:** five-year real-exchange-rate deviation or a point-in-time
   PPP/BEER measure.
4. **Optional term structure:** yield-curve slope as a separately tested carry
   refinement.

Portfolio construction:

- Convert pair positions to currency-leg exposures before aggregation.
- Rank currencies rather than treating each pair as an independent bet.
- Hold diversified long and short baskets.
- Bound net USD and individual-currency exposure.
- Volatility-scale the portfolio using only information available at decision
  time.
- Cap per-currency, per-region, and per-signal contribution.
- Apply liquidity, spread, rollover, and stale-data gates.
- Use COT crowding and broad FX-volatility state as risk controls, not as
  unproven directional signals.

Start with a transparent linear factor blend. A nonlinear model should not be
introduced until each simple factor baseline survives the same untouched
holdout. Federal Reserve research finds that more complex FX models tend to
produce localized gains rather than systematic improvements over robust simple
baselines.

Source:

- [Complexity and Exchange Rate Prediction, Federal Reserve, 2025](https://www.federalreserve.gov/econres/feds/files/2025089pap.pdf)

### 5.2 Track B: FX Fix/Reversal Engine

This must remain separate from Track A because its data, horizon, costs, and
failure modes differ.

Initial research scope:

- EURUSD, GBPUSD, USDJPY, and other demonstrably liquid majors only.
- Tokyo and London fixing windows with correct daylight-saving calendars.
- Pre-fix continuation and post-fix reversal as separate hypotheses.
- Five-minute or finer bid/ask data, not midpoint-only H1 bars.
- Measured spread, slippage, rejection, and latency by time of day.
- No averaging down, martingale, or grid recovery.

The first deliverable is a read-only research backtest. It should not connect
to execution until it passes forward paper observation.

## 6. Required repairs before testing Track A

1. Correct percent-versus-decimal units in the current carry proxy.
2. Obtain full and current short-rate, forward-point, or broker-rollover
   coverage for every tested currency.
3. Charge overnight rollover in swing labels and backtests.
4. Wire point-in-time COT data with its real publication lag.
5. Remove or populate features that are almost entirely missing.
6. Replace pair-independent positions with currency-leg portfolio accounting.
7. Acquire at least 10 years, preferably 15 to 20 years, of point-in-time daily
   spot, forward, rate, inflation, and value data.
8. Separate model and threshold development from one untouched final holdout.

These are prerequisites. They are not parameter-tuning suggestions.

## 7. Validation protocol

Every strategy definition and variant count must be frozen before the final
holdout is examined.

Minimum protocol:

1. Test carry, momentum, and value separately before testing a blend.
2. Use expanding or rolling walk-forward fits, with each fold refitting all
   learned parameters and selecting thresholds only from prior data.
3. Keep one final chronological holdout untouched until the design is frozen.
4. Include rate, volatility, and liquidity regimes such as 2008, 2020, 2022,
   and the August 2024 carry unwind where data permit.
5. Use broker-observed bid/ask, commissions, rollover, and slippage.
6. Stress total costs at 1.5x and 2.0x.
7. Report expectancy, Sharpe, Sortino, maximum drawdown, turnover, exposure,
   currency concentration, and worst regime.
8. Compute bootstrap confidence intervals, DSR over every attempted variant,
   PBO/CSCV where sample size permits, and a data-snooping correction such as
   a reality check or SPA test.
9. Require positive results across multiple non-overlapping periods, not only
   a pooled mean.
10. Run paper/shadow observation before any demo promotion.

Technical-analysis research also documents severe data-snooping and
publication-bias risk. That is why a positive optimized backtest is not enough.

Sources:

- [Technical Trading and Data Snooping, Federal Reserve Bank of St. Louis](https://files.stlouisfed.org/files/htdocs/wp/2011/2011-001.pdf)
- [Intraday Technical Trading with Realistic Costs](https://sci2s.ugr.es/keel/pdf/specific/articulo/science2_13.pdf)

## 8. Ranked decision

| Rank | Research path | Evidence quality | Athena readiness | Decision |
|---:|---|---|---|---|
| 1 | carry + cross-sectional momentum + value portfolio | strongest | data and accounting work required | build research harness |
| 2 | fixing-window continuation/reversal | credible but narrow | missing long M5/tick bid/ask | acquire data, then test |
| 3 | COT crowding risk control | useful supporting evidence | cache exists; wiring missing | add only to research risk layer |
| 4 | macro surprise trading | credible | missing consensus and minute execution data | defer |
| 5 | order flow, OTC options, bond flows | credible institutional evidence | feeds unavailable | defer |
| 6 | retail bot replication, grid, martingale, black-box AI | no verified edge evidence | technically possible but unsafe or unproven | reject |
| 7 | HFT arbitrage or market making | institution-specific | infrastructure unavailable | reject |

## 9. Falsifiable next milestone

The next milestone is not a promotion. It is a research result answering:

> Does a pre-registered, cross-sectional carry/momentum/value currency
> portfolio remain positive on an untouched chronological holdout after
> broker-realistic spread, commission, rollover, slippage, exposure, and
> concentration accounting?

Until that question is answered with the validation protocol above, the
correct status is **edge not verified**.

## 10. Implementation status (2026-06-15)

Standalone research harness paths:

- package: `athena_research/forex_edge/`
- CLI: `forex_edge_cli.py`
- focused tests: `tests/test_forex_edge_research.py`

Verified in the implementation worktree:

```text
C:\dev\athena-python\.venv\Scripts\python.exe -m pytest tests/test_forex_edge_research.py -q
```

Result: 105 synthetic/fixture tests passed.

Important limits:

- Synthetic tests prove contracts and reproducibility only. They do **not**
  prove a forex edge.
- Official live ingestion was not run in this verification pass.
- User-provided Dukascopy bid/ask files were not supplied or empirically
  tested.
- Quality caps remain pre-registration inputs; empirical runs must fail closed
  until those values are reviewed and supplied before holdout results are
  inspected.
- `BLOCKED_DATA` means required evidence is missing or fails a quality gate.
- `COMPLETED_NO_EDGE` means the frozen study completed but did not meet every
  candidate criterion.
- `RESEARCH_CANDIDATE` is research-only and still has
  `production_eligible=false`.
