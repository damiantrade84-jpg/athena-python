# Engine A + Engine B RR / TP / SL / ATR independent audit — forex focus

**Date:** 2026-07-12 · **Auditor:** Claude (independent, evidence-only) · **Data:** frozen `BACKTEST_DATA_AS_OF=2026-05-30`, pairs GBP/USD, EUR/USD, USD/JPY, style intraday · **Code state:** HEAD `a03a672e` (no uncommitted diffs in `market_structure.py`, `backtest_runner.py`, `engine_a_v3/`)

**Trigger:** GBP/USD intraday comparison showed Engine B planned TP1 RR ≈ 0.80 (TP1 closer than SL), 40/78 "winners" at breakeven, median win 0.30R; Engine A avg win 0.59R on ~1R planned TP1s.

Measurement-only audit: no scoring, gate, or config changes were applied. All quantification via scratchpad scripts calling `backtest_pair_naked` and `run_v3_backtest` directly on frozen data.

---

## 0. Environment finding — your recent numbers were produced with all RR gates bypassed

`config.local.yaml:22` has **`DISABLE_TP_SL_RR_GATES: true`** (file mtime 2026-07-11 20:46 local, i.e. after commit 75dbf7b2 added the key). Consequences:

- The saved 17:32 UTC July-11 Engine B run (139 trades) predates the flip and likely ran with gates ON.
- **Every backtest and demo scan since 20:46 on July 11 runs with `rr_passed`, `space_gate_ok`, `room_ok` forced true and MAX_SL enforcement off** (`tp_sl_rr_gate_policy.py`, `market_structure.py:2794-2801, 5406-5412, 5636-5643`). The frozen rerun that produced "152 trades / planned TP1 RR 0.80" was a gates-bypassed population.
- The bypass is fail-closed for `EXECUTOR_MODE=live` but **active for your demo execution path** — Engine B demo trades are currently taken without RR/space/max-SL gating.

Parity anchor reproduced: bypass-on frozen GBP/USD intraday = 152 trades, headline WR 52.0%, expectancy −0.23R, SQN −3.07 — matches the July-11 rerun.

**Measured cost of the bypass (GBP/USD intraday, frozen):**

| | trades | WR | expectancy | total R | planned TP1 RR mean / %<1R |
|---|---|---|---|---|---|
| Gates bypassed (current env) | 152 | 52.0% | **−0.23R** | **−34.8R** | 0.90 / 66% |
| Gates enforced | 42 | 57.1% | **+0.05R** | +1.9R | 1.10 / 50% |

The bypass admits ~110 extra trades worth −36.7R. Any RR/expectancy measurement made while this flag is on is a measurement of an un-gated population.

## 1. Engine B defects

### B-1 (HIGH) — the RR floor gate never protects TP1
`rr_used_for_gate` = `_exec_rr` (`market_structure.py:2819`), and in scale-out plans `_exec_rr` is recomputed from the **TP2 runner** (`_exec_tp = _plan_tp2`, 2761-2775). The min_rr gate (5400-5401; forex intraday min_rr 1.3, config.yaml:3638) therefore checks TP2's RR. Planned TP1 RR (`execution_rr1`) is only ever floored at **`ENGINE_B_TP1_MIN_RR = 0.30`** (config.py:1373; no yaml override) — in the scale-out guard (2672-2675), the TP1-clamp keeper (1010-1016), and the scale-out space branch (5533-5548). **A TP1 at 0.3–0.8R passes every gate**; this is the mechanism behind "average planned TP1 RR 0.80". The 2026-07-11 TP1-clamp fix (11e6e3ca) re-targets overshooting TP1s to the wall front edge but still only requires the clamped TP1 to clear 0.30R.

Frozen quantification (intraday, gates enforced): **the sub-1R TP1 population is entirely the scale-out plans.**

| Pair | n | scale-out n | scale-out TP1 RR mean / %<1R | single-TP TP1 RR mean / %<1R |
|---|---|---|---|---|
| GBP/USD | 42 | 24 | 0.62 / 88% | 1.75 / 0% |
| EUR/USD | 35 | 16 | 0.59 / 94% | 1.75 / 0% |
| USD/JPY | 38 | 21 | 0.58 / 91% | 1.76 / 0% |

