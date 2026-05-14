## Engine A Audit - Detailed Findings

Audit mode: read-only source audit of the current working tree at `C:/dev/athena-python`.

Required-file status: all required files were readable: `AGENTS.md`, `factor_scoring.py`, `scoring.py`, `indicators.py`, `regime.py`, `intermarket.py`, `confidence_engine.py`, `calibration.py`, `athena_app/services/structure_context.py`, and `config.yaml`.

Scope note: this report uses source-code evidence only. No live scan, broker action, backtest run, or runtime config import was executed.

### 1. Core Scoring Logic

Inventory and entry points:

- Live scan path is `/api/scan` -> `handle_scan_request()` -> `scanner.run_full_scan()` -> per-pair `_analyse()` -> runtime `analyze_pair()` -> `scoring.calc_confluence()` -> `factor_scoring.compute_factor_scores()`. Evidence: `C:/dev/athena-python/athena.py:5156-5161`, `C:/dev/athena-python/athena_app/services/scan_backtest_service.py:8`, `C:/dev/athena-python/scanner.py:670`, `C:/dev/athena-python/scanner.py:908-981`, `C:/dev/athena-python/athena.py:11590-11612`, `C:/dev/athena-python/scoring.py:631-682`, `C:/dev/athena-python/factor_scoring.py:1719-2311`.
- Auto-trade consumes scan output through `_auto_trader.configure(... run_full_scan ...)`, `_run_auto_scan()`, `_can_execute()`, and `_execute_signal()`. Evidence: `C:/dev/athena-python/athena.py:5053`, `C:/dev/athena-python/auto_trader.py:537-653`, `C:/dev/athena-python/auto_trader.py:692-766`.
- Backtest path is `run_backtest.py` or `/api/backtest` -> `handle_backtest_request()` -> `backtest_pair()` / `run_full_backtest()`. Evidence: `C:/dev/athena-python/run_backtest.py:51`, `C:/dev/athena-python/athena.py:7896`, `C:/dev/athena-python/athena_app/services/scan_backtest_service.py:16-62`, `C:/dev/athena-python/backtest_runner.py:1099`, `C:/dev/athena-python/backtest_runner.py:6155`.

Confirmed scoring behavior:

- Normalization is not using a fixed vote denominator in the active trend path. `_coherent_trend_score()` reads `INDICATOR_WEIGHTS.trend`, appends only available D1/H4/H1 EMA votes, and computes `coherence_ratio = dominant_w / total_w` from the active vote weights. Evidence: `C:/dev/athena-python/factor_scoring.py:333-342`, `C:/dev/athena-python/factor_scoring.py:349-419`.
- Momentum normalization uses active configured RSI/MACD/volume weights for the denominator, but RSI and MACD weights remain in the denominator when their values are missing and default to zero. This is conservative score suppression, not score inflation. Evidence: `C:/dev/athena-python/factor_scoring.py:642-693`, `C:/dev/athena-python/factor_scoring.py:726-759`.
- Final factor conviction uses raw `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS` values and clips conviction to `[0, 1]`. Current YAML entries appear balanced, but the weights are not normalized in code and `config.py` does not validate that these factor weights sum to 1. Evidence: `C:/dev/athena-python/factor_scoring.py:75-90`, `C:/dev/athena-python/factor_scoring.py:1971-1999`, `C:/dev/athena-python/config.py:1560`, `C:/dev/athena-python/config.yaml:258`.
- Directional and nondirectional score separation is preserved. Trend determines direction and can hard-abort before momentum/addons; returned diagnostics expose `directional_score=trend_score` and `nondirectional_score=mom_quality`. Evidence: `C:/dev/athena-python/factor_scoring.py:1766-1805`, `C:/dev/athena-python/factor_scoring.py:1824-1852`, `C:/dev/athena-python/factor_scoring.py:2248-2262`.
- `FACTOR_CONVICTION_FLOOR` is active. Current config is `0.20`; final score uses `base_score * (floor + (1 - floor) * conviction)`. Evidence: `C:/dev/athena-python/config.yaml:236`, `C:/dev/athena-python/factor_scoring.py:1975-2022`, `C:/dev/athena-python/factor_scoring.py:2142`.
- `FACTOR_SCORE_GROUP_MULTIPLIERS` is not present in the current active runtime files inspected. Current regression tests assert it is absent from `CONFIG`. Evidence: `C:/dev/athena-python/tests/test_engine_a_confirmed_audit_fixes.py:215`; focused runtime search over `config.yaml`, `config.py`, `scoring.py`, `factor_scoring.py`, `regime.py`, `intermarket.py`, and `structure_context.py` found no active key.
- BTC bias is guarded to non-BTC crypto pairs with a non-neutral BTC bias and a computed direction. Live passes real price arrays for Pearson correlation; backtest calls do not pass those arrays and therefore fall back to heuristic correlation labels. Evidence: `C:/dev/athena-python/scoring.py:688-710`, `C:/dev/athena-python/scoring.py:596-610`, `C:/dev/athena-python/athena.py:11565-11611`, `C:/dev/athena-python/backtest_runner.py:1643-1661`, `C:/dev/athena-python/backtest_runner.py:2158-2175`, `C:/dev/athena-python/backtest_runner.py:2653-2670`.
- `confidence_engine.py` is active as diagnostics, not as the score gate. `scoring.calc_confluence()` calls `compute_confidence()` and returns `confidence` / `confidenceDetail`. Auto-trade gates on `combinedConviction`, not this confidence detail. Evidence: `C:/dev/athena-python/scoring.py:657`, `C:/dev/athena-python/scoring.py:846-882`, `C:/dev/athena-python/auto_trader.py:692-766`.
- `calibration.py` is used by backtest reporting, not by live Engine A scan scoring. Evidence: `C:/dev/athena-python/backtest_runner.py:28`, `C:/dev/athena-python/backtest_runner.py:3006`, `C:/dev/athena-python/backtest_runner.py:3383-3384`, `C:/dev/athena-python/backtest_runner.py:4626-4627`, `C:/dev/athena-python/calibration.py:555-613`.

