# ASE v2.1 — Adaptive Specialist Engine, Implementation-Grade Specification

**Status:** Build-ready spec for Claude Code. Demo/paper only. Greenfield — zero reuse of Engine A indicators, scoring, weights, thresholds, regimes, gates, or exit policies.
**Supersedes:** ASE v2.0 spec. All v2.0 design decisions stand; this version adds exact formulas, default parameters, schemas, contracts, test matrix, monitoring, and a ticketed work order.
**Environment:** Windows, `py` launcher, `scikit-learn==1.9.0` (current as of June 2026), Python ≥ 3.11.

---

# PART I — ARCHITECTURE (recap, 1 page)

Two layers. **Layer 1**: deterministic primary signals (TSMOM, carry, cross-sectional momentum, FX mean-reversion) generate *candidate trade events* with direction. **Layer 2**: a pooled per-family meta-model estimates P(candidate is profitable net of costs) via triple-barrier labels, plus quantile heads for return/MAE/MFE/hold-time that build the brackets. ML filters and sizes confidence; it never originates direction.

Decision statuses: `TRADE / WATCH / FLAT / ERROR`. Deployment: 30-day shadow → per-family manual promotion to demo → legacy Engine A bypassed per family, removed only in a later dedicated release. All sizing stays in `risk_engine`; `risk_check()` is never bypassed; ASE emits levels and scores only.

Five model families: `forex`, `crypto`, `commodity`, `equity` (US+JSE), `index_etf`. Two horizons: `intraday` (H1 decisions) and `swing` (D1 decisions).

---

# PART II — DATA LAYER

## 1. Point-in-Time Store (`athena_ase/data/ptis.py`)

### 1.1 Storage
- Parquet partitioned by `source/series_id/year`, with a SQLite catalog (`ptis_catalog.db`) for series metadata. Location: `%LOCALAPPDATA%\Athena\ptis\`.
- Row schema (every series, no exceptions):

```
series_id      str     # e.g. "EODHD:EURUSD:H1:close", "CFTC:COT:6E:noncomm_net"
value_time     int64   # epoch ms — the time the value DESCRIBES
available_time int64   # epoch ms — earliest time the value could be KNOWN
value          float64
revision       int16   # 0 = first print; increments on restatement
ingest_time    int64   # epoch ms, audit only
```

- Query API (the ONLY read path features may use):

```python
def asof(series_id: str, decision_time_ms: int, n: int = 1) -> np.ndarray:
    """Last n values with available_time <= decision_time_ms,
    ordered by value_time. Latest revision per value_time wins."""
```

- A module-level guard: `features/build.py` imports `asof` and nothing else from data. CI test asserts no other data read path is imported by the feature builder.

### 1.2 Availability rules per source (frozen)

| Source | available_time rule |
|---|---|
| EODHD H1/H4/D1 bars | bar close time + 90 s ingestion buffer |
| Dukascopy tick volume (H1 agg) | bar close + 5 min |
| Bybit funding/OI/volume | event time + 30 s |
| CFTC COT (legacy + disaggregated) | report Tuesday → published Friday 15:30 ET → `available_time` = following **Monday 00:00 UTC** (conservative; weekend sessions for crypto don't apply to COT instruments) |
| FRED daily rate series | observation date + 1 business day, 12:00 ET (vintage `realtime_start` used where ALFRED vintage exists; else this default) |
| FRED policy rates (FEDFUNDS etc.) | release calendar where known, else obs + 1 BD |

Phase 0 exit artifact: `reports/availability_audit.md` listing every series with its rule, sample spot-checks, and any series where the rule is a default rather than verified — those are flagged `UNVERIFIED_LAG` and may not be used in enriched models until verified.

### 1.3 Dataset hashing
Reuse Athena's frozen-data SHA-256 manifest pattern: a training run materializes its exact feature/label matrices to parquet and records `dataset_hash = sha256(file bytes)` in the artifact manifest. Re-training from the same PTIS snapshot must reproduce the hash (determinism test).

## 2. Cost Model (`athena_ase/data/costs.py`)

Versioned dataclass; `cost_model_version` recorded in labels and artifacts.

```python
@dataclass(frozen=True)
class CostModel:
    version: str                 # "cm-2026.06.0"
    spread_bps: float            # half-spread x2, round trip
    commission_bps: float        # round trip
    slippage_frac_of_range: float  # x bar high-low range, per side
    swap_bps_per_day: float      # signed handled at label time from carry sign
