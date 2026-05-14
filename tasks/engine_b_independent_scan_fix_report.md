# Engine B Independent Scan Fix — Report

**Date:** 2026-05-14
**Branch:** main
**Mode:** targeted architecture fix (surgical, no threshold tuning)

## 1. Root cause

In `scanner.py`, the per-pair worker `_analyse()` inside `run_full_scan()` short-circuited as soon as Engine A produced no signal:

```python
sig_a = r.analyze_pair(pair, ...)
...
if not sig_a:
    return pair, None, None     # ← Engine B never runs
```

The entire Engine B overlay block (~440 lines starting at `ptype = pair.get("type", "")`) was unreachable when `sig_a is None`. Consequently:

- Engine B was an **overlay** on Engine A, not an independent engine: Engine B fields (`engine_b_*`) were written into `sig_a` and only existed when `sig_a` existed.
- Engine B used `direction = sig_a.get("direction")` (line 1315) and never traded its own structural direction in the full-scan path.
- Engine B failures (e.g., Bybit ATR unavailable on crypto, RR-gate fail, no-clear structural verdict) were invisible to operators whenever Engine A also failed for the same pair.
- The downstream classifier `_classify_signal` (in `scoring.py`) used `confluenceScore` and `scanThreshold` — both Engine A semantics — so even if an Engine B row had reached the classifier, it would have been mis-tiered.

The dedicated Engine B endpoint `/api/scan-naked` (in `athena.py`) was already fully independent (tests both directions, has its own funnel) — but the **full scan** at `/api/scan` was not.

`search_engine_b.py` and `search_engine_b_generic.py` are dev-only `grep static/index.html` helpers; they are not the Engine B scan path.

## 2. Files changed

| File | Purpose |
| ---- | ------- |
| `scanner.py` | Added helpers `_make_engine_b_only_signal_stub`, `_engine_b_independent_direction_probe`, `_annotate_engine_b_only_signal_for_scan`, `_classify_engine_b_only_signal`. Introduced `ENGINE_B_SOURCE` / `ENGINE_A_SOURCE` constants. Replaced the early-return with an Engine B-only stub builder. Reordered the snapshot computation so the direction probe can reuse it. Reused probe-cached `res_b` / `conf_b` so we don't double-compute Engine B for the chosen direction. Branched the downstream classification loop on `engine_source` so Engine B rows are classified by Engine B gates only. |
| `tests/test_engine_b_full_scan_audit.py` | Updated the legacy bug-asserting test (`test_scan_source_engine_b_only_after_sig_a`) to assert the corrected behaviour. |
| `tests/test_engine_b_independent_scan_fix.py` | New focused test suite (14 tests covering the 8 areas required by the brief). |
| `tasks/engine_b_independent_scan_fix_report.md` | This report. |

## 3. Exact A-dependency removed

**Before (`scanner.py:1107`):**

```python
if not sig_a:
    return pair, None, None
```

**After:**

```python
engine_b_scan_only = False
if not sig_a:
    sig_a = _make_engine_b_only_signal_stub(pair)
    engine_b_scan_only = True
else:
    sig_a.setdefault("engine_source", ENGINE_A_SOURCE)
    sig_a.setdefault("engine", "A")
    sig_a.setdefault("engine_name", "Engine A")
    sig_a["engine_a_present"] = True
```

Inside the Engine B block, when `engine_b_scan_only`, the new probe runs `analyze_structure` + `calculate_confidence` + `engine_b_confidence_passes` for **both** `LONG` and `SHORT`, picks the best candidate (gate-passed > higher score), and sets `sig_a["direction"]` from Engine B's own evidence. The Engine A-driven path is unchanged.

The legacy "independent direction recheck" (config-gated `ENGINE_B_SCAN_INDEPENDENT_DIRECTION_ENABLED`) is guarded with `not engine_b_scan_only` because the probe already evaluated both directions.

## 4. Active Engine B scan paths after fix

There are now three Engine B output paths, none of which require Engine A:

1. **`/api/scan-naked`** (`athena.py:6257`) — pure Engine B scan; tests both directions per pair; emits Engine B signals or per-direction reject rows with a dedicated funnel. **Unchanged** (was already independent).
2. **`/api/scan`** → `run_full_scan()` (`scanner.py:773`) — Engine A primary path now also yields Engine B-only rows when Engine A is silent. Engine B rows carry `engine_source = "ENGINE_B"`, `engine = "B"`, `engine_name = "Engine B"`, plus the existing `engine_b_*` overlay and `engine_b_scan_gate_funnel` diagnostics.
3. **`/api/pair-scan`** and other single-pair flows — unchanged (out of scope for this fix).

