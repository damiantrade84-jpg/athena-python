---
description: Single-surface critical audit (engine-a | engine-b | engine-d | tv-chart | data-layer). Scoped, token-efficient, evidence-only.
argument-hint: engine-a | engine-b | engine-d | tv-chart | data-layer
---

# Athena Single-Surface Audit

Audit target: **$ARGUMENTS**

## Hard rules (non-negotiable)

1. **One target only.** Audit ONLY the surface named above. Valid targets: `engine-a`, `engine-b`, `engine-d`, `tv-chart`, `data-layer`. If the argument is empty or not one of these, STOP and ask which target to audit. Never drift into other engines or surfaces, even if you spot something suspicious — note it as a one-line "out of scope, recommend separate audit" item and move on.
2. **Token discipline.**
   - Read only the files listed in the scope block for this target (plus `config.yaml` / `config.py` keys named there).
   - Never load `tasks/`, logs, old audit reports, generated artifacts, backtest outputs, or memory files.
   - Do NOT run any tests during the audit phase. Tests are run ONLY after a fix is applied, and only the single targeted test file named for that fix: `pytest tests/test_X.py -q`. Never run the full suite, never run broad test globs, never run backtests.
   - Fix-verification test lists below name **candidates** — run only the **one** file that matches the fix, not the whole list.
   - Do not re-read files you have already read. Do not summarize files that are not in scope.
3. **Evidence only — no guessing, no hallucinations.** Every finding must cite file + function + line from CURRENT source you actually read in this session. Anything not inspected is labeled `not verified`. If unsure, say `not verified` — never invent behavior.
4. **Safety gates are untouchable.** Paper-only. Never weaken or bypass risk gates, freshness gates, kill switches, execution approvals, broker checks, RR checks, SL/TP validation, score thresholds, or audit logging. AI is advisory-only.
5. **Fixes:** smallest safe diff, config-gated and default-safe where behavior changes. Do not change thresholds, scoring weights, or strategy semantics unless the finding proves they are bugs (e.g. resolver bypassed) — and say so explicitly.
6. **Improvements beyond CLAUDE.md are welcome** — list them in the Improvements section as suggestions (config-gated, default-safe), clearly separated from bug findings. Do not implement them unless asked.
7. **Backend → UI rule.** If any fix changes a backend payload field, trace it to its React read-site in `static/react-app/` and update the consumer in the same change. After any React change, run only the frontend type-check/build for the react app — not test suites.

## Universal lane (run for EVERY target): per-pair / per-group balance

The tool serves many groups and pairs. It must be balanced — never tuned so it only works for one pair. For every config-driven value in this target's scope:

- Every `*_BY_CLASS` / `*_BY_SCORE_GROUP` / `score_group_overrides` key must flow through the shared resolver `_resolve_class_keyed(...)` (in `factor_scoring.py` / `scoring.py`) or the surface's own documented resolver. Flag any hardcoded literal (RSI `14`, EMA `21`/`50`/`200`, fixed ATR mults, fixed spreads) that bypasses the resolver while an earlier hop resolves per `score_group`. **Field presence ≠ parity** — trace write site AND read site.
- Spot-check one pair per class and confirm each resolves its intended (different) values, with no silent fallback to default:
  - forex major: `EURUSD` (e.g. RSI 18)
  - crypto: `TRXUSDT` (e.g. RSI 12)
  - US stock: `AAPL`
  - default-tier index/stock (RSI 14)
- Flag asymmetries: thresholds, multipliers, or gates that exist for one group but are missing/defaulted for another with no documented reason — these silently favor one pair.
- Verify long and short paths are symmetric: any branch that handles `LONG`/`buy` must have an equivalent `SHORT`/`sell` branch with mirrored math.

## Scope blocks — use ONLY the one matching $ARGUMENTS

### engine-a

