# Engine A V4 — Data Foundation & Reconstruction Specification (FROZEN)

Status: **SPECIFICATION ONLY. NO V4 TRADING LOGIC IMPLEMENTED.**
Date: 2026-06-19
Author context: produced under the "ENGINE A V4 — DATA FOUNDATION AND RECONSTRUCTION SPECIFICATION" directive, following the three prior forensic passes that concluded Engine A's price-core has `NO_PRICE_CORE_EDGE` and both surviving recovery leads failed (see `memory/project_engine_a_context_defect_2026_06_19.md`).

Scope guard: This document **does not** modify Engine A V3, Engine B, Engine D, execution, risk, thresholds, TradingView, or any live route. It is a frozen research contract. Nothing here promotes anything to production.

Evidence base: `tmp/v4_coverage_audit.py` (read-only inventory of `data/frozen/2026-05-30/candles/*.json`), `athena_research/engine_a_ablation/harness.py` (universe loader + cost model), and the three prior forensic reports.

---

## 1. Freeze Engine A V3

The current Engine A V3 price-core is hereby classified:

```
classification:        LEGACY_PRICE_CORE_BASELINE
production promotion:   PROHIBITED
purpose:                research comparison only
```

**Do not delete or alter it.** It remains the comparison baseline for every V4 specialist (Section 7). Preserved, unchanged:

- TradingView integration and overlays;
- Engine A context payloads (`ai_review/engine_a_context.py`);
- indicator snapshots (`engine_a_v3.quant_scorer._snapshots`);
- group routing (`engine_a_v3.routing.route_specialist`);
- backtest outputs and report writers;
- the forensic research harnesses (`athena_research/engine_a_ablation/`).

V3 continues to score and display. It must never gate, rank, or veto Engine B (it does not today). V4 is a **parallel reconstruction**, not an in-place edit of V3.

---

## 2. Data Availability Audit (measured, not assumed)

Full per-file inventory computed from the frozen snapshot `data/frozen/2026-05-30/`. "gap-runs" counts inter-bar gaps > 1.5× the nominal step; for D1/H1 forex/CFD these are overwhelmingly **legitimate weekend/session closures**, not missing data (≈155 weekends in 3y). Volume is **tick volume** for all MT5 series and **contract volume** for Bybit — *these are not comparable across providers.*

### 2.1 Coverage matrix

| Symbol | Group | Provider | TFs present | D1 bars | H4 bars | H1 bars | First→Last (D1) | Span | Notes |
|---|---|---|---|---|---|---|---|---|---|
| EUR/USD | forex | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.83y | clean |
| GBP/USD | forex | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.83y | clean |
| USD/JPY | forex | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.83y | clean |
| GBP/JPY | forex (cross) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.83y | clean |
| **USD/CHF** | forex (inv-quote) | mt5 | D1/H4/H1 | **260** | **620** | **1100** | 2024-01 → 2024-09 | **0.13–0.71y** | **BROKEN/PARTIAL — H1 covers only 6 weeks (2024-02→03). NOT TESTABLE.** |
| BTC/USDT | crypto | bybit | D1/H4/H1 | 749 | 4395 | 17583 | 2024-05 → 2026-05 | 2.01y | single regime |
| ETH/USDT | crypto | bybit | D1/H4/H1 | 749 | 4395 | 17583 | 2024-05 → 2026-05 | 2.01y | single regime |
| SOL/USDT | crypto | bybit | D1/H4/H1 | 749 | 4395 | 17583 | 2024-05 → 2026-05 | 2.01y | single regime |
| XRP/USDT | crypto | bybit | D1/H4/H1 | 749 | 4395 | 17583 | 2024-05 → 2026-05 | 2.01y | single regime |
| DOGE/USDT | crypto | bybit | D1/H4/H1 | 749 | 4395 | 17583 | 2024-05 → 2026-05 | 2.01y | single regime |
| XAU/USD | commodity (metal) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.84–2.9y | clean; prior edge candidate |
| XAG/USD | commodity (metal) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.84–2.9y | clean |
| WTI Oil | commodity (energy) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.84–2.9y | clean |
| NASDAQ-100 | index | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.85–2.98y | clean |
| S&P 500 | index | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.85–2.98y | clean |
| Dow Jones | index | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-07 → 2026-05 | 2.85–2.98y | clean |
| DAX 40 | index (non-US) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | 2023-06 → 2026-05 | 2.89–3.25y | only non-US index |
| AAPL | equity (tech) | mt5 | D1/H4/H1 | 750 | 4400 | **17600** | D1 2023-06 → 2026-05 | **H1/H4 back to 2020** | D1 shallow (3y), H1 6.35y/H4 5.58y |
| MSFT | equity (tech) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | D1 ~3y | H1 6.35y / H4 5.58y | deep intraday |
| NVDA | equity (tech) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | D1 ~3y | H1 6.35y / H4 5.58y | **H1 volume missing for 3378 bars (pre-2023)** |
| TSLA | equity (tech) | mt5 | D1/H4/H1 | 750 | 4400 | 17600 | D1 ~3y | H1 6.36y / H4 5.58y | deep intraday |
| DX-Y.NYB (DXY) | context only | yfinance | H4 only | — | 3737 | — | 2024-01 → 2026-05 | 2.39y | **volume all-zero; intermarket context, NOT tradeable** |

