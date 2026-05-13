# Sentinel Pro v4 - Codex Dead Code and Duplication Audit

Date: 2026-05-12
Mode: Audit-only. No patches applied.
Repo: `C:\dev\athena-python`

## Scope Inspected

- `athena.py` full file, 14,886 lines
- All root `patch_*.py`
- All root `reproduce_*.py`, `verify_*.py`, `check_*.py`, `scratch_*.py`
- All root `test_*.py` files not under `tests/`
- `athena_legacy.py`
- `athena_runtime.py`
- `app.py`
- `legacy/`
- `tmp/`
- Root `*.json` dumps
- Cross-file references in `app.py`, `athena_app/**/*.py`, `tests/**/*.py`, and production Python files where relevant

No tests were run because this was an audit-only request and no production code changed.

## Commands and Evidence Methods

- AST parse of `athena.py` for imports, functions, route decorators, wrappers, logging templates, and config key references.
- `rg` reference checks across production Python files, with temp/cache exclusions where possible.
- Scoped Python source scans when broad `rg --files` hit permission-denied temp directories.
- `Get-Content`/line-range reads for high-risk or high-impact findings.
- `git ls-files` checks for tracked root scripts and diagnostic JSON files.
- Subagent cross-checks for:
  - full `athena.py` dead-code scan
  - root patch/repro/check/scratch/test file scan
  - `athena_legacy.py`, `athena_runtime.py`, `legacy/`, `tmp/`, root JSON scan
  - duplicate logic/static-analysis scan across `athena.py` and `athena_app/`

## Not Verified

- Dynamic references through `getattr`, `globals`, string endpoint names, importlib, shell scripts, or non-Python/static files.
- External operator workflows outside this checkout.
- Whether users still depend manually on forensic scripts or live probes.
- Access-denied temp directories:
  - `tmp/pytest-microstore-red`
  - `tmp/pytest-microstore-red2`
  - `pytest_tmp_engine_d_adj_3`
  - `pytest_tmp_env/pytest-of-damia`
  - `tests/_basetemp_trailing_atr_timed_exit_fresh`

---

# CLEANUP MANIFEST

## Table 1 - Safe to Delete Immediately

These items had no production filename references and were classified as migration leftovers, stale probes, stale root tests, or generated artifacts. Delete in batches and run relevant tests after each batch.

