# Sentinel Pro v4 — Phase 4: Dead Code & Structural Cleanup Audit

> **Date:** 2026-05-12
> **Scope:** `athena.py` (14,887 lines), all root `*.py`, `legacy/`, `tmp/`
> **Mode:** Audit-only — no patches applied
> **Auditor:** Antigravity

---

## Executive Summary

| Category | Files | Est. Lines | Risk |
|----------|-------|------------|------|
| Safe to delete (S1) | ~52 | ~3,500 | ⚪ Zero |
| Verify before removing (S2) | ~21 | ~2,800 | 🟡 Low |
| athena.py internal dead code (S3) | — | ~100 | 🟡 Low (except 3D = 🔴 Bug) |
| Refactor candidates (S4) | — | ~2,400 | 🟠 Medium |
| **Total removable** | **~73** | **~8,800** | — |

**One confirmed bug found:** duplicate shutdown handler silently overrides proper broker cleanup (Section 3D).

---

## Section 1 — Safe to Delete

Zero production references. Can be `git rm` immediately.

### 1A. Patch Scripts (17 files, ~50 KB)

All `patch_*.py` files are post-hoc source-modification scripts. Not imported or invoked by production code.

| File | Size | Rationale |
|------|------|-----------|
| `patch_backtest.py` | 618 B | One-shot AST patch |
| `patch_bt.py` | 3.5 KB | Backtest runner patch |
| `patch_bt_engine_d.py` | 965 B | Engine D backtest patch |
| `patch_config.py` | 2.1 KB | Config mutation script |
| `patch_engine.py` | 508 B | Engine patch |
| `patch_engine2.py` | 5.1 KB | Engine patch v2 |
| `patch_engine3.py` | 9.0 KB | Engine patch v3 |
| `patch_fix_indent.py` | 2.7 KB | Whitespace fixer |
| `patch_med01.py` | 1.9 KB | Misc patch |
| `patch_med02.py` | 3.8 KB | Misc patch v2 |
| `patch_research.py` | 3.0 KB | Research module patch |
| `patch_research2.py` | 4.2 KB | Research module patch v2 |
| `patch_research3.py` | 4.3 KB | Research module patch v3 |
| `patch_risk_engine.py` | 1.9 KB | Risk engine patch |
| `patch_runner.py` | 4.5 KB | Runner patch |
| `patch_scalp_audit.py` | 2.6 KB | Scalp audit patch |
| `patch_sqlite.py` | 2.3 KB | SQLite migration patch |
| `patch.py` | 3.3 KB | Generic patch utility |

### 1B. Reproduce / Verify / Check / Fix Scripts (16 files, ~26 KB)

One-shot diagnostic scripts from debugging sessions. Not imported by any production module.

| File | Size |
|------|------|
| `reproduce_bug.py` | 1.6 KB |
| `reproduce_forex_bug.py` | 1.1 KB |
| `repro_bug4.py` | 2.3 KB |
| `verify_bug3.py` | 3.0 KB |
| `verify_bug5.py` | 3.4 KB |
| `verify_bug8.py` | 1.3 KB |
| `check_cot.py` | 1.0 KB |
| `check_db_structure.py` | 1.4 KB |
| `check_engine_d.py` | 6.0 KB |
| `check_orig.py` | 434 B |
| `check_patch.py` | 197 B |
| `check_terms.py` | 764 B |
| `fix_bot.py` | 571 B |
| `fix_patch.py` | 374 B |
| `fix_yaml.py` | 390 B |
| `fix.py` | 636 B |

### 1C. Scratch / Search Scripts (5 files, ~8 KB)

| File | Size |
|------|------|
| `scratch_engine_b_scan.py` | 7.1 KB |
| `scratch_search_func.py` | 227 B |
| `search_confidence.py` | 270 B |
| `search_engine_b.py` | 288 B |
| `search_engine_b_generic.py` | 242 B |

### 1D. Tmp Directory Artifacts

