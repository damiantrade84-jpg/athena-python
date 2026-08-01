# Engine A / B Timeframe + Indicator + Threshold Matrix (v4)

Authoritative source resolution as of 2026-07-31. Timeframe roles come from
`timeframe_policy.py` (`POLICY_VERSION = timeframe_policy.v4`); indicator
periods from `config.yaml` `ENGINE_A_EMA/RSI/MACD/ATR_ADX_PERIODS_BY_CLASS`;
thresholds from `config.yaml` ADX/RANGING/score tables and `scoring.py`.

## 1. Timeframe role ladder

The universal ladder below is the **fallback** for Engine A/B when no enabled
per-group/per-style row applies. The checked-in `config.yaml` enables a
selective role-override matrix: equities use a faster intraday structure/setup
pair, thin or spread-expensive groups use an M30 trigger, and configured swing
rows use D1 structure with H4 setup and H1 trigger. Engine D (scalp) is
separate.

| Role | TF |
|---|---|
| regime | D1 |
| bias | H4 |
| structure | H4 |
| setup | H1 |
| trigger | M15 |
| execution | live-quote (advisory M15) |

Speed/liquidity may modify **setup/trigger only** — never regime/bias/structure,
never execution. Intraday vs swing differences live **below** the role ladder
(Engine A scoring weights, entry-timeframe advisory, Engine B min-score floors).

## 2. Per-group policy differences (from resolved matrix)

The active role rows in `ENGINE_TF_ROLE_OVERRIDES` resolve as follows. Roles
are listed as `R / B / S / U / T`; execution remains live-quote based.

| Applied rows | Intraday | Swing |
|---|---|---|
| `equity_index_fast`, `equity_index_standard`, `us_stock_single`, `cash_equity_standard_dynamic` | D1 / H4 / H1 / M30 / M15 | D1 / D1 / D1 / H4 / H1 |
| `forex_exotics_liquid`, `forex_exotics_restricted`, `nat_gas`, `thin_metals_base_softs`, `bond_tlt_smallcap_em_etf` | D1 / H4 / H4 / H1 / M30 (nat gas swing trigger is H4) | D1 / D1 / D1 / H4 / H1 (nat gas trigger is H4) |
| Other configured swing groups | universal fallback | D1 / D1 / D1 / H4 / H1 |
| Unconfigured intraday groups, including majors/crosses, energy oil, liquid metals, and crypto | D1 / H4 / H4 / H1 / M15 | — |

Groups whose policy differs from the pure universal ladder (M5 conditional =
M15 confirmation + M5 refinement; speed = FAST/SLOW baseline):

| Group | m5_policy | baseline_speed |
|---|---|---|
| crypto_btc, crypto_eth, crypto_alt_majors (BTC/ETH/SOL) | conditional | FAST |
| crypto_doge, crypto_other (DOGE/AVAX/…) | disabled | FAST |
| forex_majors (GBP/USD, USD/JPY) | conditional | FAST |
| forex_majors (EUR/USD, AUD/USD, NZD/USD, USD/CAD, USD/CHF) | disabled | NORMAL |
| forex_crosses (GBP/JPY) | conditional | FAST |
| forex_crosses (all others) | disabled | NORMAL |
| forex_exotics (USD/ZAR, USD/MXN) | disabled | SLOW |
| forex_exotics (USD/BRL, USD/INR, EUR/ZAR, GBP/ZAR, USD/HKD) | disabled | SLOW |
| forex_other (USD/CNH, USD/NOK, USD/SEK, EUR/HUF, …) | disabled | NORMAL/SLOW |
| precious_trackers (XAU/USD) | conditional | FAST |
| precious_trackers (XAG/USD) | disabled | NORMAL |
| energy_oil (WTI, BRENT) | conditional | FAST |
| nat_gas | disabled | FAST |
| pgm_metals (XPT/XPD), thin metals, base metals, softs | disabled | NORMAL |
| us_indices_trackers (NAS100, US30, GER40) | conditional | FAST |
| us_indices_trackers (US500, UK100, JPN225) | disabled | NORMAL |
| index_other (CHI50) | disabled | SLOW |
| us_stock_single (AAPL, SPY) | conditional | NORMAL |
| bond_tlt / smallcap_em_etf (TLT, IWM, EEM) | disabled | NORMAL |
| stock_other | disabled | NORMAL |

## 3. Indicator periods per group

EMA (trend/momentum/long), RSI, MACD (fast/slow/signal), ATR/ADX on the setup
(H1) series. ATR/ADX are uniformly Wilder 14/14 everywhere (evidence-gated
hold); MACD 12/26/9 everywhere.

| Group | EMA | RSI | MACD | ATR/ADX |
|---|---|---|---|---|
| default / commodities / indices / stocks | 21/50/200 | 14 | 12/26/9 | 14/14 |
| forex majors / crosses / other | 26/60/200 | 18 | 12/26/9 | 14/14 |
| forex exotics | 24/55/200 | 16 | 12/26/9 | 14/14 |
| crypto (all) | 18/40/200 | 12 | 12/26/9 | 14/14 |
| bond_tlt | 34/80/200 | 21 | 12/26/9 | 14/14 |