Live engine: `engine_a_v3/` package (`evaluator.py`, `quant_scorer.py`, `levels.py`, `profile.py`, `routing.py`, `promotion.py`, `execution.py`, `setups.py`, `quant_context.py`, `indicator_adapter.py`, `session_forex.py`, `session_scoring.py`), reached from `athena.py:analyze_pair` via `engine_a_v3.evaluator.evaluate_engine_a_v3` — `analyze_pair` does NOT call `factor_scoring.compute_factor_scores` (confirmed 2026-06-30; `tests/test_engine_a_v3_integration.py` asserts `calc_confluence` is absent from the consensus source).

Legacy (read-only context, not live scoring): `factor_scoring.py`, `scoring.py`, `indicators.py`. `compute_factor_scores`/`calc_confluence` are exercised only by `backtest_runner.py`, `divergence_monitor.py`, `athena_research/fast_live_gate_replay.py`, and calibration tooling. `indicators.calc_levels` is still used by legacy call sites in `athena.py` (~line 7664); `indicators.select_overlay_sl`/`struct_sl_on_correct_side` have no production caller — kept only for `tests/test_engine_a_sl_floor.py` in case Engine B structural-SL fusion is revisited for v3. `engine_a_v3/quant_scorer.py` and `engine_a_v3/profile.py` both still import small per-class resolvers from `factor_scoring.py` (`_resolve_adx_thresholds`, `_resolve_ema_periods`, `_resolve_rsi_period`, `_resolve_macd_params`, `_resolve_atr_adx_periods`) — that reuse is intentional, not drift.

- **Buy/sell direction:** `engine_a_v3.quant_scorer.score_pair` (`dir_sum` = weighted sign of `trend`/`momentum`/`location`/`volume` components vs `profile.direction_deadband`) and `engine_a_v3.setups.detect_setup` (per-family setup specialists, can upgrade WATCH→TRADE but never veto the quant direction) feeding `evaluator.evaluate_engine_a_v3`'s `use_setup` branch. Verify direction is decided symmetrically (mirror LONG/SHORT branches in each setup candidate function); verify `evaluator._validate_candles` abort paths (`unsupported_horizon`, `*_history_insufficient`, `*_ohlc_invalid`, `*_timestamps_invalid`, `blocked_reasons`) fail closed to `NO_SIGNAL`/`direction=None`.
- **Subsystem context wiring (audit hardest here — confirmed gap as of 2026-06-30):** `engine_a_v3.quant_context.build_quant_context` assembles real `carry`/`sentiment`/`microstructure`/`intermarket` signals and `athena.py:analyze_pair` (~line 13452-13470) threads them into `context=` on `evaluate_engine_a_v3` → `score_pair`. Verify `score_pair`'s `components` dict actually includes the subsystem components (via `_context_component`), not just `trend`/`momentum`/`location`/`volume` — as of this audit `_context_component` is defined but never called, so `_DEFAULT_WEIGHTS`' intermarket/carry/sentiment/macro/microstructure entries are dead and `engine_a_v3/profile.py`'s `CORE_COMPONENTS`/`EngineAV3Profile.create` schema rejects any weight key outside `{trend,momentum,location,volume}` — re-check both the call site and the profile schema before declaring this fixed. Research-only ablation in `athena_research/engine_a_ablation/shadow_scorer.py` studies this gap; do not graduate it into `quant_scorer.py` without the same walk-forward evidence `promotion.py` requires.
- **ATR SL/TP:** `engine_a_v3.levels.build_structural_levels` / `build_mean_reversion_levels` / `build_london_open_breakout_levels` — verify `invalidation`/`targets`/RR math is correct for BOTH directions and every builder fails closed (returns `None`) on insufficient bars, non-positive ATR, or invalidation crossing the current price.
- **Promotion / execution gating:** `engine_a_v3.promotion.validate_promotion_artifact` (schema/scope/provenance/cost-model/walk-forward-fold/holdout/SQN/profit-factor/drawdown/bootstrap gates — any single missing/invalid field must block `qualified`) and `engine_a_v3.execution.attest_demo_execution` / `verify_refreshed_signal` (direction/profile/exitPolicy cannot drift on signal refresh). Verify `qualified` / `executionScope` / `engineATradeEnabled` can never go `True` off a missing, malformed, or stale artifact, and that `production_registry()`'s `demo_unvalidated_activation` bootstrap path stays gated to `EXECUTOR_MODE == "demo"`.
- **Per-group keys to balance-check:** `ENGINE_A_SCORE_GROUP_THRESHOLDS` (via `profile._resolved_trade_threshold`), `ENGINE_A_RSI_PERIOD_BY_CLASS` / `ENGINE_A_EMA_PERIODS_BY_CLASS` / `ENGINE_A_MACD_PARAMS_BY_CLASS` / `ENGINE_A_ATR_ADX_PERIODS_BY_CLASS` (via `profile._resolved_periods` → `factor_scoring._resolve_*`), `_FAMILY_WEIGHTS` in `profile.py`, `ADX_TREND_MIN_CLASS` / `FACTOR_ADX_HARD_FAIL_CLASS` (via `quant_scorer._momentum_component` → `factor_scoring._resolve_adx_thresholds`).
- **Fix-verification tests (only if a fix is applied):** `tests/test_engine_a_v3_integration.py`, `tests/test_engine_a_v3.py`, `tests/test_engine_a_v3_execution.py`, `tests/test_engine_a_v3_profiles.py`, `tests/test_engine_a_v3_validation.py`, `tests/test_engine_a_ablation_shadow.py` (subsystem-context changes only) — pick the one matching the touched module, not the whole list. Legacy-path fixes only: `tests/test_engine_a_sl_floor.py`, `tests/test_engine_a_level_parity.py`, `tests/test_factor_scoring.py`.