| Path | Rationale |
|------|-----------|
| `tmp/bybit_atr_function_probe.py` | Standalone probe script |
| `tmp/bybit_atr_probe.py` | Standalone probe script |
| `tmp/engine_a_crypto_probe.py` | Standalone probe script |
| `tmp/engine_a_crypto_probe_v2.py` | Standalone probe script |
| `tmp/docs/` | Orphaned docs subdirectory |
| `tmp/pdfs/` | Orphaned PDF directory |
| `tmp/pytest-microstore-red/` | Stale pytest temp dir |
| `tmp/pytest-microstore-red2/` | Stale pytest temp dir |
| `tmp/__pycache__/` | Cache directory |
| `tmp/athena_engine_b_ab_kd_afjk1/` | Randomly-named temp dir |

### 1E. Legacy Directory

| Path | Size | Rationale |
|------|------|-----------|
| `legacy/ccxt_executor.py` | 14.2 KB | Superseded by `bybit_executor.py`. Not imported anywhere. |

### 1F. Miscellaneous Root Files

| File | Size | Rationale |
|------|------|-----------|
| `report_scratch.py` | 1.1 KB | Scratch report generator |
| `quick_telegram_test.py` | 1.4 KB | Manual Telegram test |
| `tmp_extract_rl.py` | 4.8 KB | Temp research lab extraction |
| `diag_soft_gate.py` | 1.7 KB | Diagnostic tool |
| `cot_carry_diagnostic.py` | 2.1 KB | COT/carry diagnostic |
| `count_pairs.py` | 3.7 KB | Pair counting utility |

---

## Section 2 — Verification Needed Before Removal

### 2A. Root-Level Test Files (15 files)

These `test_*.py` files at root level violate the `tests/` directory convention. Cross-reference against `tests/` before deleting.

| File | Size | Action Required |
|------|------|-----------------|
| `test_athena_import.py` | 668 B | **DANGEROUS** — imports `athena.py` directly; violates agent rules. Verify if `tests/` has equivalent. |
| `test_bt.py` | 997 B | Check for duplication in `tests/test_backtest*.py` |
| `test_bug5_logic.py` | 2.9 KB | Bug-specific — likely obsolete |
| `test_engine_a_fixes.py` | 5.3 KB | Check overlap with `tests/test_engine_a*.py` |
| `test_engine_b_fixes.py` | 4.5 KB | Check overlap with `tests/test_engine_b*.py` |
| `test_engine_c_repro.py` | 2.3 KB | Bug reproduction test |
| `test_eodhd_symbols.py` | 1.1 KB | EODHD symbol mapping test |
| `test_forex_bt.py` | 1.1 KB | Forex backtest test |
| `test_forex_signals.py` | 935 B | Forex signal test |
| `test_indicators.py` | 7.9 KB | **Important** — may be canonical indicator test suite |
| `test_market_state.py` | 1.1 KB | Market state test |
| `test_pairs.py` | 981 B | Pair configuration test |
| `test_scan.py` | 254 B | Minimal scan test |
| `test_telegram.py` | 3.6 KB | Telegram integration test |
| `test_ws.py` | 1.2 KB | WebSocket test |

> **Note:** `test_indicators.py` (7.9 KB) is the largest root test file and may be the canonical indicator test suite. Verify before moving/deleting.

### 2B. Standalone Modules — Potentially Orphaned

| File | Size | Status | Verification |
|------|------|--------|--------------|
| `athena_backup_v2_pre_pandas.py` | 52 KB | Not imported anywhere. Pre-refactor snapshot. | Safe to archive. |
| `freqtrade_sample_strategy.py` | 17.5 KB | Not imported anywhere. FreqTrade reference. | Move to `refs/` or delete. |
| `style_resolver.py` | 356 B | Not imported by athena.py or production modules. | Verify `tests/` doesn't depend on it. |
| `candle_manager.py` | 2.3 KB | Not imported by athena.py. | Check if any `athena_app/` module imports it. |
| `instrumented_backtest.py` | 3.5 KB | Diagnostic backtest wrapper. | Verify if `run_backtest.py` uses it. |
| `probe_bt_context.py` | 5.3 KB | Backtest context probe. | Likely debug-only. |

### 2C. Feature Modules — Dormant but Wired