### 2.2 Per-field findings (audit answers)

- **Timeframes:** D1, H4, H1 **only**. **NO M15. NO M5. Anywhere.** This is the single most consequential data fact in this document.
- **First/last & missing bars:** tabulated above. No duplicate timestamps anywhere (`dup=0` for all files). D1/H1 "gaps" are weekend/session closures, not data loss.
- **Provider concentration:** 21 of 22 series are **MT5 (one broker feed)**; crypto is **one exchange (Bybit linear perps)**; DXY is yfinance. **Single-source risk** — no cross-provider reconciliation is possible from this snapshot.
- **Bid/ask vs midpoint:** a **single OHLC stream per symbol** (MT5 defaults to **bid**; not verified per-broker). **No spread/ask series exists.** Therefore real spread is **not in the data** — all transaction costs are *modeled assumptions* (`harness._COSTS`), never observed.
- **Volume type:** MT5 = **tick volume** (count), Bybit = **contract volume**, DXY = **none**. Mixed and non-comparable → any cross-sectional or absolute-volume feature is **untrustworthy across groups**.
- **Timezone / DST:** all timestamps normalized to **UTC (+00:00)**. D1 bars are 00:00 UTC boundaries — **not** the 17:00-NY forex day. Session/fixing boundaries are therefore **not represented natively**.
- **Confirmed-bar availability:** yes — the harness consumes confirmed prefixes only (excludes the forming bar). Parity-safe.
- **Symbol survivorship:** universe is hand-picked *currently-liquid* symbols. **Survivorship-biased**, acutely so for crypto (no delisted alts) and equities (no delisted names).
- **Corporate-action adjustment:** equity CFDs (AAPL/MSFT/NVDA/TSLA) via MT5 — split/dividend adjustment **NOT VERIFIED**. Must be confirmed before any equity specialist is trusted (gaps on ex-div/split dates would corrupt breakout/MAE statistics).
- **Point-in-time macro/context:** per-symbol frozen snapshots exist for `factor_carry_z`, `factor_cot_z`, `factor_vol_skew_z`; crypto adds `funding` and `oi`; DXY H4 serves as intermarket context. These are PIT snapshots but **coverage/leak-freedom per factor is only partially verified** (see prior data-layer audit memory).

### 2.3 Testability verdict per timeframe (do not over-claim)

| TF | Exists? | **Properly testable?** | Reason |
|---|---|---|---|
| D1 | yes (≈750 bars / ~3y most) | **Marginal** | ~3y = too few independent swings for D1 swing edges; deep D1 (>5y) does **not** exist (equity D1 is only 3y; only equity H1/H4 reach 2020). |
| H4 | yes | **Yes for trend/breakout** on ≥2.8y instruments; tech-equity H4 reaches 5.6y | primary decision axis used by V3 harness |
| H1 | yes | **Yes** | adequate bar counts |
| M15 | **NO** | **NO** | does not exist |
| M5 | **NO** | **NO** | does not exist |

---

## 3. Required Research Universes (minimum defensible vs. what we have)

