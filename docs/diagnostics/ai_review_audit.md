# AI Review Layer Audit — Sentinel Pro v4.0

**Date:** 2026-04-25  
**Scope:** All AI integration points, input completeness, prompt quality, output contract, execution safety, chart/vision path  
**Method:** Full static analysis of `athena.py`, `engine_b_ai.py`, `signal_debate.py`, `vision_prompts.py`, `engine_c.py`, `auto_trader.py`, `execution.py`, `config.yaml`

---

## 1. AI Integration Points Found

| # | Name | File | Route | Provider | Model Key | Input | Impact |
|---|------|------|-------|----------|-----------|-------|--------|
| 1 | **Marcus Reid (Engine A)** | `athena.py` `_send_to_ai()` | `POST /api/pair-scan`, `POST /api/scan` | xAI | `AI_MODEL` | Text: signal dict → `_build_signal_message()` | Advisory only — grade/narrative for UI display |
| 2 | **Engine B AI** | `engine_b_ai.py` `get_engine_b_ai_verdict()` | Called inside `_compute_naked_analysis()` | xAI | `AI_MODEL` | Text: `build_engine_b_signal_message()` | Advisory only — grade/edgeProbability/riskLevel enriched on signal dict |
| 3 | **Chart Vision** | `athena.py` `/api/chart-analysis` | `POST /api/chart-analysis` | xAI | `VISION_MODEL` | Image (PNG base64) + structured context string | Via `apply_vision()`: can set `trade=True` (CONFIRM) or `trade=False` (CONTRADICT/AVOID) |
| 4 | **Signal Debate** | `signal_debate.py` `run_signal_debate()` | Called in `auto_trader._can_execute()` | xAI | `DEBATE_MODEL` | Text: signal dict context (score, factors, entry/SL/TP) | **Gates execution**: PASS/STRONG_AVOID → `allowed=False` → `_can_execute` returns False |
| 5 | **News Sentiment** | `news_sentiment_feed.py` | `GET/POST /api/news-sentiment` | xAI | `NEWS_SENTIMENT_MODEL` | Text: EODHD news headlines, sentiment scores | Advisory context only — injected into Engine B AI narrative; never affects pass/fail |
| 6 | **Lottery AI** | `athena.py` `/api/lottery/ai-analysis` | `POST /api/lottery/ai-analysis` | xAI | `LOTTERY_AI_MODEL` | Text: draw history + analytics | Not trading-related |

---

## 2. Input Packet Completeness

| AI Call | Engine A | Engine B | Engine C | Engine D | Entry/SL/TP/RR | Warnings | Candle Timestamps | Freshness Status | Forming Bar Flag |
|---------|----------|----------|----------|----------|----------------|----------|-------------------|-----------------|-----------------|
| Marcus Reid | ✅ Full factorScores/weights/diagnostics | ✅ eng_b overlay | ❌ | ❌ | ✅ | ✅ | ❌ **GAP** | ❌ **GAP** | ❌ **GAP** |
| Engine B AI | ✅ Cross-check only (when compare mode) | ✅ Structure/confidence full | ❌ | ❌ | ✅ | ❌ | ❌ **GAP** | ❌ **GAP** | ❌ **GAP** |
| Chart Vision | ✅ Direction/score/regime | ✅ Swing/BOS/FVG/OBs/zones | ❌ | ❌ | ✅ (if signal available) | ❌ | ❌ **GAP** | ❌ **GAP** | ❌ **GAP** |
| Signal Debate | ✅ Score/factors/regime | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ **GAP** | ❌ **GAP** | ❌ **GAP** |
| News Sentiment | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

**Summary:** No AI call receives candle timestamps, bar freshness status, or forming-bar indicator. This is a gap in AI grounding — AI cannot self-detect stale data.

**Mitigation (existing):** `api_execute()` runs `SIGNAL_MAX_AGE_SEC` freshness gate independently of all AI calls. AI cannot bypass this gate.

---

## 3. Prompt Quality Assessment

### 3.1 Marcus Reid (`EXPERT_PROMPT`)
**Rating: GOOD**

