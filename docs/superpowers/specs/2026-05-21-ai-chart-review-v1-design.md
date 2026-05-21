# AI Chart Review v1 — Native chart + Claude Opus

**Date:** 2026-05-21
**Status:** Design approved, ready for implementation plan
**Scope:** Backend slice 1, then frontend slice 2 (single spec)

---

## 1. Goal

Add a read-only AI chart review flow that takes a native lightweight-charts PNG screenshot plus server-trusted Engine A diagnostics, sends both to Claude Opus via the Messages API, normalises the response, computes Engine-A-vs-Opus concordance, persists the review, and renders it in the native chart UI.

Review-only. Never connects to execution. Engine A scoring is untouched. Anthropic is the only active provider in v1; OpenAI is a stub.

## 2. Non-goals

- Modifying any Engine A / B / C / D scoring, thresholds, gates, kill switches, freshness checks, RR validation, or broker safety logic.
- Replacing the existing `/api/chart-analysis` route or `chart_renderer.py` (those continue serving the legacy `VisionReviewCard.tsx`).
- Dual-provider execution. Provider router accepts `dual` but rejects it unless explicitly enabled.
- Live Anthropic call in tests. All provider tests mock the SDK.
- Persisting full screenshot base64.

## 3. Decisions locked in brainstorming

| Decision | Choice |
|---|---|
| Staging | Single spec, vertical slice (backend first, then frontend) |
| Persistence | New SQLite table `ai_chart_reviews` in `audit.db` |
| OpenAI scaffold depth | Stub file with `NotImplementedError` |
| Engine A source-of-truth | Re-run scoring on demand per review |
| ATR freshness thresholds | Reuse existing `VISION_FRESHNESS_POLICY.max_age_sec` |
| Token controls | `MAX_TOKENS=1500`, `MAX_IMAGE_BYTES=2 MB`, frontend downscale to 1280×720, dedup window 60s |

## 4. Architecture

```
Frontend (TVChartPanel.tsx — native lightweight-charts)
   ├── existing Camera button captures PNG (already builds outputCanvas + toBlob)
   └── NEW "AI Review" button:
        1. capture PNG → downscale to ≤1280×720 → base64 data URL
        2. collect screenshot_meta (visible range, captured_at, overlays, native_chart=true)
        3. POST /api/ai/chart-review

Backend route /api/ai/chart-review  (athena_app/api/routes_ai_chart_review.py)
   ├── ai_review.validation         — ENABLED, PNG, MAX_IMAGE_BYTES, provider gating
   ├── ai_review.engine_a_context   — re-run Engine A → trusted snapshot
   ├── ai_review.freshness          — TF-aware ATR classifier
   ├── ai_review.timestamp_contract — captured_at vs scan_ts / latest_candle_ts
   ├── ai_review.payload_schema     — ChartReviewPayload assembly
   ├── ai_review.prompt_builder     — build_chart_review_prompt(context)
   ├── ai_review.persistence        — dedup window check
   ├── ai_review.providers.router   — run_chart_review(provider, payload)
   │       ├── anthropic_provider   — active
   │       └── openai_provider      — NotImplementedError stub
   ├── ai_review.normalizer         — normalize_chart_review_response(raw)
   ├── ai_review.concordance        — compute_engine_a_ai_concordance(...)
   └── ai_review.persistence        — record_review() in audit.db
```

### 4.1 Module layout

```
ai_review/
  __init__.py              # public API + types re-export
  validation.py            # request schema gating
  payload_schema.py        # ChartReviewPayload, EngineAContext, ScreenshotMeta dataclasses
  prompt_builder.py        # build_chart_review_prompt(context) -> str
  freshness.py             # classify_atr_freshness(atr_tf, atr_age_seconds, confirmed_only)
  concordance.py           # compute_engine_a_ai_concordance(...)
  normalizer.py            # normalize_chart_review_response(raw)
  persistence.py           # ensure_schema(), record_review(), find_recent_review_by_hash()
  engine_a_context.py      # assemble_engine_a_context(symbol, timeframe)
  timestamp_contract.py    # evaluate_timestamp_mismatch(eng_a_ctx, screenshot_meta, cfg)
  providers/
    __init__.py
    anthropic_provider.py  # call_anthropic_chart_review(payload)
    openai_provider.py     # call_openai_chart_review(payload) — raises NotImplementedError
    router.py              # run_chart_review(provider, payload)
```