For each group: **[HAVE]** = present & clean in the frozen snapshot; **[MISSING]** = required for a defensible test but absent; **[OBTAINABLE]** = realistically sourceable.

### Forex
- **Minimum defensible:** ≥8 majors+crosses spanning both base- and quote-side USD (incl. USD/CHF, USD/CAD, AUD/USD, NZD/USD, EUR/JPY), multiple rate/vol regimes, H1 **plus M15/M5** for intraday, real spread/rollover.
- **[HAVE]** 4 clean pairs (EUR/USD, GBP/USD, USD/JPY, GBP/JPY), 2.83y, H4/H1, tick volume, modeled costs.
- **[MISSING]** USD/CHF (broken partial — and it is the *only* inverse-quote USD pair, so base/quote-orientation cannot be tested today); any 5th–8th pair; M15/M5; real spread/swap; a second rate regime beyond 2023–26.
- **[OBTAINABLE]** MT5/Dukascopy can supply more pairs, deeper history (pre-2020), and tick data → M5/M15 derivable. Real spread requires tick or a spread feed.

### Equities / ETFs
- **Minimum defensible:** broad sectors + styles + ETFs (avoid tech concentration), split/div-adjusted OHLCV, point-in-time index membership.
- **[HAVE]** 4 mega-cap **tech only** (AAPL/MSFT/NVDA/TSLA); deep H1/H4 (2020+) but shallow D1 (~3y); adjustment unverified; NVDA H1 volume partly missing.
- **[MISSING]** every non-tech sector, value/defensive names, broad ETFs (SPY/sector SPDRs), verified corporate-action adjustment, PIT membership.
- **[OBTAINABLE]** yes — adjusted equity/ETF OHLCV is widely available; this is the **most expandable** group.

### Indices
- **Minimum defensible:** geographically diverse liquid indices, correct cash-vs-futures + market hours.
- **[HAVE]** NASDAQ-100, S&P 500, Dow Jones (3 US, highly collinear ≈ 1 factor), DAX 40 (only non-US).
- **[MISSING]** Asia/EM (Nikkei, HSI, ASX), more Europe (FTSE, CAC, EuroStoxx); cash/futures distinction is undocumented in the snapshot.
- **[OBTAINABLE]** yes via MT5 CFDs / index data vendors.

### Commodities
- **Minimum defensible:** metals + energy + softs, contract-roll handling for futures.
- **[HAVE]** XAU/USD, XAG/USD (metals), WTI Oil (energy) — 3 instruments, 2.84y, clean. (Spot CFDs → no explicit roll.)
- **[MISSING]** platinum, palladium, copper, natural gas, Brent, ags; roll documentation.
- **[OBTAINABLE]** yes — additional metal/energy CFDs are readily sourceable; **this is the cheapest path to lift the strongest prior (XAU trend) above the cluster floor.**

### Crypto
- **Minimum defensible:** multiple liquid instruments, survivorship-aware universe, exchange-specific fees/funding/volume.
- **[HAVE]** BTC/ETH/SOL/XRP/DOGE on Bybit linear, 2.01y, with funding + OI; real funding present, fees modeled.
- **[MISSING]** longer history (>2y / a non-bull regime), survivorship handling (delisted alts), second exchange.
- **[OBTAINABLE]** yes — Bybit/Binance history extends earlier; more instruments available.

---

## 4. Pre-Registered Specialist Strategy Registry (specifications only)

**These are specifications. None are implemented. Do not combine them into one confluence model.** Each is a standalone, independently-validated specialist. A specialist that cannot be tested on available data is marked **[DATA-BLOCKED]** and must not be coded.

For every specialist: `hypothesis · group · timeframe · regime · direction rule · entry · invalidation · exit · max-hold · required data · costs · benchmark · failure condition`.

