# Sentinel Pro v4 — Audit Master Priority List

> **Generated:** 2026-05-12
> **Sources:** Phase 1 (Engine A), Phase 2 (Engine B), Phase 3 (Engine D), Phase 4 (Cleanup)
> **Execution order:** CRITICAL → HIGH → Threshold Review → Dead Code → MEDIUM → LOW

---

## 🔴 Tier 1 — CRITICAL Bugs (Fix Immediately)

### C-1 · Duplicate Shutdown Handler Overrides Broker Cleanup
| Field | Detail |
|-------|--------|
| **Phase** | 4 (Cleanup) — Section 3D |
| **File** | `athena.py` L14793–14833 (handler 1) vs L14878–14884 (handler 2) |
| **Bug ID** | Phase 4 — 3D |
| **Description** | `_graceful_shutdown()` registers proper broker cleanup (MT5/Bybit disconnect, WS stop). 45 lines later, `_shutdown_handler()` re-registers SIGINT/SIGTERM with `os._exit(0)` — **no cleanup**. The second handler silently overrides the first. |
| **Impact** | On Ctrl-C: MT5 connections leak, Bybit WS sessions orphaned, potential position data corruption on unclean shutdown. |
| **Fix** | Delete lines 14878–14884 (handler 2). Keep `_graceful_shutdown` as sole handler. |
| **Test** | Verify `signal.getsignal(signal.SIGINT)` resolves to `_graceful_shutdown` after boot. |

---

## 🔴 Tier 2 — HIGH Bugs (Fix Second)

### H-1 · Volatile Threshold 2.0 Nearly Unreachable
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 3, Bug #3 |
| **File** | `scoring.py` (threshold resolution) + `factor_scoring.py` L1770–1881 (multiplicative chain) |
| **Bug ID** | Phase 1 — Bug #3 |
| **Description** | Realistic strong crypto signals score ~1.6. Reaching 2.0 requires near-perfect alignment across ALL multiplicative gates (adx>0.95, di=1.0, dir_ramp=1.0, conviction>0.90). RANGING regime multiplier (1.1) raises effective threshold to 2.2 — practically unreachable. |
| **Impact** | Crypto signals rarely qualify for auto-trade. Engine A crypto throughput near zero. |
| **Fix** | Lower volatile threshold to ~1.7, or audit live signal P95 distribution first. **⚠️ Threshold change — review with user.** |

### H-2 · DI Alignment Zeroes Score Silently
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 3, Bug #2 |
| **File** | `factor_scoring.py` L1692–1714 |
| **Bug ID** | Phase 1 — Bug #2 |
| **Description** | When `di_align_mult = 0.0` (trend=LONG but -DI > +DI by >5 pts), `base_score` is zeroed regardless of all other factors. No `abort_reason` set, no warning emitted. |
| **Impact** | Zero-score "strong" signals indistinguishable from data-missing. Debugging requires manually checking DI alignment. |
| **Fix** | When `di_align_mult == 0.0`: (1) set an `abort_reason = "DI_ALIGNMENT_CONFLICT"` in feed_status, (2) emit `log.info` with pair + DI values. |
| **Test** | `test_di_alignment_zeroes_score` — DI misalignment → score=0 AND diagnostic flag emitted. |

### H-3 · Zone Registry — No WAL Mode, No Timeout on SQLite
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 3.1, BUG-001 |
| **File** | `zone_registry.py` L211, L233, L281 |
| **Bug ID** | Phase 2 — BUG-001 |
| **Description** | `sqlite3.connect(self._db_path)` with no `timeout` and no `PRAGMA journal_mode=WAL`. Violates project SQLite safety contract (AGENTS.md). |
| **Impact** | `database is locked` errors under concurrent scan threads when `ENGINE_B_ZONE_PERSISTENCE: true`. |
| **Fix** | Add `timeout=15.0` and `PRAGMA journal_mode=WAL` to all `sqlite3.connect()` calls. |
| **Test** | `test_zone_registry_wal_and_timeout` — verify WAL + timeout after fix. |

---

## 🟠 Tier 3 — Threshold Changes (Review With User Before Applying)

