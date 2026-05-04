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