| File / Location | Type | Lines | Reason |
|---|---:|---:|---|
| `patch_backtest.py` | one-time patch script | 18 | Replaces code from `bt_scalp_dump.py`; source-mutating patch script, not production. |
| `patch_bt.py` | one-time patch script | 46 | Injects backtest telemetry into `backtest_runner.py`; no production reference. |
| `patch_bt_engine_d.py` | one-time patch script | 13 | Adds Engine D telemetry block; no production reference. |
| `patch_config.py` | one-time patch script | 35 | Adds config keys by mutating source; no production reference. |
| `patch_engine.py` | one-time patch script | 13 | Old Engine C patch; no production reference. |
| `patch_engine2.py` | one-time patch script | 163 | Old Engine C reliability patch; no production reference. |
| `patch_engine3.py` | one-time patch script | 228 | Old Engine C reliability patch v3; no production reference. |
| `patch_fix_indent.py` | one-time patch script | 62 | Prior indentation repair script; no production reference. |
| `patch_med01.py` | one-time patch script | 39 | Removes old forex confluence references; no production reference. |
| `patch_med02.py` | one-time patch script | 85 | Threshold naming patch; no production reference. |
| `patch_research.py` | one-time patch script | 66 | Research validation patch draft; no production reference. |
| `patch_research2.py` | one-time patch script | 99 | Broader research/backtest patch; no production reference. |
| `patch_research3.py` | one-time patch script | 104 | Final research validation patch attempt; no production reference. |
| `patch_risk_engine.py` | one-time patch script | 45 | Risk-engine source mutation script; no production reference. |
| `patch_runner.py` | one-time patch script | 91 | Scalp/backtest source mutation helper; no production reference. |
| `patch_scalp_audit.py` | one-time patch script | 66 | Scalp audit source mutation helper; no production reference. |
| `patch_sqlite.py` | one-time patch script | 49 | Recursive SQLite timeout patcher; unsafe to run casually and no production reference. |
| `check_orig.py` | one-time debug script | 9 | Dumps historical `scalp_engine.py` context; no production reference. |
| `check_patch.py` | one-time debug script | 5 | Prints `_funnel["fail_reasons"]` append lines; no production reference. |
| `check_terms.py` | manual source search | 33 | Search helper only; can be replaced by `rg`. |
| `scratch_search_func.py` | one-time scratch script | 5 | Searches `static/index.html` for one function name. |
| `verify_bug8.py` | one-time verification script | 35 | Simulates D1/H4 cutoff alignment logic, not tied to production function. |
| `test_athena_import.py` | stale root test | 25 | Imports monolith directly; violates repo test guidance if kept as pytest. |
| `test_bt.py` | stale root test | 37 | Manual BTC naked backtest smoke via monolith; covered better by `tests/test_backtest_integrity.py`. |
| `test_bug5_logic.py` | stale root test | 64 | Tests copied SL override logic, not production path. |
| `test_forex_bt.py` | stale root test | 37 | Manual USD/JPY naked backtest smoke via monolith. |
| `test_forex_signals.py` | stale root test | 26 | Manual `athena.analyze_pair` probe via monolith import. |
| `test_market_state.py` | stale root test | 27 | Prints market-state split with no assertions; superseded by `tests/test_market_state_offsets.py`. |
| `tmp/bybit_atr_function_probe.py` | temp probe | 78 | Generated probe script; no production reference. |
| `tmp/bybit_atr_probe.py` | temp probe | 85 | Generated probe script; no production reference. |
| `tmp/engine_a_crypto_probe.py` | temp probe | 70 | Generated probe script; no production reference. |
| `tmp/engine_a_crypto_probe_v2.py` | temp probe | 100 | Generated probe script; no production reference. |
| `tmp/__pycache__/` | generated cache | n/a | Python bytecode cache. |
| `tmp/pdfs/fabio_review/` | rendered review artifact | binary/json/text | Untracked generated review images/text; no production reference. |
| `tmp/athena_engine_b_ab_kd_afjk1/audit_ab.sqlite` | temp SQLite artifact | binary | Untracked 134 MB A/B audit DB; no production reference. |
| `prices.json` | root data dump | 831 | No production reference found. |
| `mt5_pairs.json` | root data dump | 0 | Empty file; no production reference found. |
| `bt_results.json` | root data dump | 1 | Untracked/ignored dump; `strategy_lab.py` references `logs/backtest/bt_results.json`, not this root file. |
| `bt_forex_new.json` | root data dump | 1 | Untracked/ignored dump; no production reference found. |

Estimated safe immediate text-line reduction: about 2,700 lines, plus generated binary/DB/PDF/image artifacts.

## Table 2 - Requires Verification Before Delete

These are likely cleanup candidates, but either contain useful assertions, manual/operator workflows, live side effects, tracked research evidence, or inaccessible temp state.