### T-1 · Volatile Threshold Calibration
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Bug #3 |
| **File** | `config.yaml` → `SCORE_THRESHOLDS` |
| **Current** | `volatile: 2.0` |
| **Proposed** | Lower to `1.7` (aligns with realistic P95 signal output ~1.6) |
| **Rationale** | Multiplicative chain structurally caps realistic signals at ~1.6. Threshold 2.0 blocks virtually all crypto auto-trades. |
| **Risk** | Lower threshold increases trade volume — need to verify with live signal distribution before committing. |

### T-2 · Engine B Regime Multiplier No-Op for min_score=3
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 1.1, BUG-003 |
| **File** | `market_structure.py` L258–268 |
| **Current** | `round(base_min * regime_mult)` → all regimes resolve to 3 when base=3 |
| **Proposed** | Use `round(scaled, 1)` or `math.ceil(scaled * 10) / 10` for 1-decimal precision |
| **Rationale** | `3.0 × 0.85 = 2.55 → round() = 3`, `3.0 × 0.90 = 2.70 → round() = 3`. ALL regime multipliers are no-ops. |
| **Risk** | Changing to float comparison changes pass/fail boundary. Need regression test. |

### T-3 · Regime RANGING Multiplier Opposes Mean-Reversion
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 5.3, Bug #5 |
| **File** | `scoring.py` + `factor_scoring.py` |
| **Current** | RANGING × 1.10 raises threshold; mean-reversion additive (+0.10 to +0.15) insufficient to compensate |
| **Proposed** | Either lower RANGING multiplier to 1.00 or increase mean-reversion additive cap |
| **Rationale** | In ranging markets, mean-reversion is the valid edge, but it gets double-penalized: lower trend score + higher threshold. |

### T-4 · Addon Redistribution Default Favors Momentum for Stocks
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 1.2, Bug #1 |
| **File** | `factor_scoring.py` L1735–1740, config key `ADDON_UNSUPPORTED_SPLIT_TO_BASE` |
| **Current** | `SPLIT_TO_BASE=0.0` → all addon weight → momentum → `eff_mom_w=0.80` |
| **Proposed** | Set `SPLIT_TO_BASE=0.50` to redistribute evenly between base floor and momentum |
| **Rationale** | Stocks become momentum-dominated with no base-floor uplift. |

---

## 🗑️ Tier 4 — Dead Code Removal Manifest (Apply After Bugs Fixed)

> **Pre-requisite:** Run `python backup_db.py` before any deletions.
> **Post-step:** Run `py -m pytest tests/ -v` after each batch.

### DC-1 · Safe to Delete — Zero Risk (Phase 4, Section 1)

**52 files, ~3,500 lines. No production references.**

| Batch | Files | Count | Phase 4 Section |
|-------|-------|-------|-----------------|
| **1A — Patch scripts** | `patch_backtest.py`, `patch_bt.py`, `patch_bt_engine_d.py`, `patch_config.py`, `patch_engine.py`, `patch_engine2.py`, `patch_engine3.py`, `patch_fix_indent.py`, `patch_med01.py`, `patch_med02.py`, `patch_research.py`, `patch_research2.py`, `patch_research3.py`, `patch_risk_engine.py`, `patch_runner.py`, `patch_scalp_audit.py`, `patch_sqlite.py`, `patch.py` | 18 | 1A |
| **1B — Debug scripts** | `reproduce_bug.py`, `reproduce_forex_bug.py`, `repro_bug4.py`, `verify_bug3.py`, `verify_bug5.py`, `verify_bug8.py`, `check_cot.py`, `check_db_structure.py`, `check_engine_d.py`, `check_orig.py`, `check_patch.py`, `check_terms.py`, `fix_bot.py`, `fix_patch.py`, `fix_yaml.py`, `fix.py` | 16 | 1B |
| **1C — Scratch/search** | `scratch_engine_b_scan.py`, `scratch_search_func.py`, `search_confidence.py`, `search_engine_b.py`, `search_engine_b_generic.py` | 5 | 1C |
| **1D — tmp/ artifacts** | `tmp/bybit_atr_function_probe.py`, `tmp/bybit_atr_probe.py`, `tmp/engine_a_crypto_probe.py`, `tmp/engine_a_crypto_probe_v2.py`, `tmp/docs/`, `tmp/pdfs/`, `tmp/pytest-microstore-red/`, `tmp/pytest-microstore-red2/`, `tmp/__pycache__/`, `tmp/athena_engine_b_ab_kd_afjk1/` | 10 | 1D |
| **1E — Legacy** | `legacy/ccxt_executor.py` | 1 | 1E |
| **1F — Misc root** | `report_scratch.py`, `quick_telegram_test.py`, `tmp_extract_rl.py`, `diag_soft_gate.py`, `cot_carry_diagnostic.py`, `count_pairs.py` | 6 | 1F |

