# Engine A Nuclear Audit - Codex Phase 1 Findings

Date: 2026-05-12

Mode: Audit-only. No source patches were made.

## Scope Inspected

Files read in full by parent agent and/or read-only subagents before findings:

- `scoring.py`
- `factor_scoring.py`
- `indicators.py`
- `config.yaml`
- `regime.py`
- `intermarket.py`
- `forex_scoring.py`
- `confidence_engine.py`
- `calibration.py`

Additional snippets inspected for combined-conviction and live gate tracing:

- `scanner.py`
- `auto_trader.py`
- `config.py`

Commands/tests run:

- Read-only `Get-Content`, `rg`, `git status --short`, and YAML parse commands.
- No pytest suite was run because this was audit-only.
- A synthetic import probe of `factor_scoring` was attempted, but runtime config validation refused startup due live/real-order safety config. I did not bypass that guard.

Not verified:

- Broker execution handoff after `auto_trader.py`.
- Full real historical ADX distributions from production datasets.
- Runtime behavior in temp/pytest directories where `rg` hit Windows access-denied paths.

## Scoring Mathematics

Active Engine A path:

- `scoring.py:624-675` calls `factor_scoring.compute_factor_scores(...)`.
- `factor_scoring.py:1474-1981` computes and returns active Engine A v2 scores.
- `forex_scoring.py:4-5` states live Engine A no longer routes forex through `forex_scoring.py`.

Normalization:

- No active Engine A v2 `final_score` normalization by a hardcoded denominator was found.
- Active final score is multiplicative, not a weighted-sum normalizer:
  - `factor_scoring.py:1770-1778`: `base_score = abs(trend_score) * adx_mult * vol_scaler * session_mult * di_align_mult * dir_ramp_mult * vwap_mult`
  - `factor_scoring.py:1876-1881`: applies conviction blend, cost penalty, total adjustment, mean-reversion adjustment, then clamps to `[0.0, 3.0]`.
- Trend vote weighting uses active raw vote weights:
  - `factor_scoring.py:221-230` reads `INDICATOR_WEIGHTS.trend`.
  - `factor_scoring.py:266-288` computes long/short active weight sums and coherence ratio.
- Momentum weighting uses active RSI/MACD weights:
  - `factor_scoring.py:601-611` reads `INDICATOR_WEIGHTS.momentum.rsi_z` and `macdLine_z`, then divides by `total_w`.

Combined conviction:

- `scanner.py:897-898`: aligned A/B combined conviction is `(a_norm * _w_a) + (b_norm * _w_b)`.
- `scanner.py:911-912` and `scanner.py:930`: A-only fallback is `a_norm * _a_only_auto_weight(pair)`.
- `scanner.py:120-137`: `_a_only_auto_weight()` reads `AUTO_TRADE_A_ONLY_WEIGHT`, defaulting to `0.60`.
- `config.yaml:813-819`: `AUTO_TRADE_MIN_CONVICTION.default = 0.50`; `AUTO_TRADE_A_ONLY_WEIGHT.default = 0.60`.
- `auto_trader.py:132-157`: recompute fallback returns `A_norm * 0.6` when not aligned.
- `auto_trader.py:696-704`: execution rejects when `combined_conviction < auto_min_conviction`.

Conclusion: the known structural cap appears fixed in the current code. A perfect A-only signal can reach `1.0 * 0.60 = 0.60`, above the default gate `0.50`. Required A-only Engine A score at maxScore `3.0` is `(0.50 / 0.60) * 3.0 = 2.50`. This is reachable but stricter than scan thresholds.

BTC bias condition:

- `scoring.py:681`: BTC bias branch requires `pair.type == "crypto"`, `btc_bias` truthy and not neutral, and Engine A direction present.
- `scoring.py:682`: excludes displays containing `BTC`.
- `scoring.py:685-691`: prefers real 30d correlation if price series are supplied, else heuristic fallback.
- `scoring.py:692-708`: multiplier is only non-neutral when correlation is at least `0.50`; below `0.50`, `_btc_mult = 1.0`.
- `scoring.py:711-719`: final score is multiplied only when `_btc_mult != 1.0`.