| File / Location | Type | Lines | What to verify |
|---|---:|---:|---|
| `reproduce_bug.py` | repro script | 56 | Port useful factor-scoring/index assertion into `tests/` before deleting. |
| `reproduce_forex_bug.py` | repro script | 40 | Port useful forex scoring assertion into `tests/` before deleting. |
| `verify_bug3.py` | verification script | 79 | Port forex session parity/escape-hatch assertion into `tests/` before deleting. |
| `verify_bug5.py` | verification script | 74 | Port structural SL rejection assertion into `tests/` before deleting. |
| `check_cot.py` | manual DB diagnostic | 31 | Confirm nobody uses it for COT DB inspection. |
| `check_db_structure.py` | manual DB diagnostic | 42 | Confirm nobody uses it for COT/carry schema inspection. |
| `check_engine_d.py` | manual forensic tool | 171 | Preserve any Engine D audit regression logic if useful. |
| `scratch_engine_b_scan.py` | manual runtime diagnostic | 198 | Confirm no operator relies on it for Engine B live/runtime scan diagnosis. |
| `test_engine_a_fixes.py` | root regression-ish test | 120 | Has partial overlap with `tests/test_factor_scoring.py` and `tests/test_scoring_group_routing.py`; hardcoded stale path. Port valid assertions first. |
| `test_engine_b_fixes.py` | root regression-ish test | 124 | Has partial overlap with `tests/test_engine_b_diagnostics.py` and `tests/test_backtest_integrity.py`; hardcoded stale path. Port valid assertions first. |
| `test_engine_c_repro.py` | repro script named as test | 51 | Reproduces Engine C diagnostics string crash but has no assertions. Convert to focused test if still relevant. |
| `test_indicators.py` | root unit tests | 268 | Not an exact duplicate; has unique indicator coverage. Move to `tests/` before deleting root copy. |
| `test_eodhd_symbols.py` | live/manual API probe | 32 | Contains hardcoded API token pattern; keep only as opt-in integration test if still useful. |
| `test_pairs.py` | manual pair inventory probe | 25 | Overlap with pair/symbol tests is partial. Replace with proper assertion if needed. |
| `test_scan.py` | live localhost smoke | 7 | Convert to app-factory route contract test if desired. |
| `test_telegram.py` | live Telegram sender | 121 | Real notification side effects; `tests/test_telegram_notify.py` covers helper behavior, not real sends. Keep only as opt-in manual tool if needed. |
| `test_ws.py` | live EODHD websocket probe | 43 | No exact duplicate; keep only as opt-in integration test if needed. |
| `legacy/ccxt_executor.py` | legacy executor | 412 | No production import found; verify no external rollback workflow depends on old Binance/ccxt executor. |
| `backtest_baseline_2026-05-02.json` | tracked diagnostic dump | 116 | Decide whether it is durable research evidence; otherwise delete or move to diagnostics archive. |
| `forex_intraday_backtest.json` | tracked diagnostic dump | 26,830 | Large tracked dump; decide whether to archive under diagnostics/logs or delete. |
| `diag_h1.json` | tracked diagnostic dump | 1,723 | Parse check failed; starts with command/log text before JSON. Rename/fix if retained. |
| `tmp/docs/fabio_valentini_extracted.txt` | research evidence | 734 | Referenced by `docs/diagnostics/engine_d_fabio_scalp_tool_gap_report.md`; move under docs or update doc before deletion. |
| `tmp/pdfs/fabio_pro_scalper/` | research evidence/rendered PDF pages | binary/text | Some files appear tracked; move durable evidence out of `tmp/` or delete only after confirming docs do not need it. |
| `.mcp.json` | local connector config | 28 | Untracked local config. Do not commit; delete only if local connector setup is not needed. |
| `skills-lock.json` | local tool lock | 11 | Untracked local tool lock. Do not commit; delete only if local setup is not needed. |
| `tmp/pytest-microstore-red` | temp dir | NOT VERIFIED | Access denied. Fix permissions before inspecting or deleting. |
| `tmp/pytest-microstore-red2` | temp dir | NOT VERIFIED | Access denied. Fix permissions before inspecting or deleting. |

## Table 3 - Refactor Candidates, Not Delete

| File / Location | Type | Lines | Recommended action |
|---|---:|---:|---|
| `athena_legacy.py` | startup bridge | 53 | Keep. Required by `app.py:create_app()` and monolith-loading path. |
| `athena_runtime.py` | runtime binding bridge | 26 | Keep. Required by `athena.py`, `execution.py`, `scanner.py`, `backtest_runner.py`, `candle_feeds.py`, and `scalp_engine.py`. |
| `athena.py:222-900+` | literal pair definitions | ~700 | Move pair universe to `pairs.yaml` only after tests prove identical pair counts, symbols, source metadata, and enable flags. |
| `athena.py:7848-8614` | config persistence functions | ~760 | Move regex YAML rewrite helpers to a proper config persistence module. Consider round-trip YAML tooling. |
| `athena.py:12455-12886` | duplicate MT5/Bybit outcome monitor logic | 431 | Extract shared scalp milestone and outcome-update handling. |
| `athena.py:7052-7108`, `9105-9154` | duplicate broker preflight logic | 102 | Consolidate account/positions/symbol-info gates so execution safety behavior cannot drift. |
| `athena.py:2052-2063`, `2112-2123`, `2240-2251`, `2396-2406`, `2455-2466` | duplicate EODHD candle normalization | ~60 | Extract one normalizer. Current copies differ on missing `open`/`close` filtering. |
| `athena.py:1809-1823`, `2258-2274` | duplicate H1 to H4 resampling | ~32 | Use one helper for H1/H4 resample behavior. |
| `athena.py:5393-5402`, `5511-5518`, `7617-7631`; `athena_app/services/scan_backtest_service.py:41-54` | duplicate pair lookup | ~40 | Share a pair lookup helper with consistent error payloads. |
| `athena.py:7633-7647`, `7729-7738`; `athena_app/services/scan_backtest_service.py:29-37` | duplicate backtest option parsing | ~34 | Share validation-mode, purge-gap, and folds parsing. |
| `athena.py:13390-13868` | large route/service function | ~480 | Move performance dashboard computation into `athena_app/services/performance_service.py`. |
| `athena.py:132-157`, `135-137` | hand-rolled caches | small | Consider standardizing cache helpers for Engine B and EODHD volume cache behavior. |