Historical issue verification:

- Forming-bar lookahead is resolved in the inspected live path. Live Engine A uses confirmed candle lists from `split_market_state()` and `engine_a_scoring_candles_from_state()`. No `.iloc[-1]` / `.iloc[-2]` use was found in the focused live scan path; the path uses lists. Evidence: `C:/dev/athena-python/athena_app/services/market_state.py:201-227`, `C:/dev/athena-python/athena_app/services/candle_service.py:10-18`, `C:/dev/athena-python/athena.py:11088-11204`, `C:/dev/athena-python/factor_scoring.py:434-443`.
- The historical Engine A-only `combinedConviction` cap below the auto-trade gate is not present under current config. A-only max is `AUTO_TRADE_A_ONLY_WEIGHT.default = 0.60`; current default auto gate is `AUTO_TRADE_MIN_CONVICTION.default = 0.50`, so Engine A-only execution is reachable when Engine A score normalization is at least `0.50 / 0.60 = 0.8333`, i.e. score about `2.50 / 3.00`. Evidence: `C:/dev/athena-python/scanner.py:1331-1355`, `C:/dev/athena-python/auto_trader.py:705-758`, `C:/dev/athena-python/config.yaml:934-940`.
- `VOTE_WEIGHTS` is not an ignored current runtime config key because it is not present in current runtime config/code. Legacy votes are constructed as unweighted signs for UI compatibility. Evidence: `C:/dev/athena-python/scoring.py:776-796`; focused runtime search found no `VOTE_WEIGHTS` in active config/code files.
- `AUTO_TRADE_MIN_SCORE` is not the live execution gate. Current YAML comments mark it informational, and auto execution uses `AUTO_TRADE_MIN_CONVICTION` on `combinedConviction`. Evidence: `C:/dev/athena-python/config.yaml:910-928`, `C:/dev/athena-python/auto_trader.py:343-364`, `C:/dev/athena-python/auto_trader.py:692-766`.

### 2. Indicators and Weighting

Confirmed indicator formulas:

- RSI uses Wilder smoothing. Initial gain/loss averages are divided by period, then updated with `(avg * (period - 1) + current) / period`. Active bundle hardcodes RSI period `14`. Evidence: `C:/dev/athena-python/indicators.py:60-92`, `C:/dev/athena-python/indicators.py:1070`.
- Bollinger Bands use population standard deviation (`ddof=0`) because variance is divided by period `p`, not `p - 1`. Evidence: `C:/dev/athena-python/indicators.py:225-247`.
- ATR uses true range and Wilder smoothing. Initial ATR is the mean of `tr[1:p+1]`, then `(prior_atr * (p - 1) + tr[i]) / p`. Active bundle hardcodes ATR period `14`. Evidence: `C:/dev/athena-python/indicators.py:127-144`, `C:/dev/athena-python/indicators.py:1072`.
- ADX/+DI/-DI/DX use Wilder-style smoothed TR/+DM/-DM, then DX from `abs(+DI - -DI) / (+DI + -DI) * 100`, then ADX as first mean plus Wilder smoothing. Active bundle hardcodes ADX period `14`. Evidence: `C:/dev/athena-python/indicators.py:147-222`, `C:/dev/athena-python/indicators.py:1073`.
- Normalization lookback is config-driven by asset class for normalized indicators. Evidence: `C:/dev/athena-python/indicators.py:1078-1174`, `C:/dev/athena-python/config.yaml:1510-1520`.

Confirmed weighting behavior:

- Active trend weight keys are `d1_ema_trend`, `h4_ema_trend`, and `ema_trend`; they are consumed from `INDICATOR_WEIGHTS.trend`. Evidence: `C:/dev/athena-python/config.yaml:1258-1328`, `C:/dev/athena-python/factor_scoring.py:333-342`.
- Active momentum keys are `rsi_z`, `macdLine_z`, and, for applicable crypto/group-adjusted cases, `volume_momentum_spread`. Evidence: `C:/dev/athena-python/config.yaml:1258-1328`, `C:/dev/athena-python/factor_scoring.py:726-746`.
- YAML comments saying keys match `directional_factors` / `nondirectional_factors` are stale for the current v2 path; those names were not found as active runtime weighting maps in the inspected files. Evidence: `C:/dev/athena-python/config.yaml:1254`, `C:/dev/athena-python/factor_scoring.py:338`, `C:/dev/athena-python/factor_scoring.py:729`.