Two helpers beyond the original spec (`engine_a_context.py`, `timestamp_contract.py`) keep the route handler thin and the snapshot/mismatch logic unit-testable without HTTP.

### 4.2 Non-touch list

Per `CLAUDE.md` core rules and the repo map. Zero edits to:

- `scoring.py`, `factor_scoring.py`, `forex_scoring.py` (Engine A)
- `market_structure.py`, `engine_b_ai.py` (Engine B)
- `scalp_engine.py` (Engine D)
- `engine_c.py`, `engine_c_ai.py` (Engine C)
- `execution.py`, `auto_trader.py`, `risk_engine.py`, `guardian.py`, `mt5_executor.py`, `bybit_executor.py`
- `config.yaml` numeric thresholds, gates, kill switches
- `chart_renderer.py`, `/api/chart-analysis` route, `VisionReviewCard.tsx` (legacy path)
- `scanner.py` aside from any read-only import of an existing public scoring entry point

### 4.3 Engine A re-run

The route calls the same public scoring entry point that `scanner.py` already uses, with current candles for the requested symbol/timeframe. The exact function name is traced in the implementation plan; the design only requires it to produce the same dict shape that `scanner.py:577` (`_engine_a_block_reason`) already consumes — same `factorDiagnostics`, `direction`, `confluenceScore`, `dataFreshness`, etc.

No changes to the scoring function. Read-only call.

## 5. Schemas

