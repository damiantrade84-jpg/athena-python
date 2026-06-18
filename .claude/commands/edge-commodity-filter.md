---
description: Design + walk-forward-validate a commodity-specific entry filter for Engine A v3 on the rescaled scorer. Evidence-first; one config-gated default-OFF change only after OOS proof.
argument-hint: "[optional: max_hold_bars or cost override]"
---

# /edge-commodity-filter — Capture the commodity edge in Engine A v3

Single-pass research task. Goal: turn the KNOWN commodity edge (pooled SQN +1.33,
ranging-regime +2.52 on the rescaled v3 scorer) into a **deployable, config-gated,
walk-forward-validated** entry filter — or prove no robust filter exists. The edge
is real; a naive single-threshold gate already failed. Find a filter that survives
out-of-sample, or report that none does.

**Arguments:** `$ARGUMENTS` — optional backtest knob overrides (else use defaults below).

## Known facts — do NOT re-derive (verified 2026-06-18, this session)

See memory `project_engine_a_v3_rescale_2026_06_18.md`.

- **Scorer is rescaled** (`engine_a_v3/quant_scorer.py` ~line 394): aligned-quality,
  no `alignment*quality` double-count. Confluence now spans ~0–2.5 honestly.
- **Thresholds are uniform 2.0** in `engine_a_v3/profile.py` `_BASELINE_THRESHOLDS`
  (NOT `config.yaml`). v3 reads `profile.trade_threshold`. Baselines UNVALIDATED.
- **Per-family edge (frozen 2026-05-30, demo-qualified, modest costs):**
  commodity SQN **+1.33** (the target), index +0.31, equity +0.26, crypto −0.44,
  forex −1.27. forex/crypto are EFFICIENT at H4 → out of scope, research-only.
- **`reverseResultR` / reverse-SQN is a DIAGNOSTIC ARTIFACT** (window truncated at
  original exit + mark-to-close, `engine_a_v3/diagnostics.py:72`). Never treat it
  as a tradeable flip.
- **Commodity edge is NON-MONOTONIC in efficiency:** eff<0.2 +0.38, eff[0.2–0.3)
  −0.22, eff[0.3–0.4) +0.30, eff[0.4–0.6) −0.04, eff[0.6+) −0.17. A single
  efficiency cutoff CANNOT isolate it — this was built, swept, and REVERTED. Do
  NOT retry a single-threshold efficiency gate.
- **Commodity has NO setup overlay** today — it runs the pure quant path
  (`score_pair`). The forex-only setup overlay is in `evaluator.py` ~line 232.

## Data constraint (read before planning)

Only ONE frozen layer exists: `data/frozen/2026-05-30/`. There is no second date.
"Multi-window validation" therefore means **walk-forward IS/OOS splits within the
~700 H4 bars per pair** and/or **held-out pairs**, NOT new dates. Do not attempt to
fetch live feeds. Commodity frozen pairs: `XAU_USD`, `XAG_USD` (precious_trackers),
`WTI_Oil` (energy_oil) — only 3 pairs, n≈67 pooled, so guard hard against overfit
(report IS vs OOS, enforce an n floor, prefer pair-holdout over in-pair splits).

## Reuse (do NOT rebuild harnesses)

- `tmp/diag_v3_edge_rescaled_20260618.py` — pooled SQN per group via
  `run_v3_backtest` + `demo_unvalidated_registry()`.
- `tmp/diag_v3_direction_20260618.py` — regime / efficiency stratification (each
  trade already carries `regime`, `efficiencyAtEntry`, `max_favorable_excursion_r`,
  `max_adverse_excursion_r`, `direction`, `outcome`).
- `tmp/verify_efficiency_gate_20260618.py` — off-vs-on / threshold-sweep pattern.
- Backtest entry point: `engine_a_v3.backtest.run_v3_backtest(pair, candles,
  horizon="swing", registry=demo_unvalidated_registry(), spread_bps=2,
  commission_bps=1, slippage_bps=1, swap_bps_per_day=0)`. Env to load CONFIG:
  `ATHENA_REAL_ORDERS_CONFIRM=I_UNDERSTAND_REAL_ORDER_RISK ATHENA_DIAGNOSTIC_MODE=1
  EXECUTOR_MODE=demo ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED=1`.
- Candle loader: glob `data/frozen/2026-05-30/candles/<SYM>__<TF>__*.json`,
  truncate (`D1`≤400/`H4`≤700/`H1`≤2800) to bound run_v3_backtest's O(n²) prefix.

## Candidate filters to test (because single-threshold efficiency failed)

Test as measurement first (stratify existing trades / re-backtest), pick the best
by OOS edge, THEN implement:

1. **Regime × direction interaction** — commodity loses in `neutral`
   (eff 0.2–0.3, −0.94 SQN) but wins in `ranging` AND `trending` tails. Test a
   filter that drops only the neutral mid-band, not all high-eff.
2. **Trend-alignment composite** — D1/H4 EMA-stack agreement + ADX, not raw
   efficiency. Does requiring D1 alignment lift commodity OOS?
3. **MFE/MAE or location-quality gates** — use `max_favorable_excursion_r` and the
   `location` component quality from `factorScores` as the discriminator.
4. **Per-subclass split** — precious (XAU/XAG) vs energy (WTI) may have different
   edge shapes; n is tiny, so treat as a robustness check, not a tuning axis.

## Procedure

1. Restate the operational goal + the IS/OOS split scheme you will use (state it
   before running, so success is falsifiable).
2. Re-confirm the +1.33 commodity baseline OOS under your split (sanity check the
   harness on current code).
3. For each candidate filter: measure IS edge, then OOS edge (held-out pair or
   later bars). Record n, winRate, expectancyR, SQN for IS and OOS separately.
4. Select the filter with the best OOS SQN that also (a) keeps n above the floor,
   (b) shows IS/OOS consistency (no large degradation), (c) does not harm index.
5. ONLY if a filter passes (4): implement it config-gated **default-OFF** in the
   v3 path (single `score_pair` code path → live==backtest parity preserved),
   re-run `tmp/verify_*` off-vs-on, run ONE targeted test
   (`tests/test_engine_a_v3_profiles.py`), and document projected impact. If none
   passes, report that and change nothing.

## Governance (hard constraints)

- Engine A is research-only; execution stays gated by `engine_a_trade_gate`. Do not
  touch trade-enable maps, evidence thresholds, freshness/kill switches.
- Any scorer/threshold change is hash-tracked by the profile framework
  (`profile.py` `scorer_sha256`/`profile_sha256`); keep `status: UNVALIDATED` — do
  NOT promote. Promotion is a user decision via `configs/engine_a_v3_validation.yaml`.
- Default-OFF only; no behavior change ships enabled without the user's call.
- Repo may still be mid-merge — work the working tree, do not resolve/commit the merge.

## Output contract

One final report:
1. **Split scheme** + baseline OOS commodity SQN.
2. **Candidate results table** — filter | IS (n/expR/SQN) | OOS (n/expR/SQN) | verdict.
3. **Selected filter** (or "none robust") with the OOS evidence and overfit caveats.
4. **Change made** (file:line, default-OFF) + test result + projected impact, OR
   "no code change — no filter survived OOS."
5. **Non-goals honored:** no forex/crypto direction, no single-threshold efficiency
   gate, no promotion, no execution-gate edits.

## Budget

- Reuse `tmp/` harnesses; bound backtests via candle truncation. Batched reads.
- At most ONE pytest file, post-implementation only (skip if no code change).
- One report at the end; no incremental narration.