### A. Trend continuation / pullback  — **[TESTABLE — primary candidate]**
- **Hypothesis:** persistent directional drift in assets with structural trend (carry-funded FX, momentum commodities, equity beta) is exploitable via pullback entries within an established trend.
- **Group:** commodities (primary), forex, indices/equities (beta-controlled). **Timeframe:** H4 (decision), D1 (trend filter). **Regime:** trending only (efficiency-ratio gate).
- **Direction:** sign of higher-TF trend (D1). **Entry:** pullback to a defined zone within trend. **Invalidation:** structural swing beyond pullback. **Exit:** structural target or time. **Max-hold:** ≤30 H4 bars.
- **Required data:** D1+H4 OHLC (have). **Costs:** modeled per group. **Benchmark:** unconditional long-only drift + V3 baseline.
- **Failure condition:** directional net edge disappears once long-beta/drift is subtracted (the documented V3 failure mode) → reject.

### B. Volatility-contraction breakout — **[TESTABLE but DATA-THIN]**
- **Hypothesis:** range/vol contraction precedes directional expansion.
- **Group:** equities/ETFs, commodities, indices. **TF:** H4. **Regime:** post-contraction. **Direction:** breakout side.
- **Entry:** close beyond contraction band. **Invalidation:** re-entry into range. **Exit:** measured-move / time. **Max-hold:** ≤30 H4.
- **Required data:** H4 OHLC (have). **Benchmark:** drift + V3. **Failure:** net CI straddles zero / fails 1.5× cost (this is exactly what H2_RANGE_BREAKOUT did — see prior pass; currently **BLOCKED_BY_DATA** on cluster count).

### C. Range mean-reversion — **[TESTABLE, WEAK PRIOR]**
- **Hypothesis:** in ranging regimes price reverts to mean.
- **Group:** forex (efficient pairs), indices. **TF:** H4/H1. **Regime:** ranging. **Direction:** toward range mid.
- **Entry:** band extreme. **Invalidation:** range-break. **Exit:** mid / opposite band / time. **Max-hold:** ≤30.
- **Failure:** prior forensic showed forex range-fade negative; must clear holdout + cost + beta control or reject.

### D. Cross-sectional momentum — **[DATA-BLOCKED]**
- **Hypothesis:** within a group, recent relative winners outperform losers.
- **Blocker:** requires a broad simultaneous universe (≥~15–20 names). Have 4 forex / 4 tech equities / 5 crypto / 3 commodities. **Underpowered everywhere. Do not implement until universe breadth is obtained.**

### E. Session / fixing behaviour (forex) — **[DATA-BLOCKED — hard]**
- **Hypothesis:** systematic flow around London/NY sessions & the 16:00 London fix.
- **Blocker:** **requires M5/M15 — which do not exist in the snapshot.** Cannot be tested at all today. **NO-CODE.**

### F. Carry / value portfolios (forex, weekly/monthly) — **[DATA-BLOCKED]**
- **Hypothesis:** high-carry / cheap-value currencies earn a premium at low frequency.
- **Blocker:** 4–5 pairs over 2.8y ≈ ~10–12 non-overlapping monthly observations → hopelessly underpowered; needs many more pairs and years. **NO-CODE.**

### G. Opening-range / gap models — **[DATA-BLOCKED — hard]**
- **Hypothesis:** the first session range / overnight gap predicts day direction.
- **Blocker:** requires intraday sub-H1 around the session open (**M5/M15 absent**) and clean session boundaries (D1 is 00:00-UTC, not session-aligned). **NO-CODE.**

---

## 5. New Engine A Score Contract (design only — not implemented)

The V4 score **must not represent indicator agreement** (the V3 defect). It represents one of:

- **calibrated P(net-positive outcome)** for the specialist's defined trade, or
- **estimated expected net R** of that trade,

…conditioned on the specialist's features, on **out-of-sample** data.

Required properties:

- **Calibration:** isotonic / Platt on a held-out calibration slice; reliability diagram must be near-diagonal. The raw model score is never shown without calibration.
- **Dev/holdout separation:** model fit & calibration on dev only; holdout is touched **once** at the end. No feature, threshold, or geometry may be changed after viewing holdout.
- **Decile monotonicity:** realized net R must increase monotonically across score deciles on holdout (non-monotonic ⇒ reject the score, not re-bin).
- **Uncertainty:** every score ships a cluster-bootstrap CI; wide-CI scores abstain.
- **Missing-data behaviour:** explicit 4-state availability (present / stale / partial / absent) with a defined denominator policy; missing inputs **downgrade to abstain**, never silently impute a neutral.
- **Per-strategy & per-group comparability:** scores are calibrated *within* (specialist × group); a global ranking is only valid after each is independently calibrated to the same P(net+) meaning.
- **Minimum sample:** no score is emitted for a (specialist × group) cell with fewer than the Section 6 minimums.
- **Abstention:** abstain is a first-class output (no trade), distinct from a low score. Coverage (fraction of bars with a non-abstain score) is reported.