### 3. Asset Class and Score Group Differentiation

Confirmed behavior:

- `_resolve_class_keyed()` in both `scoring.py` and `factor_scoring.py` resolves `score_group` first, then `asset_type`, then `default`, then caller fallback. Evidence: `C:/dev/athena-python/scoring.py:172-182`, `C:/dev/athena-python/factor_scoring.py:37-53`.
- Score groups are resolved by explicit pair `score_group`, then `PAIR_PROFILES.score_group`, then asset/display mapping for forex, crypto, commodity, index, and stock. Evidence: `C:/dev/athena-python/scoring.py:102-169`.
- Score thresholds resolve through `ENGINE_A_PAIR_THRESHOLDS`, then `ENGINE_A_SCORE_GROUP_THRESHOLDS`, then fallback tiers. `is_backtest` is passed but does not branch in the resolver. Evidence: `C:/dev/athena-python/scoring.py:227-316`, `C:/dev/athena-python/backtest_runner.py:1325-1334`, `C:/dev/athena-python/scanner.py:1480`, `C:/dev/athena-python/config.yaml:722-749`.
- `PAIR_PROFILES.min_confluence` only raises the base threshold unless `allow_lower_threshold` is true. Current XAU/XAG profile values of `1.05` do not lower the default/group threshold because no `allow_lower_threshold` is present. Evidence: `C:/dev/athena-python/scoring.py:297-300`, `C:/dev/athena-python/config.yaml:1635-1661`.
- `PAIR_PROFILES.disable_filters` is consumed by `pair_filter_enabled()`. Evidence: `C:/dev/athena-python/scoring.py:329-347`.
- `PAIR_PROFILES.weight_overrides` and `bt_min` are validated as profile fields, but no active Engine A v2 score or threshold consumer was found in the inspected production path. Evidence: `C:/dev/athena-python/config.py:1320-1348`, `C:/dev/athena-python/config.yaml:1641-1649`, `C:/dev/athena-python/factor_scoring.py:75-90`, `C:/dev/athena-python/backtest_runner.py:1325-1338`.
- ETFs are partial Engine A citizens. `ETF_PAIRS` are declared with `type: "stock"`, and ATR level class can map to `etf` / `etf_bond`, but core factor maps such as ADX thresholds, RSI bounds, and lookbacks mostly treat ETFs through stock or score-group overrides. Evidence: `C:/dev/athena-python/athena.py:759-761`, `C:/dev/athena-python/scoring.py:185-213`, `C:/dev/athena-python/config.yaml:194-218`, `C:/dev/athena-python/config.yaml:420-439`, `C:/dev/athena-python/config.yaml:1510-1520`.
- `ADX_TREND_MIN_CLASS` is active and reachable through `_adx_gate()`. Current config: crypto `18`, forex `20`, commodity/stock/index `25`; hard-fail all `10`. Evidence: `C:/dev/athena-python/config.yaml:194-218`, `C:/dev/athena-python/factor_scoring.py:1058-1154`, `C:/dev/athena-python/factor_scoring.py:1812-1821`.

### 4. Addons

Status by addon:

- Carry: ACTIVE for forex-style addon scoring. Unsupported/missing/zero carry returns neutral. Evidence: `C:/dev/athena-python/factor_scoring.py:1159-1179`, `C:/dev/athena-python/factor_scoring.py:1335-1354`.
- Funding: ACTIVE for crypto addon scoring. `None` returns neutral; z-score or baseline/noise-band logic is applied when configured. Evidence: `C:/dev/athena-python/factor_scoring.py:1251-1295`, `C:/dev/athena-python/factor_scoring.py:1355-1369`.
- OI: PARTIAL / STALE-DATA RISK. OI scoring is active, but `build_oi_context_for_factor_scoring()` accepts any `oi_data` containing `oiChange` and does not reject `error: True`, `detail`, or stale cache age. Fetchers can return stale cached OI with `error: True`. Evidence: `C:/dev/athena-python/factor_scoring.py:233-258`, `C:/dev/athena-python/factor_scoring.py:1298-1332`, `C:/dev/athena-python/data_feeds.py:728-793`, `C:/dev/athena-python/data_feeds.py:800-823`.
- COT: ACTIVE for configured non-crypto addon paths. Unsupported, missing, or zero values return neutral. Evidence: `C:/dev/athena-python/factor_scoring.py:1187-1233`, `C:/dev/athena-python/factor_scoring.py:1370-1384`.
- Intermarket: PARTIAL / CURRENTLY NEUTRAL BY CONFIG. The scoring hook exists and missing input returns neutral, but current config disables both top-level intermarket confirmation and Engine A score delta. Evidence: `C:/dev/athena-python/intermarket.py:1224-1400`, `C:/dev/athena-python/config.yaml:1406-1435`, `C:/dev/athena-python/factor_scoring.py:2149-2173`.
- Research factors: ACTIVE when `ENGINE_A_RESEARCH_LAB_FACTORS.ENABLED` is true, capped by config, and added before final score calculation. Evidence: `C:/dev/athena-python/config.yaml:330-352`, `C:/dev/athena-python/factor_scoring.py:763-866`, `C:/dev/athena-python/factor_scoring.py:1847-1885`.
- Structure context: PARTIAL / PARITY RISK. The shared factor function has an optional `structure_result` hook, but `scoring.calc_confluence()` does not accept or pass `structure_result`. Live applies structure context separately after scoring; backtest does not apply that same score adjustment. Evidence: `C:/dev/athena-python/factor_scoring.py:2175-2203`, `C:/dev/athena-python/scoring.py:631-682`, `C:/dev/athena-python/athena.py:11666-11723`, `C:/dev/athena-python/backtest_runner.py:1643-1661`.

