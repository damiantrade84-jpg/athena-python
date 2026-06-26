---
description: Single-pass Engine A strictness gate attribution for a fixed scan window. Diagnosis only — no code changes.
argument-hint: "[forex|crypto|commodity|index|stock] [window e.g. last-5d]"
---

# /diagnose-engine-a — Gate attribution for Engine A strictness

Single-pass diagnostic. Goal: for a fixed scan window, attribute exactly which
stage kills each Engine A candidate, so "Engine A is too strict" becomes a
named gate with a file:line, not a feeling.

**Arguments:** `$ARGUMENTS` — optional `[class] [window]`. When omitted, default to
**forex** and **last 5 trading days**.

## Known facts — do NOT re-derive these (verified 2026-06-11)

- Engine A is research-only for ALL classes: `config.yaml:523-528`
  (`ENGINE_A_TRADE_ENABLED_BY_CLASS` all false, AB6 reasons in comments).
  Therefore zero Engine A live trades is EXPECTED. Never report the
  trade-eligibility gate itself as "the bug".
- `ENGINE_A_ORTHO_VOTE_ENABLED: false` (`config.yaml:451`) — live scoring uses
  the legacy addon path in `factor_scoring.py` (`_carry_addon`, `_cot_addon`),
  NOT `_orthogonal_vote_vector`. Do not investigate ortho weights for live
  strictness unless the flag is true.
- `SCAN_QUANTILE_ENABLED: false` (`config.yaml:1541`) — quantile gate inactive.
- COT/carry historical backtest path is VERIFIED working via the frozen layer
  (`BACKTEST_DATA_AS_OF=2026-05-30`). Do not re-audit it.
- Evidence gate params: MIN_N 30, MIN_SQN 2.0, evidence map empty
  (`config.yaml:531-534`). These are user policy. Out of scope for changes.

## Attribution funnel (in score-path order)

For each candidate pair in the window, record the FIRST stage that removes it:

1. **Freshness / session gates** — pre-scoring blocks.
2. **Directional gate / ramp** — `ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS`
   (`config.yaml:542+`): min_directional 0.20–0.25 by class. Log the raw
   directional value vs floor.
3. **ADX trend multiplier** — `_adx_multiplier_from_value` in
   `factor_scoring.py`. Log adx, trend_min, hard_fail, resulting mult.
4. **Addon penalty** — carry/cot `_ADDON_AGAINST` hits on forex. Log addon
   status and contribution vs neutral.
5. **Conviction blend** — base_w + mom_w * mom_quality (+ addon term). Log
   mom_quality.
6. **Threshold tier** — the scoring.py profile/pair/group resolver + 3-tier
   fallback (BT_MIN deleted; live == backtest). Log effective threshold vs
   final score.
7. **Trade-eligibility gate** — expected kill for everything that survives
   1–6. Count it, do not flag it.

## Gate reference map (file:line anchors)

Use this table for the per-pair funnel `file:line` column — do not re-derive
architecture.

| Stage | Primary gate / logic | Config knob | Source anchor |
|-------|---------------------|-------------|---------------|
| 1 Freshness / session | Pre-scoring candle freshness block | `PRE_SCORING_FRESHNESS_GATE_ENABLED`, freshness policy in `athena_app.services.data_freshness` | `athena.py:13087-13228` (`pre_scoring_freshness`); session/exchange blocks via scanner skip paths |
| 2 Directional ramp | `min_directional_failed` hard abort + soft ramp | `ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS` (`config.yaml:542+`) | `factor_scoring.py:105-121` (`_resolve_directional_ramp`); abort `3156-3174` |
| 3 ADX multiplier | `_adx_gate` / hard abort | `ADX_TREND_MIN_CLASS`, hard-fail keys | `factor_scoring.py:1604` (`_adx_multiplier_from_value`); gate `3176-3196`; abort `3196` (`adx_hard_abort`) |
| 4 Addon penalty | carry/cot `_ADDON_AGAINST` hits | `FACTOR_ADDON_AGAINST_MIN` | `factor_scoring.py:328`; `_carry_addon_with_status` `2077+`; `_cot_addon_with_status` `2105+` |
| 5 Conviction blend | `base_w + mom_w * mom_quality (+ addon)` | `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS`, `FACTOR_CONVICTION_FLOOR` | `factor_scoring.py:3388-3436` |
| 6 Threshold tier | score vs resolved threshold | `ENGINE_A_SCORE_GROUP_THRESHOLDS`, pair/profile overrides | `scoring.py:410-458` (`get_score_threshold`); pass/fail `1399-1402` |
| 7 Trade eligibility | expected kill (count only) | `ENGINE_A_TRADE_ENABLED_BY_CLASS` (`config.yaml:523-528`) | `engine_a_trade_gate.py:76+` (`resolve_engine_a_trade_eligibility`) |