### DC-2 · Verify Before Removing (Phase 4, Section 2)

| File | Action | Phase 4 Section |
|------|--------|-----------------|
| `test_athena_import.py` | ⚠️ Imports `athena.py` — violates rules. Check if `tests/` equivalent exists. | 2A |
| `test_bt.py` | Cross-ref with `tests/test_backtest*.py` | 2A |
| `test_bug5_logic.py` | Likely obsolete — bug-specific | 2A |
| `test_engine_a_fixes.py` | Check overlap with `tests/test_engine_a*.py` | 2A |
| `test_engine_b_fixes.py` | Check overlap with `tests/test_engine_b*.py` | 2A |
| `test_engine_c_repro.py` | Bug reproduction — likely obsolete | 2A |
| `test_eodhd_symbols.py` | Cross-ref with `tests/` | 2A |
| `test_forex_bt.py` | Cross-ref with `tests/` | 2A |
| `test_forex_signals.py` | Cross-ref with `tests/` | 2A |
| `test_indicators.py` | **⚠️ 7.9 KB — may be canonical.** Verify before touching. | 2A |
| `test_market_state.py` | Cross-ref with `tests/` | 2A |
| `test_pairs.py` | Cross-ref with `tests/` | 2A |
| `test_scan.py` | Minimal — likely redundant | 2A |
| `test_telegram.py` | Cross-ref with `tests/` | 2A |
| `test_ws.py` | Cross-ref with `tests/` | 2A |
| `athena_backup_v2_pre_pandas.py` | 52 KB snapshot — safe to archive | 2B |
| `freqtrade_sample_strategy.py` | Move to `refs/` or delete | 2B |
| `style_resolver.py` | Verify `tests/` doesn't depend on it | 2B |
| `candle_manager.py` | Check if any `athena_app/` module imports | 2B |
| `instrumented_backtest.py` | Verify if `run_backtest.py` uses it | 2B |
| `probe_bt_context.py` | Likely debug-only | 2B |

### DC-3 · athena.py Internal Cleanup (Phase 4, Section 3)

| Item | Location | Action |
|------|----------|--------|
| `import bisect` | L15 | Remove — zero references |
| `import sys` (duplicate) | L7 + L17 | Keep L7, remove L17 |
| `import signal as _signal` (duplicate) | L28 + L14878 | Remove L14878 (part of C-1 fix) |
| `import os as _os` (redundant) | L14867 | Remove (part of C-1 fix) |
| 43 blank lines | L13884–13926 | Collapse to 1 blank line |
| Stale comment tombstones | L12096, L12099 | Remove |
| Commented-out Dukascopy thread | L13775 + `_duka_seed` L13767–13773 | Remove dead function + comment |
| No-op `pass` stubs | L13319, L13343 | Remove |

### DC-4 · Dead Production Code (Phase 1, Section 4)

| Item | File | Action |
|------|------|--------|
| `_session_multiplier` (always returns 1.0) | `factor_scoring.py` L1686 | Remove from multiplication chain |
| `CRYPTO_TRANSITION_PENALTY_ENABLED` | `config.yaml` + `regime.py` | Remove config key + dead code branch |
| `forex_scoring.py` (entire file) | root | ⚠️ Verify no imports, then delete |

---

## 🟡 Tier 5 — MEDIUM Bugs (Fix After Dead Code Cleanup)