---

# Section 1 - athena.py Dead Code

## Unused Imports

AST-unused imports in `athena.py`:

| Line | Import | Classification |
|---:|---|---|
| 15 | `import bisect` | unused import |
| 39 | `import telegram_notify` | AST-unused, but nearby comment says it is imported for side effects; removal not verified safe |
| 46 | `from athena_app.api.routes_execution import normalize_pip_mode` | unused import |
| 51 | `from athena_app.services.candle_service import recompute_levels_for_style` | unused import |
| 52 | `from athena_app.repositories.audit_repo import insert_manual_error` | unused import |
| 2885-2898 | `apply_correlation_cap`, `_pair_exchange_closed`, `_build_event_risk`, `_classify_signal` from `scoring` | unused imported names |
| 2901 | `compute_consensus`, `apply_vision` from `engine_c` | unused imported names |
| 4630-4634 | `map_engine_b_grade_to_ai_state` | unused imported name |

Duplicate or redundant imports observed:

| Line | Import | Note |
|---:|---|---|
| 7 and 17 | `import sys` | duplicate top-level import |
| 28 and 14878 | `import signal as _signal` | duplicate import; second registration also overrides graceful shutdown handler |
| 14867 | `import os as _os` | local alias import although `os` is top-level imported |

## Unreachable Function Candidates

No non-comment references were found in `athena.py`, `app.py`, `athena_app`, or `tests` for these top-level functions:

| Line range | Function | Evidence |
|---|---|---|
| 1412-1437 | `_fallback_source_for_pair` | No scoped Python references found. |
| 1804-1837 | `_resample_to_h4` | No scoped Python references found; equivalent resample logic is duplicated in `_fetch_eodhd_intraday_bt`. |
| 2994-2997 | `_effective_backtest_style` | Thin wrapper around `resolve_auto_style`; no scoped references found. |
| 8074-8114 | `_apply_scan_settings_updates` | No scoped references found; `api_scan_settings` is currently read-only. |
| 8497-8530 | `_persist_scalp_group_rr_yaml` | No scoped references found. |
| 14767-14773 | nested `_duka_seed` | Only observed reference is a commented-out thread start at line 14775. |

Not flagged as unreachable: Flask route handlers, `@app.before_request` hooks, registered modular route functions, runtime namespace members, and anything with unclear dynamic references.

## Commented-Out Code Blocks

No executable commented-out code block longer than five lines was confirmed.

The only contiguous comment blocks longer than five lines were explanatory prose or planned notes:

| Line range | Classification | Description |
|---|---|---|
| 1908-1919 | explanatory/planned note | CandleBuilder source behavior for US stocks. |
| 14281-14286 | planned feature/note | Live v2 batch poller comment. |

Single-line disabled code:

| Line | Classification | Description |
|---:|---|---|
| 14775 | disabled experiment | Commented-out `_duka_seed` thread start. |

## Duplicate Route Definitions

Duplicate URL paths exist, but methods differ, so these are not exact route/method conflicts:

| Path | Lines | Methods | Functions |
|---|---|---|---|
| `/api/test-mode` | 8714-8733, 8736-8740 | POST, GET | `api_test_mode`, `api_test_mode_status` |
| `/api/auto-trade` | 9243-9246, 9249-9252 | GET, POST | `api_auto_trade_status`, `api_auto_trade_toggle` |

## Duplicate Logic Blocks

| Location | Duplicate of | Description |
|---|---|---|
| `athena.py:5186-5193`, `5202-5209`, `14661-14668`, `14686-14693` | `athena_app/api/routes_broker_status.py:32-39`, `103-110` | Position response normalization repeated; some paths silently convert broker errors to `[]`. |
| `athena.py:7052-7075`, `7082-7108`, `9105-9128`, `9131-9154` | same file | Broker account/position/symbol preflight blocks repeated for webhook and scalp execution. |
| `athena.py:2052-2063`, `2112-2123`, `2240-2251`, `2396-2406`, `2455-2466` | same file | EODHD candle normalization repeated. |
| `athena.py:1809-1823` | `athena.py:2258-2274` | H1 to H4 pandas resample logic duplicated. |
| `athena.py:5393-5402`, `5511-5518`, `7617-7631` | `athena_app/services/scan_backtest_service.py:41-54` | Pair lookup over `ALL_PAIRS` repeated. |
| `athena.py:7633-7647`, `7729-7738` | `athena_app/services/scan_backtest_service.py:29-37` | Backtest option parsing repeated. |
| `athena.py:12455-12629` | `athena.py:12637-12885` | MT5/Bybit outcome monitor and Engine D milestone handling share duplicated structure. |

