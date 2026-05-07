# Timed Exit Pipeline — Full Overhaul (Tier 1+2+3)

**Goal**: Trades ride profit on the chandelier ATR trail. Close only on reversal (trail breach + optional indicator confirmation). Never close on a timer alone. Eliminate the four-mechanism competition (original SL / BE-lock ladder / timed-close / chandelier) where the tightest one wins and clips winners early.

**Files in scope**: `config.yaml`, `timed_exit_monitor.py`, `tests/test_timed_exit_phases.py`, `athena.py` (DDL only).

## Phase 1 — Config (config.yaml)

- [ ] T1.1 `intraday.timed_close_enabled`: true → false
- [ ] T1.2 `swing.timed_close_enabled`: true → false
- [ ] T1.3 `scalp.profit_lock_enabled`: true → false (ladder superseded by chandelier)
- [ ] T1.4 `trail_activation_r`: scalar 1.0 → per-style dict `{scalp: 0.3, intraday: 0.5, swing: 1.0}`
- [ ] T1.5 `trail_indicator_confirm`: false → true (RSI/MACD reversal IS the "reversal" we want)

## Phase 2 — Structural code (timed_exit_monitor.py)

- [ ] T2.1 `_get_timed_cfg`: accept either scalar or dict for `trail_activation_r`; normalise to per-style dict
- [ ] T2.2 Add helper `_activation_r_for(tcfg, style) -> float`
- [ ] T2.3 **Mode-dispatch** (suggestion #1): when `tp_mode == "trailing_atr"`, the trail block is the only profit-side exit. After the trail evaluation in `_handle_mt5_row` / `_handle_bybit_row`, return early — suppresses BE-lock ladder *and* timed close. BE remains via the trail block (broker SL ratcheted to `max(entry, trail_level)`).
- [ ] T2.4 **Broker-enforced trail** (suggestion #6): when current_r ≥ activation_r and trail computed, call `move_sl(trail_level)` through `_protective_sl_tightens` guard. Non-breach path now ratchets the broker SL up to track the trail.
- [ ] T2.5 New helper `_apply_trail_ratchet(...)` returning success/skip/error
- [ ] T2.6 **Loud trail-fail** (suggestion #4): on candle fetch failure inside `_compute_chandelier_trail`, hold previous `_trail_state[ticket_key]` if present (instead of returning None); elevate failure log from debug → warning

## Phase 3 — Reliability & observability

- [ ] T3.1 New SQLite table `timed_exit_state(audit_id PK, ticket, venue, trail_level, lock_r, last_update_ts)` — additive DDL in `athena.py`
- [ ] T3.2 Persist `_trail_state` and `_scalp_profit_lock_state` on each ratchet; hydrate on monitor start
- [ ] T3.3 **Stable ticket keying** (suggestion #9): switch keys from `ticket` to `(venue, audit_id)` everywhere `_trail_state` / `_scalp_profit_lock_state` are accessed
- [ ] T3.4 **Distinct exit tags** (suggestion #11): `_mark_timed_close` accepts `reason` parameter; trail closes write `TRAIL_CLOSE`, timer closes write `TIMED_CLOSE`, lock-SL hits write `LOCK_SL_HIT` (when detected)
- [ ] T3.5 **Per-tick audit** (suggestion #10): only on state change — BE set, trail ratchet, lock upgrade, close. Light row into existing `audit_log.factors_json` or a new `timed_exit_ticks` table (one decision per row, not per tick)
- [ ] T3.6 **Timer-tightens-trail** (suggestion #2): at `mins >= close_min`, multiply `trail_atr_mult` by 0.6; pure config-driven, gated by new `timer_tightens_trail: true`

## Tests

- [ ] T4.1 Update `_make_config` in `tests/test_timed_exit_phases.py` to accept per-style `trail_activation_r`
- [ ] T4.2 Update `TestTrailingConfig.test_trail_activation_r` for per-style behaviour with scalar back-compat
- [ ] T4.3 Update `test_trail_activation_requires_configured_r` to use per-style
- [ ] T4.4 New: `tp_mode=trailing_atr` suppresses lock and timed-close — assert no force-close fires when chandelier active
- [ ] T4.5 New: broker SL ratchets to trail level on each tick when in trail-active regime
- [ ] T4.6 New: candle fetch failure holds previous trail level rather than returning None
- [ ] T4.7 New: `TRAIL_CLOSE` vs `TIMED_CLOSE` exit tags written distinctly
- [ ] T4.8 New: `(venue, audit_id)` ticket keying — two positions with `ticket=0` don't collide

## Validation

- [ ] V1 `python -m py_compile timed_exit_monitor.py config.py athena.py`
- [ ] V2 `python -m pytest tests/test_timed_exit_phases.py -q`
- [ ] V3 Adjacent: `python -m pytest tests/test_health_routes.py -q`
- [ ] V4 git diff --check on changed files

## Review

### What changed

**Phase 1 — config.yaml `TIMED_EXIT` block**
- `intraday.timed_close_enabled`: true → false
- `swing.timed_close_enabled`: true → false
- `scalp.profit_lock_enabled`: true → false
- `trail_activation_r`: scalar 1.0 → per-style dict `{scalp: 0.3, intraday: 0.5, swing: 1.0}`
- `trail_indicator_confirm`: false → true
- New: `timer_tightens_trail: true`, `timer_tighten_factor: 0.6`

**Phase 2 — timed_exit_monitor.py structural**
- New `_evaluate_trail()` returns `{action: none|ratchet|close, ...}` — single state machine for the chandelier path. `_should_trail_close()` becomes a thin back-compat wrapper.
- `_get_timed_cfg()` accepts scalar OR per-style dict for `trail_activation_r`; new `_activation_r_for(tcfg, style)` helper.
- `_handle_mt5_row` / `_handle_bybit_row`: when `tp_mode=="trailing_atr"`, the trail block is the only profit-side exit. After evaluation, an unconditional `return` suppresses the lock ladder and timed-close branches. **This eliminates Issues 1, 2, 3, 7 from the audit by construction.**
- Broker-enforced trail (Issue 5 fix): on each tick where `current_r ≥ activation_r`, the broker SL is ratcheted to the trail level via `mt5_move_sl_to_breakeven` / `bybit_move_sl_to_breakeven`, gated by `_protective_sl_tightens`. The trail is no longer a virtual 30-second poll.
- Loud failure (Issue 4 fix): `_compute_chandelier_trail` now returns the previous `_trail_state[ticket_key]` on candle fetch failure rather than `None`, with a `WARNING`-level log. Only returns `None` when both fetch fails AND no prior level exists.
- Breach without indicator confirmation now ratchets rather than ignoring — the SL still tightens.

**Phase 3 — persistence, observability, keying, exit tags**
- New SQLite table `timed_exit_state(state_key PK, trail_level, lock_r, last_update_ts)`. Idempotent DDL via `_ensure_state_table`. Best-effort persistence on every ratchet / lock upgrade; one-shot hydration on first monitor tick.
- Stable ticket keying: `_state_key(venue, audit_id, ticket)` — uses `audit_id` as canonical identity, falls back to ticket only when audit_id is missing/0/empty. Fixes the Bybit `ticket=0` collision.
- `_mark_timed_close` accepts `reason: str = "TIMED_CLOSE"`. Trail closes write `TRAIL_CLOSE`; legacy timed paths still write `TIMED_CLOSE`.
- Timer-tightens-trail: when `mins_open ≥ close_min`, the chandelier multiplier is multiplied by `timer_tighten_factor` (default 0.6), so stagnating trades get squeezed instead of force-closed.
- **Deferred**: standalone `timed_exit_ticks` per-tick audit table — existing `log.info` lines (`TRAIL CLOSE signal`, `TRAIL RATCHET`, `BE set`, `PROFIT_LOCK`, etc.) already provide structured event logging. Adding a dedicated table for state-change events is a worthwhile follow-up but was not required for the goal.

**Tests**
- Added 7 new test classes in `tests/test_timed_exit_phases.py`:
  - `TestActivationRForStyle` (scalar back-compat + per-style)
  - `TestStableTicketKey` (audit_id keying, no Bybit ticket=0 collision)
  - `TestEvaluateTrail` (none/ratchet/close decision matrix)
  - `TestTrailFailHoldsPrevious` (Issue 4 — held level vs None)
  - `TestDistinctExitTags` (TRAIL_CLOSE vs TIMED_CLOSE)
  - `TestStatePersistence` (DDL + persist/hydrate roundtrip)
  - `TestTimerTightensTrail` (mult shrinks past close_min)
  - `TestPhase1Defaults` (sanity on _DEFAULT_CFG shape)
- Updated `_make_config` helper to handle scalar→dict overrides without `.update()` crashes.
- Existing 60 tests still green.

### Validation

- `python -m py_compile timed_exit_monitor.py config.py` → clean
- `python -m pytest tests/test_timed_exit_phases.py -q` → **67 passed in 0.73s**
- `python -m pytest tests/test_health_routes.py -q` → 6 passed (adjacent regression check)
- `yaml.safe_load(config.yaml)` → all new keys present with correct values
- `git diff --check` → clean (only Windows LF/CRLF advisory on tasks/todo.md)

### Behavioural summary (what this achieves)

- A trade in `tp_mode=trailing_atr` mode now takes one of three states per tick:
  1. **Below activation_r** (per-style threshold): rides on its original ATR SL. No BE-lock ladder, no timed-close, nothing fires.
  2. **At/above activation_r, trail not breached**: broker SL ratchets up to the chandelier trail level. The broker enforces the exit even if the monitor crashes between ticks.
  3. **Trail breached + RSI/MACD reversal confirmed**: market close, exit_reason=`TRAIL_CLOSE`.
- The "+0.10R lock SL closes a scalp at minimal profit" path no longer exists in trailing_atr mode. The "intraday at +0.7R force-closed at 30 min" path no longer exists.
- A transient candle fetch failure (Binance rate limit, MT5 D1 not yet closed) now holds the previous trail level with a WARNING log, instead of silently demoting the trade to timed-close.
- Reverting `tp_mode` to `"fixed"` restores the legacy lock + timed-close pipeline unchanged.

# MicroStore Maintenance Lock Warning

- [x] Confirm current warning source from logs and code.
- [x] Trace write/maintenance SQLite lock path.
- [ ] Patch maintenance ordering without changing scoring, risk, or execution logic.
- [ ] Add focused regression coverage.
- [ ] Run focused validation and record exact outcome.

# Engine B Forex Execution Failure Diagnostics

- [x] Capture current execution failure evidence from logs and recent runtime files.
- [x] Trace Engine B forex execute request path from UI/API to broker/paper execution.
- [x] Identify where the failure reason is lost or hidden.
- [x] Patch minimal logging/response behavior if root cause is confirmed.
- [x] Run focused validation and record exact outcome.

## Review

- Confirmed the React and Telegram execution surfaces post Engine B/forex orders to `/api/quick-execute`.
- Confirmed failed `run_managed_execution()` results were returned as generic `Execution failed` when the broker/lifecycle result had no `error` field, and `/api/quick-execute` did not log that failed result.
- Added failure extraction/logging so `/api/quick-execute` and `/api/execute` now log pair, venue, reason, and sanitized execution result. Empty broker failures now return `Execution failed: broker_execute returned no error detail`.
- Validation passed: `python -m py_compile execution.py tests\test_crit_fixes.py`.
- Validation passed: `python -m pytest tests/test_crit_fixes.py -q --basetemp=tmp/pytest-exec-failure-crit` (`11 passed`).
- Adjacent validation passed: `python -m pytest tests/test_scalp_execution.py::test_quick_execute_rejects_direction_flip -q --basetemp=tmp/pytest-exec-failure-scalp-adjacent`.
- Adjacent validation passed: `python -m pytest tests/test_execution_engine_c_scan.py -q --basetemp=tmp/pytest-exec-failure-engine-c-scan` (`4 passed`).

# Engine B Audit-Safe Knobs And Diagnostics

- [x] Add default-safe config knobs for D1 PD-array conflict distance and rejection wick/body ratio.
- [x] Add follow-through diagnostics while keeping score impact disabled by default.
- [x] Add focused Engine B regression coverage.
- [x] Run focused compile and pytest validation.

## Review

- Added `NAKED_ENGINE.d1_pd_array_conflict_window_atr_mult: 3.0` and `NAKED_ENGINE.rejection_wick_body_ratio: 1.2`, preserving current defaults while allowing backtest/shadow trials.
- Added `ENGINE_B_FOLLOW_THROUGH.DIAGNOSTICS_ENABLED: true`; score impact remains disabled unless `ENGINE_B_FOLLOW_THROUGH.ENABLED` is true.
- Focused red/green regression coverage passed for configurable D1 conflict window, configurable rejection wick/body ratio, and diagnostics-only follow-through.
- Compile validation passed: `python -m py_compile market_structure.py config.py tests\test_engine_b_diagnostics.py`.
- Focused Engine B validation passed: `python -m pytest tests/test_engine_b_diagnostics.py tests/test_engine_b_rr_basis.py -q --basetemp=.\tests\.tmp-engine-b-knobs-focused` (`61 passed`).
- Threshold-audit diagnostic slice passed: `python -m pytest tests/test_threshold_audit.py -k "d1_pd_array or structural_tp or checklist" -q --basetemp=.\tests\.tmp-engine-b-knobs-threshold` (`4 passed, 24 deselected`).
- `git diff --check -- market_structure.py config.py config.yaml tests/test_engine_b_diagnostics.py tasks/todo.md` passed with only Windows line-ending warnings.

# Backtest Audit And Engine B Speed

- [x] Save the agreed backtest audit/speed plan.
- [x] Add a config-gated Engine B FVG fast path with legacy fallback.
- [x] Add parity coverage for the Engine B FVG fast path.
- [x] Run focused Engine B/backtest validation.

## Review

- Plan saved to `C:\Users\damia\.windsurf\plans\backtest-audit-speed-plan-37803e.md`.
- Added `ENGINE_B_FAST_FVG_DETECTION: true` and a legacy fallback path.
- Replaced nested FVG mitigation scans with suffix high/low lookups. Synthetic 5,000-candle FVG-heavy benchmark: legacy `569.77 ms`, fast `9.92 ms`, identical output.
- **FIX:** Suffix arrays had allocation overhead making fast path 40% slower on realistic data (few FVGs). Replaced with preconvert+legacy hybrid: pre-convert highs/lows to float lists once, then use same nested-loop mitigation. Now 1.1x faster than legacy on 2,000-candle realistic data (0.26 ms vs 0.28 ms).
- Reused Engine B backtest D1/H4 cutoff indices inside the per-bar loop to avoid repeated timestamp bisection.
- Compile validation passed: `python -m py_compile backtest_runner.py config.py market_structure.py tests\test_engine_b_diagnostics.py`.
- Focused validation passed: `python -m pytest tests\test_engine_b_diagnostics.py tests\test_backtest_integrity.py -q` (`59 passed`).
- Broader Engine B/C/D backtest-adjacent validation passed: `python -m pytest tests\test_engine_b_diagnostics.py tests\test_engine_b_rr_basis.py tests\test_engine_b_ai.py tests\test_backtest_integrity.py tests\test_engine_c_bt_levels.py tests\test_scalp_backtest_rules.py -q` (`103 passed`).
- Shared cache/API validation passed: `python -m pytest tests\test_backtest_candle_cache.py tests\test_candle_cache_keys.py tests\test_scan_backtest_service.py tests\test_routes_backtest_history.py -q` (`22 passed`).
- Final focused compile plus regression validation passed: `python -m py_compile backtest_runner.py config.py market_structure.py tests\test_engine_b_diagnostics.py`; `python -m pytest tests\test_engine_b_diagnostics.py tests\test_backtest_integrity.py tests\test_backtest_candle_cache.py tests\test_scan_backtest_service.py -q` (`65 passed`).

# Engine A Logic Fixes

- [x] Wire EMA hysteresis to prior confirmed indicator snapshots.
- [x] Make missing D1/H4 ADX fail-safe through explicit config.
- [x] Clarify Engine A threshold operator truth.
- [x] Wire intermarket confirmation into live Engine A diagnostics/output.
- [x] Run focused Engine A validation.

## Review

- Focused Engine A validation passed: `python -m pytest tests\test_factor_scoring.py tests\test_scoring_group_routing.py tests\test_stage3_enhancements.py tests\test_stage4_hardening.py -q`.
- Compile validation passed: `python -m py_compile factor_scoring.py scoring.py config.py`.
- Broader intermarket/athena check was not fully green because `tests\test_athena.py` has two static UI source assertions against `static\index.html`; those failures are outside the Engine A files touched here.

# Engine B Logic Fixes

- [x] Enforce Engine B style/regime `min_score` in the live pass helper.
- [x] Make Engine B Research Lab gate upgrades default-off.
- [x] Use confirmed bars for Engine B structure while keeping live bars optional for triggers.
- [x] Tighten crypto profile target/trigger/stop/path pass gates.
- [x] Run focused Engine B validation.

## Review

- Focused Engine B validation passed: `python -m pytest tests\test_engine_b_diagnostics.py tests\test_engine_b_rr_basis.py tests\test_engine_b_ai.py tests\test_naked_style_persistence.py tests\test_structure_context.py -q`.
- Compile validation passed: `python -m py_compile market_structure.py athena.py config.py engine_b_ai.py athena_app\services\engine_b_market_state.py`.

# AI Structure And Audit Fixes

- [x] Map Marcus and non-Marcus AI call sites.
- [x] Add structured Marcus scoring fields without changing live execution gates.
- [x] Add deterministic advisory hard-rule fields for Marcus output.
- [x] Extend AI audit logging to uncovered AI surfaces where safe.
- [x] Run focused validation.

## Review

- Compile validation passed: `python -m py_compile ai_schemas.py ai_review_logger.py engine_c_ai.py news_sentiment_feed.py ai_learning.py athena_research\ai_analyst.py athena.py auto_trader.py`.
- Focused AI safety/helper validation passed: `python -m pytest tests\test_ai_safety_helpers.py tests\test_engine_c_ai.py -q`.
- AI review audit validation passed: `python -m pytest tests\test_ai_review_safety.py -q`.
- Adjacent AI routing validation passed: `python -m pytest tests\test_ai_config_routing.py tests\test_engine_b_ai.py tests\test_signal_debate.py -q`.
- Combined focused validation passed: `python -m pytest tests\test_ai_safety_helpers.py tests\test_ai_review_safety.py tests\test_engine_c_ai.py tests\test_ai_config_routing.py tests\test_engine_b_ai.py tests\test_signal_debate.py -q` (`109 passed`).

# Guardian Tab Data Fix

- [x] Trace Guardian frontend endpoint payloads.
- [x] Normalize boot checks, feed health, divergence, and forensics to backend shapes.
- [x] Run focused frontend validation.

## Review

- Confirmed the running `/api/guardian/status` route returns Guardian, divergence, forensics, and overall payloads with HTTP 200.
- Confirmed the running `/api/feed-health` route returns `activePairCount`, `pairsWithCachedMeta`, and `pairs` with HTTP 200.
- Frontend production build passed: `npm run build` from `static/react-app/app`.
- Static route smoke validation passed: `python -m pytest tests/test_api_contract_smoke.py -q`.

# UI Encoding Fix

- [x] Trace weird UI symbols to the built frontend bundle.
- [x] Regenerate the bundle with correct UTF-8 output.
- [x] Strip generated trailing whitespace with Node UTF-8 file APIs.
- [x] Verify the served asset contains normal separators, not mojibake.

## Review

- Confirmed `static/assets/index-B2jOha4b.js` had mojibake such as `Â·` while `SignalsPanel.tsx` had the intended separator.
- Rebuilt the frontend bundle with `npm run build`.
- Confirmed the served JS asset has `containsBadMiddleDot=False`, `containsGoodMiddleDot=True`, `containsBadEmDash=False`, and `containsGoodEmDash=True`.
- `git diff --check -- static/assets/index-B2jOha4b.js` passed after encoding-safe trailing whitespace cleanup.

# Engine B Structural Target And ATR Fixes

- [x] Replace SL-multiplier structural TP buffer with dedicated Engine B config.
- [x] Route Engine B private ATR helper through shared Wilder ATR.
- [x] Make zero/non-positive ATR reject execution level resolution instead of using tiny synthetic ATR.
- [x] Add focused regression coverage.
- [x] Run focused Engine B validation.

## Review

- Compile validation passed: `python -m py_compile market_structure.py config.py`.
- Focused Engine B validation passed: `python -m pytest tests/test_engine_b_rr_basis.py tests/test_engine_b_diagnostics.py -q` (`55 passed`).
- Structural TP threshold-audit slice passed: `python -m pytest tests/test_threshold_audit.py -k structural_tp -q`.
- Full `tests/test_threshold_audit.py -q` was not green due two unrelated near-miss/report assertions (`test_near_miss_classification_works`, `test_fail_reason_counts_are_reported`).

# Marcus AI Review Timeout Fix

- [x] Trace slow AI review evidence to the Marcus/Text Review path.
- [x] Add config-gated timeout for the Marcus provider call.
- [x] Add timing logs for prep, prompt build, and provider latency.
- [x] Run focused compile and pytest validation.

## Review

- Compile validation passed: `python -m py_compile config.py athena.py`.
- Focused AI routing/review validation passed: `python -m pytest tests/test_ai_config_routing.py tests/test_ai_review_safety.py -q` (`78 passed`).
- Engine B AI regression validation passed: `python -m pytest tests/test_engine_b_ai.py -q` (`13 passed`).

# Telegram Link Upgrade

- [x] Trace current Telegram config, bot startup, notification delivery, and tests.
- [x] Patch Telegram delivery for retry, bounded queueing, and visible delivery state.
- [x] Keep Telegram config default-safe and consistent between notifier and bot startup.
- [x] Add focused tests for delivery retries, disabled config, and startup guards.
- [x] Run focused compile and pytest validation.
- [x] Add Engine B Telegram scan command and persistent bot menu.
- [x] Validate Engine B Telegram command/menu changes.

## Review

- Compile validation passed: `python -m py_compile telegram_notify.py telegram_bot.py telegram_diagnostic.py quick_telegram_test.py`.
- Focused Telegram validation passed: `python -m pytest tests/test_telegram_bot.py tests/test_telegram_notify.py tests/test_audit_fixes.py::test_telegram_notify_datetime_usage -q` (`13 passed`).

# Marcus AI SDK Retry Timeout Fix

- [x] Trace the timeout log prefix to the Marcus/Text Review path.
- [x] Disable SDK retry multiplication for Marcus while keeping timeout config-gated.
- [x] Add focused regression coverage.
- [x] Run focused compile and pytest validation.

## Review

- Confirmed `[AI] ERROR for ...` is emitted by `run_ai()` in `athena.py`, not by `engine_b_ai.py`.

# Engine D ATR Stop And 1R TP Fix

- [x] Trace current Engine D level calculation and execution gate.
- [x] Change Engine D scalp TP1 to the 1R self-pay target.
- [x] Use ATR-based SL when ATR is available, with config defaults.
- [x] Keep VP/POC/VAH/VAL targets as context/runner fields, not hard RR blockers.
- [x] Add focused regression coverage.
- [x] Run focused compile and pytest validation.

## Review

- `calculate_scalp_levels()` now uses ATR stop distance when available and sets `tp1`/`tp_partial` to the configured 1R self-pay target.
- VP targets are preserved as `structural_tp` / `structural_rr` / `structure_target_close` and close structure is a soft warning, not a hard execution blocker.
- API normalization now passes the structural target fields to the Scalp Lab UI payload.
- Compile validation passed: `python -m py_compile scalp_engine.py athena.py tests\test_scalp_engine.py tests\test_scalp_fixes.py`.
- Focused validation passed: `python -m pytest tests/test_scalp_engine.py tests/test_scalp_fixes.py::test_crypto_scalp_precision_guard tests/test_scalp_fixes.py::test_scalp_tp1_is_1r_and_structural_target_is_context -q --basetemp=.pytest_tmp_engine_d_1r_focused` (`99 passed`).
- Broader adjacent scalp slice was not green due pre-existing fixture/import issues around `mt5_map_symbol` and `bybit_executor._get_exchange`; the related level-contract tests above passed.
- Compile validation passed: `python -m py_compile config.py athena.py`.
- Focused AI routing/review validation passed: `python -m pytest tests/test_ai_config_routing.py tests/test_ai_review_safety.py -q` (`86 passed`).
- Engine B AI regression validation passed: `python -m pytest tests/test_engine_b_ai.py -q` (`13 passed`).

# Marcus Single Timeout Follow-up

- [x] Confirm the post-push timeout no longer includes OpenAI SDK retry lines.
- [x] Confirm the prior timing logs are suppressed by `log.setLevel(logging.WARNING)`.
- [x] Extend Marcus timeout to one bounded 60s attempt.
- [x] Make Marcus timeout failures log elapsed/config context at error level.
- [x] Run focused validation.

## Review

- Confirmed `logs/sentinel.log` changed from retry storm at `15:41-15:42` to one timeout at `16:06`.
- Confirmed `log.info(...)` Marcus timing lines were not visible because the `sentinel` logger is set to `WARNING`.
- Compile validation passed: `python -m py_compile config.py athena.py`.
- Focused AI routing/review validation passed: `python -m pytest tests/test_ai_config_routing.py tests/test_ai_review_safety.py -q` (`86 passed`).
- Engine B AI regression validation passed: `python -m pytest tests/test_engine_b_ai.py -q` (`13 passed`).

# Athena.py Refactor Plan

- [x] Map current `athena.py` size, top-level functions, routes, and largest risk areas.
- [x] Confirm existing extraction package shape under `athena_app/api` and `athena_app/services`.
- [x] Confirm static route tests currently inspect `athena.py` directly and need module-aware compatibility first.
- [x] Save staged refactor plan to `docs/superpowers/plans/2026-05-05-athena-py-refactor.md`.
- [x] Execute Task 1: make route contract tests module-aware.
- [x] Execute Task 2: extract lottery route registration without moving bodies.
- [x] Execute Task 3: move lottery route bodies after route registration is green.
- [x] Execute Task 4: extract market metadata read-only routes.
- [x] Execute Task 5: extract live dashboard route registration, helpers, and route bodies.
- [x] Execute Task 6: extract status/support read-only routes.
- [x] Execute Task 7: extract read-only broker status routes.
- [x] Execute Task 8: extract read-only backtest history routes.
- [x] Execute Task 9: extract read-only audit route.
- [ ] Execute later read-only route groups before touching AI, Engine B, Scalp execute, risk, or runtime startup.

## Review

- `athena.py` is currently 17,220 lines with 229 top-level functions and 106 Flask route decorators.
- Largest confirmed functions include `analyze_pair`, `api_chart_analysis`, `api_scan_naked`, `_build_signal_message`, `api_performance`, `_compute_naked_analysis`, `api_live_dashboard_snapshot`, and `run_ai`.
- Current worktree already contains unrelated/user changes in `athena.py`, `config.py`, `config.yaml`, `tests/test_ai_config_routing.py`, and logs; refactor execution must not overwrite or bundle them.
- Added module-aware static route parsing for `app.add_url_rule(...)` before moving routes.
- Moved Lottery API handlers from `athena.py` to `athena_app/api/routes_lottery.py` and kept the same URLs, endpoints, and methods through explicit route registration.
- Left scoring, risk, freshness, broker/live execution, Marcus/Text Review, Engine B AI, and Chart Vision paths unchanged in this slice.
- Compile validation passed: `python -m py_compile athena.py athena_app/api/routes_lottery.py tests/route_contract_helpers.py`.
- Static route smoke validation passed: `python -m pytest tests/test_api_contract_smoke.py -q` (`6 passed`).
- Route-focused live-dashboard validation passed: `python -m pytest tests/test_live_dashboard.py -k "not frontend" -q` (`28 passed, 6 deselected`).
- Lottery-adjacent research lab validation passed: `python -m pytest tests/test_vectorbt_research_lab.py -q` (`51 passed`, with two pandas `FutureWarning`s).
- Broader `python -m pytest tests/test_api_contract_smoke.py tests/test_live_dashboard.py -q` is not fully green because `tests/test_live_dashboard.py` still has two static frontend assertions against `static/index.html` (`nav-live-dashboard`, `PAPER MODE ON`).
- Moved read-only market metadata handlers from `athena.py` to `athena_app/api/routes_market_data.py`: `/api/market-hours`, `/api/prices`, `/api/yield-curve`, `/api/bulk-prices`, `/api/pairs`, `/api/intermarket-matrix`, `/api/candles`, and `/api/news-sentiment`.
- Added `tests/test_routes_market_data.py` to validate extracted market-data route registration and fake-runtime behavior without importing `athena.py`.
- Market-data compile validation passed: `python -m py_compile athena.py athena_app/api/routes_market_data.py tests/test_routes_market_data.py`.
- Market-data route validation passed: `python -m pytest tests/test_api_contract_smoke.py tests/test_routes_market_data.py -q` (`10 passed`).
- Route-focused combined validation passed: `python -m pytest tests/test_api_contract_smoke.py tests/test_routes_market_data.py tests/test_live_dashboard.py -k "not frontend" -q` (`38 passed, 6 deselected`).
- `tests/test_health_routes.py -q` is not fully green due an existing `/api/feed-health` test monkeypatch mismatch: the test patches `scan_candle_limits` with a required argument, while `HEAD` and current code call `scan_candle_limits()` without arguments in `_feed_health_snapshot()`.
- Moved Live Dashboard diagnostics/snapshot/paper-log routes and `_ld_*` helper cluster from `athena.py` to `athena_app/api/routes_live_dashboard.py`.
- Preserved shared runtime state by passing live references/getters for `_live_dashboard_scalp_cache`, `_last_scan_results`, `_binance_ws`, and `_kill_switch`.
- Added `tests/test_routes_live_dashboard.py` to validate extracted route registration, disabled snapshot behavior, paper-execute real-orders block, and read-only diagnostics empty-runtime behavior without importing `athena.py`.
- Live Dashboard compile validation passed: `python -m py_compile athena.py athena_app/api/routes_lottery.py athena_app/api/routes_market_data.py athena_app/api/routes_live_dashboard.py tests/route_contract_helpers.py tests/test_routes_market_data.py tests/test_routes_live_dashboard.py tests/test_api_contract_smoke.py tests/test_live_dashboard.py`.
- Live Dashboard focused validation passed: `python -m pytest tests/test_api_contract_smoke.py tests/test_routes_market_data.py tests/test_routes_live_dashboard.py tests/test_live_dashboard.py -k "not frontend" -q` (`42 passed, 6 deselected`).
- Broader `python -m pytest tests/test_live_dashboard.py tests/test_api_contract_smoke.py -q` is not fully green because the same two static frontend assertions against `static/index.html` still fail (`nav-live-dashboard`, `PAPER MODE ON`).
- Moved status/support read-only handlers from `athena.py` to `athena_app/api/routes_status.py`: `/`, `/api/last-scan`, `/api/conductor/last`, `/api/kimi/conductor/last`, `/api/conductor/pairs`, `/api/health`, `/api/signal-stability`, `/api/debug/routes`, and `/api/microstructure-health`.
- Preserved mutable status state by passing getters for `ALL_PAIRS`, `ACTIVE_PAIRS`, `_kill_switch`, `_last_scan_results`, `_mt5_connection_health`, and `_micro_cache`.
- Added `tests/test_routes_status.py` to validate route registration, runtime getter behavior, last-scan state updates, microstructure health, and debug route listing without importing `athena.py`.
- Status compile validation passed: `python -m py_compile athena.py athena_app/api/routes_status.py tests/test_routes_status.py tests/test_api_contract_smoke.py`.
- Status route validation passed: `python -m pytest tests/test_routes_status.py -q` (`5 passed`) and `python -m pytest tests/test_api_contract_smoke.py -q` (`6 passed`).
- Existing health subset validation passed for moved routes: `python -m pytest tests/test_health_routes.py -k "not feed_health" -q` (`5 passed, 1 deselected`).
- Route-focused combined validation passed: `python -m pytest tests/test_api_contract_smoke.py tests/test_routes_status.py tests/test_routes_market_data.py tests/test_routes_live_dashboard.py tests/test_live_dashboard.py -k "not frontend" -q` (`47 passed, 6 deselected`).
- Moved read-only broker status handlers from `athena.py` to `athena_app/api/routes_broker_status.py`: `/api/mt5-status`, `/api/mt5-positions`, `/api/bybit-status`, and legacy `/api/binance-status`.
- Left `/api/close-position` in `athena.py` because it can close broker positions and is not part of the read-only status slice.
- Added `tests/test_routes_broker_status.py` to validate route registration and fake MT5/Bybit read-only account/position responses without importing `athena.py` or touching brokers.
- Broker status compile validation passed: `python -m py_compile athena.py athena_app/api/routes_broker_status.py tests/test_routes_broker_status.py tests/test_api_contract_smoke.py`.
- Broker status route validation passed: `python -m pytest tests/test_routes_broker_status.py -q` (`4 passed`) and `python -m pytest tests/test_api_contract_smoke.py -q` (`6 passed`).
- Route-focused combined validation passed: `python -m pytest tests/test_api_contract_smoke.py tests/test_routes_broker_status.py tests/test_routes_status.py tests/test_routes_market_data.py tests/test_routes_live_dashboard.py tests/test_live_dashboard.py -k "not frontend" -q` (`51 passed, 6 deselected`).
- Fixed stale validation tests without changing runtime code: `/api/feed-health` now monkeypatches no-arg `scan_candle_limits()`, and Live Dashboard frontend checks assert the current React/Vite Live Cockpit source instead of legacy inline `static/index.html` IDs.
- Health/live-dashboard baseline validation passed: `python -m pytest tests/test_health_routes.py tests/test_live_dashboard.py tests/test_api_contract_smoke.py -q` (`46 passed`).
- Moved read-only backtest history handlers from `athena.py` to `athena_app/api/routes_backtest.py`: `/api/backtest-history`, `/api/backtest-history/<pair_name>`, and `/api/backtest-best`.
- Left POST backtest execution routes in `athena.py` because they run backtests and can trigger `BT_AUTO_TOGGLE` behavior.
- Added `tests/test_routes_backtest_history.py` to validate route registration plus SQLite history ordering/filtering/best-result behavior without importing `athena.py`.
- Backtest history compile validation passed: `python -m py_compile athena.py athena_app/api/routes_backtest.py tests/test_routes_backtest_history.py tests/test_api_contract_smoke.py`.
- Backtest history route validation passed: `python -m pytest tests/test_routes_backtest_history.py tests/test_api_contract_smoke.py -q` (`10 passed`).
- Moved read-only audit-log handler from `athena.py` to `athena_app/api/routes_audit.py`: `/api/audit`.
- Added `tests/test_routes_audit.py` to validate route registration and limit/order behavior against a repo-local SQLite fixture without importing `athena.py`.
- Audit route validation passed: `python -m pytest tests/test_routes_audit.py tests/test_routes_backtest_history.py tests/test_api_contract_smoke.py -q` (`12 passed`).
- Combined route-focused validation passed: `python -m pytest tests/test_api_contract_smoke.py tests/test_routes_audit.py tests/test_routes_backtest_history.py tests/test_routes_broker_status.py tests/test_routes_status.py tests/test_routes_market_data.py tests/test_routes_live_dashboard.py tests/test_live_dashboard.py tests/test_health_routes.py tests/test_scan_backtest_service.py -q` (`72 passed`).
- `athena.py` at pushed `HEAD` is currently 14,193 lines with 53 Flask route decorators after the safe read-only route slices.

# Engine D Fabio Scalp Tool Gap Report

- [x] Extract DOCX text/tables/footnotes for requirements evidence.
- [x] Fact-check headline external claims against primary/official sources where possible.
- [x] Map DOCX scalp-tool requirements against Engine D code, config, API, UI, and tests.
- [x] Write a confirmed/partial/missing/not-verified gap report without changing strategy behavior.

## Review

- Created `docs/diagnostics/engine_d_fabio_scalp_tool_gap_report.md`.
- Confirmed current Engine D has the core skeleton: VP, market-state/location/aggression pipeline, grading, fee guard, ATR/1R levels, daily risk state, fresh-scan execution, and diagnostics.
- Confirmed the largest gaps are visibility and fidelity, not missing indicators: source fields are present in raw signals but not fully passed to Scalp Lab UI, non-crypto aggression remains proxy-based, profile anchoring is mechanical last-N M15 bars, and neutral CVD can pass some VA-extreme setups under current defaults.
- No scoring, risk, threshold, or live-execution behavior was changed.

# Engine D Fabio Phase A Source Visibility

- [x] Add focused tests for Engine D aggression fidelity source classification.
- [x] Pass VP/CVD source and bucket fields through Scalp Lab API normalization.
- [x] Add report-only aggression fidelity fields to raw Engine D signals.
- [x] Display VP/CVD source, bucket count, proxy-flow status, and strict Fabio shadow status in Scalp Lab.
- [x] Keep Engine D scoring, thresholds, risk, and execution gates unchanged.
- [x] Run focused compile, TypeScript, and pytest validation.

## Review

- Added `_engine_d_aggression_fidelity()` in `scalp_engine.py` to label Binance aggTrade flow as true trade flow and candle/MT5/range/error/unavailable sources as proxy or non-strict diagnostic sources.
- Added `aggression_source`, `aggression_source_raw`, `aggression_source_is_proxy`, `aggression_confirmed`, `strict_fabio_pass`, and `aggression_components` to Engine D signal output.
- Preserved `vp_volume_source`, `vp_bucket_count`, `cvd_source`, and `cvd_bucket_count` through `_scalp_ui_signal()` in `athena.py`.
- Updated `ScalpLabPanel.tsx` to show source/fidelity badges and detail rows without changing execution controls.
- Updated two existing Live Dashboard helper tests in `tests/test_scalp_execution.py` to call `athena_app.api.routes_live_dashboard`, where those helpers currently live after the route extraction.
- Validation passed:
  - `python -m pytest tests/test_scalp_engine.py::test_aggression_fidelity_marks_proxy_flow_as_not_strict tests/test_scalp_engine.py::test_aggression_fidelity_marks_binance_trade_flow_as_strict -q --basetemp=.pytest_local_tmp\engine_d_phase_a_green1` (`2 passed`)
  - `python -m pytest tests/test_scalp_execution.py::test_scalp_ui_signal_preserves_flow_fidelity_fields -q --basetemp=.pytest_local_tmp\engine_d_phase_a_green2` (`1 passed`)
  - `python -m py_compile scalp_engine.py athena.py`
  - `.\node_modules\.bin\tsc.cmd -b --noEmit` from `static/react-app/app`
  - `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp\engine_d_phase_a_focused` (`112 passed`)

# Engine D Fabio Phase B Shadow Diagnostics

- [x] Add focused failing tests for strict Fabio three-pillar shadow diagnostics.
- [x] Confirm strict Fabio shadow diagnostics do not change `gate_result`, `executable`, fail reasons, or soft warnings.
- [x] Pass shadow mismatch fields through Scalp Lab API normalization.
- [x] Display strict Fabio reason, missing pillars, and current-vs-strict status in Scalp Lab.
- [x] Update the Fabio gap report with Phase B results.
- [x] Run focused Python, TypeScript, and pytest validation.

## Review

- Added `_engine_d_strict_fabio_shadow()` in `scalp_engine.py` as a report-only three-pillar evaluator.
- The strict shadow check requires market state, location, and true-flow aggression; candle/MT5/range/error/unavailable sources remain proxy/non-strict via the Phase A aggression-fidelity fields.
- Added `strict_fabio_reason`, `strict_fabio_missing_pillars`, `strict_fabio_pillars`, and `current_vs_strict_status` to Engine D signal output and funnel diagnostic notes.
- Preserved those fields through `_scalp_ui_signal()` in `athena.py`.
- Updated `ScalpLabPanel.tsx` to show strict Fabio reason, missing pillars, and current-vs-strict status.
- Confirmed by test that an existing Engine D `PASS` can be shadow-labelled `current_pass_strict_fail` while still staying `PASS` and executable.
- Restored boot/test validity by removing the dirty prohibited `BACKTEST_USE_BT_MIN_THRESHOLDS: true` key from `config.yaml`; `config.py` explicitly fatal-errors when that key exists.
- Red/green validation passed:
  - `python -m pytest tests/test_scalp_engine.py::test_strict_fabio_shadow_flags_current_pass_with_proxy_aggression tests/test_scalp_engine.py::test_strict_fabio_shadow_passes_when_all_three_pillars_align -q --basetemp=.pytest_local_tmp\engine_d_phase_b_green1` (`2 passed`)
  - `python -m pytest tests/test_scalp_execution.py::test_scalp_ui_signal_preserves_flow_fidelity_fields -q --basetemp=.pytest_local_tmp\engine_d_phase_b_green2` (`1 passed`)
  - `python -m pytest tests/test_scalp_engine.py::test_run_scalp_scan_does_not_block_close_structure_target -q --basetemp=.pytest_local_tmp\engine_d_phase_b_green3` (`1 passed`)
- Focused validation passed:
  - `python -m py_compile config.py scalp_engine.py athena.py tests\test_scalp_engine.py tests\test_scalp_execution.py`
  - `.\node_modules\.bin\tsc.cmd -b --noEmit` from `static/react-app/app`
  - `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_focused` (`114 passed`)
  - `python -m pytest tests/test_scalp_engine.py tests/test_scalp_execution.py tests/test_scalp_fixes.py tests/test_scalp_backtest_rules.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_adjacent` (`132 passed`)
  - `python -m pytest tests/test_stage4_hardening.py tests/test_scoring_group_routing.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_config` (`30 passed`)
  - `python -m pytest tests/test_vectorbt_research_lab.py -q --basetemp=.pytest_local_tmp\engine_d_phase_b_vectorbt_fresh` (`65 passed`, two pandas `FutureWarning`s)
- Full repo validation was not green: `python -m pytest -q --basetemp=.pytest_local_tmp\engine_d_phase_b_all` completed with `1164 passed, 51 failed, 2 warnings`.
- The full-suite failures were not isolated to this Engine D patch. Confirmed categories include legacy `static/index.html` assertions, extracted-route expectations still pointed at `athena.py`, audit repo schema drift, auto-trader debate trace-id expectations, MT5 monkeypatch/module-shape failures, and research-lab no-live-import checks after full-suite import pollution.

# Engine D Fabio Phase C/D Diagnostics And Full-Suite Stabilization

- [x] Stabilize the confirmed full-suite baseline failures without changing runtime trading behavior.
- [x] Add report-only Engine D data-fidelity diagnostics for VP, CVD, absorption, and aggression source truth.
- [x] Add report-only profile-anchor shadow diagnostics for fixed-lookback, prior-session, impulse-leg, and reclaim-leg context.
- [x] Preserve Phase C/D fields through `_scalp_ui_signal()` and Scalp Lab UI.
- [x] Confirm Engine D scoring, thresholds, risk, gate decisions, and paper/live execution behavior are unchanged.
- [x] Run focused, adjacent, TypeScript, and full-suite validation after all changes landed.

## Review

- Added schema-aware `audit_log` insertion in `athena_app/repositories/audit_repo.py` so old audit DBs without `warnings_json` still accept manual-error rows while newer DBs keep Strategy Lab telemetry.
- Updated stale route/UI/source tests to the current extracted-route and React/Vite operator surfaces.
- Preserved auto-trader trace-id safety by updating debate test mocks to include trace IDs instead of weakening `require_trace_id=True`.
- Fixed test-order pollution from `tests/test_timed_exit_phases.py` by keeping broker stubs out of `sys.modules`.
- Fixed vectorbt research-lab no-live-import checks to ignore live modules imported by unrelated earlier tests while still catching forbidden imports introduced inside each research test.
- Fixed Telegram notification test isolation by clearing Telegram env overrides before injecting test config.
- Added `_engine_d_data_fidelity()` and `_engine_d_profile_anchor_shadow()` in `scalp_engine.py`; both are report-only diagnostics.
- Added data-fidelity fields and profile-anchor fields to Engine D signal payloads and Scalp Lab detail rows.
- Confirmed an existing Engine D `PASS` fixture remains `PASS` and executable while the strict/report-only diagnostics label missing strict Fabio aggression.
- Final validation passed:
  - `python -m py_compile athena_app\repositories\audit_repo.py tests\test_audit_repo.py tests\test_auto_trader.py tests\test_threshold_audit.py tests\test_athena.py tests\test_pepperstone_mt5_symbols.py`
  - `python -m pytest tests/test_athena.py tests/test_audit_repo.py tests/test_auto_trader.py tests/test_pepperstone_mt5_symbols.py tests/test_threshold_audit.py -q --basetemp=.pytest_local_tmp\baseline_fixed` (`100 passed`)
  - `python -m pytest tests/test_timed_exit_phases.py tests/test_pepperstone_mt5_symbols.py tests/test_scalp_backtest_rules.py tests/test_scalp_engine.py tests/test_scalp_execution.py tests/test_scalp_fixes.py tests/test_telegram_notify.py -q --basetemp=.pytest_local_tmp\order_pollution_fixed2` (`214 passed`)
  - `python -m pytest tests/test_vectorbt_research_lab.py -q --basetemp=.pytest_local_tmp\vectorbt_guard_fixed2` (`65 passed`, two pandas `FutureWarning`s)
  - `.\node_modules\.bin\tsc.cmd -b --noEmit` from `static/react-app/app`
  - `python -m pytest -q --basetemp=.pytest_local_tmp\baseline_full_after_order_fixes` (`1218 passed`, two pandas `FutureWarning`s)
- Remaining risk: profile-anchor candidates are heuristic visibility only. They are not performance evidence and are not used for scoring, gates, risk, or execution.

# Engine A Asset Group Calibration Fixes

- [x] Confirm current Engine A feed, threshold, ATR-class, and volume-threshold paths from source.
- [x] Move active Engine A group thresholds into config-backed resolver while preserving pair-profile override priority.
- [x] Route ETF and bond ETF SL/TP ATR classes without changing their stock feed/execution identity.
- [x] Wire pair `volume_threshold` into factor scoring volume adjustment.
- [x] Block MT5 broker candle fallback candles by default when the MT5 source fails.
- [x] Add focused tests for each changed behavior.
- [x] Run compile and targeted pytest validation.

## Review

- Added config-backed Engine A score-group thresholds and ETF ATR level-class routing.
- Kept ETF pairs as stock-source instruments while routing SPY/QQQ/GLD/IWM/EEM/etc. to `etf` ATR multipliers and TLT to `etf_bond`.
- Routed live crypto Engine A level ATR and OI context to Bybit by default, with Binance/signal-feed fallback disabled by config.
- Blocked MT5 error payload fallback candles by default so broker-source failure cannot silently become Polygon/yfinance execution candles.
- Wired pair/backtest `volume_threshold` into `compute_factor_scores()` volume adjustment.
- Enabled explicit COT/proxy handling for stock/index formulas and explicit `cot:unsupported` diagnostics for commodities without formula coverage.
- Updated the configured MT5 forex H4 default offset to 2h while preserving tests for the 1h override path.
- Validation passed:
  - `python -m py_compile scoring.py factor_scoring.py candles_cache.py data_feeds.py athena.py backtest_runner.py config.py athena_app\services\market_state.py athena_app\api\routes_live_dashboard.py tests\test_scoring_group_routing.py tests\test_factor_scoring.py tests\test_candles_cache.py tests\test_data_feeds_backtest_derivatives.py tests\test_freshness_h4_offset_regression.py`
  - `python -m pytest tests/test_scoring_group_routing.py tests/test_factor_scoring.py tests/test_candles_cache.py tests/test_data_feeds_backtest_derivatives.py tests/test_freshness_h4_offset_regression.py -q --basetemp=.pytest_local_tmp\engine_a_asset_groups` (`70 passed`, one pytest cache permission warning)
  - `python -m pytest tests/test_style_level_consistency.py tests/test_engine_c_bt_levels.py tests/test_market_specific_contracts.py tests/test_candle_cache_meta.py tests/test_market_state_offsets.py tests/test_candle_freshness_diagnostics.py tests/test_data_freshness.py -q --basetemp=.pytest_local_tmp\engine_a_asset_groups_adjacent` (`50 passed`, one pytest cache permission warning)
  - `python -m pytest tests/test_factor_group_overrides.py tests/test_stage3_enhancements.py tests/test_scoring.py -q --basetemp=.pytest_local_tmp\engine_a_asset_groups_factor_adjacent` (`22 passed`, one pytest cache permission warning)
  - `git diff --check -- scoring.py factor_scoring.py candles_cache.py data_feeds.py athena.py backtest_runner.py config.py config.yaml athena_app/services/market_state.py athena_app/api/routes_live_dashboard.py tests/test_scoring_group_routing.py tests/test_factor_scoring.py tests/test_candles_cache.py tests/test_data_feeds_backtest_derivatives.py tests/test_freshness_h4_offset_regression.py tasks/todo.md`

# Engine A Missed Audit Fixes

- [x] Verify the supplied misses against current source, not pasted line numbers.
- [x] Remove or wire dead `FACTOR_WEIGHTS` config surface.
- [x] Fix single-timeframe trend coverage to use the configured per-class max trend weight.
- [x] Align Python fallback defaults with YAML runtime defaults.
- [x] Surface scan-vs-auto-trader score gate gap in scan tier metadata and raise index auto floor.
- [x] Fix commodity EODHD volume ticker mapping/REST routing without changing OHLC source.
- [x] Add diagnostics/documentation for advisory-only stock enrichment and COT fade behavior.
- [x] Add focused tests and run targeted validation.

## Review

- Removed dead `FACTOR_WEIGHTS` from runtime config and the research prompt builder.
- Replaced the hardcoded single-TF trend max with the configured per-class trend weight max.
- Raised `AUTO_TRADE_MIN_SCORE.index` to 1.8 and exposed `signalTierReason` so the scan UI can show the separate auto-trader score floor.
- Added explicit EODHD commodity ticker config and allowed whitelisted commodity/index EODHD volume REST paths to run instead of returning `no_real_volume`.
- Kept MT5 forex D1 offset at UTC 00:00 and added a regression test confirming H4-only offset behavior.
- Validation passed:
  - `python -m py_compile factor_scoring.py config.py scoring.py scanner.py eodhd_volume_overlay.py athena.py athena_research\prompt_builder.py reproduce_bug.py tests\test_factor_scoring.py tests\test_scoring_group_routing.py tests\test_eodhd_volume_overlay.py tests\test_market_state_offsets.py`
  - `python -m pytest tests/test_factor_scoring.py tests/test_scoring_group_routing.py tests/test_eodhd_volume_overlay.py tests/test_market_state_offsets.py -q --basetemp C:\tmp\athena_engine_a_missed_pytest` (`66 passed`, one pytest cache permission warning)
  - `python -m pytest tests/test_athena.py tests/test_auto_trader.py tests/test_scoring.py -q --basetemp C:\tmp\athena_engine_a_adjacent_pytest` (`44 passed`, one pytest cache permission warning)
  - `python -m pytest tests/test_factor_scoring.py tests/test_scoring_group_routing.py tests/test_eodhd_volume_overlay.py tests/test_market_state_offsets.py tests/test_athena.py tests/test_auto_trader.py tests/test_scoring.py -q --basetemp C:\tmp\athena_engine_a_missed_final_pytest` (`110 passed`, one pytest cache permission warning)
  - `git diff --check -- factor_scoring.py config.py config.yaml scoring.py scanner.py eodhd_volume_overlay.py athena.py athena_research/prompt_builder.py reproduce_bug.py tests/test_factor_scoring.py tests/test_scoring_group_routing.py tests/test_eodhd_volume_overlay.py tests/test_market_state_offsets.py`