### 5.1 Request body

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "H4",
  "provider": "default | anthropic",
  "screenshot_base64": "data:image/png;base64,iVBORw0...",
  "screenshot_meta": {
    "width": 1280,
    "height": 720,
    "native_chart": true,
    "visible_range_start": "2026-04-20T00:00:00Z",
    "visible_range_end":   "2026-05-21T16:30:00Z",
    "chart_timeframe": "H4",
    "overlays": ["candles","volume","entry","sl","tp"],
    "captured_at": "2026-05-21T16:31:02Z"
  }
}
```

### 5.2 Validation matrix

| Reject reason | HTTP |
|---|---|
| `AI_CHART_REVIEW.ENABLED` is false (no dev override) | 503 |
| missing `symbol` / `timeframe` / `screenshot_base64` | 400 |
| `screenshot_base64` not prefixed `data:image/png;base64,` | 415 |
| decoded image bytes > `MAX_IMAGE_BYTES` | 413 |
| `screenshot_meta.native_chart` not `true` | 400 |
| `provider == "openai"` while `ALLOW_OPENAI_PROVIDER` is false | 403 |
| `provider == "dual"` while `ALLOW_DUAL_PROVIDER` is false | 403 |
| Engine A re-run returns no result for symbol/tf | 422 |

Engine A score, threshold, ATR, RR, multipliers — **never** read from the request. Frontend cannot influence them. The request body builder on the frontend grep-rejects any of those keys (enforced by test F1).

### 5.3 Server-trusted Engine A context

Assembled by `engine_a_context.assemble_engine_a_context(symbol, timeframe)`. Every field comes from server state; none come from the request.

```python
EngineAContext = {
  "symbol", "timeframe", "asset_class", "asset_group",
  "direction": "LONG|SHORT|NONE",
  "regime",
  "scan_timestamp",          # UTC ISO from re-run
  "candidate_timestamp",
  "latest_candle_ts",
  "d1_candle_ts", "h4_candle_ts", "h1_candle_ts",
  "engine_a_provider",        # data source Engine A scored on
  "chart_provider_hint",      # from screenshot_meta if present
  "provider_mismatch": bool,
  "confluence_score": float | None,
  "max_score_override": float | None,
  "threshold": float | None,  # CONFIG-derived
  "passed": bool,
  "factor_diagnostics": dict,         # opaque pass-through
  "multiplier_diagnostics": dict,     # opaque pass-through
  "equity_session": {
     "applied": bool, "reason": str | None, "utc_hour": int | None, "multiplier": float | None
  },
  "session_diagnostics": dict,
  "directional_alignment": dict,
  "atr": {
     "atr_value", "atr_tf", "atr_source", "atr_candle_last_ts",
     "atr_age_seconds", "atr_confirmed_only", "atr_cache_hit",
     "atr_freshness_status"  # filled by freshness.py
  },
  "geometry": {
     "candidate_entry", "current_price", "stop_loss", "take_profit",
     "risk_points", "reward_points", "rr",
     "price_displacement_from_candidate_entry", "sl_tp_source"
  },
  "freshness": {
     "cache_hit", "bucket_lag", "stale_warnings": [...]
  }
}
```

**Invariant enforced at boundary:** any `None` field is sent through the prompt builder as the literal string `unavailable` (never `0`, never omitted, never a placeholder example value). The prompt builder's source code contains no numeric literals for `threshold`, `multiplier`, `atr_value`, `atr_tf`, `rr` — all substituted from `context`. Test #9 grep-enforces this.

### 5.4 TF-aware ATR freshness

```python
classify_atr_freshness(atr_tf, atr_age_seconds, confirmed_only) -> {
  "status": "fresh | expected_lag | stale | unknown",
  "reason": str,
  "max_expected_age_seconds": int
}
```

Defaults from `CONFIG["VISION_FRESHNESS_POLICY"]["max_age_sec"]`:

| TF | max_age_sec |
|---|---|
| M1 | 180 |
| M5 | 900 |
| M15 | 1800 |
| H1 | 5400 |
| H4 | 18000 |
| D1 | 97200 |

Rules:

1. `atr_age_seconds is None` → `unknown`, reason `"ATR age unavailable"`.
2. `atr_tf == "D1"` and `confirmed_only is True` and `atr_age_seconds ≤ 172800` → `expected_lag`, reason `"D1 confirmed-only ATR within natural close window"`. This carve-out prevents false-stale flags on D1.
3. `atr_age_seconds ≤ max_age` → `fresh`.
4. `max_age < atr_age_seconds ≤ 2 × max_age` → `expected_lag`, reason `"ATR slightly past TF window"`.
5. `atr_age_seconds > 2 × max_age` → `stale`.

Optional override: `CONFIG["AI_CHART_REVIEW"]["ATR_FRESHNESS_MAX_AGE_SEC"]`, per-TF dict. If `None`, falls back to `VISION_FRESHNESS_POLICY`. No hardcoded numbers inside `freshness.py`.

### 5.5 Normalized AI response

```python
{
  "verdict": "VALID | CAUTION | INVALID | NO_TRADE",
  "confidence": int,             # 0–100, clamped
  "setup_type": str,
  "visual_confirmation": str,
  "visual_contradiction": str,
  "engine_a_alignment": str,
  "atr_rr_assessment": str,
  "freshness_assessment": str,
  "entry_quality": str,
  "supporting_reasons": list[str],
  "risks": list[str],
  "missing_context": list[str],
  "human_action": "take | wait | reject | needs_fresher_data | needs_better_rr",
  "raw_model_response": str
}
```

**Parse-failure fallback:** `verdict="CAUTION"`, `confidence=0`, `human_action="wait"`, `risks=["AI response JSON parse failed: <reason>"]`, `raw_model_response` preserved. Verdict clamped to enum; confidence clamped 0–100; arrays coerced to lists; extra model fields dropped.

### 5.6 Concordance

```python
{
  "engine": "A",
  "engine_a_direction": "LONG | SHORT | NONE",
  "engine_a_score": float,
  "engine_a_threshold": float,
  "engine_a_passed": bool,
  "ai_verdict": "VALID | CAUTION | INVALID | NO_TRADE",
  "ai_human_action": str,
  "concordance": "agree | partial | disagree | unknown",
  "divergence_type": "none | visual_contradiction | atr_rr_issue | freshness_issue | entry_displacement | missing_context | other",
  "divergence_note": str,
  "should_flag_for_review": bool
}
```

Deterministic decision table (no LLM call):

| Engine A passed | AI verdict | + condition | concordance | divergence_type |
|---|---|---|---|---|
| ✓ | VALID | no visual contradiction text | agree | none |
| ✓ | VALID | visual contradiction non-empty | partial | visual_contradiction |
| ✓ | CAUTION | — | partial | inherits from AI's risks (default `other`) |
| ✓ | INVALID / NO_TRADE | — | disagree | inherits |
| ✗ | VALID | — | disagree | other |
| ✗ | INVALID / NO_TRADE | — | agree | none |
| any | any | required diagnostics missing | downgrade to `unknown` | missing_context |
| any | any | `atr_freshness_status == stale` | downgrade one step | freshness_issue |
| any | any | `price_displacement > MAX_DISPLACEMENT_ATR_MULTIPLE × atr_value` | downgrade one step | entry_displacement |

`should_flag_for_review = concordance in {disagree, unknown}`. Pure data flag. Not used for execution.

### 5.7 Route response

```json
{
  "review_id": "uuid4-hex",
  "provider": "anthropic",
  "model": "claude-opus-4-7",
  "engine_a_context": { ...EngineAContext... },
  "ai_review": { ...normalized response... },
  "concordance": { ...concordance... },
  "timestamps": {
    "scan_timestamp": "...",
    "chart_captured_at": "...",
    "latest_candle_ts": "..."
  },
  "mismatch_warnings": [...],
  "dedup_hit": false
}
```

Response never contains API keys, never the screenshot base64, never `raw_b64`. `raw_model_response` (LLM text) is preserved inside `ai_review`.

## 6. Anthropic provider call shape

```python
# providers/anthropic_provider.py
import anthropic
import os, time
from config import CONFIG

