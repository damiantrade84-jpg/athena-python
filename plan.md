# Narrow Implementation Plan — Valid Engine D Improvements (Second Audit Validated)

Date: post second-audit validation
Status: Ready for surgical implementation
User directive: "Ok just implement fixes that is valid and improve tool"
Standing constraint (verbatim): "Important Engine A and Engine B must not block each other as that is by design and that is current setup and should not be changed."

## Scope (strictly limited to 3 highest-ROI, lowest-risk items)
Only these from the validated 7:
1. Enrich HTF bias payload passed to Engine D with lightweight Engine A diagnostics (regime + ADX strength bucket + mom quality/conviction) — **data only**.
2. Promote CVD divergence (price extreme vs CVD delta) to an explicit numeric component inside the Engine D 0-100 ai_quality_grade model.
3. Add POC rotation bias (prior completed session POC shift vs current) as directional input into _classify_setup (MR vs continuation) and small grading contribution.

**Deprioritized (already strong coverage or higher risk):** proxy-vs-real weighting in aggression (heavy proxy-aware + TVQ + delayed-real caps already exist in ai_quality_grade + config 2026-05 section); microstructure into A (out of scope, would touch factor_scoring); regime weighting in A core (research-lab prerequisite).

## Hard Invariants (must hold after changes)
- Engine A and Engine B remain fully independent and non-blocking. No new call paths, no shared mutable state, no D logic that can suppress or gate A or B signals.
- Engine D (scalp_engine.py) stays a self-contained 3-pillar Fabio system. All new data fields are **additive / optional / advisory only**.
- Zero mutation of Engine A deterministic 3-factor scores, thresholds, or final_score.
- AI review (engine_d_context + prompt_builder) remains read-only advisory. Never grants execution.
- All behavior changes behind existing or new `SCALP_ENGINE.*_ENABLED` flags with safe legacy defaults (no change to current numeric grades or pass/fail when flags false or maps empty).
- Research-lab remains the future source of tuned per-group values; these changes only add the **mechanism** for richer D context.
- No edits to any execution.py, risk_engine.py, guardian.py, auto_trader.py, mt5_executor.py, bybit_executor.py (athena-audit routing not triggered because scope is D diagnostics + grading only).
- Targeted tests + manual Engine D parity only. No full test suite runs.

## Evidence Baseline (post-read)
- scalp_engine.py: Thin HTF = only `infer_bias_from_ema_stack` (EMA21/50/200 stack on H1, 5 pts max in ai_quality_grade). Rich fidelity already emitted (cvd_*, absorption_*, vp_*, data_fidelity, htf_bias str). _classify_setup already handles CVD direction + generic volume_divergence. volume_profile.py already provides `split_completed_sessions`.
- ai_scalp_review/engine_d_context.py: `build_engine_d_prompt_context` surfaces marketState, aggression{cvd,cvdSlope,absorption}, sourceContract real/proxy flags, htf_bias (simple). No Engine A fields today.
- ai_quality_grade: components include "htf_bias":0-5, "volume_divergence", proxy caps (GRADE_PROXY_*), TVQ. CVD currently contributes via alignment only (not explicit divergence delta).
- Config: SCALP_ENGINE has rich "Engine D additive grading knobs (audit 2026-05)" section + PROXY_AWARE_GRADING + VOLUME_DIVERGENCE_*. Precedent for safe additive maps exists.
- No A/B cross references in D files.

## Implementation Approach (surgical, minimum diff)
**For item 1 (HTF A enrichment):**
- In `run_scalp_scan` (crypto and MT5 branches), after building `pair_dict`, accept and pass through optional `engine_a_htf` (or `htf_engine_a`) from caller-provided pair metadata if present.
- After `htf_bias = infer...`, compute/attach sibling:
  ```python
  htf_engine_a = pair_dict.get("engine_a_htf") or pair_dict.get("htf_engine_a") or None
  ```
  (shape: {"regime": "trend|range|...", "adx_bucket": "strong|moderate|weak|none", "mom_quality": 0.0-1.0 or "high|med|low", "score": float|None, "source": "engine_a"} or None).