✅ Evidence-only: Rules 2–4 explicitly forbid unsupported claims and require citing specific data points  
✅ No "will/guaranteed/definitely"  
✅ Every claim must reference factor name/score/weight  
✅ Counter-trend = automatic grade drop (Rule 5)  
✅ Direction flip detection (Rule 6)  
✅ Critical cross-checks mandated: momentum divergence, confidence < 0.5, trendCoherence < 0.7  
✅ JSON-only output enforced  
✅ edgeProbability formula provided (not free-form)  

⚠️ No explicit "missing data = REVIEW_INCOMPLETE" directive  
⚠️ No explicit "stale candle = reject review" directive  
⚠️ No "contradictions" list field in output schema  
⚠️ No "missing_information" list field in output schema  
⚠️ `should_execute` field absent from output schema (by design — advisory only)  

### 3.2 Engine B AI (`expert_prompt` in `get_engine_b_ai_verdict`)
**Rating: GOOD**

✅ Evidence-only: "Focus ONLY on structure and liquidity evidence"  
✅ Graded rubric: A+ to F with explicit criteria  
✅ Forced JSON output: `response_format={"type": "json_object"}`  
✅ Style-specific ratings for SCALP/INTRADAY/SWING  
✅ Cross-engine conflict flagged explicitly  

⚠️ No missing-data detection directive  
⚠️ No stale-data = REJECT directive  
⚠️ No contradictions list field  
⚠️ No `should_execute` field (correct — advisory only, but makes the contract implicit)  

### 3.3 Signal Debate (`_get_debate_case` / `_get_judge_verdict`)
**Rating: ADEQUATE with one concern**

✅ Adversarial format reduces single-perspective bias  
✅ Judge role is impartial  
✅ `grade` constrained to `STRONG_GO | WEAK_GO | PASS | STRONG_AVOID`  
✅ Structured output via Pydantic schemas (`DebateCaseResponse`, `JudgeVerdictResponse`)  
✅ Fail-open on exception (does not halt execution)  

⚠️ `score_adjustment` range is `-1.0 to +1.0`. **Positive adjustment can increase `confluenceScore`.**  
   Safety analysis: The conviction gate in `_can_execute()` runs **before** debate (line 631 vs line 740). Signals below the gate never reach debate. Positive adjustment on an already-passing signal is cosmetic — re-check confirms signal still must be above minimum after adjustment. **Not immediately dangerous but architecturally unclear.**  
⚠️ No freshness metadata in context string  

### 3.4 Chart Vision Prompts (`vision_prompts.py`)
**Rating: GOOD**

✅ Image-first workflow mandated  
✅ No invented patterns: "Only describe what is visible"  
✅ Advisory language: "no guarantees, no certainty language"  
✅ A-E Framework enforces structured right-edge reading  
✅ Structured footer required (`RIGHT EDGE: CONFIRMS|REVIEW|POTENTIAL REVERSAL`, etc.)  
✅ Entry quality rules (Rules 9–12)  
✅ FINAL VERDICT constrained to `HOLD / ADJUST / CLOSE`  

⚠️ No candle timestamp or freshness status in context string  
⚠️ Stale chart image not explicitly rejected at prompt level (relies on caller to send fresh image)  

---

## 4. Output Schema Validation

### Current state
Each AI call uses its own schema. There is **no unified `ai_review_state` schema** across all AI paths.

| AI Call | JSON enforced | Schema validated | Required fields checked | Fallback on missing fields |
|---------|---------------|-----------------|------------------------|---------------------------|
| Marcus Reid | ✅ JSON-only | Partial (key presence) | Partial | Safe defaults |
| Engine B AI | ✅ `json_object` format | ✅ `_validate_engine_b_ai_payload` | ✅ | ✅ Safe defaults |
| Signal Debate | ✅ Pydantic schemas | ✅ Grade normalized | ✅ | ✅ PASS default |
| Chart Vision | ✅ Footer parsing | ✅ `_extract_vision_structured()` | Partial | ✅ defaults |

**Gap:** No unified `ai_review_state: CONFIRM|CAUTION|REJECT|REVIEW_INCOMPLETE` field exists across AI calls. The new `ai_review_logger.py` provides mapping functions (`map_debate_grade_to_ai_state`, `map_engine_b_grade_to_ai_state`, `map_vision_rating_to_ai_state`) to normalize this retroactively.

---

## 5. Execution Safety Analysis

### 5.1 Can AI affect execution?