Denominator behavior:

- Unsupported addon data is redistributed into base weight when `ADDON_UNSUPPORTED_SPLIT_TO_BASE` is configured, so unavailable addons do not inflate the final conviction denominator in the main final-score formula. Evidence: `C:/dev/athena-python/factor_scoring.py:1981-1992`.
- Missing RSI/MACD inside momentum remains in the momentum denominator as zero-score components. This is conservative and can suppress momentum quality. Evidence: `C:/dev/athena-python/factor_scoring.py:642-746`.

### 5. Engine B Structure Integration

Confirmed behavior:

- `athena_app/services/structure_context.py` returns the base score unchanged when input is missing/non-dict, when `structural_verdict` is not `CLEAR`, or when `ENGINE_B_STRUCTURE_SCORE_INFLUENCE_ENABLED` is false. Evidence: `C:/dev/athena-python/athena_app/services/structure_context.py:88-160`.
- When enabled and structure is clear, `apply_structure_context_to_score()` applies a bounded multiplier, not a hard gate. Evidence: `C:/dev/athena-python/athena_app/services/structure_context.py:168-221`.
- Live Engine A builds Engine B structure data after core scoring and can adjust `res["score"]` post-score. Evidence: `C:/dev/athena-python/athena.py:11666-11723`.
- Live scan Engine B confirmation gate is disabled in current config. The scanner can surface Engine A-only signals if Engine A score is high enough for its threshold / auto-required score path. Evidence: `C:/dev/athena-python/config.yaml:2142`, `C:/dev/athena-python/scanner.py:350-362`, `C:/dev/athena-python/scoring.py:1061-1111`.
- Backtest has a separate `ENGINE_A.structure_first_entry` gate enabled in current config. It instantiates `NakedEngine()` and rejects failed structure checks before the Engine A score threshold in swing, intraday, and scalp loops. Evidence: `C:/dev/athena-python/config.yaml:303-309`, `C:/dev/athena-python/backtest_runner.py:104-229`, `C:/dev/athena-python/backtest_runner.py:1390-1403`, `C:/dev/athena-python/backtest_runner.py:1677-1709`, `C:/dev/athena-python/backtest_runner.py:2192-2224`, `C:/dev/athena-python/backtest_runner.py:2687-2719`.
- `RANGING` config feeds regime classification / legacy trend-state labeling; it does not feed `_adx_gate()`. Evidence: `C:/dev/athena-python/regime.py:35`, `C:/dev/athena-python/scoring.py:801-811`, `C:/dev/athena-python/factor_scoring.py:1058-1154`, `C:/dev/athena-python/config.yaml:1208`.

### 6. Threshold Calibration Assessment

Confirmed threshold chain:

- Active scan/backtest threshold source is `get_score_threshold()`: pair threshold -> score-group threshold -> fallback tiers -> profile min override -> optional regime multiplier. Evidence: `C:/dev/athena-python/scoring.py:227-316`.
- Current `ENGINE_A_REGIME_DYNAMIC_THRESHOLDS.ENABLED` is false, so regime multipliers are not applied to active thresholds. Evidence: `C:/dev/athena-python/config.yaml:712`.
- Current `SCAN_QUANTILE_ENABLED` is false, so scan quantile thresholding is not active. Evidence: `C:/dev/athena-python/config.yaml:795`, `C:/dev/athena-python/scanner.py:1490-1549`.
- Current score-group thresholds are reachable on the 0-3 Engine A scale because the checked-in values are in the 1.5-2.2 range, and the Engine A cap is 3.0. Evidence: `C:/dev/athena-python/config.yaml:722-749`, `C:/dev/athena-python/scoring.py:874`, `C:/dev/athena-python/factor_scoring.py:2142-2147`.
- Auto-trade Engine A-only reachability is stricter than scan threshold reachability. With current default min conviction `0.50` and A-only weight `0.60`, A-only auto execution requires Engine A score normalization `>=0.8333`, or score about `>=2.50/3.00`. Evidence: `C:/dev/athena-python/config.yaml:934-940`, `C:/dev/athena-python/scanner.py:1331-1355`, `C:/dev/athena-python/auto_trader.py:705-758`.
- `VOLATILITY_SCALER_BANDS` is active and resolves by score group, asset type, then fallback. Evidence: `C:/dev/athena-python/config.yaml:318-328`, `C:/dev/athena-python/factor_scoring.py:1563-1594`.
- `RSI_BOUNDS` is active and resolves by score group/asset type. Evidence: `C:/dev/athena-python/config.yaml:420-439`, `C:/dev/athena-python/factor_scoring.py:631-633`.
- ADX thresholds are active through `_adx_gate()` and are not blocked by `RANGING`. Evidence: `C:/dev/athena-python/config.yaml:194-218`, `C:/dev/athena-python/factor_scoring.py:1114-1154`.