### M-1 · Engine C Backtest Never Populates MFE/MAE
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 5.2, BUG-002 |
| **File** | `backtest_runner.py` L4857–4897 |
| **Description** | Engine C BT initializes MFE/MAE tracking variables but never updates them inside the exit loop. All Engine C trade records show `max_favorable_excursion_r: 0.0`, `max_adverse_excursion_r: 0.0`. |
| **Fix** | Copy per-bar R tracking from Engine B BT (L4098–4131) into Engine C exit loop. |
| **Test** | `test_engine_c_bt_mfe_mae_populated` |

### M-2 · Intermarket Divergence Double-Counted
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 6.1, Bug #6 |
| **File** | `factor_scoring.py` L1860–1906 |
| **Description** | Stage 1 applies `_inter_adj = -0.02` inline. Stage 2 independently re-evaluates same data via `apply_confirmation_to_score()`. Same divergence penalized twice: ~±0.07 total vs intended ~±0.02. Stage 2 not bounded by `_total_adj_cap`. |
| **Fix** | Remove Stage 1 inline adjustment, keep only Stage 2 (confirmation engine). Or gate Stage 2 to skip if Stage 1 already applied. |
| **Test** | `test_intermarket_no_double_count` |

### M-3 · Zone Persistence DELETE-All + INSERT-All Pattern
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 3.2, BUG-004 |
| **File** | `zone_registry.py` L259–292 |
| **Description** | Every zone update deletes ALL rows then re-inserts full in-memory state. Crash between DELETE and COMMIT loses all persisted zones. Excessive I/O. |
| **Fix** | Use UPSERT (INSERT OR REPLACE) with composite primary key `(symbol, timeframe, type, direction, bottom)`. |
| **Test** | `test_zone_persistence_crash_recovery` |

### M-4 · CVD Proxy Veto Asymmetry (Engine D)
| Field | Detail |
|-------|--------|
| **Phase** | 3 (Engine D) — BUG-010 |
| **File** | `scalp_engine.py` L2560–2567 vs L2710–2714 |
| **Description** | Mean reversion hard-vetoes CVD proxy conflict even when absorption confirms. Trend continuation allows absorption/VWAP override. Inconsistent treatment of same data quality issue. |
| **Fix** | Align CVD proxy override logic: if absorption confirms and CVD source is proxy, downgrade to advisory (grade reduction) consistently across both setup types. |

### M-5 · `_as_fraction` Silent Conversion (Engine D)
| Field | Detail |
|-------|--------|
| **Phase** | 3 (Engine D) — BUG-011 |
| **File** | `scalp_engine.py` L720–734 |
| **Description** | Values >1.0 silently converted to `v/100`. Values like `1.5` become `0.015` (almost certainly unintended). No warning emitted. |
| **Fix** | Add `log.warning` on auto-conversion, or restrict auto-conversion to `v >= 10.0`. |
| **Test** | `test_as_fraction_edge_cases` — `VP_VALUE_AREA_PCT: 1.5` → 0.015 flagged. |

---

## 🟢 Tier 6 — LOW Bugs (Fix Last)

### L-1 · Dead `_session_multiplier` in Multiplication Chain
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 4.3, Bug #4 |
| **File** | `factor_scoring.py` L1686 |
| **Description** | Always returns 1.0. No math impact; adds meaningless `feed_status["session"]` entry. |
| **Fix** | Remove from multiplication chain and `feed_status`. |

### L-2 · Bollinger `ddof=1` vs Industry `ddof=0`
| Field | Detail |
|-------|--------|
| **Phase** | 1 (Engine A) — Section 2.2 |
| **File** | `indicators.py` |
| **Description** | Uses sample std (`ddof=1`) vs industry population std (`ddof=0`). ~2-3% wider bands on short lookbacks. BB-squeeze triggers slightly later than TradingView/MT5. |
| **Fix** | Change to `ddof=0` for platform parity, or document as intentional divergence. |