def call_anthropic_chart_review(payload) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    cfg = CONFIG["AI_CHART_REVIEW"]
    model = cfg["ANTHROPIC_MODEL"]
    max_tokens = int(cfg.get("MAX_TOKENS", 1500))

    data_url = payload.screenshot_base64
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("non-PNG data URL reached provider")
    raw_b64 = data_url.split(",", 1)[1]      # NEVER the data: prefix

    client = anthropic.Anthropic(api_key=api_key)
    t0 = time.monotonic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": raw_b64,
                    },
                },
                {"type": "text", "text": payload.prompt},
            ],
        }],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    raw_text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return {"raw_text": raw_text, "model": resp.model, "latency_ms": latency_ms}
```

Logging: `raw_b64` is never logged. Log only `model`, `latency_ms`, `len(raw_b64)`, and `screenshot_hash[:16]`.

## 7. OpenAI stub

```python
# providers/openai_provider.py
def call_openai_chart_review(payload):
    raise NotImplementedError(
        "OpenAI provider not enabled for AI chart review v1. "
        "Set AI_CHART_REVIEW.ALLOW_OPENAI_PROVIDER=True and implement Responses API call."
    )
```

Router rejects `provider="openai"` before reaching this function whenever `ALLOW_OPENAI_PROVIDER` is False. The `NotImplementedError` is defence-in-depth if config is flipped without implementation.

## 8. Router

```python
def run_chart_review(provider: str, payload) -> dict:
    cfg = CONFIG["AI_CHART_REVIEW"]
    if provider in ("", "default", None):
        provider = cfg["DEFAULT_PROVIDER"]   # "anthropic"
    if provider == "anthropic":
        return call_anthropic_chart_review(payload)
    if provider == "openai":
        if not cfg["ALLOW_OPENAI_PROVIDER"]:
            raise PermissionError("OpenAI provider disabled")
        return call_openai_chart_review(payload)
    if provider == "dual":
        if not cfg["ALLOW_DUAL_PROVIDER"]:
            raise PermissionError("Dual provider disabled")
        raise NotImplementedError("Dual provider not implemented for v1")
    raise ValueError(f"Unknown provider: {provider!r}")
```

## 9. Prompt structure

`build_chart_review_prompt(context: dict) -> str` emits a single text block. Structure (excerpt):

```
You are reviewing a trading setup using:
1. a native chart PNG screenshot (provided as an image input)
2. server-trusted Engine A diagnostics
3. ATR / SL / TP / RR / freshness diagnostics

Return strict JSON only, matching this schema exactly:
{ verdict, confidence, setup_type, visual_confirmation, visual_contradiction,
  engine_a_alignment, atr_rr_assessment, freshness_assessment, entry_quality,
  supporting_reasons, risks, missing_context, human_action }

Rules:
- Do not approve a trade only because Engine A score is high.
- Check whether the screenshot visually agrees with Engine A direction.
- ATR freshness uses timeframe-aware logic. D1 confirmed-only ATR can naturally
  be 24–48h old — do not flag it as stale solely on age.