## 4. Thresholds per class

### 4.1 ADX gates (Engine A factor gate + trend-state)

| Class | ADX trend-min gate | TRENDING cutoff | DEVELOPING cutoff | RANGING dead/choppy |
|---|---|---|---|---|
| crypto | 18 (doge/other 20) | 28 | 18 | 18/23 |
| forex | 20 (exotics 18) | 30 | 20 | 18/23 |
| commodity | 25 (copper/pgm 26, nat_gas 27) | 35 | 25 | 18/23 |
| stock | 25 | 35 | 25 | — |
| index | 25 | 35 | 25 | — |

ADX hard-fail: default 10 (crypto_other/nat_gas 12, forex_exotics 9). ADX missing
both D1+H4 aborts.

### 4.2 Engine A score threshold (`scoring.py` 3-tier)

| Tier | Threshold | Groups |
|---|---|---|
| volatile | 2.0 | crypto class, nat_gas, crypto_doge |
| exotic | 1.7 | forex_exotics, softs |
| stable | 1.5 | everything else (XAU/XAG override 1.5) |

Per-pair profile `min_confluence` overrides all tiers. Regime dynamic
thresholds (TRENDING x0.90 / RANGING x1.10 / HIGH_VOL x1.15) exist but are
config-gated OFF by default.

### 4.3 Engine B style floors

| Style | min score | (shadow quality ratio) |
|---|---|---|
| scalp | 4.0 | 0.30 |
| intraday | 4.5 | 0.35 |
| swing | 5.0 | 0.40 |

`ENGINE_B_MIN_SCORE_BASIS: total` — quality-ratio basis is placeholder/shadow
until n>=30 evidence.

### 4.4 Engine B trigger-TF calibration (setup/trigger ATR + candle geometry)

| Group | trigger ATR | rejection wick/body | engulfing body ATR | strong close % |
|---|---|---|---|---|
| forex majors | 10 | 1.5 | 0.25 | 0.70 |
| forex crosses | 11 | 1.4 | 0.22 | 0.70 |
| forex other | 11 | 1.3 | 0.22 | 0.70 |
| forex exotics | 12 | 1.2 | 0.20 | 0.65 |
| crypto btc/eth | 10 | 1.0 | 0.30 | 0.75 |
| crypto doge/alt/other | 11 | 1.0 | 0.35 | 0.75 |
| precious/copper/pgm/base/softs/commodity_other | 10 | 1.3 | 0.25 | 0.70 |
| energy_oil / nat_gas | 12 | 1.2 | 0.30 | 0.70 |
| stocks / indices / bond | 10 | 1.4 | 0.22 | 0.70 |

### 4.5 Directional ramp (Engine A)

crypto 0.04–0.05, forex 0.045–0.05, stocks 0.06, indices/commodities 0.05.

## 5. Intraday vs swing — where they actually differ

The active role matrix now creates the following role differences in addition
to the scoring knobs:

1. **Intraday equities** use H1 structure, M30 setup, and M15 trigger; thin
   groups use H4 structure, H1 setup, and M30 trigger.
2. **Swing rows** use D1 regime/bias/structure, H4 setup, and H1 trigger;
   nat gas uses an H4 trigger.
3. **Engine A trend weights** (`ENGINE_A_SCORING_PROFILE.BY_STYLE`): swing
   D1 0.50 / H4 0.30 / H1 0.20; intraday D1 0.42 / H4 0.33 / H1 0.25.
4. **Engine A advisory entry TF** (`resolve_v3_entry_timeframe`): intraday H1,
   swing H4. Superseded in the live path by the policy setup rung (H1) when the
   policy is authoritative — see `evaluator.py:511` / `quant_scorer.py:1080`.
5. **Engine B min-score floor**: intraday 4.5 vs swing 5.0.
6. **Per-group scoring overrides**: `energy_oil`/`commodity_other` drop D1
   (H4 0.55 / H1 0.45); stock/index D1 0.40; `bond_tlt` momentum+regime on D1.

## 6. Implementation and remaining evidence gaps

- **The active matrix is selective, not universal.** The fallback remains
  D1/H4/H4/H1/M15, while the configured rows above change intraday equities,
  thin groups, and swing roles.
- **The recommendation document is not identical to the shipped matrix.** FX
  majors/crosses have no intraday override and therefore retain the universal
  ladder. CHI50 resolves through `equity_index_standard`, so its active
  intraday row is H1/M30 rather than the recommendation's H4/M30.
- **External trading-consensus claims and profitability are not verified by
  this source audit.** They should not be treated as proof that the promoted
  rows are optimal without outcome evidence and a separately approved policy
  decision.
