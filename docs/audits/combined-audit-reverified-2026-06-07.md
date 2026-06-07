# Combined Engine A/B Audit — Re-Verification Report

**Date:** 2026-06-07  
**Scope:** Submitted combined audit (Engine A + Engine B + risk + parity) vs current `main`  
**Commits verified:** `e3c98ac6` (TV chart / Engine A remediations), `3da536a1` (Engine B fixes)  
**Prior passes:** [`engine-a-audit-findings-verified-2026-06-07.md`](engine-a-audit-findings-verified-2026-06-07.md)

---

## Overall verdict

Eleven findings from the submitted audit are **fixed or mitigated on `main`** and require no further action. Several original **CRITICAL** claims are **stale, overstated, or intentional design**. The only live-crash blocker (Engine B `execution_sl=None` on fallback RR) is **resolved**.

**Revised severity (production yaml loaded):**

| Category | Submitted | After re-review |
|----------|-----------|-----------------|
| CRITICAL | 5 | **0** |
| HIGH | 9 | **3** (calibration depth) |
| MEDIUM | 11 | **6** (backlog / documented design) |
| LOW | 11 | unchanged |

---

## Ignore — already fixed

| ID | Claim | Evidence |
|----|-------|----------|
| B-CRIT-1 | `execution_sl=None` on fallback RR | `resolve_engine_b_execution_levels` clamps via `_engine_b_clamp_sl_to_max_pct`; test passes |
| A-CRIT-3 | Score-group adjustments silent False | `validate_config` warns when `ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED` is False |
| A-HIGH-7 | Carry static rates stale | `CARRY_STATIC_RATES_AS_OF` + age warning in `validate_config` |
| B-HIGH-9 | Commodity swing macro forced ON | `commodity_swing_force_macro_align: false` in yaml + code |
| A-HIGH-12 | VWAP not asset-gated | `_vwap_direction_filter` crypto-only when enabled; chart `indicatorPeriods` parity |
| B-HIGH-14 | Zone proximity hardcoded 0.5 | `NAKED_ENGINE.zone_proximity_atr_mult` per asset/score_group |
| A-HIGH-13 | Funding z-score off | `FACTOR_FUNDING_USE_ZSCORE: true` in yaml |
| MED-26 | TV chart hardcoded periods | Server-driven `indicatorPeriods` in chart API + `TVChartPanel` |
| A-HIGH-6 (partial) | COT softs / coverage flag | Softs formulas + `_cot_coverage` in feed status |
| B-HIGH-15 | Dual FVG paths diverge | `test_engine_b_fast_fvg_detection_matches_legacy` + legacy fallback |
| MED-22 | Asian session skip unused | Used in `scanner.py` and `backtest_runner.py` (not in `calculate_confidence` by design) |

---

## Downgrade / reject — not bugs as stated

| ID | Original | Verdict |
|----|----------|---------|
| B-CRIT-2 | Hard counter veto blocks BOS+sweep | Default `hard_counter_mode: penalty` — score penalty only. Veto mode is explicit opt-in. |
| A-CRIT-4 | Forex conviction floor in RANGING | Intentional — forex omitted from `ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES` for score reachability. |
| A-HIGH-11 | RSI 70/30 wrong for forex | `RSI_BOUNDS` populated; forex 70/30 is documented Wilder standard. |
| A-CRIT-5 | ADX missing for 25+ groups | Overstated — `_resolve_class_keyed` inherits asset_type; runtime warn on hard fallback. Per-score_group keys added in this pass. |
| A-HIGH-10 | Factor weights empty | Stale — yaml populated; extended in this pass for remaining score_groups. |
| MED-16 | Room gate allows None distance | Intentional when trend + BOS confirmed (`ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE`). |
| MED-18 | D1 PD penalty not on gate_score | Intentional — score-only penalty per yaml comment. |
| MED-19 | Follow-through diagnostics when bonus off | By design — diagnostics without score impact. |

---

## Still open (ranked)

### P1 — addressed in this pass

| Item | Action |
|------|--------|
| A-CRIT-5 remainder | Explicit `ADX_TREND_MIN_CLASS` / `FACTOR_ADX_HARD_FAIL_CLASS` for commodity/index/stock score_groups |
| A-HIGH-10 remainder | `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS` extended for remaining score_groups |
| A-HIGH-6 remainder | COT altcoin policy documented (`unsupported`); stock proxy limitation documented |

### P1 — remains (feed / research)

| Item | Status |
|------|--------|
| COT altcoins | Empty `_PAIR_FORMULA` → `unsupported` (no BTC/ETH proxy for alts) — by policy |
| Factor weights | Judgment values; research-lab validation still needed |

### P2 — addressed in this pass

| Item | Action |
|------|--------|
| B-HIGH-8 | `ENGINE_B_BOS_VOLUME_FOR_TICKVOL` documented — stays **false** until backtest evidence |
| MED-21 | Forex TP bounds parity — `MAX_TP_PCT` in yaml + Engine B applies bounds for non-crypto |

### P2 — backlog (needs explicit approval)

| Item | Notes |
|------|-------|
| A-CRIT-4 policy change | Add forex to regime-sensitive classes only after backtest |
| B-CRIT-2 veto exception | BOS+sweep exception in veto mode if ever enabled |
| MED-23–25 | CHOCH strict, BOS lookback, breaker ATR — calibration only |

### P3 — design split

| Item | Notes |
|------|-------|
| Backtest forming bar | Live includes forming H1/H4; backtest confirmed-only — documented bias |

---

## COT policy (2026-06-07)

| Pair class | Policy | Rationale |
|------------|--------|-----------|
| BTC/ETH | CME COT legs | Real positioning data |
| Altcoins (`[]` formula) | **unsupported** | No reliable CFTC/CME leg; proxy would mis-rank alts |
| US stocks | SP500/NQ100 **macro proxy** | No single-name COT; equity-index positioning only |
| Softs / nat gas / metals | Dedicated CFTC legs | Formulas in `cot_feed._PAIR_FORMULA` |

See `ENGINE_A_COT_POLICY` in [`config.yaml`](../config.yaml).

---

## BOS volume gate (2026-06-07)

`ENGINE_B_BOS_VOLUME_FOR_TICKVOL` remains **false**. MT5 tick volume is not exchange contract volume; enabling without backtest evidence risks false BOS confirmations on forex. Crypto uses real perp volume when available.

---

## Implementation from this pass

1. Per-score_group ADX + factor weight keys in `config.yaml`
2. `validate_config` warns on score_groups missing explicit ADX keys
3. `ENGINE_A_COT_POLICY` + `cot_feed.py` policy header
4. `MAX_TP_PCT` asset-class table in yaml; Engine B TP bounds for all asset classes
5. Tests: forex TP bounds parity, ADX key resolution

---

## Tests run

- `pytest tests/test_engine_b_diagnostics.py::test_engine_b_structural_tp_below_min_rr_uses_fallback_rr_tp -q`
- `pytest tests/test_engine_b_diagnostics.py -k "forex_tp or adx" -q` (this pass)
- `pytest tests/test_factor_scoring.py -k "cot_coverage" -q` (this pass)

## Not verified

- Live FRED/COT fetch at runtime
- Full backtest matrix for new ADX/weight keys
- Enabling `ENGINE_B_BOS_VOLUME_FOR_TICKVOL` on forex