No threshold-change recommendation is made from this audit. The current threshold values are reachable on the 0-3 score scale. The confirmed problems are wiring/parity issues, not arithmetic proof that the configured thresholds themselves are unreachable or inherently too strict/loose.

### 7. Dead Code and Dead Config

Confirmed:

- `AUTO_TRADE_MIN_SCORE`: informational/operator metadata only; not live scan or auto-execution gate. Evidence: `C:/dev/athena-python/config.yaml:910-928`, `C:/dev/athena-python/auto_trader.py:343-364`, `C:/dev/athena-python/auto_trader.py:692-766`.
- `VOTE_WEIGHTS`: no current runtime usage found in active config/code. Legacy votes are sign-mapped for UI compatibility. Evidence: `C:/dev/athena-python/scoring.py:776-796`; focused runtime search found no active config/code key.
- `FACTOR_SCORE_GROUP_MULTIPLIERS`: no current runtime usage found in active config/code. Current regression test asserts it is absent. Evidence: `C:/dev/athena-python/tests/test_engine_a_confirmed_audit_fixes.py:215`.
- `CRYPTO_TRANSITION_PENALTY_ENABLED`: no current runtime usage found in active config/code. Current regression test asserts it is absent. Evidence: `C:/dev/athena-python/tests/test_engine_a_confirmed_audit_fixes.py:218`.
- `PAIR_PROFILES.weight_overrides`: current config validates and defines the field, but active Engine A v2 scoring does not consume it in the inspected path. Evidence: `C:/dev/athena-python/config.py:1320-1340`, `C:/dev/athena-python/config.yaml:1641-1647`, `C:/dev/athena-python/factor_scoring.py:75-90`, `C:/dev/athena-python/factor_scoring.py:1971-1999`.
- `PAIR_PROFILES.bt_min`: current config validates and defines the field, but Engine A backtest threshold uses `get_score_threshold()`, not profile `bt_min`, in the inspected path. Evidence: `C:/dev/athena-python/config.py:1342-1348`, `C:/dev/athena-python/config.yaml:1649`, `C:/dev/athena-python/backtest_runner.py:1325-1338`.
- `MACRO_LOOKBACK` and `WEINSTEIN_LOOKBACK`: suspected config-only in the inspected active Engine A path. Evidence of declaration: `C:/dev/athena-python/config.yaml:445`, `C:/dev/athena-python/config.yaml:461`, `C:/dev/athena-python/config.py:579`, `C:/dev/athena-python/config.py:586`. Not promoted to ranked issues because a full all-repo exclusion search was not completed.

### 8. Live vs Backtest Parity

Confirmed parity matches:

- Both live and backtest use `scoring.calc_confluence()` and `factor_scoring.compute_factor_scores()` for core Engine A score. Evidence: `C:/dev/athena-python/athena.py:11590-11612`, `C:/dev/athena-python/backtest_runner.py:1643-1661`, `C:/dev/athena-python/backtest_runner.py:2158-2175`, `C:/dev/athena-python/backtest_runner.py:2653-2670`, `C:/dev/athena-python/scoring.py:631-682`.
- Score-group and score-threshold resolver code is shared. Evidence: `C:/dev/athena-python/scoring.py:102-169`, `C:/dev/athena-python/scoring.py:227-316`, `C:/dev/athena-python/backtest_runner.py:1325-1334`, `C:/dev/athena-python/scanner.py:1480`.
- Backtest signal windows avoid the current entry bar. Swing uses `d1_raw[i-MIN_BARS:i]` and enters on bar `i`; intraday/scalp use point-in-time context slices before entry time. Evidence: `C:/dev/athena-python/backtest_runner.py:1495-1534`, `C:/dev/athena-python/backtest_runner.py:1542-1576`, `C:/dev/athena-python/backtest_runner.py:1718-1726`, `C:/dev/athena-python/backtest_runner.py:2037-2057`, `C:/dev/athena-python/backtest_runner.py:2546-2562`.

Confirmed parity divergences:

- Live confirmed/forming split is explicit; backtest relies on raw cached/fetched bars and loop indexing. Signal windows avoid lookahead, but raw fetch/cache does not explicitly strip a current open kline. Evidence: `C:/dev/athena-python/athena_app/services/candle_service.py:10-18`, `C:/dev/athena-python/athena.py:11088-11204`, `C:/dev/athena-python/backtest_runner.py:1495-1534`, `C:/dev/athena-python/backtest_candle_cache.py:177`, `C:/dev/athena-python/data_feeds.py:100`. Runtime impact is SUSPECTED until verified with live fetch samples against cached newest rows.
- Live indicator calculations use full confirmed fetched series after minimum-bar checks; backtest uses fixed rolling windows (`220` D1 swing, `250` H4 intraday, `250` H1 scalp) plus point-in-time context slices. Evidence: `C:/dev/athena-python/athena.py:11205-11328`, `C:/dev/athena-python/backtest_runner.py:1485`, `C:/dev/athena-python/backtest_runner.py:2000`, `C:/dev/athena-python/backtest_runner.py:2503`, `C:/dev/athena-python/backtest_runner.py:2056`, `C:/dev/athena-python/backtest_runner.py:2557`.
- Live applies Engine B structure context as a post-score multiplier; backtest does not pass `structure_result` through `calc_confluence()` and instead has a separate hard `structure_first_entry` gate. Evidence: `C:/dev/athena-python/athena.py:11666-11723`, `C:/dev/athena-python/scoring.py:631-682`, `C:/dev/athena-python/factor_scoring.py:2175-2203`, `C:/dev/athena-python/backtest_runner.py:1677-1709`, `C:/dev/athena-python/backtest_runner.py:2192-2224`, `C:/dev/athena-python/backtest_runner.py:2687-2719`.
- Crypto candle source is cache/source dependent. Crypto Engine A signal candles use `_crypto_bt_signal_candles(... engine="A")`; current crypto pairs are Binance-sourced. Live crypto level ATR uses Bybit when configured. Evidence: `C:/dev/athena-python/backtest_runner.py:302-344`, `C:/dev/athena-python/backtest_runner.py:1132-1156`, `C:/dev/athena-python/athena.py:951-957`, `C:/dev/athena-python/athena.py:11645-11656`, `C:/dev/athena-python/config.yaml:118-122`.
- Default backtest volume threshold differs from live unless validation mode is `live_parity`. Evidence: `C:/dev/athena-python/research_validation.py:340-355`, `C:/dev/athena-python/backtest_runner.py:1025-1035`, `C:/dev/athena-python/backtest_runner.py:1338`, `C:/dev/athena-python/athena.py:11601-11604`, `C:/dev/athena-python/config.yaml:829-831`.
- Live BTC bias can use real H4 price correlation arrays; backtest calls do not pass `asset_prices` / `benchmark_prices` and use heuristic fallback. Evidence: `C:/dev/athena-python/scoring.py:596-610`, `C:/dev/athena-python/athena.py:11565-11611`, `C:/dev/athena-python/backtest_runner.py:1643-1661`, `C:/dev/athena-python/backtest_runner.py:2158-2175`, `C:/dev/athena-python/backtest_runner.py:2653-2670`.
- Live payload contains `scoreGroup`, `liveThreshold`, `dataFreshness`, `candleFetchMeta`, `engine_b`, levels, and candle snippets. Backtest trade rows store score/entry/sl/tp/factors/factor weights/validation labels and summary calibration fields, not the same per-trade payload. Evidence: `C:/dev/athena-python/athena.py:12044-12161`, `C:/dev/athena-python/backtest_runner.py:1950`, `C:/dev/athena-python/backtest_runner.py:2453`, `C:/dev/athena-python/backtest_runner.py:2944`, `C:/dev/athena-python/backtest_runner.py:3425`.

### 9. Identified Issues - Ranked by Severity

BUG-A-1
Severity: HIGH
File: `C:/dev/athena-python/backtest_runner.py`; `C:/dev/athena-python/config.yaml`; `C:/dev/athena-python/scanner.py`; `C:/dev/athena-python/scoring.py`
Line/function: `backtest_runner.py:104-229`, `backtest_runner.py:1677-1709`, `backtest_runner.py:2192-2224`, `backtest_runner.py:2687-2719`; `config.yaml:303-309`; `scanner.py:350-362`; `scoring.py:1061-1111`
Evidence: Current config enables `ENGINE_A.structure_first_entry`; backtest rejects failed structure checks before the Engine A score threshold in all Engine A style loops. Live scan has Engine B scan confirmation gate disabled in current config and can surface Engine A-only signals when the A-only threshold/auto-required score path passes.
What it does: Backtest adds a hard Engine B/BOS-style entry gate to Engine A while live Engine A does not apply the same hard gate.
What it should do: Live and backtest should share the same Engine A structure policy: either both hard-gate Engine A before threshold, or both treat structure as advisory/multiplier-only.
Impact: Backtest can reject trades that live scan can surface or auto-execute, distorting Engine A performance metrics and parity.
Fix: Move `structure_first_entry` behind a mode that is mirrored in live scan, or change backtest to use the same advisory structure-context path as live. Add a parity regression where Engine A score passes but BOS/structure check fails and assert live/backtest classification agrees.
Confidence: CONFIRMED

BUG-A-2
Severity: HIGH
File: `C:/dev/athena-python/scoring.py`; `C:/dev/athena-python/factor_scoring.py`; `C:/dev/athena-python/athena.py`; `C:/dev/athena-python/backtest_runner.py`
Line/function: `scoring.calc_confluence()` at `scoring.py:631-682`; `compute_factor_scores(... structure_result=...)` and internal hook at `factor_scoring.py:1719-1736`, `factor_scoring.py:2175-2203`; live post-score application at `athena.py:11666-11723`; backtest calls at `backtest_runner.py:1643-1661`, `backtest_runner.py:2158-2175`, `backtest_runner.py:2653-2670`
Evidence: `compute_factor_scores()` has a `structure_result` hook, but `calc_confluence()` has no `structure_result` parameter and calls `compute_factor_scores()` without one. Live applies `apply_structure_context_to_score()` after core scoring. Backtest does not pass or apply the same structure context score adjustment.
What it does: The shared Engine A score function has a partially unused structure hook, while live and backtest implement different structure behavior outside the shared call.
What it should do: Structure context should be applied once through a shared live/backtest path, or the unused hook should be removed and the separate policies made explicit.
Impact: Same candles/factors can produce different final Engine A scores between live and backtest when structure context is enabled.
Fix: Add `structure_result` to `calc_confluence()` and use it from both live/backtest, or remove the internal hook and make both paths call the same external helper. Add a regression that feeds identical structure context to live/backtest scoring and asserts matching adjusted score/diagnostics.
Confidence: CONFIRMED

