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