### engine-b

Files: `market_structure.py` (primary), `engine_b_ai.py` (read-only context — AI assembly only, no level math).

- **Structural SL/TP:** `NakedEngine.analyze_structure_direction` (the `recommended_stop_loss` / `recommended_take_profit` / `tp_source` write site), `_engine_b_structural_target_price` (opposing-zone TP with ATR buffer), `_engine_b_structural_tp_buffer_atr_mult` (`NAKED_ENGINE.structural_tp_buffer_atr_mult`), structural SL from `NAKED_ENGINE.zone_multipliers[regime].sl` with optional `ENGINE_B_STRUCTURAL_SL_USE_STYLE_ATR_MULTS`.
- **ATR helper:** `NakedEngine._compute_atr_from_candles` and `_ENGINE_B_TF_MATRIX` (per-asset struct/zone/trigger/atr TF roles), `resolve_engine_b_asset_class` — verify the ATR TF and asset class used for stops match the matrix for every asset class, not just one.
- **RR gate (known past-issue surface — audit hardest here):** `resolve_engine_b_execution_levels` — ATR-vs-structural SL preference, structural TP, synthetic fallback TP (`ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP`, `fallback_rr`), MAX_SL clamp (`ENGINE_B_ENFORCE_MAX_SL_PCT`), `validate_tp_exchange_bounds`, nested `_compute_exec_rr`. Then `calculate_confidence` gate #5: `rr_ok = execution_levels_valid and rr_used_for_gate >= min_rr`. Verify the RR used for the gate is the RR of the levels that would actually execute — no recomputation drift between gate and execution.
- **Per-group keys to balance-check:** `NAKED_ENGINE.style_profiles[style].min_rr` / `.fallback_rr` / `.min_room_atr`, `NAKED_ENGINE.score_group_overrides`, `NAKED_ENGINE.zone_multipliers`.
- **Fix-verification test:** `tests/test_engine_b_rr_basis.py` (add `tests/test_engine_b_profile_gating.py` only if gating logic was touched).

### engine-d

File: `scalp_engine.py`.

