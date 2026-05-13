# Audit Master Priority

Generated from:
- Phase 1: `tasks/audit_phase_1codex_engine_a.md`
- Phase 2: `tasks/audit_phase_2codex_engine_b.md`
- Phase 3: `tasks/audit_phase_3codex_engine_d.md`
- Phase 4: `tasks/audit_phase_4codex_dead_code.md`

Scope note: this is a derived priority index from the four phase reports. The phase reports' code references are preserved below; this file does not re-verify the underlying source code.

## 1. CRITICAL Bugs - Fix First

| Priority | Phase | Audit ref | Source file/line | Item | Fix first because |
|---:|---|---|---|---|---|
| 1 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:175` | `execution.py:2003`, `execution.py:2122-2142` | BUG-D-4 - Modular Engine D execution route can execute blocked or Grade D payloads. | Direct execution-safety issue: a blocked or Grade D posted signal can reach broker placement if the modular route is registered or called directly. |
| 2 | Phase 4 - Dead Code/Cleanup | `tasks/audit_phase_4codex_dead_code.md:267-269` | `athena.py:14793-14833`, `athena.py:14878-14884` | Confirmed operational bug - duplicate shutdown handler overrides graceful Bybit/MT5/WS cleanup and calls `os._exit(0)`. | Runtime cleanup is bypassed on SIGINT/SIGTERM, leaving broker and websocket cleanup dead on script startup. |
| 3 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:123` | `backtest_runner.py:5131` | BUG-D-1 - Crypto Engine D backtest uses a tuple instead of a candle list. | Breaks or malforms crypto Engine D backtest evidence, which can invalidate parity and calibration work. |

## 2. HIGH Bugs - Fix Second