- Store in output signal: "htf_bias": htf_bias, "htf_bias_tf": ..., **"htf_engine_a": htf_engine_a**.
- In `ai_quality_grade(..., htf_engine_a=None)`: if htf_engine_a and aligns with setup_dir, add **tiny** configurable points (default 0 or 3 max, behind GRADE_HTF_ENGINE_A_POINTS or similar in the additive knobs section). Legacy 5pt EMA path unchanged.
- In `engine_d_context.py` `build_engine_d_prompt_context`: surface under `htfEngineAContext` (or `htfA`) so naked D review prompt sees authoritative A regime/ADX/mom as HTF context (data only).
- Caller side (future): scanner / ai routes can attach the lightweight snapshot from recent Engine A run on same symbol. D never calls A.

**For item 2 (CVD divergence numeric):**
- Extend `_detect_volume_divergence` or add lightweight `_detect_cvd_divergence(cvd_dict, price_loc, candles)` that returns {"divergence": bool, "type": "bullish|bearish", "strength": 0.0-1.0} using cvd["cvd_slope"] + price extremes vs CVD direction delta.
- In `_classify_setup`, capture `cvd_div = ...` and include in returned setup dict (alongside existing "volume_divergence").
- In `ai_quality_grade`: add component `"cvd_divergence": 0` (max +8 / -8 range, config via GRADE_CVD_DIVERGENCE_* similar to volume_divergence). Apply only when CVD source is real (respect existing proxy caps). Add to reasons.
- Default: no score change when new knob absent or CVD_PROXY.

**For item 3 (POC rotation bias):**
- In volume_profile usage sites or inside `_classify_setup` / new helper, use existing `split_completed_sessions` (or equivalent in VP computation) to compute `poc_rotation = "bullish"|"bearish"|"neutral"` (current POC vs prev session POC shift direction + magnitude).
- Pass `poc_rotation` into `_classify_setup(..., poc_rotation)`.
- Use in MR vs continuation decision as soft directional hint (e.g. rotation with impulse strengthens continuation).
- In `ai_quality_grade(..., poc_rotation)`: small additive "poc_rotation" component (0-4 pts) when rotation aligns with setup_dir. Config: GRADE_POC_ROTATION_BONUS (default 0 for zero behavior change today).
- Emit "poc_rotation" and "poc_prev" / "poc_current" in signal for context + AI review.

**Files touched (minimal):**
- `scalp_engine.py` (bias attachment + grading + classify extensions + tiny helpers)
- `ai_scalp_review/engine_d_context.py` (surface 1-2 new optional fields in prompt context)
- `config.yaml` (add 4-6 new additive keys under SCALP_ENGINE in the "additive grading knobs" section with strong comments + safe defaults)
- `plan.md` (this file — for record)

**No new files, no refactors, no docstring/comment additions beyond what's required for the patch.**

## Verification (targeted only)
1. Existing Engine D / scalp tests that touch grading, classify, run_scalp_scan (run via `pytest tests/test_*scalp* -q --tb=line` or equivalent targeted).
2. Manual parity: run a few live symbols through run_scalp_scan (with/without mock engine_a_htf attached) → confirm identical scores/grades when new fields absent or flags off.
3. Confirm via grep: zero new imports or calls from factor_scoring / scoring (except the pre-existing get_pair_score_group) into D, and zero D symbols referenced from A/B cores.
4. engine_d_context build still produces valid prompt context; snapshot fidelity fields untouched.
5. After edits: `git diff --stat` + manual review of changed hunks only.
6. All changes preserve A/B non-interference (obvious from scope).

## Success Criteria
- Existing numeric grades / pass/fail / htf_bias str behavior identical when new config keys absent/zero.
- When engine_a_htf supplied, it appears in signal and prompt context; may add ≤3-5 pts in grading (tiny, advisory).
- CVD divergence and POC rotation appear as optional enriched fields and small optional grade contributors.
- No execution path, risk gate, or A/B decision altered.
- "Engine A and Engine B must not block each other" remains true (no code changed that could affect it).

## Out of Scope (explicit)
- Any change to Engine A factor periods, scoring, research addon, or AI review payload for A.
- Any blocking or suppression logic between engines.
- Execution/risk/guardian files.
- Full backtest or research-lab integration (future work after these mechanisms exist).
- TV chart or legacy surfaces.

This plan follows the first-cycle precedent (plan.md + targeted verification + flag-gated additive only + A/B separation explicit) and Karpathy surgical/minimum-code rules. All diffs will trace directly to one of the three validated items.

Ready for implementation upon confirmation.