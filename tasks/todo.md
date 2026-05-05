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