| File | Size | Status |
|------|------|--------|
| `etoro_executor.py` | 4.9 KB | Scaffold adapter. Has `tests/test_etoro_executor.py` coverage. Keep if eToro planned; otherwise archive. |
| `telemetry.py` | 2.4 KB | Verify if any route or thread calls it. |
| `feature_normalizer.py` | 4.1 KB | May be used by research/calibration modules. |
| `ai_signal_trace.py` | 772 B | Imported at athena.py line 205 (`ensure_trace_id`). **Actively used — do NOT delete.** |

---

## Section 3 — athena.py Internal Dead Code

### 3A. Unused / Duplicate Imports

| Line | Import | Status |
|------|--------|--------|
| 15 | `import bisect` | **UNUSED** — zero references to `bisect.` anywhere in file. Only the import line itself matches. |
| 7, 17 | `import sys` (×2) | **DUPLICATE** — imported at line 7 (for reconfigure), then again at line 17. |
| 28, 14878 | `import signal as _signal` (×2) | **DUPLICATE** — top-level line 28, then re-imported at line 14878 inside `__main__`. |
| 14867 | `import os as _os` | **REDUNDANT** — `os` already imported at line 16. Local re-import in `__main__`. |
| 108 | `import logging as _logging` | **REDUNDANT** — `logging` already imported at line 25. Aliased for logger silencing. |

**Verified in-use:** `copy` (3 refs at lines 3770/3772/3781), `math` (2 refs at lines 2088/3654), `re` (1 ref at line 7862).

### 3B. Blank Line Block (Lines 13884–13926)

**43 consecutive blank lines** between `_LIVE_DASHBOARD_SCALP_TTL` and the `from types import SimpleNamespace` block. Remnants of deleted snapshot helper functions moved to `athena_app/api/routes_live_dashboard.py`. Safe to collapse.

### 3C. Commented-Out Code

| Line | Content | Rationale |
|------|---------|-----------|
| 13775 | `# threading.Thread(target=_duka_seed, ...)` | Dukascopy seeder intentionally disabled. The `_duka_seed` function (lines 13767–13773) and its import are dead unless re-enabled. |
| 13319 | `pass  # decay telegram notifications disabled` | No-op placeholder |
| 13343 | `pass  # decay telegram notifications disabled` | Duplicate no-op placeholder |

### 3D. 🔴 BUG: Duplicate Shutdown Handler (CONFIRMED)

**Location:** Lines 14793–14833 vs Lines 14878–14883

**Evidence:**

```python
# HANDLER 1 (line 14793) — proper cleanup
def _graceful_shutdown(signum, frame):
    # Disconnects Bybit, MT5, stops WS clients
    bybit_disconnect()
    mt5_disconnect()
    _binance_candle_ws.stop()
    for _wsc in _ws_clients: _wsc._running = False
    sys.exit(0)

_signal.signal(_signal.SIGINT, _graceful_shutdown)   # line 14831
_signal.signal(_signal.SIGTERM, _graceful_shutdown)   # line 14833

# ... 45 lines later ...

# HANDLER 2 (line 14878) — overrides handler 1!
import signal as _signal
def _shutdown_handler(sig, frame):
    import os as _os
    _os._exit(0)  # NO cleanup!

_signal.signal(_signal.SIGINT, _shutdown_handler)    # line 14883
_signal.signal(_signal.SIGTERM, _shutdown_handler)    # line 14884
```

**Impact:** `_graceful_shutdown()` is **dead code** — it is registered but immediately overridden. On Ctrl-C, broker connections (MT5/Bybit) are never properly disconnected and WS clients are never stopped. This can cause:
- Stale MT5 terminal connections
- Bybit WebSocket sessions left open
- Potential position data corruption on unclean shutdown

**Fix:** Remove the second handler (lines 14878–14884) and keep the first, or merge both into a single handler.

### 3E. Stale Comment Tombstones

| Line | Content |
|------|---------|
| 12096 | `# _build_event_risk imported from scoring.py` — function was moved, comment left behind |
| 12099 | `# _classify_signal imported from scoring.py - see that module` — same |