BUG-A-3
Severity: HIGH
File: `C:/dev/athena-python/athena.py`; `C:/dev/athena-python/backtest_runner.py`; `C:/dev/athena-python/config.yaml`
Line/function: live crypto level ATR at `athena.py:11645-11656`; backtest ATR selector at `backtest_runner.py:374-409`; crypto pair source at `athena.py:951-957`; config at `config.yaml:118-122`
Evidence: Current config sets `ENGINE_A_CRYPTO_LEVELS_FEED: bybit` and `ENGINE_A_CRYPTO_BT_LEVEL_ATR_USE_SIGNAL_FEED: true`. Live uses Bybit ATR for crypto Engine A levels when configured. Backtest returns signal-feed ATR first for Binance-sourced crypto pairs when the backtest signal-feed flag is true.
What it does: Crypto live levels can be sized from Bybit ATR while crypto backtest levels use Binance/signal-feed ATR.
What it should do: Crypto backtest SL/TP ATR should use the same source as live level sizing for the audited mode, or the mode should explicitly report a non-parity ATR basis.
Impact: Backtest stop/target distances, R multiples, and trade outcomes can diverge from paper/live execution levels.
Fix: When `ENGINE_A_CRYPTO_LEVELS_FEED` is `bybit`, make backtest level ATR use point-in-time Bybit ATR as well, or require an explicit non-parity mode flag. Add a mocked test where Binance ATR and Bybit ATR differ and assert the selected source matches live config.
Confidence: CONFIRMED

BUG-A-4
Severity: MEDIUM
File: `C:/dev/athena-python/factor_scoring.py`; `C:/dev/athena-python/data_feeds.py`
Line/function: `build_oi_context_for_factor_scoring()` at `factor_scoring.py:233-258`; OI scoring at `factor_scoring.py:1298-1332`; Bybit/Binance stale cache returns at `data_feeds.py:728-793`, `data_feeds.py:800-823`
Evidence: OI context is built from any `oi_data` containing `oiChange`; it does not reject `error: True`, `detail`, or stale cache age. OI fetchers can return stale cached values with `error: True`.
What it does: Stale/error OI data can still enter the Engine A crypto addon if it includes cached `oiChange`.
What it should do: Missing, stale, or error-marked OI should be neutral/fail-closed for scoring unless the fetcher proves freshness.
Impact: Crypto addon score can be boosted or penalized by stale derivative data during OI API failures.
Fix: Reject `oi_data` when `error` is true or when a configured max age is exceeded before building `oi_context`. Add a unit test with `{"error": True, "oiChange": ...}` and assert addon status is neutral/missing.
Confidence: CONFIRMED

BUG-A-5
Severity: MEDIUM
File: `C:/dev/athena-python/scoring.py`; `C:/dev/athena-python/athena.py`; `C:/dev/athena-python/backtest_runner.py`
Line/function: BTC correlation fallback at `scoring.py:596-610`; BTC bias gate at `scoring.py:688-710`; live price arrays at `athena.py:11565-11611`; backtest calls at `backtest_runner.py:1643-1661`, `backtest_runner.py:2158-2175`, `backtest_runner.py:2653-2670`
Evidence: Live passes asset and BTC price arrays into `calc_confluence()`. Backtest calls do not pass `asset_prices` or `benchmark_prices`, so the BTC bias guard uses heuristic correlations.
What it does: BTC bias can be data-driven in live scan but heuristic in backtest for the same crypto pair.
What it should do: Backtest should pass point-in-time asset/BTC price arrays to the same BTC correlation guard used live, or report that BTC bias parity is disabled.
Impact: Backtest can apply or skip BTC bias differently from live, especially for alts whose current correlation differs from the hardcoded fallback labels.
Fix: Build point-in-time H4 close arrays for the asset and BTC in backtest before `calc_confluence()`. Add a regression where heuristic and actual correlation cross the BTC-bias threshold and assert parity mode uses actual correlation.
Confidence: CONFIRMED