- Check SL/TP/RR quality and whether price has drifted from candidate entry.
- Treat any field labelled "unavailable" as uncertainty, not as zero.
- Treat provider/timestamp mismatch as uncertainty.
- This is review-only. Do not issue execution instructions.

== SYMBOL ==
{symbol} {timeframe} asset_group: {asset_group}

== ENGINE A (server-trusted) ==
direction:           {direction}
confluence_score:    {confluence_score | "unavailable"}
threshold:           {threshold | "unavailable"}
max_score_override:  {max_score_override | "unavailable"}
passed:              {passed}
regime:              {regime}
equity_session:      applied={...} utc_hour={...} multiplier={...} reason={...}
factor_diagnostics:  {factor_diagnostics}
multiplier_diagnostics: {multiplier_diagnostics}
directional_alignment: {directional_alignment}

== ATR ==
atr_value:           {atr_value | "unavailable"}
atr_tf:              {atr_tf | "unavailable"}
atr_source:          {atr_source}
atr_confirmed_only:  {atr_confirmed_only}
atr_age_seconds:     {atr_age_seconds | "unavailable"}
atr_freshness:       {atr_freshness_status} (max_expected={max_expected_age_seconds}s)
atr_cache_hit:       {atr_cache_hit}

== GEOMETRY ==
candidate_entry:     {candidate_entry | "unavailable"}
current_price:       {current_price | "unavailable"}
stop_loss:           {stop_loss | "unavailable"}
take_profit:         {take_profit | "unavailable"}
risk_points:         {risk_points | "unavailable"}
reward_points:       {reward_points | "unavailable"}
rr:                  {rr | "unavailable"}
price_displacement_from_candidate_entry: {...}
sl_tp_source:        {sl_tp_source}

== TIMESTAMPS ==
scan_timestamp:        {scan_timestamp}
candidate_timestamp:   {candidate_timestamp}
latest_candle_ts:      {latest_candle_ts}
d1_ts / h4_ts / h1_ts: {...}
chart_captured_at:     {chart_captured_at}
provider (engine A):   {engine_a_provider}
provider (chart):      {chart_provider_hint}    provider_mismatch={...}
mismatch_warnings:     {...}