Directional vs nondirectional:

- `factor_scoring.py:202-319` computes `trend_score` and direction from EMA trend only.
- `factor_scoring.py:498-618` computes `mom_quality` from H4 RSI/MACD only and does not change direction.
- `factor_scoring.py:1934-1935` returns `directional_score = trend_score`, `nondirectional_score = mom_quality`.
- No cross-contamination was found in active scoring.

Score group multipliers:

- `config.yaml:1374-1376` comments that `REGIME_WEIGHTS`, `FACTOR_SCORE_GROUP_MULTIPLIERS`, and `CRYPTO_FACTOR_WEIGHT_CAPS` are legacy/inactive for Engine A v2.
- `rg` found no active read of `FACTOR_SCORE_GROUP_MULTIPLIERS` in `scoring.py` or `factor_scoring.py`.
- Therefore the requested "applied after normalization vs before" check resolves to: not applied at all.

## Indicator Mathematics

RSI:

- `indicators.py:60-61`: `calc_rsi(c, p)` is documented as Wilder RSI.
- `indicators.py:79`: initial average gain/loss uses `g / p`, `loss / p`.
- `indicators.py:86-88`: recursive update uses `(ag * (p - 1) + gain) / p` and `(al * (p - 1) + loss) / p`.
- Verdict: standard Wilder smoothing for `calc_rsi`.

Bollinger Bands:

- `indicators.py:241`: `calc_bb()` divides by `(p - 1)`.
- `indicators.py:296`, `indicators.py:319`, and `indicators.py:816` repeat sample-std calculation in squeeze / BB-width helpers.
- `factor_scoring.py:1346` uses `len(window) - 1` in mean-reversion BB math.
- Verdict: sample std (`ddof=1`) is used. Standard Bollinger Band implementation should use population std (`ddof=0`).

ATR:

- `indicators.py:127-131`: true range is `max(high-low, abs(high-prev_close), abs(low-prev_close))`.
- `indicators.py:139`: initial ATR is simple average of the first period true ranges.
- `indicators.py:141-142`: subsequent ATR uses Wilder smoothing.
- `indicators.py:1104`: core bundle hardcodes ATR period `14`.
- Verdict: Wilder ATR, period hardcoded to `14` in the core bundle.

ADX:

- `indicators.py:167-175`: computes `+DM`, `-DM`, and true range.
- `indicators.py:177-189`: applies Wilder smoothing to TR, +DM, -DM.
- `indicators.py:191-192`: `+DI` and `-DI` are smoothed DM over smoothed TR times `100`.
- `indicators.py:200`: DX is `abs(+DI - -DI) / (+DI + -DI) * 100`.
- `indicators.py:202-218`: ADX is the smoothed DX series, not raw DX.
- `indicators.py:1105`: core bundle hardcodes ADX period `14`.
- Verdict: ADX math is structurally correct; period is hardcoded to `14`.

Config lookbacks and periods:

- `indicators.py:1099-1106` hardcodes EMA `21/50/200`, RSI `14`, ATR `14`, ADX `14`, BB `20,2`.
- `indicators.py:1118` reads `NORMALIZATION_LOOKBACK` through `get_normalization_lookback(asset_type)`.
- `factor_scoring.py:460-465` reads stochastic RSI config periods and thresholds.
- `factor_scoring.py:1248-1254` reads VWAP filter config.
- `factor_scoring.py:1330-1337` reads mean-reversion config weights.

## Findings

BUG-A-1 - Dead Factor Weight Surface

Severity: HIGH
File: `config.yaml`, `factor_scoring.py`
Line: `config.yaml:1160`, `config.yaml:1392`, `factor_scoring.py:221`, `factor_scoring.py:601`, `factor_scoring.py:1719`, `factor_scoring.py:1876`
What: Active Engine A only applies `INDICATOR_WEIGHTS.trend` and `INDICATOR_WEIGHTS.momentum.rsi_z/macdLine_z`. Configured `derivatives`, `microstructure`, `volatility`, `volume`, `carry`, and `volume_momentum_spread` are not score inputs. `FACTOR_SCORE_GROUP_MULTIPLIERS` is not read by active scoring at all.
Should: Either remove/mark these as legacy-only, or wire the configured factor surfaces into the active v2 scoring path.
Impact: Operators can tune config keys that have no production effect. Crypto subgroup multipliers and many factor weights are dead, which makes calibration misleading.
Fix: Minimal concrete option if the intent is to wire subgroup multipliers into v2 after normalized component scores:

```python
group_mults = CONFIG.get("FACTOR_SCORE_GROUP_MULTIPLIERS", {}).get(score_group, {})
trend_score *= float(group_mults.get("trend", 1.0))
mom_quality = max(0.0, min(1.0, mom_quality * float(group_mults.get("momentum", 1.0))))
```

If the intent is that these remain legacy-only, remove them from active `config.yaml` or rename them under a `LEGACY_ENGINE_A_V1_*` block.
Test: Set `FACTOR_SCORE_GROUP_MULTIPLIERS.crypto_alt_majors.momentum = 0.5`; verify identical input produces lower `mom_quality` or `final_score`. Also add a config-contract test that every non-legacy `INDICATOR_WEIGHTS` key is consumed by active Engine A.

BUG-A-2 - Bollinger Bands Use Sample Std

Severity: MEDIUM
File: `indicators.py`, `factor_scoring.py`
Line: `indicators.py:241`, `indicators.py:296`, `indicators.py:319`, `indicators.py:816`, `factor_scoring.py:1346`
What: Bollinger calculations divide variance by `p - 1` / `len(window) - 1`, i.e. sample standard deviation.
Should: Bollinger Bands should use population standard deviation (`ddof=0`), dividing by `p` / `len(window)`.
Impact: Bands are wider than standard BB, which changes squeeze detection, BB width percentile, and mean-reversion factor output. Wider bands can under-detect touches/compression.
Fix:

```python
sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / p)
```

For `factor_scoring.py`:

```python
variance = sum((x - mean) ** 2 for x in window) / max(1, len(window))
```

Test: Compare `calc_bb([1..20], 20, 2)` to a NumPy reference using `np.std(window, ddof=0)`. Add a regression asserting `calc_squeeze()` and `calc_bb_width_percentile()` use the same population-std convention.

BUG-A-3 - Pair Profiles Can Undercut Global Threshold

Severity: HIGH
File: `scoring.py`, `config.yaml`
Line: `scoring.py:288-297`, `config.yaml:1669`, `config.yaml:1681`
What: `PAIR_PROFILES.min_confluence` overrides all pair/group thresholds. `XAU/USD` and `XAG/USD` set `min_confluence: 1.05`, below the global default `1.5`.
Should: Profile overrides should not silently bypass the configured floor unless the key explicitly means "allow below global minimum" and is separately audited.
Impact: Precious-metal signals can pass scan/backtest gates below the global minimum score. This is especially risky because `PAIR_PROFILES` also contains legacy-looking `weight_overrides` that active Engine A does not consume.
Fix:

```python
configured = _configured_score_threshold(pair)
floor = configured if configured is not None else _get_threshold_tier(pair)
if profile.get("min_confluence") is not None:
    base_threshold = max(float(profile.get("min_confluence")), float(floor))
else:
    base_threshold = float(floor)
```

Alternative: rename the profile key to `allow_below_global_min_confluence` and require explicit `true`.
Test: Assert `get_score_threshold({"display": "XAU/USD", "type": "commodity"}) >= 1.5` unless an explicit allow-below-global flag is set.

BUG-A-4 - Stochastic RSI Uses Simple RSI

Severity: LOW
File: `indicators.py`, `factor_scoring.py`
Line: `indicators.py:588-607`, `factor_scoring.py:456-472`
What: `calc_stochastic_rsi()` computes RSI using simple average gains/losses, while `calc_rsi()` uses Wilder smoothing.
Should: StochRSI should reuse Wilder RSI unless deliberately documented as a separate simple-RSI variant.
Impact: Currently `ENGINE_A_STOCHASTIC_RSI.ENABLED` is false, so production impact is dormant. If enabled, this would introduce RSI math inconsistent with the rest of Engine A.
Fix:

```python
closes = [float(c["close"]) for c in candles if c.get("close") is not None]
rsi_values = calc_rsi(closes, rsi_period)
```

Test: Enable stochastic RSI in a unit test and compare `%K/%D` against a Wilder-RSI-derived StochRSI reference.

BUG-A-5 - Conviction Floor Comment Is Mathematically Backwards

Severity: MEDIUM
File: `config.yaml`, `factor_scoring.py`
Line: `config.yaml:183`, `factor_scoring.py:1876`
What: Config says lowering `FACTOR_CONVICTION_FLOOR` from `0.60` to `0.20` "allow[s] more signals through". The formula is `base_score * (floor + (1-floor) * conviction)`, so lowering the floor reduces low-conviction scores.
Should: Comment and calibration rationale should match the formula.
Impact: Threshold tuning is misleading. With `conviction = 0.20`, max-base score becomes `3.0 * (0.20 + 0.80 * 0.20) = 1.08`. With floor `0.60`, it was `3.0 * (0.60 + 0.40 * 0.20) = 2.04`.
Fix:

```yaml
FACTOR_CONVICTION_FLOOR: 0.20 # lowers weak-conviction scores; high-conviction scores unchanged
```

Test: Unit test fixed `base_score = 3.0`, `conviction = 0.20`, floor `0.20` vs `0.60`; assert the lower floor produces the lower final score.

## Threshold Assessment

`AUTO_TRADE_MIN_CONVICTION.default = 0.50` - CALIBRATED - A-only cap is `0.60`, so a perfect A-only signal can pass; required A score is about `2.50/3.0`.

`AUTO_TRADE_A_ONLY_WEIGHT.default = 0.60` - CALIBRATED - fixes the old structural issue where A-only could not exceed gate, but still requires high Engine A score.

`AUTO_TRADE_A_ONLY_WEIGHT.crypto = 0.60` - CALIBRATED - same A-only cap as default.

`AUTO_TRADE_MIN_SCORE.crypto = 2.0` - NOT LIVE GATE - config comments and `auto_trader.py:329-346` indicate informational score floor; live gate is combined conviction.

`AUTO_TRADE_MIN_SCORE.forex = 2.1` - NOT LIVE GATE - informational for live execution; still useful as scan/operator metadata.

`AUTO_TRADE_MIN_SCORE.commodity = 1.8` - NOT LIVE GATE - informational for live execution.

`AUTO_TRADE_MIN_SCORE.stock = 1.8` - NOT LIVE GATE - informational for live execution.