**Out of scope reminders:**
- Ortho path only when `ENGINE_A_ORTHO_VOTE_ENABLED: true` (`config.yaml:451`);
  live uses legacy addons in `factor_scoring.py` ~2659+.
- Quantile gate inactive (`SCAN_QUANTILE_ENABLED: false`, `config.yaml:1541`).
- Evidence params are user policy — do not flag as bugs.

## Procedure

1. Pick ONE window (e.g. last 5 trading days) and ONE class to start (forex —
   that is where the user feels it). Reuse existing scan/backtest telemetry if
   present in `tmp/` before generating new runs.

   **Telemetry reuse (check before new runs):**
   - `tmp/verify_gate_funnel_probe.json` — backtest funnel counts
     (`funnel_fail_score`, `funnel_fail_trade_enabled`, etc.)
   - `tmp/diag_majors_summary.json` — forex majors AB6 aggregate (SQN, exit
     breakdown)
   - `tmp/ab6_freeze_2026-05-30.txt` — frozen-layer reference date
   - Live scan: `calibration_diagnostics.py` →
     `logs/calibration_diagnostics/calibration_events.jsonl`
     (`failure_reason`, `abort_reason` for Engine A)
   - Engine B side-by-side: `scanner.py:1104+`
     (`_attach_engine_b_scan_gate_funnel`); primary scoring in
     `market_structure.py`

2. Produce a per-pair funnel table: pair | stage killed | value vs threshold |
   file:line of the gate.

3. Run the SAME window through Engine B (`market_structure.py`) and report
   signal counts side by side. A vs B must be compared at SIGNAL level, never
   live-trade level (see known facts).

4. Output one findings block:
   - Top 2 stages by kill count, with file:line and the config knob that owns
     each.
   - For each: is the gate behaving per spec (config intent) or is there a
     computation bug? Mark VERIFIED / SUSPECT / NOT REVIEWED with evidence.
   - Explicit statement: what threshold/knob change would release the most
     candidates, and what AB6/backtest evidence says about whether those
     released candidates historically won or lost.

5. NO code changes in this command. Diagnosis only. Fixes are a separate,
   user-approved session.

## Output contract

Deliver one final report (no incremental narration):

1. **Coverage map** — window, class, telemetry files read, pairs in funnel table.
2. **Per-pair funnel table** — `pair | stage_killed | value vs threshold | file:line | config_knob`.
3. **A vs B signal counts** — same window; compare signal/tier counts, not live trades.
4. **Findings** — top 2 kill stages, VERIFIED/SUSPECT/NOT REVIEWED, knob that
   would release most candidates + AB6 evidence for those candidates.
5. **Explicit non-goals** — no code changes, no pytest, no threshold edits.

**Not the same tool:** `tools/diagnose_engine_a_stops.py` analyzes closed-trade
stop-outs from `audit.db` — do not conflate with this gate-attribution command.

## Budget

- Batched greps + targeted file reads only; `athena.py` and other large files
  via offset/limit.
- Zero pytest runs (diagnosis, not fix).
- One findings report at the end. No incremental narration.