### L-3 · Zone `asset_type` Lost on Restart
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 3.4, BUG-005 |
| **File** | `zone_registry.py` L214–228 |
| **Description** | `asset_type` stored in-memory but not in DB schema. After restart, all zones fall back to `"unknown"` TTL (168h forex default). |
| **Fix** | Add `asset_type TEXT DEFAULT 'unknown'` column to zones table schema + persist/load. |
| **Test** | `test_zone_asset_type_survives_restart` |

### L-4 · Backtest Duplicate Barrier Exit Check
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 5.1, BUG-006 |
| **File** | `backtest_runner.py` L4072–4173 |
| **Description** | Exit loop calls `_resolve_barrier_exit()` then manually re-checks TP/SL/BE inline. Redundant — first check handles all cases. Inline BE logic could diverge. |
| **Fix** | Remove inline manual TP/SL/BE check, keep only `_resolve_barrier_exit`. |

### L-5 · Mitigated Zones Never Pruned
| Field | Detail |
|-------|--------|
| **Phase** | 2 (Engine B) — Section 3.3, BUG-007 |
| **File** | `zone_registry.py` L96–98 |
| **Description** | Mitigated zones excluded from pruning. Unbounded memory/storage growth over months. |
| **Fix** | Add max-age prune for mitigated zones (e.g., 30 days). |

### L-6 · Double VP Computation (Engine D)
| Field | Detail |
|-------|--------|
| **Phase** | 3 (Engine D) — BUG-012 |
| **File** | `volume_profile.py` L275–428 |
| **Description** | `compute_fixed_range_volume_profile` doesn't emit `lvn_levels` or `distribution`. Caller supplements from internal fallback, causing double VP computation. Performance cost only. |
| **Fix** | Add LVN detection to `compute_fixed_range_volume_profile`. |

### L-7 · Balance Ratio `None` Defaults to "balance"
| Field | Detail |
|-------|--------|
| **Phase** | 3 (Engine D) — BUG-013 |
| **File** | `scalp_engine.py` L1369–1371 |
| **Description** | Potentially blocks valid trend continuation setups when session bounds unavailable. Fail-safe by design but documented as low risk. |
| **Fix** | Emit warning when falling back to "balance" from None. |

### L-8 · Engine D Signal Case Inconsistency
| Field | Detail |
|-------|--------|
| **Phase** | 3 (Engine D) — BUG-014 |
| **File** | `scalp_engine.py` L4420 |
| **Description** | Signal emits `"engine": "SCALP"` (uppercase) but timed_exit bypass checks lowercase `"scalp"`. Currently safe because consumer lowercases, but maintenance hazard. |
| **Fix** | Normalize to lowercase at emission site. |

---

## 📋 Appendix — Refactor Candidates (Non-Bug Technical Debt)

From Phase 4, Section 4. Not bugs — structural improvements for maintainability.

| ID | Item | File | Lines | Phase 4 Section |
|----|------|------|-------|-----------------|
| R-1 | Engine B manual TTL cache → `cachetools.TTLCache` | `athena.py` L132–157 | ~25 | 4A |
| R-2 | EODHD volume cache → centralized cache | `athena.py` L135–137 | ~15 | 4B |
| R-3 | Pair definition lists → `pairs.yaml` | `athena.py` L222–900+ | ~700 | 4C |
| R-4 | Config persistence regex → `ruamel.yaml` | `athena.py` L7848–8614 | ~500 | 4D |
| R-5 | `api_performance()` → `performance_service.py` | `athena.py` L13389–13868 | ~480 | 4E |
| R-6 | Outcome monitor duplication → `_apply_scalp_milestones()` | `athena.py` L12455–12886 | ~430 | 4F |

---

## 📊 Summary Counts

| Severity | Count | Status |
|----------|-------|--------|
| 🔴 CRITICAL | 1 | Fix immediately |
| 🔴 HIGH | 3 | Fix second |
| 🟠 Threshold Review | 4 | Review with user |
| 🗑️ Dead Code | 4 batches (73+ files) | After bugs |
| 🟡 MEDIUM | 5 | After dead code |
| 🟢 LOW | 8 | Fix last |
| 🔧 Refactor | 6 | Non-urgent debt |
| **Total** | **31 items** | — |