`AUTO_TRADE_MIN_SCORE.index = 1.8` - NOT LIVE GATE - informational for live execution.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.default = 1.5` - CALIBRATED - reachable with full trend, ADX, DI alignment, and moderate conviction.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_btc = 2.0` - TOO_STRICT for A-only auto but reachable - scan can pass at 2.0, but live A-only requires about 2.5.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_eth = 2.0` - TOO_STRICT for A-only auto but reachable - same live A-only gap.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_alt_majors = 2.0` - TOO_STRICT for A-only auto but reachable - scan threshold below A-only live requirement.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_doge = 2.0` - TOO_STRICT for A-only auto but reachable - volatility scaler clamp helps, but A-only live still needs about 2.5.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_meme = 2.2` - TOO_STRICT but reachable - high scan threshold, still below A-only live requirement.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_other = 2.0` - TOO_STRICT for A-only auto but reachable.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.nat_gas = 2.0` - TOO_STRICT but reachable - `vol_scaler` is clamped neutral-or-boost for `nat_gas`.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.forex_exotics = 1.7` - CALIBRATED/STRICT - above default; reachable but lower than A-only auto equivalent.

`ENGINE_A_SCORE_GROUP_THRESHOLDS.softs = 1.7` - CALIBRATED/STRICT - above default; reachable.

`PAIR_PROFILES.XAU/USD.min_confluence = 1.05` - TOO_LOOSE - profile override can undercut global default `1.5`.

`PAIR_PROFILES.XAG/USD.min_confluence = 1.05` - TOO_LOOSE - same undercut.

`ADX_TREND_MIN_CLASS.crypto = 18` - CALIBRATED - full credit at ADX >= 18 after hard fail 10; not unreachable.

`ADX_TREND_MIN_CLASS.forex = 20` - CALIBRATED - full credit at ADX >= 20; developing trends can pass.

`ADX_TREND_MIN_CLASS.commodity = 25` - STRICT - reachable, but more demanding.

`ADX_TREND_MIN_CLASS.stock = 25` - STRICT - reachable, but more demanding.

`ADX_TREND_MIN_CLASS.index = 25` - STRICT - reachable, but may suppress lower-volatility index trends.

`FACTOR_ADX_HARD_FAIL_CLASS.* = 10` - CALIBRATED - creates linear ramp to class trend minimum.

`ADX_MISSING_BOTH_ABORT = true` - CALIBRATED - fail-closed when ADX is unavailable.

`FACTOR_MIN_DIRECTIONAL = 0.25` - CALIBRATED - reachable; weak/noisy trend aborts.

`FACTOR_DIRECTIONAL_SOFT_SPAN = 0.20` - CALIBRATED - creates smooth ramp from 0.25 to 0.45.

`FACTOR_MIN_DIRECTIONAL_CRYPTO = 0.20` - CALIBRATED - slightly looser for crypto.

`FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO = 0.30` - CALIBRATED - wider ramp for crypto.

`FACTOR_CONVICTION_FLOOR = 0.20` - TOO_STRICT vs comment - mathematically lowers weak-conviction scores compared to old 0.60 floor.

`FACTOR_FUNDING_BASELINE = 0.0001` - CALIBRATED - matches configured funding noise intent; exact live distribution not verified.

`FACTOR_FUNDING_NOISE_BAND = 0.0001` - CALIBRATED/STRICT - narrower band makes more funding deviations active; historical funding distribution not verified.

`FACTOR_CRYPTO_ADDON_COMBO_CONFIRM_CAP = 0.25` - CALIBRATED - bounded crypto funding/OI combo.

`FACTOR_CRYPTO_ADDON_COMBO_AGAINST_CAP = -0.20` - CALIBRATED - bounded negative combo.

`ENGINE_A_RESEARCH_LAB_FACTORS.BONUS = 0.15` - CALIBRATED - bounded addon; contributes through `addon_val`.

`ENGINE_A_RESEARCH_LAB_FACTORS.PENALTY = -0.10` - CALIBRATED - bounded negative addon.

`ENGINE_A_RESEARCH_LAB_FACTORS.MAX_ABS = 0.20` - CALIBRATED - aligned with addon cap.

`ENGINE_A_MEAN_REVERSION.MAX_ABS = 0.15` - DISABLED - dormant unless `ENGINE_A_MEAN_REVERSION.ENABLED` becomes true.

`ENGINE_A_MEAN_REVERSION.BB_WEIGHT = 0.40` - DISABLED - dormant.

`ENGINE_A_MEAN_REVERSION.RSI_WEIGHT = 0.35` - DISABLED - dormant.

`ENGINE_A_MEAN_REVERSION.Z_WEIGHT = 0.25` - DISABLED - dormant.

`ENGINE_A_VWAP_FILTER.MAX_BOOST = 0.03` - DISABLED - dormant.

`ENGINE_A_VWAP_FILTER.MAX_PENALTY = -0.03` - DISABLED - dormant.

`ENGINE_A_VWAP_FILTER.CANDLE_LOOKBACK = 96` - DISABLED - dormant.

`ENGINE_A_STOCHASTIC_RSI.RSI_PERIOD = 14` - DISABLED/PARTIAL - dormant, and StochRSI uses simple RSI if enabled.

`ENGINE_A_STOCHASTIC_RSI.STOCH_PERIOD = 14` - DISABLED - dormant.

`ENGINE_A_STOCHASTIC_RSI.K_SMOOTH = 3` - DISABLED - dormant.

`ENGINE_A_STOCHASTIC_RSI.D_SMOOTH = 3` - DISABLED - dormant.

`ENGINE_A_STOCHASTIC_RSI.OVERBOUGHT = 80` - DISABLED - dormant.

`ENGINE_A_STOCHASTIC_RSI.OVERSOLD = 20` - DISABLED - dormant.

`RSI_BOUNDS.crypto = 80/20` - CALIBRATED - wired through `_resolve_class_keyed`.

`RSI_BOUNDS.forex = 70/30` - CALIBRATED - wired.

`RSI_BOUNDS.commodity = 75/25` - CALIBRATED - wired.

`RSI_BOUNDS.stock = 70/30` - CALIBRATED - wired.

`RSI_BOUNDS.index = 70/30` - CALIBRATED - wired.

`RSI_BOUNDS.precious_trackers = 75/25` - CALIBRATED - score-group override wired.

`RSI_BOUNDS.energy_oil = 75/25` - CALIBRATED - score-group override wired.

`RSI_BOUNDS.softs = 70/30` - CALIBRATED - score-group override wired.

`RANGING.crypto.dead = 18`, `choppy = 23` - STRICT - can classify many ADX < 23 crypto states as non-trending, but crypto auto-trader does not block `RANGING` in current config.

`RANGING.forex.dead = 18`, `choppy = 23` - CALIBRATED - forex auto-trader blocked states are empty.

`RANGING.commodity.dead = 18`, `choppy = 23` - STRICT - auto-trader blocks `RANGING` for commodity.

`RANGING.stock.dead = 16`, `choppy = 21` - STRICT - auto-trader blocks `RANGING` for stock.

`RANGING.index.dead = 16`, `choppy = 21` - STRICT - auto-trader blocks `RANGING` for index.

`VOLATILITY_SCALER_BANDS.forex = 0.0005..0.0025` - CALIBRATED - matches config comment; historical distribution not verified.

`VOLATILITY_SCALER_BANDS.crypto = 0.010..0.040` - CALIBRATED - reachable for BTC/ETH style H4 ATR%.

`VOLATILITY_SCALER_BANDS.commodity = 0.003..0.015` - CALIBRATED - broad commodity band.

`VOLATILITY_SCALER_BANDS.stock = 0.005..0.020` - CALIBRATED - broad single-name band.

`VOLATILITY_SCALER_BANDS.index = 0.002..0.010` - CALIBRATED/STRICT - high band may be strict for calmer index regimes.

`VOLATILITY_SCALER_BANDS.precious_trackers = 0.003..0.015` - CALIBRATED - score-group override wired.

`VOLATILITY_SCALER_BANDS.energy_oil = 0.003..0.015` - CALIBRATED - score-group override wired.

`VOLATILITY_SCALER_BANDS.crypto_meme = 0.015..0.060` - CALIBRATED - wider meme band.

`NORMALIZATION_LOOKBACK.crypto = 300` - PARTIAL - used by indicator normalization, but active Engine A v2 score mostly consumes raw snap RSI/MACD/ATR rather than normalized `*_z` fields.

`NORMALIZATION_LOOKBACK.forex = 400` - PARTIAL - same.

`NORMALIZATION_LOOKBACK.commodity = 300` - PARTIAL - same.

`NORMALIZATION_LOOKBACK.stock = 350` - PARTIAL - same.

`NORMALIZATION_LOOKBACK.index = 350` - PARTIAL - same.

`INTERMARKET_CONFIRMATION.enabled = false` - DISABLED - no score effect unless enabled.

`INTERMARKET_CONFIRMATION.engine_a_enabled = false` - DISABLED - `apply_confirmation_to_score()` returns base score unchanged.

`INTERMARKET_CONFIRMATION.engine_a_score_cap = 0.18` - DISABLED - dormant while engine A intermarket is disabled.

`INTERMARKET_CONFIRMATION.severe_contradiction_blocks = false` - DEAD AS GATE - not used as a blocking gate in `intermarket.py`.

`CRYPTO_TRANSITION_PENALTY.enabled = false` - DEAD/DISABLED - no active score read found.

`CRYPTO_TRANSITION_PENALTY_ENABLED = false` - DEAD - no Python consumer found outside config schema/defaults.

## Factor Pipeline Audit

One-line status for factors listed in `INDICATOR_WEIGHTS`:

- `trend.d1_ema_trend` - ACTIVE - computed in `factor_scoring.py:238-240`, weighted by `_w()` from config.
- `trend.h4_ema_trend` - ACTIVE - computed in `factor_scoring.py:248-250`, weighted by `_w()` from config.
- `trend.ema_trend` - ACTIVE - computed in `factor_scoring.py:256-258`, weighted by `_w()` from config.
- `momentum.rsi_z` - ACTIVE/PARTIAL - key is used for weighting, but value comes from raw H4 RSI score, not normalized `rsi_z`.
- `momentum.macdLine_z` - ACTIVE/PARTIAL - key is used for weighting, but value comes from raw H4 MACD histogram sign, not normalized `macdLine_z`.
- `momentum.volume_momentum_spread` - DEAD - configured for crypto but not read by `_momentum_quality()`.
- `derivatives.cot_z` - PARTIAL/DEAD WEIGHT - COT addon exists, but this configured key is not used for active weighting.
- `derivatives.funding_rate` - PARTIAL/DEAD WEIGHT - funding addon exists, but this configured key is not used for active weighting.
- `derivatives.oi_change_z` - PARTIAL/DEAD WEIGHT - OI addon exists, but this configured key is not used for active weighting.
- `derivatives.oi_price_divergence` - PARTIAL/DEAD WEIGHT - OI addon exists, but this configured key is not used for active weighting.
- `microstructure.order_book_imbalance` - DEAD FOR SCORE - only passed into confidence diagnostics if present.
- `microstructure.liquidity_wall_detection` - DEAD FOR SCORE - only passed into confidence diagnostics if present.
- `microstructure.orderflow_delta` - DEAD FOR SCORE - only passed into confidence diagnostics if present.
- `microstructure.liquidity_pressure` - DEAD FOR SCORE - only passed into confidence diagnostics if present.
- `volatility.atr_z` - DEAD FOR SCORE - active score uses `_volatility_scaler()`, not configured volatility weights.
- `volatility.bbWidth_z` - DEAD FOR SCORE - not used in active score weighting.
- `volatility.realized_vol_z` - DEAD FOR SCORE - not used in active score weighting.
- `volume.volume_ratio` - PARTIAL - contributes bounded `_vol_adj`, but configured `INDICATOR_WEIGHTS.volume.volume_ratio` is not used.
- `volume.obv_trend` - DEAD FOR SCORE - OBV helper exists but active Engine A score does not use this configured weight.
- `carry.carry_z` - PARTIAL/DEAD WEIGHT - carry addon exists, but configured `carry_z` weight is not used.

## Regime and Intermarket

Regime:

- `regime.py:45-62` uses mutually exclusive ADX branches for primary `TRENDING` vs `RANGING`.
- `regime.py:79-87` can reassign `RANGING` to `HIGH_VOLATILITY` or `LOW_VOLATILITY` when BB width percentile is supplied.
- Missing ADX returns `RANGING` with low confidence (`regime.py:45-48`).
- Catch-all branch also returns `RANGING` (`regime.py:59-62`).
- The four labels are mutually exclusive in the return payload, and missing/indeterminate state falls back to `RANGING`.

Ranging block:

- Live scan classification only blocks `ENGINE_A_BLOCKED_TREND_STATES` for live scope; flat list config applies to backtest only (`scoring.py:977-981`).
- Auto-trader separately blocks by `AUTO_TRADE_BLOCKED_TREND_STATES` and `AUTO_TRADE_BLOCKED_REGIMES` (`auto_trader.py:714-739`).
- Current config blocks `RANGING` for default/commodity/stock/index, not crypto/forex (`config.yaml:829-855`).
- Therefore valid commodity/stock/index signals can be blocked entirely in auto-trade when `regimeName` or `trendState` is `RANGING`.

Crypto transition penalty:

- `config.yaml:1588` says `CRYPTO_TRANSITION_PENALTY_ENABLED: false` has no score effect.
- `rg` found no active Python score consumer for `CRYPTO_TRANSITION_PENALTY_ENABLED`.
- `CRYPTO_TRANSITION_PENALTY` was found in config schema/defaults only, not active score code.
- Verdict: note is accurate for current active scoring; penalty is dead/dormant.

Intermarket:

- `intermarket.py:28-46` default config has intermarket and Engine A adjustment disabled.
- `intermarket.py:1233-1271` returns neutral when evidence is missing or inactive.
- `intermarket.py:1273-1292` computes aggregate score as `(alignment - contradiction + lead_bonus) * regime_confidence`, clamped to `[-1, 1]`.
- `intermarket.py:1366-1400` applies a bounded additive delta only if both `enabled` and `engine_a_enabled` are true.
- No hard gate/blocking usage of `severe_contradiction_blocks` was found in `intermarket.py`.
- Verdict: intermarket is additive, not a hard gate, in the inspected path.

## Pair Profiles and Group Overrides

- `scoring.py:96-99`: `get_pair_profile()` reads `CONFIG["PAIR_PROFILES"]` by display or symbol.
- `scoring.py:102-120`: `get_pair_score_group()` prioritizes explicit `pair["score_group"]`, then profile `score_group`, then asset-specific fallback.
- `scoring.py:227-248`: configured thresholds resolve pair threshold, score group, asset type, then default.
- `scoring.py:273-313`: final threshold priority is profile `min_confluence`, configured threshold, then 3-tier fallback, with optional dynamic regime multiplier if enabled.
- Pairs without a profile fall through to `ENGINE_A_SCORE_GROUP_THRESHOLDS` and then 3-tier fallback.
- Profile `weight_overrides` are not consumed by active Engine A score; `rg` only found config validation and tests documenting they do not change live factor output.

## Dead Factors

Confirmed dead or partially dead:

- `FACTOR_SCORE_GROUP_MULTIPLIERS` - `config.yaml:1392`; not read by active Engine A scoring.
- `REGIME_WEIGHTS` - `config.yaml:1378`; explicitly marked legacy/inactive in config comments and not read by active Engine A v2.
- `CRYPTO_FACTOR_WEIGHT_CAPS` - `config.yaml:1406`; not read by active Engine A v2.
- `CRYPTO_TRANSITION_PENALTY_ENABLED` - `config.yaml:1588`; no active score consumer found.
- `INDICATOR_WEIGHTS.derivatives.*` - `config.yaml:1236-1252`; active addon path uses fixed blend/weights, not these keys.
- `INDICATOR_WEIGHTS.microstructure.*` - `config.yaml:1260-1268`; confidence diagnostics only, not score.
- `INDICATOR_WEIGHTS.volatility.*` - `config.yaml:1272-1278`; active score uses `_volatility_scaler()`, not these weights.
- `INDICATOR_WEIGHTS.volume.obv_trend` - `config.yaml:1286`; not used in active score.
- `INDICATOR_WEIGHTS.volume.volume_ratio` - `config.yaml:1284`; partially live through bounded `_vol_adj`, but configured weight is not used.
- `INDICATOR_WEIGHTS.carry.carry_z` - `config.yaml:1292-1294`; carry addon is live for forex, but this configured weight is not used.
- `PAIR_PROFILES.weight_overrides` - `config.yaml:1663-1667`, `config.yaml:1677-1679`, `config.yaml:1689-1691`, `config.yaml:1709-1721`; not consumed by active Engine A score.

## Recommended Negative-Case Tests

- Config contract: every non-legacy `INDICATOR_WEIGHTS` key must have an active score consumer or be declared legacy.
- Config contract: every `PAIR_PROFILES.min_confluence` must be greater than or equal to group/default floor unless an explicit allow-below-global flag is set.
- Math reference: BB population std against `np.std(ddof=0)`.
- A-only auto gate: score `2.49/3.0` rejects and score `2.50/3.0` passes at weight `0.60`, min conviction `0.50`, no meta delta.
- Dead multiplier test: changing `FACTOR_SCORE_GROUP_MULTIPLIERS` should either alter score if wired or fail a legacy-only config test.
- StochRSI consistency: if enabled, StochRSI must be derived from Wilder RSI.