```

**Default v0 table (replace with broker-measured values in Phase 0; these are deliberately conservative):**

| Family | spread_bps | commission_bps | slippage_frac | swap_bps/day |
|---|---|---|---|---|
| forex majors | 1.2 | 0.6 | 0.05 | from carry feed |
| forex crosses/EM | 3.5 | 0.6 | 0.08 | from carry feed |
| crypto majors | 2.0 | 7.0 (taker 2×3.5) | 0.05 | funding actual |
| crypto alts | 6.0 | 7.0 | 0.10 | funding actual |
| commodity CFD | 3.0 | 0.0 | 0.08 | broker swap |
| equity US | 2.5 | 1.0 | 0.05 | 0 (no overnight CFD assumed; if CFD, broker swap) |
| equity JSE | 8.0 | 5.0 | 0.10 | broker swap |
| index_etf | 1.5 | 0.5 | 0.05 | broker swap |

Round-trip cost in R-units is computed at label time as `cost_bps / (k_sl × σ_h_bps)` so expectancy is natively net.

---

# PART III — LAYER 1, EXACT DEFINITIONS

All prices are log-prices. σ definitions:

- `σ_h` (horizon vol): EWMA std of 1-bar log returns, span = 32 bars (intraday) / 21 bars (swing), expressed per-bar; horizon-scaled as `σ_h × sqrt(maxHoldBars)` where needed.
- `σ_long`: same with span 256 (intraday) / 126 (swing).

## 3. Signals

### 3.1 TSMOM (`signals/tsmom.py`)
For lookbacks L ∈ {24, 72, 168} H1 (intraday) / {21, 63, 126, 252} D1 (swing):

```
z_L = (p_t − p_{t−L}) / (σ_1bar × sqrt(L))
blend = Σ w_L × clip(z_L, −3, 3) / Σ w_L      # w_L = 1/sqrt(L)
direction = sign(blend) if |blend| ≥ 0.5 else NONE
rawStrength = min(|blend| / 3, 1.0)
```

### 3.2 Carry (`signals/carry.py`)
- Forex: `carry = (r_base − r_quote)` annualized from FRED short rates (reuse `carry_feed.py` plumbing only). Crypto: annualized funding basis (negative funding = long carry for shorts). Commodity: curve slope where the feed exists, else emit NONE.
- `cvr = carry / (σ_1bar × sqrt(252 bars-per-year-equivalent))`, winsorized at ±2.
- `direction = sign(carry) if |cvr| ≥ 0.4 else NONE`; `rawStrength = min(|cvr|/2, 1)`.
- Carry fires on the **swing** horizon only.

### 3.3 Cross-sectional momentum (`signals/xsec.py`) — equity, crypto, index_etf
- Universe = family members with valid data that bar. `r63 = vol-scaled 63-bar return`. Rank to percentile `pct`.
- `LONG if pct ≥ 0.8`, `SHORT if pct ≤ 0.2`, else NONE. `rawStrength = |pct − 0.5| × 2`.
- Swing horizon only. Minimum universe size 10 that bar, else signal disabled (logged).

### 3.4 FX short-horizon mean reversion (`signals/meanrev.py`) — forex intraday only

```
m = mean(p, 20 bars);  s = std(p, 20 bars)
z = (p_t − m) / s
direction = SHORT if z ≥ 2.0; LONG if z ≤ −2.0; else NONE
rawStrength = min((|z| − 2.0) / 1.5, 1.0)
Suppress if |TSMOM blend| ≥ 1.5 in the opposing... (i.e. don't fade a strong trend):
if sign(direction) == −sign(tsmom_blend) and |tsmom_blend| ≥ 1.5 → NONE
```

### 3.5 Arbitration (`signals/arbitrate.py`)
Per instrument-horizon-bar, collect fired signals.
- All agree (or one fires) → candidate `{direction, signals[], agreement_count, max_rawStrength}`.
- Conflict where both sides have `rawStrength ≥ 0.3` → NONE.
- Conflict where one side's max strength ≥ 2× the other's → stronger side wins, `conflict_flag=True` (feature).
- **De-duplication / event spacing:** after a candidate is emitted for an instrument-horizon, suppress same-direction candidates for `maxHoldBars/2` bars unless direction flips. Prevents overlapping-label leakage and serial-correlation inflation of trade counts.

A candidate is an event, never an order.

---

# PART IV — LAYER 2, EXACT DEFINITIONS

## 4. Labels (`labels/triple_barrier.py`)

Per candidate at decision time t, direction d ∈ {+1,−1}:

```
σ_bar = EWMA vol at t (per Part III)
upper = p_t + d × k_tp × σ_bar × sqrt(H)     # H = maxHoldBars
lower = p_t − d × k_sl × σ_bar × sqrt(H)
vertical = t + H bars
```

- Defaults (research-tunable, frozen pre-validation): `k_sl = 1.0`, `k_tp = 1.0`, `H = 16` H1 (intraday), `10` D1 (swing). Symmetric barriers ⇒ base rate interpretable; asymmetric variants live in the trials registry only.
- Touch detection uses bar highs/lows; if both barriers within one bar → label by **adverse-first assumption** (conservative): loss barrier deemed touched first.
- `gross_R = (exit − p_t) × d / (k_sl × σ_bar × sqrt(H))`; `net_R = gross_R − cost_R`; **label `y = 1 if net_R > 0 else 0`**.
- Auxiliary targets per candidate: `net_R` (for quantile heads), `MAE_R`, `MFE_R` (max adverse/favorable excursion in R), `hold_bars`.
- Sample weights: uniqueness weights per López de Prado (average overlap of concurrent labels) — implemented; event-spacing in 3.5 already limits overlap, weights handle the rest.

## 5. Features (`features/build.py`)

All from `ptis.asof()` only; all lagged so the newest input bar is **closed** at decision time. Exact set, names frozen:

**Return/vol block:** `ret_z_1, ret_z_4, ret_z_8, ret_z_24` (intraday) / `ret_z_1, ret_z_5, ret_z_21` (swing) — vol-scaled returns; `vol_level = σ_bar / σ_long`; `vol_of_vol` (std of σ_bar over 32 bars / its mean); `vol_regime = σ_bar/σ_long` bucketed {low<0.8, mid, high>1.25} as ordinal.

**Path block:** `dd_64` (max drawdown over 64 bars / (σ_bar×8)), `ru_64` (runup, same scale), `range_pos = (p − min64)/(max64 − min64)`.

**Liquidity block:** `volu_z` = log volume z-score vs 64-bar mean (Dukascopy for forex, exchange volume otherwise); missing → core model handles NaN natively (HGB supports NaN) but `dataQuality.missingFeeds` records it.

**Cross-sectional block:** `xsec_pct` (as 3.3), `family_dispersion` (cross-sectional std of r63 within family).

**Correlation block:** `beta_bench` = rolling 64-bar β to family benchmark (forex→DXY proxy basket, crypto→BTC, equity→SPX, index_etf→SPX, commodity→BCOM proxy or NONE).

**Signal context block:** `sig_tsmom, sig_carry, sig_xsec, sig_mr` (each −1/0/+1 × rawStrength), `agreement_count`, `conflict_flag`, `candidate_dir`.

**Calendar block:** `hour_sin, hour_cos, dow_sin, dow_cos`, `session ∈ {asia, london, ny, overlap}` ordinal.

**Instrument block:** `instrument_id` (native categorical — HGB `categorical_features`), `subclass` (e.g. major/cross/EM; L1/alt; sector for equities).

**Enriched block (enriched variant only):** `cot_pct` = 156-week percentile of non-commercial net positioning (release-lagged), `cot_delta_4w`; crypto: `funding_z`, `oi_delta_z`. Series flagged `UNVERIFIED_LAG` are excluded until verified.

**Banned:** EMA, RSI, ADX, VWAP scoring, ATR scoring, any legacy factor vote, any feature lacking a PTIS availability rule.

## 6. Models (`models/meta.py`, `models/calibrate.py`)

- Classifier: `HistGradientBoostingClassifier(max_iter=400, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0, early_stopping=True, validation_fraction=0.15, categorical_features=[instrument_id, subclass, session, vol_regime], monotonic_cst={agreement_count:+1}, class_weight="balanced", random_state=fixed)`.
  Hyperparameter search: small grid (3×3×2 over lr/leaves/min_leaf), every config logged to trials registry. No Bayesian search in v2.1 — keep the trial count small and honest for PBO.
- Calibration: `IsotonicRegression` fitted on a dedicated chronologically-last 15% slice of each training fold (never the eval fold). Report Brier, Brier skill vs base rate, and reliability table (10 bins) per family per fold.
- Quantile heads: `HistGradientBoostingRegressor(loss="quantile", quantile=q)` for q ∈ {0.1,0.25,0.5,0.75,0.9} on each of `net_R`, `MAE_R`, `MFE_R`, `hold_bars` (20 regressors per family-horizon; trained with the same folds; acceptable cost).
- Quantile-crossing fix: sort predicted quantiles per row post-hoc.
- Core vs enriched: two artifacts per family-horizon. Inference routing: enriched if **all** enriched features present and verified, else core. Route recorded in `dataQuality`.

## 7. Pair residual adapters (`models/adapters.py`) — off by default
Unchanged criteria: ≥500 labels, ≥30 OOS signals, improvement in ≥3/4 folds, no Brier degradation. Implementation: per-instrument logistic regression on `[pooled_logit, ret_z_1, vol_level]` only (3 features max — adapters must stay shallow). Enabled per instrument via artifact manifest, never config.

## 8. Decision rule (`inference/predict.py`)

```
E_win  = q50(MFE_R) capped at k_tp           # realistic win magnitude
E_loss = −max(q50(MAE_R), 0.25)              # never assume loss < 0.25R
expectedNetR = P_cal × E_win + (1 − P_cal) × E_loss   (already net of costs via labels)

TRADE  iff expectedNetR ≥ thr_family
       and P_cal ≥ 0.55
       and |P_cal − 0.5| ≥ 0.05
       and dataQuality.coreOk
WATCH  iff expectedNetR > 0 and not TRADE
FLAT   otherwise (incl. no candidate)
ERROR  on artifact/schema/integrity failure
```

- `thr_family` is set per family from walk-forward as the threshold maximizing OOS expectancy subject to ≥40 OOS trades remaining, then **frozen into the artifact manifest**. Starting research prior: 0.10 R.
- `signalStrength = round(100 × clip(expectedNetR / 0.5, 0, 1))` — purely cosmetic for compatibility.
- One inference path: scan, backtest, calibration, chart review, shadow, and demo all call `inference.predict.predict_batch()`. CI parity test hashes features+predictions from research and runtime paths on a fixture window and asserts equality.

---

# PART V — LEVELS & EXITS (`levels/brackets.py`)

```
entryReference = decision-bar close
entryZone      = [entryReference − d×0.25×σ_bar×sqrt(H)^0.5 → entryReference]  (one-sided, favorable)
sl   = entryReference − d × max(q90(MAE_R), 0.6) × R_unit      # R_unit = k_sl×σ_bar×sqrt(H) in price
tp1  = entryReference + d × q50(MFE_R) × R_unit
tp2  = entryReference + d × q75(MFE_R) × R_unit
maxHoldBars = H (time stop, hard)
```

- Sanity clamps: `sl` distance ∈ [0.5, 1.5] × R_unit; `tp1 ≥ 0.6 × sl distance` else demote to WATCH (RR floor remains enforced downstream too).
- Coverage validation: realized MAE may breach the q90-based stop on ≤ 12% of OOS trades per family; breach → widen `k_sl` and re-validate, do not patch at runtime.
- Static brackets + time stop only. No trailing, no exit modes. MT5 TP2 convention unchanged (separate pending limit, half volume, after main fill).

---

# PART VI — VALIDATION & RESEARCH HARNESS

## 9. Splits (`athena_research/ase/walkforward.py`)
- Chronological: final 20% holdout untouched until the end (one evaluation, ever; result recorded whether good or bad).
- Remaining 80% → 4 expanding folds. **Purge:** drop training candidates whose label window overlaps the test window. **Embargo:** additionally drop training candidates within `H+1` bars before test start and after test end.
- All folds constructed on candidate *events*, not bars.

## 10. Trials registry (`trials_registry.py`)
Append-only JSONL: `{trial_id, timestamp, family, horizon, config_hash, fold_results, aggregate_sharpe, notes}`. Every configuration ever evaluated — including abandoned, including Layer 1 parameter variations — is a row. DSR/PBO computed from this registry (`dsr_pbo.py`: Bailey–López de Prado deflated Sharpe; CSCV with 16 partitions for PBO). Deleting rows is a process violation; the registry hash goes in the artifact manifest.

## 11. Evidence ladder (unchanged gates, restated as code constants)

```python
PROVISIONAL = dict(min_oos_trades=40, min_instruments=4, folds_nonneg=3,
                   max_instrument_profit_share=0.40, max_dd_R=12,
                   cost_stress_ok_at=1.5, brier_skill_min=0.0)
VALIDATED   = dict(min_oos_trades=150, bootstrap_lb_expectancy_gt=0.0,
                   dsr_min=0.95, pbo_max=0.20, cost_stress_ok_at=2.0,
                   reliability_monotone_tol=0.05)
```

Per-family pass/fail; failing family ships FLAT-only. Bootstrap: stationary block bootstrap, block = 10 trades, 5000 resamples.

## 12. Monitoring & drift (`inference/monitor.py`) — NEW in v2.1
- **Feature drift:** PSI per feature, weekly, live vs training distribution. PSI > 0.25 on ≥3 features → `modelHealth.driftScore` escalates; ≥5 features or any single PSI > 0.5 → family auto-demotes to WATCH-max (cannot emit TRADE) and flags for retrain review. Auto-demotion to WATCH is the only automatic state change permitted; promotion is always manual.
- **Calibration drift:** rolling 50-trade realized win rate vs mean P_cal; |gap| > 0.12 → WATCH-max, flag.
- **Shadow parity:** after 30 shadow days, KS test on shadow expectedNetR vs same-period backtest at α=0.05; fail → investigate before promotion.

---

# PART VII — DEPLOYMENT, GATES, CONTRACTS

## 13. Demo-only gate (`gates/demo_only.py`)
In the **inference path** (defense in depth; executor safeguards unchanged):

```python
def assert_demo() -> GateResult:
    ok_mode  = EXECUTOR_MODE in {"paper", "demo"}
    ok_mt5   = mt5.account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO  # when MT5 route
    ok_bybit = bybit_base_url.endswith("api-testnet.bybit.com")          # when Bybit route
    # any failure → engine emits ERROR with reason; never TRADE
```

Gate result is recorded on every emitted signal. There is no override flag. Going live later requires a code change in a reviewed release, by design.

## 14. Canonical contract (`contracts.py`)

```python
@dataclass(frozen=True)
class ASESignal:
    engineVersion: str; modelFamily: str; modelVersion: str
    horizon: Literal["intraday","swing"]
    decisionStatus: Literal["TRADE","WATCH","FLAT","ERROR"]
    direction: Literal["LONG","SHORT","NONE"]
    expectedNetR: float; expectedNetBps: float
    probabilityPositive: float; decisionMargin: float; signalStrength: int
    returnQ: dict; maeQ: dict; mfeQ: dict; holdQ: dict   # {q10..q90}
    entryReference: float; entryZone: tuple[float,float]
    sl: float; tp1: float; tp2: float; maxHoldBars: int
    primarySignals: list[dict]            # {name, direction, rawStrength}
    predictionDiagnostics: dict           # topFeatureAblation: top-5 leave-one-out logit deltas
    dataQuality: dict                     # coreOk, route("core"|"enriched"), missingFeeds[]
    modelHealth: dict                     # artifactHash, trainedAt, brier, driftScore, gateResult
    # compatibility properties:
    @property
    def confluenceScore(self): return self.signalStrength
    @property
    def maxScore(self): return 100
    @property
    def scoreNorm(self): return self.signalStrength/100
    @property
    def confidence(self): return self.probabilityPositive
```

Engine C consumes `decisionStatus/probabilityPositive/signalStrength` + levels; no legacy floors. Chart-AI review receives the full payload as text context plus `primarySignals` so Opus reviews economics, not a bare number. React: separate ASE panel — expectedNetR, P_cal with uncertainty band (q10–q90 net_R), horizon, route, model health, drift badge, top-5 ablations, primary-signal chips; SHADOW watermark until promoted. EMA/RSI/ADX overlays stay as generic visuals, parity claims removed.

## 15. Artifacts & CLI
- `%LOCALAPPDATA%\Athena\models\ase\{family}\{horizon}\{version}\` → `model_core.pkl, model_enriched.pkl, quantile_heads.pkl, calibrator.pkl, manifest.json`.
- Manifest: model hashes, feature schema+hash, dataset hash, PTIS snapshot id, cost model version, label params (k_sl,k_tp,H), thr_family, validation report, trials-registry hash, sklearn version, train timestamp, adapter list, signer.
- `ase_cli.py`: `train --family --horizon`, `validate`, `promote --family`, `demote --family`, `shadow-report`, `parity-check`, `drift-report`. Append-only audit log `ase_audit.jsonl` with operator/timestamp/artifact hash. No automatic promotion ever; auto-demotion to WATCH-max per §12 only.
- Shadow journal `ase_shadow_journal.parquet`: full ASESignal payload + later-filled realized outcome (the journal is also the OOS evidence accumulator for the 150-trade ladder).

---

# PART VIII — TESTS (one file per concern; run individually per repo policy)

| File | Asserts |
|---|---|
| `test_ptis_availability.py` | asof() never returns rows with available_time > decision_time; COT Friday→Monday lag; FRED vintage |
| `test_feature_causality.py` | shifting future data leaves features unchanged (shuffle-future invariance test) |
| `test_label_leakage.py` | labels for fold-k test events never appear in fold-k training (purge+embargo) |
| `test_triple_barrier.py` | both-barriers-one-bar resolves adverse-first; net_R cost arithmetic; uniqueness weights |
| `test_signals_tsmom/carry/xsec/meanrev.py` | formulas vs hand-computed fixtures; mean-rev trend-suppression rule |
| `test_arbitration.py` | conflict→NONE; 2× dominance; event spacing suppression |
| `test_calibration.py` | isotonic fitted on calib slice only; reliability monotone within tol on fixture |
| `test_quantile_heads.py` | crossing fix; bracket clamps; WATCH demotion on RR floor |
| `test_decision_rule.py` | TRADE/WATCH/FLAT boundaries incl. thresholds from manifest, not config |
| `test_artifacts.py` | hash verification; schema mismatch → ERROR; determinism (same snapshot → same dataset hash) |
| `test_adapters.py` | gating criteria; ≤3 features; Brier non-degradation |
| `test_missing_feeds.py` | enriched-missing → core route; core-missing → FLAT; UNVERIFIED_LAG exclusion |
| `test_demo_gate.py` | non-demo MT5 trade_mode → ERROR; non-testnet Bybit URL → ERROR; no override path exists |
| `test_legacy_bypass.py` | every legacy Engine A entry point raises LegacyEngineBypassed for promoted families; representative forex/crypto/commodity/index/US-equity/JSE/ETF/TLT scans still complete |
| `test_parity.py` | research vs runtime feature+prediction hash equality on fixture window |
| `test_drift_monitor.py` | PSI thresholds; auto-demote to WATCH-max; manual-promotion-only invariant |
| `test_contract_aliases.py` | confluenceScore/maxScore/scoreNorm/confidence aliases; Engine C consumes without legacy floors |

Plus `npm run build` in `static/react-app/app` after Phase 3 UI work.

---

# PART IX — TICKETED WORK ORDER (Claude Code)

**Phase 0 — Data foundations** *(blockers for everything)*
- T0.1 `ptis.py` + catalog + asof() + tests
- T0.2 Ingest EODHD/Dukascopy/Bybit into PTIS with availability rules
- T0.3 COT release-lag ingestion (per CFTC publication calendar) + FRED vintage handling
- T0.4 `costs.py` v0 table + broker measurement script (sample MT5/Bybit live spreads by session for one week → cm-2026.06.1)
- T0.5 `reports/availability_audit.md`
**Exit:** audit report; determinism test green.

**Phase 1 — Layer 1 + cost-aware event backtest (no ML)**
- T1.1–T1.4 four signal modules + tests
- T1.5 arbitration + event spacing + tests
- T1.6 event backtester over PTIS history → per-family candidate counts, raw net expectancy
**Exit:** ≥ ~500 candidates per family-horizon over history; if not, tune Layer 1 thresholds via trials registry before Phase 2 — the meta-model starves otherwise. Layer 1 raw expectancy report (it does NOT need to be positive standalone — the meta-model's job is selection — but it must not be catastrophically negative after costs; < −0.15R mean → revisit signal definitions).

**Phase 2 — Labels, features, models, validation**
- T2.1 triple-barrier + weights; T2.2 feature builder + causality tests
- T2.3 HGB + isotonic + quantile heads + small grid, all trials logged
- T2.4 walk-forward harness, purge/embargo, bootstrap, DSR/PBO
- T2.5 per-family validation reports; freeze thr_family + label params; build artifacts
**Exit:** provisional-grade report per family; holdout still untouched.

**Phase 3 — Runtime integration**
- T3.1 contracts.py + inference path + artifact loading + ERROR paths
- T3.2 demo gate; T3.3 shadow journal + scanner shadow wiring; T3.4 React SHADOW panel + npm build
- T3.5 parity check; T3.6 drift monitor
**Exit:** 30-day shadow clock starts; parity green.

**Phase 4 — Promotion**
- T4.1 holdout evaluation (single shot, recorded) + provisional gate check per family
- T4.2 `promote` per passing family; legacy bypass tests; demo trades flow via unchanged risk_engine/executor
- T4.3 weekly drift + shadow-vs-demo reports
**Exit:** demo trading live for promoted families; failing families remain shadow/FLAT.

---

# PART X — FAILURE-MODE PLAYBOOK

| Symptom | Diagnosis order | Action |
|---|---|---|
| P_cal stuck near base rate, Brier skill ≈ 0 | features uninformative for this family | family ships FLAT-only; do NOT loosen thresholds; revisit Layer 1 candidate quality first |
| Great folds, bad holdout | overfitting despite purge → check trials count, PBO | accept the holdout verdict; family fails; reduce search space before any retry |
| Shadow KS fail | runtime/research divergence: PTIS gaps, route flapping core↔enriched, clock issues | parity-check + dataQuality route histogram before touching the model |
| Stop coverage breach >12% | vol regime shift vs training | widen k_sl, re-validate; never clamp at runtime |
| One instrument dominates profit | concentration → 40% share gate | gate already fails it; check whether that instrument alone passes adapter criteria |
| Live spreads ≫ cost model | cm version stale | re-measure, bump cost model version, re-validate at new costs |

# PART XI — OPEN DECISIONS (need G.'s call, defaults applied if silent)
1. Crypto weekend bars: include in intraday training (24/7) — **default yes**, session feature handles it.
2. JSE equities at intraday horizon: liquidity likely insufficient — **default swing-only**.
3. Commodity curve-slope carry: only if a curve feed already exists in Athena — **default: carry=NONE for commodities in v2.1**, add later.
4. Benchmark proxies (DXY basket, BCOM proxy): construct from existing EODHD symbols — list to be confirmed in T0.2.

# PART XII — HONEST EXPECTATIONS (unchanged, restated)
Phase 0–2 is several weeks before one shadow signal exists. Most likely first outcome: some families pass, some fail — that is the system working; failed families ship FLAT-only. The 150-trade validated bar at swing horizon is a 2027 milestone; intraday accumulates faster. Provisional → shadow → demo is the realistic 2026 path. Do not loosen gates to get trades.