## Legacy Shim Functions

| Line range | Function | Description |
|---|---|---|
| 176-183 | `_merge_forex_forming_ws` | Thin wrapper around `_merge_forex_forming_ws_core`. |
| 2816-2831 | `fetch_candles` | Delegates to `_fetch_candles_routed`. This may be public compatibility surface, not safe to delete without broader reference checks. |
| 2994-2997 | `_effective_backtest_style` | Thin wrapper around `resolve_auto_style`; no references found. |

## Config Keys Referenced in `athena.py` but Absent from `config.yaml`

Direct top-level `CONFIG.get(...)` / `CONFIG[...]` references absent from current `config.yaml`:

| Key | Lines | Notes |
|---|---|---|
| `LEVEL_ATR_PRIORITY` | 1479 | May be legacy fallback/default surface. |
| `POLYGON_KEY` | 2539 | Likely env-style key; not a YAML bug by itself. |
| `FINNHUB_KEY` | 3553, 3609-3612 | Likely env-style key. |
| `CRYPTOPANIC_KEY` | 3580-3583 | Likely env-style key. |
| `EODHD_SENTIMENT_BATCH_SIZE` | 3646 | Missing from YAML; code has default. |
| `EODHD_SENTIMENT_CONNECT_TIMEOUT_SEC` | 3649 | Missing from YAML; code has default. |
| `EODHD_SENTIMENT_READ_TIMEOUT_SEC` | 3652 | Missing from YAML; code has default. |
| `EXCHANGE_SOURCE` | 5525 | Missing from YAML; code defaults to `binance`. |
| `ENGINE_B_CRYPTO_PROFILE_ENABLED` | 6602 | Missing from YAML; code defaults false. |
| `ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS` | 6604 | Missing from YAML; code default path. |
| `AUTO_EXECUTE_MIN_SCORE` | 7315, 7340, 7382 | Missing from YAML; UI/runtime config surface. |
| `AUTO_EXECUTE_MIN_GRADE` | 7316, 7349, 7383 | Missing from YAML; UI/runtime config surface. |
| `BT_MIN` | 7957, 7959, 8144, 8206, 8220 | YAML comments say Stage 4.2 deleted `BT_MIN`; route still references it. |
| `BACKTEST_USE_BT_MIN_THRESHOLDS` | 8139-8231 | YAML comments say Stage 4.2 deleted this; route still references it. |
| `VISION_MAX_TOKENS` | 10059 | Missing from YAML; code has model/default fallback. |
| `MAX_SL_PCT` | 11679 | Missing from YAML; code default fallback. |
| `ENGINE_A_DIVERGENCE_MONITOR_ENABLED` | 11792 | Missing from YAML; code defaults true. |

Not verified as bugs: some keys may intentionally come from env vars, `config.py` defaults, or runtime UI mutation rather than `config.yaml`.

## Confirmed Bug Found During Cleanup Audit

| Line range | Severity | Finding |
|---|---|---|
| 14793-14833 and 14878-14884 | Critical operational bug | `_graceful_shutdown()` registers clean Bybit/MT5/WS cleanup, then a second `_shutdown_handler()` immediately overrides SIGINT/SIGTERM and calls `os._exit(0)`. This makes graceful cleanup dead on script startup. |

This was not patched because the user requested audit-only.

---

# Section 2 - `patch_*.py` Files