| AI Call | Can block execution? | Can enable execution? | Can increase position size? |
|---------|---------------------|----------------------|-----------------------------|
| Marcus Reid | ❌ Advisory only | ❌ | ❌ |
| Engine B AI | ❌ Advisory only (Hard Rule 17) | ❌ | ❌ |
| Chart Vision (`apply_vision`) | ✅ CONTRADICT/AVOID → `trade=False` | ✅ **CONFIRM + ≥0.35 → `trade=True`** | ❌ (sizing via conviction tier, not direct) |
| Signal Debate | ✅ PASS/STRONG_AVOID → `allowed=False` | ❌ (cannot set below-threshold signal to pass) | ❌ |
| News Sentiment | ❌ Advisory only | ❌ | ❌ |

### 5.2 Execution safety gates that AI cannot bypass

All of the following run **independently** of AI output:

1. **Signal freshness** — `api_execute()` checks `SIGNAL_MAX_AGE_SEC=300`. Stale signals are re-analyzed or rejected before any AI call result is applied.
2. **`risk_check()`** — kill switch, drawdown, position sizing. Runs in `api_execute()` after freshness check, after all AI.
3. **Paper mode** — `REAL_ORDERS_ALLOWED=false` enforced at execution layer in `mt5_executor.py` / `bybit_executor.py`.
4. **`_validate_exit_levels()`** — SL/TP sanity check; emergency close if invalid.
5. **`MAX_SL_PCT`** — per-asset SL distance gate in `_can_execute()`, runs before debate.
6. **Sentiment/Event risk gates** — `SENTIMENT_GATE_ENABLED`, `EVENT_RISK_ENABLED`, run before debate.

### 5.3 One architectural concern

**Signal Debate `score_adjustment` (positive values):**

The judge can return `score_adjustment` up to +1.0. This modifies `signal["confluenceScore"]` in-place. However:
- The conviction gate runs at line 631 in `auto_trader._can_execute()` **before** the debate (line 740).
- Therefore, a sub-threshold signal is rejected before debate runs.
- Positive adjustment on an already-passing signal does not enable a blocked trade.
- After adjustment, the signal must still pass: `if signal["combinedConviction"] < auto_min_conviction: return False`.

**Verdict:** Not exploitable for unsafe execution given the current gate order. However, the `score_adjustment` positive range is architecturally ambiguous — if the gate order ever changes, this becomes a vector. Recommend clamping `score_adjustment` to `min(score_adjustment, 0.0)` (downgrade-only) on the next safety review pass.

### 5.4 Vision `trade=True` upgrade

Per CLAUDE.md: `CONFIRM + conviction≥0.35 → trade=True` is **intentional design** for Engine C Vision confirmation path. This is the only sanctioned AI upgrade. It is guarded by:
- The conviction threshold (≥0.35)
- The subsequent `api_execute()` freshness + `risk_check()` gates

---

## 6. Chart / Vision Path Assessment

**Chart creation:**
- Server-side: `chart_renderer.render_chart_image()` when `server_render=True` — generates fresh PNG from candle data passed in request
- Client-side: `html2canvas` in browser — screenshot of rendered chart

**Chart metadata checks:**
- Server renderer: candles, entry, SL, TP, Engine B overlays (FVG/OB/BOS/CHoCH/SR) all included ✅
- Chart title includes symbol + timeframe ✅
- Engine B structural context injected as text alongside image ✅

**Gap:** Neither path explicitly passes `last_candle_timestamp` to the Vision API call. The chart image shows candles visually (timestamps on x-axis), but the context string does not contain a machine-readable `last_candle_ts` field.

**Gap:** No "image freshness" check — a cached or old image from browser could theoretically be submitted. No timestamp is validated against current time at the `/api/chart-analysis` endpoint.

---

## 7. Missing Data Issues

| # | Issue | Severity | Mitigation |
|---|-------|----------|------------|
| 1 | No candle timestamps in AI context (all calls) | Medium | `api_execute()` freshness gate is independent |
| 2 | No freshness status field in AI context | Medium | Same mitigation |
| 3 | No forming-bar flag in AI context | Low | AI has no execution permission; this is informational |
| 4 | No unified `ai_review_state` schema | Low | Mapping functions added in `ai_review_logger.py` |
| 5 | No JSONL audit trail before this PR | Medium | `ai_review_logger.py` provides this now |
| 6 | Vision image freshness not timestamp-validated | Low | Server-render mode always generates from current data |
| 7 | Debate `score_adjustment` can be positive | Low | Gate order prevents sub-threshold exploitation |