- **Direction:** `_classify_setup` (mean reversion vs trend continuation + direction), `infer_bias_from_ema_stack`, `_check_vwap_lean`, `_compute_cvd_direction` — verify symmetric long/short handling and that grade `ai_quality_grade` does not bias direction.
- **Levels:** `calculate_scalp_levels` (VP/ATR SL, mechanical `tp1 = TP1_R_MULT × SL distance`, optional tp2 runner), `rebase_scalp_signal_levels` (live mid-price rebase — verify rebase cannot invert SL/TP or break RR), `_scalp_min_rr_for_group` (`SCALP_ENGINE.MIN_RR` + `score_group_overrides`).
- **Data feeds:** MT5 for forex/commodity/index/stock (`mt5_fetch_scalp_candles`), Binance WS M1 for crypto (`candle_feeds.fetch_candles_live` with `athena_runtime.fetch_candles` fallback), EODHD stock volume overlay `_overlay_eodhd_volume_for_scalp` (volume source labels `eodhd_candle_volume` / `ws_tick_volume`). Verify market staleness gates: `MARKET_TICK_MAX_AGE_SEC`, `MARKET_CANDLE_MAX_AGE_SEC`, `_engine_d_atr_freshness_block`.
- **Per-group keys to balance-check:** `SCALP_ENGINE.MAX_SPREAD_PIPS_BY_SCORE_GROUP` / `MAX_SPREAD_POINTS_BY_SCORE_GROUP`, `SCALP_ENGINE.score_group_overrides`, `GRADE_THRESHOLDS`.
- **Fix-verification tests:** `tests/test_scalp_engine.py`, `tests/test_engine_d_execute_gate.py`.

### tv-chart

Files: `athena_app/api/routes_market_data.py`, `static/react-app/**/TVChartPanel.tsx`.

- **Closed-loop parity (golden rule — mark each hop PASS / FAIL / NOT REVIEWED):**

```
config.yaml → factor_scoring._resolve_rsi_period / _resolve_ema_periods / _resolve_atr_adx_periods / _resolve_macd_params
  → routes_market_data._resolve_chart_indicator_periods
  → _format_chart_candles (ema_trend / ema_momentum / ema_long / rsi / adx14 / atr14 / vwap, meta indicator_periods, indicator_basis: confirmed_only)
  → API envelope (indicator_periods, price_precision: score_group / ema_periods / rsi_period / resolver_fallback)
  → TVChartPanel.tsx (emaPeriods, indicatorPeriods)
  → buildChartStudySnapshot (API series first; local fallback must use resolved periods, never literals)
  → per-group test
```

  Stop and flag FAIL if any hop uses a literal (RSI 14, EMA 21/50/200, ATR 14, ADX 14) while an earlier hop resolves per `score_group`. Never accept a TypeScript interface field or green test as proof — trace into the computation. Watch for client fallbacks in `buildChartStudySnapshot` that ignore `price_precision.rsi_period` / `indicator_periods`.
- **Adversarial greps (run before declaring parity PASS):**

```bash
rg -n "rsi\(.*,\s*14\)|calc_rsi\(.*14\)|atr\(.*,\s*14\)|calc_atr\(.*14\)|adx\(.*,\s*14\)" static/react-app athena_app/api indicators.py
rg -n "rsi_period|ema_periods|indicator_periods|price_precision" static/react-app athena_app/api factor_scoring.py
```

  For each field: find the write site (API) and the read site (compute or UI). Write without read = FAIL.
- **Engine B overlays:** `_normalize_engine_b_overlay_payload` — overlay payload may carry its own `price_precision`; verify it does not desync from the main chart payload.
- **Masked-test check:** a parity test that omits `score_group` or only uses default-tier pairs (both sides wrong the same way) is masked — flag it.
- **Fix-verification tests:** `tests/test_chart_api_indicator_period_parity.py`, `tests/test_engine_a_crypto_chart_parity.py`. If React changed: frontend type-check/build only.

### data-layer

Files: `data_feeds.py`, `candle_feeds.py`, `candles_cache.py`, `atr_diagnostics.py`, `athena_app/services/data_freshness.py`, `athena_app/services/market_state.py`, `eodhd_volume_overlay.py`, `eodhd_volume_batch.py`, `athena/datafeeds/binance_ws.py` (+ `athena/datafeeds/bybit_ws.py` if Bybit micro feeds enabled).