Roughly half of all gate-passing forex intraday signals carry a TP1 planned at ~0.6R — TP1 nearly half as far as the stop — and the RR gate never sees it. (In the bypassed population it's worse: 111/152 GBP trades scale-out, TP1 RR 0.63, 90% sub-1R, median planned TP1 RR of the whole run 0.75 — the source of your "0.80 average".)

### B-2 (reclassified: symptom, not defect) — unconditional BE-after-TP1 is currently protective
`ENGINE_B_BT_MOVE_SL_TO_BE_AFTER_TP1` fires the instant TP1 fills, with no minimum TP1 distance guard (`backtest_runner.py:5755-5760`; the single-TP BE path *does* have guards, 5830-5845). This produces the BE pile-up (42/152 BE exits in the parity run; realized ≈ 0.5×rr1 ≈ 0.15–0.2R) and the ~0.3R median win. **However, the counterfactual shows removing it makes everything worse** — the instant BE saves more in avoided runner stop-outs than it forfeits in upside:

| Pair (gates on) | baseline expR | BE-off expR | delta |
|---|---|---|---|
| GBP/USD | +0.05R | −0.02R | −0.06R |
| EUR/USD | −0.12R | −0.23R | −0.12R |
| USD/JPY | −0.31R | −0.37R | −0.06R |

The early BE is a rational adaptation to sub-1R TP1s (B-1): with a 0.6R TP1, the runner has poor odds and BE caps its damage. Fix B-1 first; with a TP1 floor of 1.0 the BE path nearly empties on its own (2 BE exits vs 8 in the GBP floor run). Do **not** ship a BE guard as a standalone change.

### B-3 (MEDIUM) — forex space gate wide open
`ENGINE_B_FOREX_RR_CAN_SATISFY_SPACE_GATE: true` (config.yaml:405) with the RR-substitution ATR floor **disabled** (`ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED: false`, config.yaml:423). Runner RR alone can substitute for room near an opposing wall (subject to `tp1_path_clear`). Combined with B-1 this admits forex trades whose reachable target is far below 1R.

### B-4 (MEDIUM) — win/BE classification inconsistent across surfaces
`_format_backtest_results` counts wins by label — `TP1`/`TP2` outcomes plus positive TIMEOUTs (`backtest_runner.py:4246-4257`); positive-R `BE` exits are neither win nor loss but stay in the denominator (deflating WR and profit factor). Other surfaces (1310-1311, 1713, 1784) count wins by `resultR > 0`. Engine A V3 counts by sign (`engine_a_v3/backtest.py:148`). Cross-surface and cross-engine win rates are not comparable. (Note: contrary to the audit hypothesis, BE exits do **not** inflate the headline win rate — if anything positive BE-runners are under-credited.)

### B-5 (LOW) — config default drift
`ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED`: code default **True** (`market_structure.py:5572`) vs **False** in config.py:1442 and config.yaml:423. Effective behavior is floor-off, but a deployment missing the key silently flips it.

### Engine B baseline (gates enforced) and the TP1-floor counterfactual

| Scenario | Pair | n | WR | expR | total R |
|---|---|---|---|---|---|
| baseline | GBP/USD | 42 | 57.1% | +0.05R | +1.9R |
| baseline | EUR/USD | 35 | 45.7% | −0.12R | −4.0R |
| baseline | USD/JPY | 38 | 42.1% | −0.31R | −11.9R |
| **`ENGINE_B_TP1_MIN_RR` 0.30 → 1.0** | GBP/USD | 37 | 51.4% | **+0.10R** | **+3.9R** |

The TP1 floor is the strongest single intervention measured: it barely reduces trade count (42→37, because the tighter-SL retry at `market_structure.py:2675-2723` re-plans most scale-outs instead of dropping them), lifts mean planned TP1 RR from 1.10 to 1.47 (only 2.7% remain sub-1R), and doubles GBP expectancy. **Caveat:** one pair, one style, one frozen window, n=37 — this is a candidate for proper walk-forward calibration (the existing `athena_research/calibrate_engine_b_min_rr.py` harness sweeps exactly this family of floors), not a shippable result. EUR/USD and USD/JPY baselines are negative regardless; a TP1 floor does not by itself create edge there.

Engine B SL geometry (for completeness): mean stop distance 0.54–0.63% of price, max 1.5%, zero trades near the 2.5% forex MAX_SL_PCT cap — the cap is not shaping forex intraday brackets.

## 2. Engine A (V3) findings

### A-1 (HIGH) — 1R planned TP with tight stops makes fixed costs enormous in R; effective bracket is ~+0.66R / −1.30R
Structural levels plan TP1 at **exactly 1R** (`engine_a_v3/levels.py:77/91`) and the shipped exit policy is `SINGLE_TP1` (full exit at TP1, `profile.py:247`, `backtest.py:116-117`), so TP2 (adaptive 1.5–2.5R) is computed but never realized. Stops are structural with a 0.8-ATR minimum — on H1 forex that is **0.29–0.35% of price on average** (frozen measurement; max 0.84%, zero trades near the 2.5% MAX_SL_PCT cap). Against stops that tight, the flat V3 cost model (2bp spread + 2×1bp commission + 2×1bp slippage + 0.5bp/day swap, `config.py:1866`) costs **0.26–0.46R per round trip**, measured directly from frozen trades:

| Pair | TP1 winners realize | SL losers realize | Implied cost |
|---|---|---|---|
| GBP/USD | +0.59R | −1.30R | 0.30–0.46R |
| EUR/USD | +0.69R | −1.33R | 0.30–0.33R |
| USD/JPY | +0.73R | −1.26R | 0.26–0.27R |

Effective RR after costs ≈ **0.5** → breakeven win rate ≈ **66%**. Even USD/JPY at 54.5% WR nets −0.09R expectancy; GBP/USD (27.8% WR) nets −0.64R. This — not the SL/TP formula being "wrong" per se — is the core Engine A forex RR problem. (The 2bp spread assumption is itself ~2× a realistic GBP/USD spread; but even at half, the bracket is ~+0.85R/−1.15R, breakeven ≈ 57%.)

### A-2 — SPLIT_50_50 does not rescue expectancy (counterfactual)
Forcing SPLIT_50_50 in `_simulate_exit`: GBP −0.67R (vs −0.64), EUR −0.37R (vs −0.35), JPY −0.07R (vs −0.09). MFE beyond TP1 on TP1-winners averages only +0.21 to +0.35R and only 7–33% of winners reach 1.5×TP1 — there is no large runner being forfeited at H1 intraday. Exit-policy change alone is not the fix; cost-vs-stop-width and directional quality are.

### A-3 (MEDIUM) — dead SL-floor knob
The 0.35-ATR "floor" (`levels.py:69-71, 83-85`) can never bind: LONG takes `min(invalidation, current − 0.35·ATR)` after invalidation is already ≤ `current − 0.8·ATR`. `ENGINE_A_V3_STRUCTURAL_SL_FLOOR_ATR_MULT` and `ENGINE_A_STRUCTURAL_SL_FLOOR_ATR` are inert; the true minimum stop is 0.8 ATR. Either implement honestly (widen too-tight structural stops needs max-distance logic) or remove the knob.

### A-4 (LOW, confirmed immaterial for forex intraday) — mean-reversion sub-1R TP1s admitted when gates bypassed
`_build_levels` zeroes the MR min_rr guard whenever `tp_sl_rr_gates_disabled` (`evaluator.py:182-199`). Currently active given the local-config flag. **Measured impact: zero** — the frozen intraday forex population contains no `mean_reversion` setups (all trades are `fx_trend_pullback` / `quant_trend` / `fx_session_breakout_retest`); gates-on and gates-off runs were identical. Latent hazard for swing/other groups, not a current forex driver.

### A-5 (LOW) — MAX_SL_PCT never reconciled with V3 geometry
The 2.5% forex cap is applied downstream (risk/execution), not in level construction. Measured: zero frozen intraday trades within reach of the cap (max stop 0.84%). Real but currently immaterial for forex intraday.

## 3. Shared / accounting notes

- **Forex slippage in the legacy/Engine B cost path is fractional-of-price, not pip-true**: `_BASE_BACKTEST_SLIP["forex"] = 0.0001` (`backtest_runner.py:1009`) → ~1.1 pips effective on EUR/USD but ~1.5 pips on USD/JPY. True pip/JPY handling exists only in `athena_fx/backtest.py:104`.
- **V3 crypto costs understated** (no `BY_ASSET` override in `ENGINE_A_V3_BACKTEST` → crypto rides forex-grade 1bp commission vs FEE_PCT crypto 11bp). Out of scope for forex; noted for completeness.
- **V3 applies no fill-price slippage** (all friction via bps `cost_r`; entry clamped into zone, `engine_a_v3/backtest.py:416-420`) while legacy/Engine B slip the fill — the two "Engine A" cost models are not parity.

## 4. Verified NOT defects

- ATR: Wilder-14 on the entry timeframe, confirmed candles only, both engines; no pip/JPY scale bug in level math (R is a unitless price ratio).
- LONG/SHORT bracket construction is mirror-symmetric in both engines.
- Same-bar TP+SL resolves adverse (SL-first) in all backtesters.
- Costs ARE applied to forex backtests (fee + slippage in legacy/B; bps model in V3).
- BE exits do not inflate headline win rate (see B-4 — the error direction is under-crediting).

## 5. Incidental safety finding — backtest bootstrap starts the broker exit thread

Executing `athena.py` by file path to initialize the backtest runtime (the documented pattern in `engine_b_batch.py:_worker_init` and `tools/run_backtest_matrix.py:_load_athena_module`, whose docstrings claim "no broker threads") **starts the TimedExitMonitor broker thread**: `athena.py:6060` calls `_auto_trader.configure(...)` at module level, which calls `start_monitor()` unless `ATHENA_DIAGNOSTIC_MODE` is set (`auto_trader.py:850`). Observed live during this audit: a research process attempted to close 4 open MT5 demo tickets every ~15s (all failed only because the market was closed — Saturday). `scripts/diagnostics/*` set the flag; the batch tools do not. **Fix is one line in each bootstrap.** Flagged as a separate task.

## 6. Recommended fixes — application status (2026-07-12)

Mechanical / accounting (low risk):
1. **B-4** — ✅ APPLIED. Unified win classification to `resultR > 0` (`backtest_runner.py:_format_backtest_results`), matching the convention at lines ~1310/1713/1784. Positive-R BE exits now count as wins; this surface's win rate / profit factor are now comparable to the others.
2. **B-5** — ✅ APPLIED. Aligned the `..._MIN_ATR_FLOOR_ENABLED` code fallback default to `False` (`market_structure.py:5572`) so a missing key can no longer silently enable the floor. Zero runtime effect at current config (key is defined False in both config.py and config.yaml); removes the latent hazard.
3. **A-3** — ⏸ HELD. The audit gave a fork ("remove **or** honestly implement"), and the knob lives in Engine A SL construction and is config-reachable (setting the mult > 0.8 would make it bind). Deleting it removes a user-reachable stop-widening capability, and "implement honestly" is a real SL-widening feature — both are SL-semantics decisions, not clean accounting. Needs an explicit call. Inert at default (0.35 < 0.8), so no urgency.
4. **§5** — ✅ APPLIED. Set `ATHENA_DIAGNOSTIC_MODE` (via `os.environ.setdefault`) before `exec_module` in both `engine_b_batch._worker_init` and `run_backtest_matrix._load_athena_module`, so the backtest bootstrap no longer starts the TimedExitMonitor broker thread.
5. Pip-true forex slippage — ⏸ HELD. No shared pip-size helper exists in the core cost path; a correct fix must detect JPY vs non-JPY and convert per-pair, and it shifts **every** forex backtest cost number (governance-relevant). Deserves a measured before/after on frozen data rather than a hand-rolled heuristic in this batch.

Scoring / gate changes (need explicit approval, then walk-forward validation):
6. **Environment (done first)** — ✅ APPLIED. Removed `DISABLE_TP_SL_RR_GATES: true` from `config.local.yaml` (a machine-local, gitignored line added 2026-07-11). Measured cost on GBP/USD alone: −36.7R across 110 extra trades. It had un-gated demo execution and every backtest, poisoning any RR measurement made while on. Removing a config line the audit added is not a scoring change. **Restart Flask** for the app to pick up the change.
7. **B-1**: floor planned TP1 RR — raise `ENGINE_B_TP1_MIN_RR` toward 1.0 (or make the min_rr gate check `execution_rr1` for scale-out plans). Measured on GBP/USD: +0.05R → +0.10R expectancy with only 5 fewer trades. Calibrate the exact floor via `athena_research/calibrate_engine_b_min_rr.py` walk-forward before shipping; validate on EUR/USD and USD/JPY, which stay negative and need more than this.
8. **B-2**: do NOT change BE-after-TP1 in isolation — the counterfactual shows it's protective. Revisit only after B-1 lands.
9. **B-3**: re-enable `ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED` for forex (largely subsumed by a TP1 floor; low priority after #7).
10. **A-1**: the Engine A forex intraday bracket needs a structural decision — wider stops / smaller cost-to-risk ratio, TP beyond 1R, or per-group cost-aware RR floors. No single knob fixes it (SPLIT_50_50 measured: no improvement); propose calibrating on frozen data before any change.

## Appendix — run provenance

- Scripts + per-trade JSON evidence copied to `tmp/audit_engine_{a,b}_rr_20260712.{py,json}`.
- Engine A: `run_v3_backtest`, horizon=intraday (H1 primary), costs 2/1/1/0.5 bps, KEEP H1=2800 bars, registry `demo_unvalidated_registry()`.
- Engine B: `backtest_pair_naked(style="intraday")` via athena.py runtime bootstrap with `ATHENA_DIAGNOSTIC_MODE=1`; scenario CONFIG overrides applied in-process and restored.
- Not verified: live scanner parity (no live services run); swing style (not measured); non-forex groups.