---

## 8. Is AI Currently Useful, Dangerous, or Neutral?

### Marcus Reid (Engine A analysis)
**Useful, Safe**  
Provides rich trade narrative with mandatory evidence citations. Does not affect execution. Visible in UI only.

### Engine B AI
**Useful, Safe**  
Grade/edgeProbability enriches Engine B signal for human review. Hard Rule 17 correctly classifies this as advisory-only. Does not affect pass/fail checklist.

### Chart Vision (`apply_vision`)
**Useful, Conditionally Safe**  
The only AI that can directly influence `trade` flag. Designed to: confirm (upgrade to trade=True) or contradict (downgrade to trade=False). Risk_check still runs after. The design is intentional and documented. The upgrade path is the only architectural exception to "AI cannot enable execution."

### Signal Debate
**Useful, Mostly Safe — one concern**  
Effectively a pre-execution advisory layer. PASS/STRONG_AVOID correctly blocks. Error fails open (intentional). The `score_adjustment` positive range is the only concern (see §5.3).

### News Sentiment
**Neutral, Safe**  
Advisory narrative context only. Never affects pass/fail.

---

## 9. Recommended Changes

### Priority 1 — Add candle timestamp to AI context (diagnostic only, no execution change)
Add a `candle_last_bar_ts` field to `_build_signal_message`, `build_engine_b_signal_message`, and the debate context string. This lets AI detect staleness and include it in narrative warnings.

### Priority 2 — Clamp `score_adjustment` to ≤ 0.0 in debate
In `auto_trader._can_execute()` around line 754:
```python
_adj = min(0.0, debate.get("score_adjustment", 0.0))  # downgrade-only
```
This makes the debate a pure safety gate (can reduce score, cannot increase it).

### Priority 3 — Add image timestamp validation in `/api/chart-analysis`
Warn (not block) if `server_render=False` and no `chart_timestamp` field is present. This surfaces potentially stale browser screenshots.

### Priority 4 — Add `candle_freshness_status` field to Vision context string
Include `last_candle_ts` in the algo context string so Vision can flag "candle data may be stale" in its narrative.

### Priority 5 — Wire `ai_review_logger.log_ai_review()` into all 4 AI call sites
Current state: logger module exists but is not called from production code. Add calls in:
- `athena.py` `_send_to_ai()` (Marcus Reid)
- `engine_b_ai.py` `get_engine_b_ai_verdict()`
- `athena.py` `/api/chart-analysis`
- `auto_trader.py` `_can_execute()` debate block

---

## 10. Final Verdict

| Question | Answer |
|----------|--------|
| Is AI review **safe**? | **YES** — AI cannot bypass risk_check, freshness gate, paper mode, or kill switch. The only execution-capable AI path (Vision) is correctly guarded by conviction threshold and downstream risk_check. |
| Is AI review **useful**? | **YES** — Marcus Reid provides evidence-based narrative. Engine B AI grades structural setups. Vision confirms or contradicts chart alignment. Debate provides adversarial pre-execution review. |
| Is AI review **grounded enough**? | **PARTIALLY** — Input packets are rich in engine scores, factors, and levels, but lack candle timestamps and freshness metadata. AI cannot self-detect stale context. The execution-layer freshness gate compensates. |
| Should AI be allowed to **affect execution**? | **YES, with current design** — Vision (downgrade + controlled upgrade), Debate (downgrade only for PASS/STRONG_AVOID). Both have independent safety gates below them. |
| Should AI be **commentary-only, downgrade-only, or execution-confirming**? | Current design is: Marcus Reid = **commentary-only**; Engine B AI = **commentary-only**; Debate = **downgrade-only**; Vision = **downgrade + one controlled upgrade**. This is a reasonable architecture. Recommend making Debate **downgrade-only** (clamp score_adjustment ≤ 0). |

---

*Audit conducted 2026-04-25. Files reviewed: `athena.py`, `engine_b_ai.py`, `signal_debate.py`, `vision_prompts.py`, `engine_c.py`, `auto_trader.py`, `execution.py`, `config.yaml`, `ai_review_logger.py` (new).*