- **Microstructure feeds ("MicroFeeds"):** `_micro_cache` in `athena.py` (`_start_micro_feeds`, `_apply_micro_cache_update`) → injected into H4 snap (`order_book_imbalance`, `orderflow_delta`, `liquidity_wall_detection`, `liquidity_pressure`) → consumed in `factor_scoring.compute_factor_scores`. Verify: enablement keys (`MICROSTRUCTURE_FEEDS_ENABLED`, `MICROSTRUCTURE_BYBIT_FEEDS_ENABLED`, `MICROSTRUCTURE_BINANCE_STALE_SYMBOL_SEC`), stale micro data is dropped or penalized (`AUTO_TRADE_MICROSTRUCTURE_STALE_BLOCK` / `_PENALTY`), and missing micro data degrades gracefully (no crash, no fake values).
- **Bybit ATR:** `data_feeds._fetch_bybit_klines` → `athena._bybit_atr_for_levels` (and `execution._engine_b_atr_for_scan_levels`) → provenance via `atr_diagnostics.build_engine_a_diagnostics` / `build_engine_b_diagnostics` (`atr_source`, `atr_tf`, `atr_age_seconds`, `bybit_atr_available`). Config: `ENGINE_A_CRYPTO_LEVELS_FEED`, `ENGINE_B_CRYPTO_LEVELS_FEED`, `*_SIGNAL_FEED_FALLBACK`. Verify fallback to signal feed is explicit in diagnostics (never silently mislabeled as Bybit) and `ATR_FRESHNESS.*` gates (`ENABLED`, `BLOCK_EXECUTION_ON_STALE_ATR`, `MAX_AGE_SECONDS`) fail closed.
- **Freshness & rate:** `evaluate_execution_data_freshness`, `candle_freshness_diagnostic`, `evaluate_live_quote_age`, pre-scoring gate (`PRE_SCORING_FRESHNESS_GATE_ENABLED`, abort `STALE_DATA_PRE_SCORING:<TF>`), `DATA_FRESHNESS_GATES` (`BLOCK_EXECUTION_ON_STALE`, `BLOCK_TIMEFRAMES`, `BLOCK_SEVERITIES`), `LIVE_PRICE_MAX_AGE_SEC`. Rate limiting: `rate_limited` propagation from fetch (`candles_cache.py`) → scanner/execution skip — verify a rate-limited fetch can never be scored as fresh data.
- **EOD US stocks volume:** OHLC stays MT5; volume overlaid from EODHD without mutating OHLC. Trace: `eodhd_volume_batch.LiveV2VolumeBatcher` / `build_non_ws_stock_pairs` (ws:false pairs), `CandleBuilder` (ws:true + D1 bulk), REST fallback `athena._fetch_eodhd_volume_only`, merge `eodhd_volume_overlay.overlay_candle_volumes`, whitelist `_STOCK_ALL_TFS`, `supports_eodhd_volume_overlay`. Verify: timestamps align before volume replacement (misaligned bars must be skipped, not shifted), volume source labels are accurate, `EODHD_LIVE_V2_MAX_QUOTE_LAG_SEC` enforced, and non-whitelisted stocks are not silently given wrong volume.
- **Fix-verification tests:** name the single most targeted existing test for the touched module (search `tests/` by module name); if none exists, write one focused test.

## Procedure

1. Restate the target and list the exact files you will read (from the scope block) before reading them.
2. Read the scoped files. Trace producer → consumer for each contract in the scope block.
3. Run the universal per-group balance lane against everything you read.
4. Record findings as you go. For missing/null/stale/malformed inputs, verify the path fails closed.
5. Apply fixes only for confirmed bugs (smallest safe diff). After each fix, run ONLY its named targeted test.
6. If a fix touched a backend payload, trace and fix the React read-site, then run the frontend type-check/build only.

## Output contract

1. **Coverage map** — files read, files in scope but NOT read (and why), assumptions.
2. **Findings table** — for each: severity (CRITICAL/HIGH/MEDIUM/LOW), file + function + line, why it is real (evidence), expected behavior, minimal fix, the ONE regression test that proves it.
3. **Per-group balance verdict** — PASS/FAIL per checked key, with the spot-check pairs used; list any key that favors one pair/group.
4. **Improvements (beyond current CLAUDE.md rules)** — suggestions only, config-gated and default-safe; clearly marked as not bugs.
5. **Not verified** — explicit list of everything in scope you did not fully trace. Never claim "no issues found" for anything on this list.