Now analyse the chart and return JSON only.
```

## 10. Persistence

DDL (idempotent, runs in `persistence.ensure_schema()` at blueprint init):

```sql
CREATE TABLE IF NOT EXISTS ai_chart_reviews (
  review_id              TEXT PRIMARY KEY,
  created_at             TEXT NOT NULL,
  symbol                 TEXT NOT NULL,
  timeframe              TEXT NOT NULL,
  asset_group            TEXT,
  provider               TEXT NOT NULL,
  model                  TEXT NOT NULL,
  latency_ms             INTEGER,
  screenshot_hash        TEXT NOT NULL,
  screenshot_bytes       INTEGER,
  screenshot_meta_json   TEXT NOT NULL,
  engine_a_snapshot_json TEXT NOT NULL,
  ai_review_json         TEXT NOT NULL,
  concordance_json       TEXT NOT NULL,
  scan_timestamp         TEXT,
  chart_captured_at      TEXT,
  latest_candle_ts       TEXT,
  mismatch_warnings_json TEXT,
  parse_success          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_chart_reviews_symbol_tf
  ON ai_chart_reviews(symbol, timeframe, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_chart_reviews_concordance
  ON ai_chart_reviews(json_extract(concordance_json, '$.concordance'), created_at DESC);
```

DB path resolution mirrors `ai_outcome_linker.py`:

```python
audit_db = audit_db or os.environ.get("ATHENA_AUDIT_DB") or _DEFAULT_AUDIT_DB
```

Screenshot hash = `sha256(raw_bytes).hexdigest()[:16]`. Full base64 is never persisted.

Dedup: `find_recent_review_by_hash(symbol, timeframe, screenshot_hash, window_seconds)` returns the most-recent row within the window. If hit, the route returns that row with `dedup_hit=true` and does not call Claude.

## 11. Config block

Added to `CONFIG` dict in `config.py`:

```python
"AI_CHART_REVIEW": {
    "ENABLED": False,
    "DEFAULT_PROVIDER": "anthropic",
    "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7"),
    "OPENAI_MODEL":    os.environ.get("OPENAI_CHART_REVIEW_MODEL", ""),
    "MAX_TOKENS": 1500,
    "REQUIRE_SCREENSHOT": True,
    "REQUIRE_ATR_DIAGNOSTICS": False,
    "REQUIRE_FRESHNESS_DIAGNOSTICS": False,
    "MAX_IMAGE_BYTES": 2 * 1024 * 1024,        # 2 MB
    "PERSIST_REVIEWS": True,
    "ALLOW_OPENAI_PROVIDER": False,
    "ALLOW_DUAL_PROVIDER": False,
    "MAX_DISPLACEMENT_ATR_MULTIPLE": 1.0,
    "MISMATCH_WARN_MAX_SECONDS": 120,
    "ATR_FRESHNESS_MAX_AGE_SEC": None,         # None → VISION_FRESHNESS_POLICY
    "DEDUP_WINDOW_SECONDS": 60,
},
```

Env: `ANTHROPIC_API_KEY` (required at call time), `OPENAI_API_KEY` (future). Keys never logged, never returned to frontend.

## 12. Route

`POST /api/ai/chart-review` lives in new file `athena_app/api/routes_ai_chart_review.py`, registered as a Flask blueprint. `athena.py` gains one line: `from athena_app.api.routes_ai_chart_review import bp as ai_chart_review_bp; app.register_blueprint(ai_chart_review_bp)`.

Handler is thin orchestration:

```python
@bp.route("/api/ai/chart-review", methods=["POST"])
def api_ai_chart_review():
    cfg = CONFIG["AI_CHART_REVIEW"]
    if not cfg["ENABLED"]:
        return jsonify({"error": "AI chart review disabled"}), 503
    data = request.get_json(silent=True) or {}
    err = validate_request(data, cfg)
    if err:
        return jsonify({"error": err.message}), err.status
    engine_a_ctx = assemble_engine_a_context(data["symbol"], data["timeframe"])
    if engine_a_ctx is None:
        return jsonify({"error": "Engine A returned no result"}), 422
    engine_a_ctx["atr"]["atr_freshness_status"] = classify_atr_freshness(...)
    mismatch = evaluate_timestamp_mismatch(engine_a_ctx, data["screenshot_meta"], cfg)
    screenshot_hash = sha256(decoded_bytes).hexdigest()[:16]
    dedup = find_recent_review_by_hash(symbol, tf, screenshot_hash, cfg["DEDUP_WINDOW_SECONDS"])
    if dedup:
        return jsonify({**dedup, "dedup_hit": True})
    payload = build_payload(data, engine_a_ctx, prompt=build_chart_review_prompt(engine_a_ctx))
    raw = run_chart_review(data.get("provider"), payload)
    normalized = normalize_chart_review_response(raw["raw_text"])
    concordance = compute_engine_a_ai_concordance(engine_a_ctx, normalized)
    if cfg["PERSIST_REVIEWS"]:
        record_review(...)
    return jsonify({...})
```

## 13. Frontend slice

**Files:**

- NEW `static/react-app/app/src/lib/aiChartReview.ts` — helper: `captureChartScreenshot()`, `downscaleToCap(canvas, maxW=1280, maxH=720)`, `postChartReview(body)`, types.
- NEW `static/react-app/app/src/components/athena/AIReviewCard.tsx` — sibling to `VisionReviewCard.tsx`.
- EDIT `static/react-app/app/src/components/panels/TVChartPanel.tsx` — add **AI Review** button next to existing Camera button (around line 1682). Wire screenshot capture + downscale + POST.
- EDIT `static/react-app/app/src/types/athena.ts` — add `AIChartReviewResponse`, `AIChartReviewConcordance`, `AIChartReviewEngineAContext` types matching backend response.

**Screenshot flow:**

```
1. Existing code builds outputCanvas from chart panes (TVChartPanel.tsx:1364)
2. NEW: if outputCanvas.width > 1280 → drawImage onto offscreen canvas (1280, 1280 * h/w)
3. canvas.toBlob → FileReader.readAsDataURL → "data:image/png;base64,..."
4. Build screenshot_meta { width, height, native_chart: true, captured_at, visible_range_start/end, chart_timeframe, overlays }
5. POST /api/ai/chart-review { symbol, timeframe, provider: "default", screenshot_base64, screenshot_meta }
6. On 200: pass response to <AIReviewCard /> below the chart
7. On 4xx/5xx: surface error inline, no auto-retry
```

**Frontend guarantee:** request body never contains `engineAScore`, `confluence`, `threshold`, `atr`, `rr`, `multiplier`, `passed`, or any Engine A field. Test F1 grep-enforces this in the helper source.

**AIReviewCard layout (read-only — no execute button):**

```
┌─ AI Chart Review ─────────────────────────────────────┐
│ [VALID] [confidence: 76]   anthropic/claude-opus-4-7  │
│ Human action: take          [agree]                   │
│ Divergence: none                                      │
├───────────────────────────────────────────────────────┤
│ Visual confirmation:  ...                             │
│ Visual contradiction: ...                             │
│ Engine A alignment:   ...                             │
│ ATR / RR assessment:  ...                             │
│ Freshness assessment: ATR fresh (H4, age 2h)          │
│ Entry quality:        ...                             │
├───────────────────────────────────────────────────────┤
│ Supporting reasons:  • ... • ... • ...                │
│ Risks:               • ... • ...                      │
│ Missing context:     —                                │
├───────────────────────────────────────────────────────┤
│ chart captured: 2026-05-21T16:31:02Z                  │
│ scan timestamp: 2026-05-21T16:30:55Z (Δ 7s)           │
│ latest candle:  2026-05-21T16:00:00Z                  │
│ mismatch warnings: none                               │
└───────────────────────────────────────────────────────┘
```

Concordance pill: `agree`=green, `partial`=yellow, `disagree`=red, `unknown`=grey. Reuses Tailwind tokens already used in `VisionReviewCard.tsx`.

## 14. Tests

**Backend — new file `tests/test_ai_chart_review.py`:**

| # | Test | Key assertion |
|---|---|---|
| 1 | route_rejects_missing_screenshot | 400, provider mock not_called |
| 2 | route_rejects_missing_symbol_or_timeframe | 400 |
| 3 | route_rejects_non_png_data_url | 415 (sends `data:image/jpeg;...`) |
| 4 | route_rejects_openai_when_disabled | 403 |
| 5 | anthropic_strips_data_url_prefix | image block `source.data` does NOT start with `data:` |
| 6 | anthropic_image_block_has_raw_base64 | image block before text block, media_type=image/png |
| 7 | anthropic_uses_max_tokens_from_config | mock asserts `max_tokens == CONFIG[...]["MAX_TOKENS"]` (≥1500) |
| 8 | prompt_includes_engine_a_score | confluence value substring in prompt |
| 9 | prompt_threshold_not_hardcoded | grep `prompt_builder.py` source for numeric literals on threshold/multiplier/ATR/RR lines |
| 10 | prompt_includes_factor_diagnostics | factor keys present |
| 11 | prompt_includes_equity_session_applied_and_multiplier | both fields rendered |
| 12 | prompt_includes_atr_diagnostics | atr_value, atr_tf, atr_age in prompt |
| 13 | prompt_includes_sl_tp_rr | all three rendered |
| 14 | prompt_includes_tf_aware_atr_freshness_wording | D1 confirmed-only sentence present |
| 15 | d1_confirmed_only_atr_not_auto_stale | `classify_atr_freshness("D1", 100000, True)["status"] == "expected_lag"` |
| 16 | missing_atr_creates_uncertainty | None ATR → prompt `unavailable`, freshness `unknown` |
| 17 | normalizer_stable_schema_valid_json | every key present, types correct |
| 18 | normalizer_caution_on_parse_fail | verdict=CAUTION, confidence=0, raw preserved |
| 19 | concordance_agree_when_passed_and_valid | |
| 20 | concordance_partial_when_passed_and_caution | |
| 21 | concordance_disagree_when_passed_and_invalid | |
| 22 | persistence_stores_engine_a_ai_concordance | SELECT row + JSON loads check |
| 23 | persistence_stores_hash_not_full_base64 | `screenshot_meta_json` no `base64` substring; `screenshot_hash` non-empty |
| 24 | timestamp_mismatch_creates_warning | captured_at 200s after scan_timestamp → mismatch warning string |
| 25 | route_rejects_image_above_max_bytes | 413 |
| 26 | dedup_within_window_skips_api_call | second POST same hash within window → provider mock not_called |
| 27 | router_default_resolves_to_anthropic | provider="default" → anthropic_provider called |

All provider tests mock `anthropic.Anthropic`. No live API calls.

**Frontend — new file `static/react-app/app/src/components/athena/__tests__/AIReviewCard.test.tsx` + helper test:**

| Test | Assertion |
|---|---|
| F1 | helper sends `screenshot_base64` + `screenshot_meta`, body contains no `engineAScore`/`confluence`/`threshold`/`atr`/`rr` keys |
| F2 | `downscaleToCap` caps width to 1280, preserves aspect ratio |
| F3 | `<AIReviewCard />` renders verdict, confidence, concordance pill, ATR/RR/freshness fields, and three timestamps |

If the React app lacks a test runner config (verified during plan), frontend tests fall back to a TypeScript-checked component-rendering smoke test invoked from the build step. v1 does not expand the frontend testing surface.

## 15. Manual verification protocol

After backend lands, before frontend:

1. Set env `ANTHROPIC_API_KEY`; set `AI_CHART_REVIEW.ENABLED=True` in `config.local.yaml`.
2. `curl POST /api/ai/chart-review` with a real PNG and a live index symbol where `equity_session.applied` is currently true.
3. Inspect `response.engine_a_context`: `threshold`, `equity_session.multiplier`, `atr_value`, `atr_tf`, `rr` all come from live server state — no placeholder values.
4. Confirm `response.concordance.engine_a_passed` matches scanner's current pass/fail.
5. `SELECT * FROM ai_chart_reviews` → one new row with non-empty hash, populated JSON columns.

After frontend lands:

6. Click **AI Review** on `TVChartPanel` for the same symbol.
7. Confirm `screenshot_meta.captured_at` within Δ < 120s of `scan_timestamp` (no mismatch warning).
8. Card shows verdict, concordance pill, all the spec'd fields, three timestamps.
9. Double-click within 60s → second response identical, `dedup_hit=true` in response and logs.

## 16. Files touched

**Backend slice 1:**

- NEW `ai_review/__init__.py`, `validation.py`, `payload_schema.py`, `prompt_builder.py`, `freshness.py`, `concordance.py`, `normalizer.py`, `persistence.py`, `engine_a_context.py`, `timestamp_contract.py`
- NEW `ai_review/providers/__init__.py`, `anthropic_provider.py`, `openai_provider.py`, `router.py`
- NEW `athena_app/api/routes_ai_chart_review.py`
- EDIT `config.py` — add `AI_CHART_REVIEW` block to `CONFIG`
- EDIT `athena.py` — one line: register blueprint
- NEW `tests/test_ai_chart_review.py`

**Frontend slice 2:**

- NEW `static/react-app/app/src/lib/aiChartReview.ts`
- NEW `static/react-app/app/src/components/athena/AIReviewCard.tsx`
- EDIT `static/react-app/app/src/components/panels/TVChartPanel.tsx` — AI Review button + result state
- EDIT `static/react-app/app/src/types/athena.ts` — response types
- NEW `static/react-app/app/src/components/athena/__tests__/AIReviewCard.test.tsx`

**Engine A / B / C / D / execution / thresholds:** zero edits.

## 17. Risks and unknowns

- **Engine A re-run latency** — call cost is human-triggered, but if scoring for an unloaded symbol takes >5s the UI needs a loading state. Frontend slice handles this with a spinner; not a backend concern.
- **`anthropic` SDK not pinned in `requirements.txt`** — verified during plan-writing; add to requirements if missing.
- **`scanner.py` public scoring entry point** — exact function name traced during plan-writing. Design only depends on its existence.
- **Frontend test runner** — Vitest presence in `react-app/app` verified during plan-writing; F1–F3 may downgrade to smoke tests if not configured.
- **D1 confirmed-only carve-out cap of 48h** — chosen to match the spec's "naturally 24-48h" hint. Configurable via `ATR_FRESHNESS_MAX_AGE_SEC` override.