BUG-A-6
Severity: MEDIUM
File: `C:/dev/athena-python/research_validation.py`; `C:/dev/athena-python/backtest_runner.py`; `C:/dev/athena-python/athena.py`; `C:/dev/athena-python/config.yaml`
Line/function: `volume_threshold_for_backtest()` at `research_validation.py:340-355`; backtest threshold at `backtest_runner.py:1025-1035`, `backtest_runner.py:1338`; live threshold at `athena.py:11601-11604`; config at `config.yaml:829-831`
Evidence: Live uses pair profile `volume_threshold` or `VOLUME_THRESHOLD` (`1.5`). Standard backtest uses `VOLUME_THRESHOLD_BACKTEST` (`1.2`) unless validation mode is explicitly `live_parity`.
What it does: Default backtest accepts a lower volume threshold than live scan.
What it should do: Any parity backtest should use the live volume threshold by default, or standard-mode output should clearly label the threshold basis per trade.
Impact: Default backtest can pass momentum/volume conditions that live scan would score differently.
Fix: Make `live_parity` the explicit parity mode used for Engine A parity comparisons, or store/report the threshold basis and keep standard mode out of parity claims. Add a test that verifies standard and live_parity threshold selection.
Confidence: CONFIRMED

BUG-A-7
Severity: MEDIUM
File: `C:/dev/athena-python/config.py`; `C:/dev/athena-python/config.yaml`; `C:/dev/athena-python/factor_scoring.py`; `C:/dev/athena-python/backtest_runner.py`
Line/function: profile validation at `config.py:1320-1348`; YAML profile fields at `config.yaml:1641-1649`; active factor weights at `factor_scoring.py:75-90`; backtest threshold at `backtest_runner.py:1325-1338`
Evidence: `PAIR_PROFILES.weight_overrides` and `bt_min` are defined and validated, but active Engine A v2 scoring resolves weights from global/class maps and backtest thresholds from `get_score_threshold()`.
What it does: Profile fields appear configurable but do not affect active Engine A v2 scoring or the inspected backtest threshold path.
What it should do: Either wire these fields into the active Engine A path with tests, or mark/remove them as legacy so operators do not tune dead knobs.
Impact: Operator changes to these fields can create false confidence that pair-specific Engine A behavior changed.
Fix: Add config-contract tests for profile fields. If kept, assert changing `weight_overrides` or `bt_min` changes the intended active behavior; otherwise fail validation for legacy-only keys or rename them under a legacy namespace.
Confidence: CONFIRMED

BUG-A-8
Severity: LOW
File: `C:/dev/athena-python/factor_scoring.py`; `C:/dev/athena-python/config.py`; `C:/dev/athena-python/config.yaml`
Line/function: factor weight resolution at `factor_scoring.py:75-90`; conviction formula at `factor_scoring.py:1971-1999`; validation reference at `config.py:1560`; YAML weights at `config.yaml:258`
Evidence: Factor weights are consumed as raw values from `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS`; final conviction is clipped to `[0, 1]`. The inspected validation covers trend weight sums, not these factor weight triplets.
What it does: Future mis-summed `momentum/addon/base` weights can silently over- or under-weight conviction until clipping.
What it should do: Config validation should require active factor weight sets to sum to the intended total, or scoring should normalize them before use.
Impact: A config edit can change Engine A calibration without an explicit threshold/scoring code change.
Fix: Add validation for every `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS` entry and a regression test with intentionally mis-summed weights.
Confidence: CONFIRMED

BUG-A-9
Severity: SUSPECTED / MEDIUM
File: `C:/dev/athena-python/backtest_candle_cache.py`; `C:/dev/athena-python/data_feeds.py`; `C:/dev/athena-python/backtest_runner.py`; `C:/dev/athena-python/athena_app/services/market_state.py`
Line/function: cache insert/fetch path at `backtest_candle_cache.py:177`; raw feed path at `data_feeds.py:100`; backtest exit/signal raw bars around `backtest_runner.py:1495-1534`, `backtest_runner.py:1845`; live split at `market_state.py:201-227`
Evidence: Live explicitly splits confirmed/forming candles. Backtest signal windows avoid the current entry bar, but fetch/cache code does not explicitly strip an exchange-returned current open kline before storage/use. Runtime behavior depends on provider response at fetch time.
What it does: If a provider returns the current open kline, backtest raw data may include it.
What it should do: Backtest parity mode should either strip current open candles using the same bucket logic as live or prove cached rows are all closed.
Impact: Potential current-bar contamination in cached backtest data or exit simulation. This was not proven in a runtime fetch during this audit.
Fix: Verify with a live fetch sample and cached newest row against `split_market_state()`. If confirmed, add a cache-side or backtest-load-side confirmed-candle filter and regression coverage.
Confidence: SUSPECTED

### Overall Assessment

Engine A core scoring is now primarily routed through the v2 factor engine (`scoring.calc_confluence()` -> `factor_scoring.compute_factor_scores()`), and the inspected historical issues for forming-bar scoring, hardcoded vote-denominator normalization, `VOTE_WEIGHTS`, `FACTOR_SCORE_GROUP_MULTIPLIERS`, `CRYPTO_TRANSITION_PENALTY_ENABLED`, and `AUTO_TRADE_MIN_SCORE` are not active scoring bugs in the current runtime path.

The main confirmed risk is not the core formula; it is live/backtest parity around structure integration, crypto ATR source selection, BTC-bias correlation inputs, and default backtest validation thresholds. The highest-priority fixes should make Engine A parity mode explicit and shared: same structure policy, same ATR source basis, same BTC-correlation inputs, same volume threshold basis, and same diagnostics payload where needed.

No threshold changes are recommended from this audit because the current threshold values are reachable on the active 0-3 score scale. The confirmed findings are wiring, parity, stale-data handling, and dead-config issues.