| File | Purpose | Safe to delete? | Action |
|---|---|---|---|
| `patch_backtest.py` | Replace `backtest_pair_scalp` from `bt_scalp_dump.py`. | Yes | Delete. |
| `patch_bt.py` | Inject strategy-lab telemetry into backtest trade dicts. | Yes | Delete. |
| `patch_bt_engine_d.py` | Add Engine D telemetry block to backtest runner. | Yes | Delete. |
| `patch_config.py` | Add sentiment/event API fail-closed config keys. | Yes | Delete. |
| `patch_engine.py` | Add Engine A confidence field into `engine_c.py` normalization. | Yes | Delete. |
| `patch_engine2.py` | Add Engine C reliability/decision-state fields. | Yes | Delete. |
| `patch_engine3.py` | Extend Engine C reliability handling for A/B-only paths. | Yes | Delete. |
| `patch_fix_indent.py` | Repair prior slippage indentation patch and compile-check. | Yes | Delete. |
| `patch_med01.py` | Remove old forex confluence config/docs references. | Yes | Delete. |
| `patch_med02.py` | Patch execution/scanner/UI/backtest/docs threshold naming. | Yes | Delete. |
| `patch_research.py` | Incomplete research-validation patch draft. | Yes | Delete. |
| `patch_research2.py` | Broader backtest validation-mode patch. | Yes | Delete. |
| `patch_research3.py` | Finalized validation-mode/OOS enrichment patch attempt. | Yes | Delete. |
| `patch_risk_engine.py` | Insert TP-side validation into `risk_engine.py`. | Yes | Delete. |
| `patch_runner.py` | Add Engine D fee guard and dump scalp backtest function. | Yes | Delete. |
| `patch_scalp_audit.py` | Add `_funnel` gate logging before `continue` in scalp scan. | Yes | Delete. |
| `patch_sqlite.py` | Recursively add `timeout=1.0` to sqlite connects. | Yes | Delete. |

All are one-time source-mutating scripts. None should run repeatedly. None serves production behavior directly.

---

# Section 3 - `reproduce_*.py`, `verify_*.py`, `check_*.py`, `scratch_*.py`

| File | Purpose | Safe to delete? | Preserve logic in tests? |
|---|---|---|---|
| `check_cot.py` | Inspect COT DB tables/recent EUR dates. | Unknown | No; manual DB diagnostic. |
| `check_db_structure.py` | Inspect COT/carry DB schema/counts. | Unknown | No; manual DB diagnostic. |
| `check_engine_d.py` | Snapshot `audit.db`, analyze Engine D/scalp audit rows. | Unknown | Partly; preserve any reusable audit-regression logic if valuable. |
| `check_orig.py` | Dump old `scalp_engine.py` git context to `_orig_ctx.txt`. | Yes | No. |
| `check_patch.py` | Print `_funnel["fail_reasons"]` append lines. | Yes | No. |
| `check_terms.py` | Search selected RR/SL/TP terms in source files. | Yes | No. |
| `reproduce_bug.py` | Reproduce factor-scoring index issue with synthetic UK100 data. | No, port first | Yes. |
| `reproduce_forex_bug.py` | Reproduce forex scoring path with synthetic EUR/USD data. | No, port first | Yes. |
| `scratch_engine_b_scan.py` | Manual Engine B live/runtime scan diagnostic. | Unknown | No; maybe convert to explicit tool if still needed. |
| `scratch_search_func.py` | Locate `formatConfluenceText` in `static/index.html`. | Yes | No. |
| `verify_bug3.py` | Verify forex session parity/escape hatch. | No, port first | Yes. |
| `verify_bug5.py` | Verify structural SL rejection through `athena.analyze_pair` mocks. | No, port first | Yes. |
| `verify_bug8.py` | Simulate D1/H4 cutoff alignment fix. | Yes | Only if tied to a real production function. |

---

# Section 4 - Root `test_*.py` Files Not Under `tests/`

| File | Covered by `tests/`? | Stale? | Action |
|---|---|---|---|
| `test_athena_import.py` | No exact duplicate | Yes | Delete; direct monolith import violates test guidance. |
| `test_bt.py` | Covered better by `tests/test_backtest_integrity.py` | Yes | Delete. |
| `test_bug5_logic.py` | No exact duplicate | Yes | Delete; tests copied logic instead of production path. |
| `test_engine_a_fixes.py` | Partial overlap with `tests/test_factor_scoring.py`, `tests/test_scoring_group_routing.py` | Yes/unknown | Port any valid assertions, then delete. |
| `test_engine_b_fixes.py` | Partial overlap with `tests/test_engine_b_diagnostics.py`, `tests/test_backtest_integrity.py` | Yes/unknown | Port any valid assertions, then delete. |
| `test_engine_c_repro.py` | No exact duplicate | Unknown | Convert repro into asserted test if bug is still relevant; otherwise delete. |
| `test_eodhd_symbols.py` | No exact duplicate | Manual/live | Do not keep as normal pytest. Convert to opt-in integration or delete. |
| `test_forex_bt.py` | Covered better by backtest tests | Yes | Delete. |
| `test_forex_signals.py` | No exact duplicate | Yes | Delete or convert to app/module-level test. |
| `test_indicators.py` | Partial duplicate only | No | Move to `tests/`; has unique indicator coverage. |
| `test_market_state.py` | Superseded by `tests/test_market_state_offsets.py` | Yes | Delete. |
| `test_pairs.py` | Partial overlap only | Unknown | Convert to pair-universe assertion if needed. |
| `test_scan.py` | No exact duplicate | Manual/live | Convert to app-factory route test or delete. |
| `test_telegram.py` | `tests/test_telegram_notify.py` covers helpers, not real sends | Manual/live side effects | Keep only as opt-in manual tool or delete. |
| `test_ws.py` | `tests/test_ws_ssl.py` covers SSL only | Manual/live | Keep only as opt-in integration test or delete. |