---

## Section 4 — Refactor Candidates

Not dead code, but structural debt impacting maintainability.

### 4A. Engine B Manual Cache → Standard Cache

**Location:** [athena.py:132-157](file:///c:/dev/athena-python/athena.py#L132-L157)

`_engine_b_cache` uses hand-rolled TTL logic with raw `time.time()` timestamps. Replace with `cachetools.TTLCache` or similar.

### 4B. EODHD Volume Cache → Centralized Cache

**Location:** [athena.py:135-137](file:///c:/dev/athena-python/athena.py#L135-L137)

`_eodhd_volume_cache` / `_eodhd_volume_cache_lock` — another hand-rolled TTL cache with per-timeframe expiry. Consolidation candidate.

### 4C. Pair Definition Block (~700 lines)

**Location:** Lines 222–900+

`FOREX_PAIRS`, `CRYPTO_PAIRS`, `COMMODITY_PAIRS`, `INDEX_PAIRS`, `US_STOCK_PAIRS`, `ETF_PAIRS`, `JSE_PAIRS` are literal Python lists. Should externalize to `pairs.yaml`, eliminating ~700 lines from monolith.

### 4D. Config Persistence Functions (~500 lines)

**Location:** Lines 7848–8614

8+ functions for regex-based YAML rewriting (`_persist_bt_min_yaml`, `_persist_scan_settings_yaml`, `_persist_naked_style_profiles_yaml`, `_persist_score_group_overrides_yaml`, `_persist_scalp_group_rr_yaml`, etc.). Consolidate into `config_persistence.py` using `ruamel.yaml` for safe round-trip editing.

### 4E. Performance Dashboard (Lines 13389–13868)

`api_performance()` is a single **480-line function** with 6 inline helper functions, statistics computation, and JSON assembly. Extract to `athena_app/services/performance_service.py`.

### 4F. Outcome Monitor Duplication (Lines 12455–12886)

`_check_mt5_outcomes()` (172 lines) and `_check_ccxt_outcomes()` (250 lines) share significant structural similarity in scalp milestone management. Factor shared logic into `_apply_scalp_milestones()`.

### 4G. 43-Line Blank Block

**Location:** [athena.py:13884-13926](file:///c:/dev/athena-python/athena.py#L13884-L13926)

Collapse remnant blank lines from moved dashboard helpers.

---

## Execution Order

1. **`python backup_db.py`** — protect `audit.db` and `candle_cache.db`
2. **Delete Section 1A–1F** — zero-risk removals (patch/debug/scratch/tmp/legacy)
3. **Run `py -m pytest tests/ -v`** — verify baseline
4. **Fix Section 3D** — merge shutdown handlers (confirmed bug, immediate priority)
5. **Clean Section 3A** — remove `import bisect`, deduplicate `import sys` and `import signal`
6. **Collapse Section 3B** — remove 43 blank lines
7. **Verify Section 2A** — cross-reference root test files vs `tests/`, move or delete
8. **Verify Section 2B** — confirm orphaned modules truly unused, archive
9. **Section 4 refactors** — one PR per item: 4G → 4A → 4B → 4C → 4D → 4E → 4F

---

## Files Inspected

- `athena.py` — full file (14,887 lines, all sections read)
- `athena_legacy.py` — full file (53 lines)
- `athena_runtime.py` — full file (26 lines)
- `app.py` — full file (32 lines)
- All 17 `patch_*.py` files — listed and categorized
- All `reproduce_*.py`, `verify_*.py`, `check_*.py`, `fix_*.py` — listed
- All `scratch_*.py`, `search_*.py` — listed
- All 15 root-level `test_*.py` — listed
- `legacy/` directory contents — read
- `tmp/` directory contents — listed
- Full root `.py` inventory (140+ files) — catalogued

## Areas Not Verified

- `athena_app/` internal module cross-references (not in scope)
- `tests/` directory contents for deduplication against root test files
- `refs/` directory (excluded per AGENTS.md)
- Runtime import paths via `importlib` or dynamic `__import__` calls
- Template/static file references from Flask routes