Engine B rows from path (2) are routed through the new `_classify_engine_b_only_signal` and capped at `tier="watchlist"`. They appear in the `watchlist` array of the scan response (or `skipped` with a B-specific reason), never in `tradeSignals`.

## 5. Engine A / B / C separation enforcement

| Engine | Where it runs | What it touches |
| ------ | ------------- | --------------- |
| A | `scoring.py`, `factor_scoring.py`, `forex_scoring.py`, `analyze_pair` | Factor / indicator math; never imports Engine B. (Verified by `test_engine_a_core_does_not_import_engine_b`.) |
| B | `market_structure.py`, `engine_b_ai.py`, `/api/scan-naked`, the Engine B overlay block in `scanner.py` | Naked structure / SMC; standalone. |
| C | `engine_c.py`, `ENGINE_C_AB_WEIGHTS` consumed by `scanner.py` for the A+B conviction blend | The only place A and B are combined. (Verified by `test_engine_c_combines_engine_a_and_engine_b`.) |
| D | `scalp_engine.py` / `/api/scalp-scan` | Independent. Unchanged. |

The downstream loop in `run_full_scan` now branches on `engine_source`:

- `engine_source == ENGINE_A_SOURCE` → existing A-driven pipeline (threshold, quantile floor, `_classify_signal`, Engine C A/B blend on the overlay).
- `engine_source == ENGINE_B_SOURCE` → minimal annotation (`_annotate_engine_b_only_signal_for_scan`) + `_classify_engine_b_only_signal` only. Engine A threshold / quantile / `combinedConviction` are skipped.

B-only rows are excluded from the cross-sectional quantile cohort (`confluenceScore=0` would skew the floor).

## 6. Crypto behaviour when Bybit ATR unavailable

When `pair.type == "crypto"` and `ENGINE_B_CRYPTO_LEVELS_FEED == "bybit"`:

- `r.bybit_atr_for_levels(pair, resolved_style_b)` is called and the result is recorded in the funnel under `bybit_atr_available`.
- If Bybit ATR is missing and `ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK` is `false`, the worker sets `sig_a["engine_b_error"] = "bybit_atr_unavailable"` and forces `atr = 0.0`, which routes to `engine_b_skip_stage = "crypto_bybit_atr_unavailable"`. The funnel is attached on `sig_a` either way.
- For Engine A-driven rows: the funnel is visible on the existing scan row.
- For Engine B-only rows: the new classifier picks up `engine_b_error` and tier is `"skip"` with reason `Engine B error: bybit_atr_unavailable` — **visible to the operator instead of vanishing behind a missing Engine A signal**. (Verified by `test_crypto_engine_b_missing_bybit_atr_reports_rejection_visibly` and `test_crypto_engine_b_skip_stage_surfaced_in_b_only_classifier`.)

No fallback policy was changed; the failure surface was made visible in the Engine B-only path.

## 7. RR failure visibility

`engine_b_confidence_passes` already separates `structure_ok`, `location_ok`, `entry_ok`, `space_gate_ok`, and `rr_ok`. The funnel records each. The new B-only classifier:

```python
if any(str(g).startswith("rr=") or str(g).lower() == "rr_gate" for g in failed):
    return "skip", f"Engine B RR gate failed: {failed_str}"
```

…surfaces RR-gate fails with an explicit reason on the B-only row. (Verified by `test_rr_failure_is_visible_in_engine_b_only_classifier` and `test_rr_gate_failure_surfaced_from_engine_b_funnel`.)

Existing `resolve_engine_b_execution_levels` continues to emit `execution_level_reject_reason = "structural_tp_below_min_rr"` when RR cannot be reached; that flows through the funnel unchanged.

## 8. Tests added / run

New file `tests/test_engine_b_independent_scan_fix.py` — 14 tests, all passing:

1. `test_engine_b_scan_runs_without_engine_a_signal` — confirms the early-return regression is gone.
2. `test_engine_b_only_stub_has_required_independent_fields` — checks stub shape (`engine_source`, `engine`, `engine_name`, direction None, `enginesAligned` False).
3. `test_engine_b_independent_direction_probe_picks_best_passing_direction` — gate-passed wins over higher unpassed score.
4. `test_engine_b_independent_direction_probe_returns_none_when_no_clear` — no CLEAR verdict → no direction.
5. `test_engine_a_core_does_not_import_engine_b` — `scoring.py` / `factor_scoring.py` do not import `market_structure`.
6. `test_engine_c_combines_engine_a_and_engine_b` — A/B blend uses Engine C weights.
7. `test_full_scan_routes_engine_b_only_rows_through_b_classifier_path` — source contract: downstream branch on `engine_source`.
8. `test_crypto_engine_b_missing_bybit_atr_reports_rejection_visibly` — `engine_b_error = "bybit_atr_unavailable"` surfaces in B-only tier.
9. `test_crypto_engine_b_skip_stage_surfaced_in_b_only_classifier` — `engine_b_skip_stage` reaches the classifier output.
10. `test_engine_b_classifier_does_not_consult_engine_a_threshold` — `confluenceScore = 999` cannot promote a B-only row to trade.
11. `test_engine_b_classifier_uses_only_engine_b_gates_when_passed_is_false` — failing B gates produce explicit `Engine B gates failed: ...` reason.
12. `test_rr_failure_is_visible_in_engine_b_only_classifier` — `failed_gate_names=["rr=0.8"]` → explicit RR reason.
13. `test_rr_gate_failure_surfaced_from_engine_b_funnel` — funnel-only fallback path for RR fail still emits an RR-tagged reason.
14. `test_engine_b_only_classifier_never_returns_trade_tier` — B-only rows can never become `tier="trade"`.

Existing test that asserted the bug (`test_scan_source_engine_b_only_after_sig_a` in `tests/test_engine_b_full_scan_audit.py`) was renamed to `test_scan_does_not_short_circuit_engine_b_when_sig_a_missing` and inverted to assert the **correct** behaviour.

Full sweep run:

```
pytest tests/test_engine_b_independent_scan_fix.py \
       tests/test_engine_b_full_scan_audit.py \
       tests/test_scanner_diagnostics.py \
       tests/test_scanner_safety.py \
       tests/test_scan_quantile.py \
       tests/test_naked_scan_service.py \
       tests/test_scan_backtest_service.py \
       tests/test_execution_engine_c_scan.py
→ 51 passed

pytest tests/ -k "scan or engine_b or engine_a or naked or scanner or classify or signal or quantile"
→ 429 passed
```

(3 collection-time errors in `test_routes_ai_agent.py` are due to a Windows pytest tmp-dir permission issue and are unrelated to this fix.)

## 9. Confirmations

- ✅ **No threshold tuning.** No edits to `min_score`, `min_rr`, score-group floors, or any `ENGINE_B_*` thresholds in `config.yaml`. `config.yaml` was not modified at all.
- ✅ **No Engine A scoring changes.** `scoring.py` and `factor_scoring.py` untouched. The Engine A-driven downstream path is byte-identical to before for any pair where Engine A produces a signal.
- ✅ **No live execution changes.** `execution.py`, `risk_engine.py`, `auto_trader.py`, `mt5_executor.py`, `bybit_executor.py` untouched. Engine B-only rows are capped at `tier="watchlist"` and cannot reach the auto-trader.
- ✅ **No AI changes.** `engine_b_ai.py`, `ai_tools.py`, `ai_agent_safety.py`, Marcus / Vision / Strategist code untouched.
- ✅ **No risk / kill-switch / guardian changes.** `r.kill_switch()` gate at the top of `run_full_scan` still applies to all engines.
- ✅ **Engine C is still the only A/B combiner.** `ENGINE_C_AB_WEIGHTS` is used only on the A-driven path's overlay blend; the B-only path does not consult Engine C.

## 10. Remaining blockers

None for this scope.

Out-of-scope follow-ups (do not block this fix):

- The UI scan-mode selector currently has no dedicated "Engine B mode" routed at `/api/scan`; operators must call `/api/scan-naked` for a B-only scan. The full scan now exposes Engine B rows when Engine A is silent, but does not yet have a UI toggle to suppress Engine A entirely. That is a UI-layer change, not an architectural one.
- The `_apply_engine_b_only_watchlist_scan_tier` helper (gated by `ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED`) overlaps in spirit with the new B-only classifier path. They serve different cases (overlay on existing A row vs standalone B row) and can coexist; future cleanup could merge them but it is not required.
- `backtest_runner.py` has not been audited as part of this fix; live/backtest parity for Engine B-only rows in the historical replay path is a separate task.