---

# Section 5 - `athena_legacy.py`

## What `athena_legacy.load()` Does

`app.py:create_app()` calls:

1. `_load_legacy()`
2. `_athena.ensure_runtime_services_started()`
3. `app = _athena.app`
4. optional `/healthz` registration

`athena_legacy.load()`:

- Reuses `__main__` when `python athena.py` is already running, preventing duplicate monolith execution.
- Otherwise loads `athena.py` from disk as module name `athena_monolith`.
- Executes `athena.py` top-level statements through `spec.loader.exec_module(module)`.
- Caches the loaded module in `_MONOLITH`.

## Verified Side Effects of Loading `athena.py`

- Loads `.env`.
- Configures logging, including `logs/athena.log`.
- Reads `toggle_state.json`.
- Initializes or migrates `audit.db` and related stores.
- Creates the Flask `app`.
- Registers legacy monolith routes.
- Registers modular routes through `athena_app`.
- Calls `athena_runtime.set_runtime(...)` with a large runtime namespace.

`ensure_runtime_services_started()` then starts runtime services once:

- EODHD websocket/candle seed flows.
- Volume warmer.
- MT5 tick poller.
- Binance candle websocket.
- Optional microstructure feeds.

The `if __name__ == "__main__"` block is not run through `app.py` import startup. Script-only startup extras there include startup reconciliation, COT/carry seed, browser open, outcome monitor, auto-trader thread, backup, Telegram bot startup, signal handlers, and `app.run()`.

## Required for Production Startup

Required now:

- `athena_legacy.py`
- `athena_runtime.py`
- `athena.py` top-level module state
- `ensure_runtime_services_started()`
- Runtime namespace registration at `athena.py:13939+`

Dead migration leftovers in `athena_legacy.py`: none confirmed. It is intentionally a loader shim.

Migration recommendation: yes, it is safe to progressively migrate remaining monolith startup logic into `athena_app/` modules, but not by deleting `athena_legacy.py` first. The migration should be incremental and tested around app factory startup, route registration, runtime namespace availability, and script startup parity.

---

# Section 6 - Duplicate Lines in `athena.py`

## 5+ Line Identical or Near-Identical Blocks

High-signal confirmed clones:

| Lines | Duplicate lines | Description |
|---|---|---|
| 1809-1823 | 2258-2274 | H1 to H4 pandas resample block duplicated. |
| 2052-2063 | 2112-2123, 2240-2251, 2396-2406, 2455-2466 | EODHD candle dict normalization repeated. |
| 5186-5193 | 5202-5209, 14661-14668, 14686-14693 | Broker positions response normalization repeated. |
| 7052-7075 | 7082-7108, 9105-9128, 9131-9154 | Broker preflight account/positions/symbol-info checks repeated. |
| 5393-5402 | 5511-5518, 7617-7631 | Pair lookup by `symbol` or `display` repeated. |
| 7633-7647 | 7729-7738 | Backtest options parsing repeated. |
| 12455-12629 | 12637-12885 | MT5/Bybit outcome monitor shape duplicated. |

High-noise duplicates such as repeated literal pair dictionaries were not exhaustively listed as individual clone findings; they are covered by the pair-list refactor candidate.

## Config Key Loaded Multiple Times into Same Local Variable

| Function | Variable | Lines | Description |
|---|---|---|---|
| `api_bt_min` | `use_bt` | 8138-8141, 8199-8202, 8222-8225 | Same `BACKTEST_USE_BT_MIN_THRESHOLDS or RESEARCH_MODE` expression recomputed three times. |