The score **never** mutates Engine A indicator fields and **never** gates Engine B.

---

## 6. Validation Contract (every specialist passes independently)

A specialist is promotable to *research candidate* only if **all** hold:

1. **Untouched chronological holdout** — positive net (last ~30% of history, touched once).
2. **Unseen-symbol holdout** — positive on symbols never used in fit, *where the universe permits* (today it largely does not — see §3; this is a binding blocker, not a waiver).
3. **Long/short separation** — edge present on both sides, not only the beta side.
4. **Regime stability** — positive (or non-negative) across trending/ranging and across years.
5. **Cost stress** — positive at **1×, 1.5× and 2×** modeled costs.
6. **Cluster-aware CIs** — bootstrap **resampling whole symbols**; the net-edge 95% CI must exclude zero.
7. **Concentration limits** — no single symbol contributes > ~35% of net edge; ≥ **8 independent clusters** (symbols and/or non-overlapping time blocks).
8. **DSR / PBO** — deflated Sharpe / probability-of-backtest-overfit computed with **all attempted variants counted** (every cell inspected across all passes).
9. **Score→return monotonicity** — §5 decile test passes on holdout.
10. **Beats unconditional drift/beta** — net edge survives subtracting long-only/drift (the V3 killer).
11. **Beats / complements Engine B** — see §7.

**Backtest discovery thresholds remain separate from (stricter) live thresholds.** Nothing in this contract authorizes a live threshold change.

A specialist failing any item is `NO_EDGE` or `BLOCKED_BY_DATA`, never "promote anyway."

---

## 7. Engine B Comparison (mandatory benchmark)

Engine B is the standing benchmark. Future testing must report, side by side:

- **Engine B alone** (current expectancy);
- **each V4 specialist alone**;
- **Engine B trades ranked by each specialist** (does the score improve B's selection?);
- **genuine conflicts** (B-long vs specialist-short) and their resolution outcomes;
- **incremental expectancy** of adding the specialist to B.

**Engine A/V4 agreement must NOT become a mandatory Engine B gate.** Engine C continues to own agreement/conflict/A-only/B-only comparison. A V4 specialist earns its place only by adding incremental expectancy, never by suppressing B.

---

## 8. Rates-Macro Contract (document only — do NOT execute here)

The non-FX `carry` factor is a mislabel: for index/commodity/equity/ETF it is an **inverted 10-year-Treasury-yield macro proxy**, not a carry differential. A future migration will rename it `carry` → `rates_macro` for non-FX groups, keeping genuine FX policy-rate `carry` distinct.

**Migration is NOT performed in this phase.** When scheduled, it is a standalone cross-surface closed-loop PR proving every consumer first: `carry_feed.py`, `factor_scoring.py`, `forex_scoring.py`, `quant_context.py`, `engine_c.py`, `execution.py`, `ai_review/engine_a_context.py`, `confidence_engine.py`, `config.yaml`, and `TVChartPanel.tsx`. Field presence ≠ parity — trace write→read per the repo cross-surface checklist before merge.

---

## 9. Deliverable Summary & First-Experiment Decision

### 9.1 Data-coverage matrix
See §2.1 (measured). 21 tradeable symbols + 1 context series; D1/H4/H1 only; ~2.0–2.9y for most, with tech-equity H1/H4 reaching 2020 (5.6–6.4y, but D1 only ~3y).

### 9.2 Blocked research areas (explicit NO-CODE)
- **Families E (session/fixing) and G (opening-range/gap): hard-blocked — no M5/M15 exists.** Do not code.
- **Family D (cross-sectional momentum): blocked — universe too narrow in every group.** Do not code.
- **Family F (carry/value portfolios): blocked — too few pairs × too little history for low-frequency stats.** Do not code.
- **Base/quote orientation in forex: untestable — USD/CHF (the only inverse-quote pair) is a broken partial.**
- **Unseen-symbol holdout: largely impossible today** (≤5 names per group; tech-only equities).

### 9.3 Obtainable-data plan (priority order)
1. **Add commodity instruments** (platinum, palladium, copper, natural gas, Brent) — cheapest lift of the strongest prior (XAU trend) above the 8-cluster floor. **[HIGH / EASY]**
2. **Broaden equities/ETFs beyond tech** (sectors + SPY/sector SPDRs) with **verified corporate-action adjustment**. **[HIGH / EASY]**
3. **Add forex pairs incl. a valid USD/CHF + USD/CAD/AUD/NZD** and **deeper history (pre-2020)** for a second rate regime. **[HIGH / MEDIUM]**
4. **Source tick/M5 forex** to unblock families E/G and to derive *real* spread. **[MEDIUM / HARDER]**
5. **Extend crypto history + add instruments** for a non-bull regime; handle survivorship. **[MEDIUM]**
6. **Add geographically diverse indices** (Asia/EM/Europe). **[MEDIUM]**

### 9.4 Frozen specialist registry
§4 — A,B,C testable (A primary); D,E,F,G data-blocked. Registry is frozen; predicates may not be tuned post-hoc.

### 9.5 V4 score contract
§5 — calibrated P(net+) or expected net R; never indicator agreement; abstention first-class; missing-data downgrades to abstain.

### 9.6 Validation contract
§6 — 11 independent gates, cluster CIs, 1.5× cost floor, ≥8 clusters, DSR/PBO with full trial accounting, beats drift/beta, beats/complements Engine B.

### 9.7 Recommended first specialist + exact reasons
**Recommend: Family A — Trend-continuation/pullback, first pre-registered on COMMODITIES at H4 (XAU/XAG/WTI + obtainable metals/energy), beta-controlled.**

Exact reasons it should be tested first:
1. **Only family with both adequate data depth and a defensible, not-yet-refuted prior.** Commodity/XAU trend persistence was the *single* standalone directional signal observed in prior large-n forensic maps (XAU SQN ≈ +3.23), and it survived where forex/crypto direction was a coin-flip.
2. **Needs no blocked data** — H4/D1 only; no M5/M15, no large cross-sectional universe, no low-frequency portfolio.
3. **Least beta-contaminated direction test available.** Commodities are not a single secular-uptrend factor the way US-tech equities/indices are, so a directional edge there is the *cleanest* to separate from the long-beta leakage that killed V3.
4. **Cheapest path to a powered test** — adding 3–5 metal/energy instruments (§9.3 item 1) lifts it over the 8-cluster floor without sourcing tick data.

### 9.8 Explicit NO-CODE decision (binding)
**Do NOT implement Family A (or any specialist) yet.** Even the best candidate currently has only **3 commodity instruments = 3 clusters**, below the §6 minimum of 8, with **no unseen-symbol holdout possible**. The pre-registered first experiment is therefore **gated on §9.3 item 1**: obtain ≥5 additional liquid metal/energy instruments (target ≥8 commodity clusters) with verified provenance, **then** run the single pre-registered, properly-powered Family-A trend experiment with the full §6 validation contract.

Until that data exists, the correct action is **NO CODE** — source data, not strategies.

---

## Handoff

- **No production change.** Engine A V3, Engine B/D, execution, risk, thresholds, TradingView, live routes: untouched. This is a spec doc only.
- **New artifact:** this file. **Read-only evidence:** `tmp/v4_coverage_audit.py` (rerun to regenerate §2.1).
- **Decisions:** V3 frozen as `LEGACY_PRICE_CORE_BASELINE`; families D/E/F/G are NO-CODE (data-blocked); recommended first experiment = Family A trend on commodities, **itself gated** on obtaining ≥8 commodity clusters.
- **Next session, cold-start:** (1) source additional commodity instruments per §9.3-1 with provenance; (2) only then pre-register and run the single Family-A H4 trend experiment under the §6 contract; (3) do **not** build V4 trading logic before that experiment is powered. Rates-macro rename (§8) remains a separate future PR.