| Priority | Phase | Audit ref | Source file/line | Item | Impact |
|---:|---|---|---|---|---|
| 4 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:324` | `athena.py:9196`, `athena.py:9226-9227`, `athena.py:9229-9237` | BUG-D-12 - Active monolith scalp execute can return success after audit insert failure. | A fill can exist without an audit row, breaking BE/TP1 tracking, dashboard truth, reconciliation, and PnL accounting. |
| 5 | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:54` | `execution.py:812`, `execution.py:1648`, `risk_engine.py:835` | BUG-B-2 - Level override can bypass Engine B style minimum RR. | Manual/AI override can reduce RR below the accepted Engine B threshold and still pass risk. |
| 6 | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:30` | `execution.py:775` | BUG-B-1 - Quick execute rebuilds generic levels for structural Engine B signals. | Structural Engine B setup can execute with non-Engine-B SL/TP, bypassing the structural level contract. |
| 7 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:194` | `execution.py:2066-2091` | BUG-D-5 - Modular Engine D rebase drops group-specific minimum RR. | Group-specific RR requirements can be lost during modular route level recalculation. |
| 8 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:215` | `backtest_runner.py:5348-5352` | BUG-D-6 - Stock backtest volume acceptance does not match live EODHD safety gates. | Engine D stock backtests can validate setups that live scan would block. |
| 9 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:252` | `eodhd_volume_batch.py:205-209` | BUG-D-8 - EODHD LiveV2 first quote injects cumulative session volume as one delta. | First warmed stock candle can get a massive synthetic volume spike, distorting VP and aggression. |
| 10 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:270` | `athena.py:137`, `athena.py:1873` | BUG-D-9 - EODHD low-timeframe cache TTL can serve stale VP volume for 15 minutes. | Stale low-timeframe volume can drive current Engine D profile decisions. |
| 11 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:140` | `backtest_runner.py:5131` | BUG-D-2 - Crypto backtest can use forming-bar data while non-crypto backtest uses closed bars. | Crypto Engine D backtests can contain lookahead. |
| 12 | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:157` | `scalp_engine.py:3070-3071` | BUG-D-3 - Engine D TP1 is not the configured 1R self-pay target. | TP1 can be stretched to minimum RR instead of remaining the 1R self-pay target. |
| 13 | Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:176` | `scoring.py:288-297`, `config.yaml:1669`, `config.yaml:1681` | BUG-A-3 - Pair profiles can undercut global threshold. | XAU/USD and XAG/USD can pass below global Engine A minimum. |
| 14 | Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:135` | `config.yaml:1160`, `config.yaml:1392`, `factor_scoring.py:221`, `factor_scoring.py:601`, `factor_scoring.py:1719`, `factor_scoring.py:1876` | BUG-A-1 - Dead factor weight surface. | Operators can tune active-looking Engine A config that has no production effect. |

## 3. Threshold Changes Recommended - Review Before Applying

Do not apply these as part of the first bug-fix pass without user review, because they change calibration or operator policy.

| Phase | Audit ref | Source file/line or key | Recommendation to review |
|---|---|---|---|
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:251` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_btc` | Marked too strict for A-only auto but reachable; scan can pass at 2.0 while live A-only needs about 2.5. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:253` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_eth` | Same A-only scan/live gap as BTC. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:255` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_alt_majors` | Scan threshold below A-only live requirement. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:257` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_doge` | Volatility scaler helps, but A-only live still needs about 2.5. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:259` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_meme` | Marked too strict but reachable. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:261` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.crypto_other` | Marked too strict for A-only auto but reachable. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:263` | `ENGINE_A_SCORE_GROUP_THRESHOLDS.nat_gas` | Marked too strict but reachable. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:269-271` | `PAIR_PROFILES.XAU/USD.min_confluence`, `PAIR_PROFILES.XAG/USD.min_confluence` | Marked too loose because profile overrides undercut global default 1.5. |
| Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:277-281` | `ADX_TREND_MIN_CLASS.commodity`, `stock`, `index` | Marked strict; review suppression on lower-volatility assets before changing. |
| Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:213-217` | `NAKED_ENGINE.style_profiles.*.min_score` | Scalp/intraday/swing score floors marked too loose or mostly non-binding after mandatory gates. |
| Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:219-221` | `ENGINE_B_REGIME_MULTIPLIERS.HIGH_VOLATILITY`, `LOW_VOLATILITY` | Multiplier policy is too loose/too strict and partly neutralized by rounding. |
| Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:223` | `NAKED_ENGINE.*.min_rr` | Mechanically calibrated, but not re-enforced after execution level override for standalone Engine B. |
| Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:227` | `ENGINE_B_FOREX_ADX_MIN` | Dead as a gate; decide whether to enforce or rename/comment as diagnostic-only. |
| Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:229` | `ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE`, `min_room_atr` | Fail-closed policy is hardcoded; configured `min_room_atr` is ignored by effective distance thresholds. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:347` | `VP_LVN_THRESHOLD` | Marked too strict; inside-VA LVN restriction can miss external LVNs. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:348` | `VP_PROXIMITY_PCT` | Marked too loose when ATR is unavailable. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:350` | `BALANCE_THRESHOLD` | Not verified; needs config/distribution proof before tuning. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:352` | `ABSORPTION_MAX_MOVE_ATR` | Marked too strict; may reject valid aggressive absorption on volatile symbols. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:355` | `CVD_MIN_SLOPE` | Marked too loose; default 0.0 treats any positive/negative slope as aligned. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:362` | `TP1_R_MULT` | Marked unreachable as configured because code raises TP1 to at least `MIN_RR`. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:363-364` | `TP1_MAX_RR`, `TREND_EXT_MAX_RR` | Marked dead/not effective; decide whether to wire or remove. |
| Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:304` | `SCALP_ENGINE.GRADE_THRESHOLDS` | Missing from config; add only after reviewing desired A/B/C/D boundaries. |

## 4. Dead Code Removal Manifest - Apply After Bugs Are Fixed

### Safe to delete immediately, in batches

Run relevant tests after each batch. These entries come from the Phase 4 cleanup manifest Table 1.

| Audit ref | File/location | Action |
|---|---|---|
| `tasks/audit_phase_4codex_dead_code.md:58` | `patch_backtest.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:59` | `patch_bt.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:60` | `patch_bt_engine_d.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:61` | `patch_config.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:62` | `patch_engine.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:63` | `patch_engine2.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:64` | `patch_engine3.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:65` | `patch_fix_indent.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:66` | `patch_med01.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:67` | `patch_med02.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:68` | `patch_research.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:69` | `patch_research2.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:70` | `patch_research3.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:71` | `patch_risk_engine.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:72` | `patch_runner.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:73` | `patch_scalp_audit.py` | Delete one-time source-mutating patch script. |
| `tasks/audit_phase_4codex_dead_code.md:74` | `patch_sqlite.py` | Delete unsafe recursive SQLite patcher. |
| `tasks/audit_phase_4codex_dead_code.md:75` | `check_orig.py` | Delete one-time debug script. |
| `tasks/audit_phase_4codex_dead_code.md:76` | `check_patch.py` | Delete one-time debug script. |
| `tasks/audit_phase_4codex_dead_code.md:77` | `check_terms.py` | Delete manual search helper; replace with `rg`. |
| `tasks/audit_phase_4codex_dead_code.md:78` | `scratch_search_func.py` | Delete one-time scratch script. |
| `tasks/audit_phase_4codex_dead_code.md:79` | `verify_bug8.py` | Delete unless tied to real production regression coverage. |
| `tasks/audit_phase_4codex_dead_code.md:80` | `test_athena_import.py` | Delete stale root test; imports monolith directly. |
| `tasks/audit_phase_4codex_dead_code.md:81` | `test_bt.py` | Delete stale root test; covered better by `tests/test_backtest_integrity.py`. |
| `tasks/audit_phase_4codex_dead_code.md:82` | `test_bug5_logic.py` | Delete stale root test; tests copied logic. |
| `tasks/audit_phase_4codex_dead_code.md:83` | `test_forex_bt.py` | Delete stale manual root smoke. |
| `tasks/audit_phase_4codex_dead_code.md:84` | `test_forex_signals.py` | Delete or convert; monolith probe. |
| `tasks/audit_phase_4codex_dead_code.md:85` | `test_market_state.py` | Delete print-only root probe; superseded by `tests/test_market_state_offsets.py`. |
| `tasks/audit_phase_4codex_dead_code.md:86` | `tmp/bybit_atr_function_probe.py` | Delete generated probe. |
| `tasks/audit_phase_4codex_dead_code.md:87` | `tmp/bybit_atr_probe.py` | Delete generated probe. |
| `tasks/audit_phase_4codex_dead_code.md:88` | `tmp/engine_a_crypto_probe.py` | Delete generated probe. |
| `tasks/audit_phase_4codex_dead_code.md:89` | `tmp/engine_a_crypto_probe_v2.py` | Delete generated probe. |
| `tasks/audit_phase_4codex_dead_code.md:90` | `tmp/__pycache__/` | Delete generated bytecode cache. |
| `tasks/audit_phase_4codex_dead_code.md:91` | `tmp/pdfs/fabio_review/` | Delete generated review artifacts. |
| `tasks/audit_phase_4codex_dead_code.md:92` | `tmp/athena_engine_b_ab_kd_afjk1/audit_ab.sqlite` | Delete large temp DB if audit evidence no longer needed. |
| `tasks/audit_phase_4codex_dead_code.md:93` | `prices.json` | Delete/archive stale root data dump. |
| `tasks/audit_phase_4codex_dead_code.md:94` | `mt5_pairs.json` | Delete empty root data dump. |
| `tasks/audit_phase_4codex_dead_code.md:95` | `bt_results.json` | Delete/archive root dump. |
| `tasks/audit_phase_4codex_dead_code.md:96` | `bt_forex_new.json` | Delete/archive root dump. |

### Verify, port, or archive before delete

| Audit ref | File/location | Required decision before removal |
|---|---|---|
| `tasks/audit_phase_4codex_dead_code.md:106` | `reproduce_bug.py` | Port useful factor-scoring/index assertion into `tests/`. |
| `tasks/audit_phase_4codex_dead_code.md:107` | `reproduce_forex_bug.py` | Port useful forex scoring assertion into `tests/`. |
| `tasks/audit_phase_4codex_dead_code.md:108` | `verify_bug3.py` | Port forex session parity/escape-hatch assertion into `tests/`. |
| `tasks/audit_phase_4codex_dead_code.md:109` | `verify_bug5.py` | Port structural SL rejection assertion into `tests/`. |
| `tasks/audit_phase_4codex_dead_code.md:110` | `check_cot.py` | Confirm nobody uses it for COT DB inspection. |
| `tasks/audit_phase_4codex_dead_code.md:111` | `check_db_structure.py` | Confirm nobody uses it for COT/carry schema inspection. |
| `tasks/audit_phase_4codex_dead_code.md:112` | `check_engine_d.py` | Preserve reusable Engine D audit-regression logic if useful. |
| `tasks/audit_phase_4codex_dead_code.md:113` | `scratch_engine_b_scan.py` | Confirm no operator relies on it for Engine B runtime diagnosis. |
| `tasks/audit_phase_4codex_dead_code.md:114` | `test_engine_a_fixes.py` | Port valid assertions, then delete root copy. |
| `tasks/audit_phase_4codex_dead_code.md:115` | `test_engine_b_fixes.py` | Port valid assertions, then delete root copy. |
| `tasks/audit_phase_4codex_dead_code.md:116` | `test_engine_c_repro.py` | Convert to focused asserted test if still relevant. |
| `tasks/audit_phase_4codex_dead_code.md:117` | `test_indicators.py` | Move unique indicator coverage to `tests/`. |
| `tasks/audit_phase_4codex_dead_code.md:118` | `test_eodhd_symbols.py` | Keep only as opt-in integration test if still useful. |
| `tasks/audit_phase_4codex_dead_code.md:119` | `test_pairs.py` | Replace with proper pair-universe assertions if needed. |
| `tasks/audit_phase_4codex_dead_code.md:120` | `test_scan.py` | Convert to app-factory route test if desired. |
| `tasks/audit_phase_4codex_dead_code.md:121` | `test_telegram.py` | Keep only as opt-in manual tool if needed. |
| `tasks/audit_phase_4codex_dead_code.md:122` | `test_ws.py` | Keep only as opt-in integration test if needed. |
| `tasks/audit_phase_4codex_dead_code.md:123` | `legacy/ccxt_executor.py` | Verify no external rollback workflow depends on old ccxt executor. |
| `tasks/audit_phase_4codex_dead_code.md:124` | `backtest_baseline_2026-05-02.json` | Decide archive vs delete. |
| `tasks/audit_phase_4codex_dead_code.md:125` | `forex_intraday_backtest.json` | Decide archive vs delete; large tracked dump. |
| `tasks/audit_phase_4codex_dead_code.md:126` | `diag_h1.json` | Fix/rename/archive/delete corrupt mixed log/JSON artifact. |
| `tasks/audit_phase_4codex_dead_code.md:127` | `tmp/docs/fabio_valentini_extracted.txt` | Move under docs or update referencing diagnostics doc before deletion. |
| `tasks/audit_phase_4codex_dead_code.md:128` | `tmp/pdfs/fabio_pro_scalper/` | Confirm docs do not need rendered/research evidence. |
| `tasks/audit_phase_4codex_dead_code.md:129` | `.mcp.json` | Local connector config; do not commit; delete only if local setup is not needed. |
| `tasks/audit_phase_4codex_dead_code.md:130` | `skills-lock.json` | Local tool lock; do not commit; delete only if local setup is not needed. |
| `tasks/audit_phase_4codex_dead_code.md:131` | `tmp/pytest-microstore-red` | Access denied; fix permissions before inspecting/deleting. |
| `tasks/audit_phase_4codex_dead_code.md:132` | `tmp/pytest-microstore-red2` | Access denied; fix permissions before inspecting/deleting. |

### Refactor candidates, not delete

| Audit ref | File/location | Action |
|---|---|---|
| `tasks/audit_phase_4codex_dead_code.md:138` | `athena_legacy.py` | Keep; required by app-factory monolith-loading path. |
| `tasks/audit_phase_4codex_dead_code.md:139` | `athena_runtime.py` | Keep; required runtime binding bridge. |
| `tasks/audit_phase_4codex_dead_code.md:140` | `athena.py:222-900+` | Move pair universe to `pairs.yaml` only after parity tests. |
| `tasks/audit_phase_4codex_dead_code.md:141` | `athena.py:7848-8614` | Move YAML config persistence helpers to a proper module later. |
| `tasks/audit_phase_4codex_dead_code.md:142` | `athena.py:12455-12886` | Extract shared scalp milestone/outcome monitor logic later. |
| `tasks/audit_phase_4codex_dead_code.md:143` | `athena.py:7052-7108`, `athena.py:9105-9154` | Consolidate duplicate broker preflight logic later. |
| `tasks/audit_phase_4codex_dead_code.md:144` | `athena.py:2052-2063`, `athena.py:2112-2123`, `athena.py:2240-2251`, `athena.py:2396-2406`, `athena.py:2455-2466` | Extract one EODHD candle normalizer later. |
| `tasks/audit_phase_4codex_dead_code.md:145` | `athena.py:1809-1823`, `athena.py:2258-2274` | Use one H1/H4 resample helper later. |

## 5. MEDIUM/LOW Bugs - Fix Last

| Priority | Severity | Phase | Audit ref | Source file/line | Item | Impact |
|---:|---|---|---|---|---|---|
| 15 | MEDIUM | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:70` | `market_structure.py:258`, `config.yaml:2040` | BUG-B-3 - Regime multipliers are rounded into non-effects or inconsistent gates. | Calibration is misleading; some regimes become no-ops while swing changes materially. |
| 16 | MEDIUM | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:86` | `market_structure.py:2806`, `market_structure.py:2901` | BUG-B-4 - `min_room_atr` config overrides are ignored. | Forex exotics and DOGE room calibration does not apply in production scoring. |
| 17 | MEDIUM | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:108` | `config.yaml:2088`, `market_structure.py:2840` | BUG-B-5 - `ENGINE_B_FOREX_ADX_MIN` is dead as a gate. | Operators can tune a key expecting execution impact when it does nothing. |
| 18 | MEDIUM | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:124` | `market_structure.py:3010`, `market_structure.py:3035` | BUG-B-6 - Follow-through bonus can distort denominator when enabled. | Raw scores and percent calibration drift if enabled. |
| 19 | MEDIUM | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:140` | `market_structure.py:56` | BUG-B-7 - Confirmed-only structure helper fails open. | Direct callers can leak forming bars when confirmed-only policy cannot be proven. |
| 20 | MEDIUM | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:156` | `zone_registry.py:211`, `zone_registry.py:281` | BUG-B-8 - Zone persistence SQLite path violates DB safety rules. | Dormant by default, but can lock or lose state when enabled. |
| 21 | MEDIUM | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:286` | `athena.py:1878-1886`, `athena.py:1920-1979` | BUG-D-10 - Backtest EODHD path can use live CandleBuilder stock volume. | Stock Engine D backtests can be contaminated by current live session volume. |
| 22 | MEDIUM | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:233` | `scalp_engine.py:1225-1228` | BUG-D-7 - LVN detection only searches inside the value area. | Trend continuation can miss external low-volume pullback nodes. |
| 23 | MEDIUM | Phase 3 - Engine D | `tasks/audit_phase_3codex_engine_d.md:304` | `scalp_engine.py:3327` | BUG-D-11 - Grade thresholds are hardcoded fallbacks and missing from config. | Operators cannot calibrate A/B/C/D distribution from config. |
| 24 | MEDIUM | Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:215` | `config.yaml:183`, `factor_scoring.py:1876` | BUG-A-5 - Conviction floor comment is mathematically backwards. | Threshold tuning comments are misleading. |
| 25 | MEDIUM | Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:154` | `indicators.py:241`, `indicators.py:296`, `indicators.py:319`, `indicators.py:816`, `factor_scoring.py:1346` | BUG-A-2 - Bollinger Bands use sample standard deviation. | Bands are wider than standard BB, changing squeeze/touch/mean-reversion output. |
| 26 | LOW | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:172` | `zone_registry.py:108`, `config.yaml:2388` | BUG-B-9 - Zone TTL config is misplaced for `ZoneRegistry`. | Crypto 72h zone TTL is ignored; default 168h applies. |
| 27 | LOW | Phase 2 - Engine B | `tasks/audit_phase_2codex_engine_b.md:195` | `market_structure.py:1500`, `market_structure.py:1544` | BUG-B-10 - FVG detection has no minimum gap filter. | Micro-gaps can mark zones as overlapping and influence context. |
| 28 | LOW | Phase 1 - Engine A | `tasks/audit_phase_1codex_engine_a.md:198` | `indicators.py:588-607`, `factor_scoring.py:456-472` | BUG-A-4 - Stochastic RSI uses simple RSI. | Dormant while stochastic RSI is disabled; if enabled, RSI math is inconsistent. |

## Recommended Fix Order Summary

1. Fix the three CRITICAL items first, with focused negative tests around direct execution, shutdown handler registration, and crypto Engine D backtest tuple normalization.
2. Fix HIGH execution-safety and audit-truth items next: Engine D audit success-after-failure, Engine B override RR, Engine B quick-execute levels, and Engine D modular RR handling.
3. Review threshold/calibration changes with the user before applying them.
4. Apply dead-code removal only after bug fixes are stable, deleting safe items in batches and porting useful root-test assertions first.
5. Fix MEDIUM/LOW bugs last, unless one becomes a blocker for a higher-priority regression.