## Identical Logging Text

| Location | Text |
|---|---|
| `athena.py:13063`, `athena.py:13123` | `[DECAY-AI] audit log failed for %s: %s` |
| `athena_app/api/routes_lottery.py:708`, `athena_app/api/routes_lottery.py:750` | `[LOTTERY-AI] audit log failed: %s` |

---

# Section 7 - `tmp/`, `legacy/`, and Root JSON Dumps

## `legacy/`

| File | Referenced from production code? | Safe to delete? | Notes |
|---|---|---|---|
| `legacy/ccxt_executor.py` | No production import found | Unknown | `bybit_executor.py` says it replaces `ccxt_executor.py`; archive/delete only after confirming no external rollback workflow uses it. |

## `tmp/`

| File / Directory | Referenced from production code? | Safe to delete? | Notes |
|---|---|---|---|
| `tmp/bybit_atr_function_probe.py` | No | Yes | Probe script. |
| `tmp/bybit_atr_probe.py` | No | Yes | Probe script. |
| `tmp/engine_a_crypto_probe.py` | No | Yes | Probe script. |
| `tmp/engine_a_crypto_probe_v2.py` | No | Yes | Probe script. |
| `tmp/athena_engine_b_ab_kd_afjk1/audit_ab.sqlite` | No | Yes for production | Large temp DB, delete if audit evidence no longer needed. |
| `tmp/docs/fabio_valentini_extracted.txt` | Referenced by diagnostics doc | No, unless doc updated | Move to diagnostics if retained. |
| `tmp/pdfs/fabio_pro_scalper/` | No production reference found | Unknown | Some files may be tracked research evidence. |
| `tmp/pdfs/fabio_review/` | No | Yes for production | Generated render artifacts. |
| `tmp/__pycache__/` | No | Yes | Generated cache. |
| `tmp/pytest-microstore-red` | NOT VERIFIED | Unknown | Access denied. |
| `tmp/pytest-microstore-red2` | NOT VERIFIED | Unknown | Access denied. |

## Root JSON Dumps

| File | Referenced from production code? | Safe to delete? | Action |
|---|---|---|---|
| `toggle_state.json` | Yes, read by `athena.py` | No | Keep unless replacing persisted pair toggles. |
| `prices.json` | No | Yes for production | Delete/archive stale dump. |
| `mt5_pairs.json` | No | Yes | Delete empty placeholder. |
| `bt_results.json` | No root production reference | Yes | Delete/archive local dump. |
| `bt_forex_new.json` | No | Yes | Delete/archive local dump. |
| `backtest_baseline_2026-05-02.json` | No | Unknown | Move to diagnostics or delete via tracked cleanup. |
| `forex_intraday_backtest.json` | No | Unknown | Move to logs/docs or delete via tracked cleanup. |
| `diag_h1.json` | No | Unknown | Corrupt mixed log/JSON artifact; fix, archive, or delete. |
| `.mcp.json` | No | Unknown | Local connector config; do not commit. |
| `skills-lock.json` | No | Unknown | Local tool lock; do not commit. |

---

# Estimated Reduction

| Category | Estimated reduction |
|---|---:|
| Safe immediate root scripts/tests | ~1,525 lines |
| Safe immediate temp probe scripts | ~333 lines |
| Safe immediate root JSON dumps | ~833 text lines plus large dump bytes |
| Safe immediate generated binary/DB artifacts | 134 MB+ plus rendered images |
| Requires-verification scripts/tests | ~1,482 lines |
| Requires-verification tracked JSON dumps | ~28,669 lines |
| `athena.py` low-risk cleanup | ~100-200 lines |

Conservative immediate text reduction: about 2,700 lines plus generated artifacts.

Potential total reduction after verification/porting: 30,000+ text lines, mostly tracked diagnostic JSON dumps, plus refactor-driven monolith reduction.

---

# Recommended Cleanup Order

1. Delete safe one-time `patch_*.py` scripts.
2. Delete safe one-time `check_*/scratch_*/verify_bug8.py` scripts and stale root tests listed in Table 1.
3. Delete safe temp probe/generated artifacts.
4. Run focused compile and relevant tests.
5. Port useful root test/repro assertions into `tests/`.
6. Decide archive/delete policy for tracked JSON diagnostics.
7. Fix the duplicate shutdown handler bug in `athena.py`.
8. Refactor duplicated broker preflight, candle normalization, pair lookup, and config persistence in separate PRs.
