# AI Chart Review v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the native-chart AI review v1 — Claude Opus reviews a PNG of the lightweight-charts panel against server-trusted Engine A diagnostics, returns a normalised verdict + concordance, persists the review, and renders read-only inside `TVChartPanel.tsx`.

**Architecture:** Backend modules under `ai_review/` already exist (validation, prompt builder, freshness, normalizer, concordance, persistence, anthropic provider, route at `/api/ai/chart-review`). The remaining slice is frontend: capture PNG in `TVChartPanel`, downscale to ≤1280×720, POST to the route, render `AIReviewCard` with the verdict + concordance + timestamps. Backend is exercised first via existing pytest suite and a manual curl smoke test; frontend then lands behind the existing `AI_CHART_REVIEW.ENABLED` config gate. Zero edits to scoring, gates, kill switches, or the legacy `chart_renderer.py` / `VisionReviewCard.tsx` flow.

**Tech Stack:** Python (Flask, Anthropic SDK ≥0.49.0, sqlite3), TypeScript (React 18, lightweight-charts, Vite, no Vitest installed → TS-checked smoke), Tailwind, existing `Card`/`Badge` UI primitives.

---

## Current ground truth (pre-execution audit)

Before writing tasks, an audit was run. Read this before starting:

| Item | Status |
|---|---|
| `ai_review/` package (validation, payload_schema, prompt_builder, freshness, concordance, normalizer, persistence, engine_a_context, timestamp_contract) | **EXISTS** |
| `ai_review/providers/` (anthropic_provider, openai_provider, router) | **EXISTS** |
| `athena_app/api/routes_ai_chart_review.py` | **EXISTS — 150 lines, wired** |
| `config.py` `AI_CHART_REVIEW` block | **EXISTS** at line 344 |
| `athena.py` blueprint registration via `register_ai_chart_review_routes` | **EXISTS** at line 14525 |
| `tests/test_ai_chart_review.py` (497 lines) + `tests/test_ai_review_safety.py` (1339 lines) | **EXIST** |
| `anthropic>=0.49.0` in `requirements.txt` | **PRESENT** |
| `static/react-app/app/src/lib/aiChartReview.ts` | **MISSING** |
| `static/react-app/app/src/components/athena/AIReviewCard.tsx` | **MISSING** |
| `static/react-app/app/src/types/athena.ts` — AI chart review response types | **MISSING** |
| `TVChartPanel.tsx` AI Review button | **MISSING** (Camera button at line 1682) |
| Frontend test runner (Vitest/Jest) in `static/react-app/app/package.json` | **NOT INSTALLED** → F1–F3 downgrade to TS-compile + manual checks per spec §14 footnote |

**Implication:** This plan is gap-closing, not greenfield. Phase 0 verifies backend before frontend work. Phase 1–4 build the frontend slice. Phase 5 is end-to-end manual verification.

**Non-touch list (per spec §4.2 and CLAUDE.md):** `scoring.py`, `factor_scoring.py`, `forex_scoring.py`, `market_structure.py`, `engine_b_ai.py`, `scalp_engine.py`, `engine_c.py`, `engine_c_ai.py`, `execution.py`, `auto_trader.py`, `risk_engine.py`, `guardian.py`, `mt5_executor.py`, `bybit_executor.py`, `chart_renderer.py`, the legacy `/api/chart-analysis` route, `VisionReviewCard.tsx`, `config.yaml` thresholds. Engine A scoring is **not** modified. The route only **calls** the existing public scoring entry point read-only.

---

## File structure

```
Backend (already implemented — verify only):
  ai_review/__init__.py
  ai_review/validation.py
  ai_review/payload_schema.py
  ai_review/prompt_builder.py
  ai_review/freshness.py
  ai_review/concordance.py
  ai_review/normalizer.py
  ai_review/persistence.py
  ai_review/engine_a_context.py
  ai_review/timestamp_contract.py
  ai_review/providers/{__init__,anthropic_provider,openai_provider,router}.py
  athena_app/api/routes_ai_chart_review.py
  tests/test_ai_chart_review.py
  tests/test_ai_review_safety.py

Frontend (this plan creates / edits):
  static/react-app/app/src/types/athena.ts            (EDIT — append response types)
  static/react-app/app/src/lib/aiChartReview.ts       (CREATE)
  static/react-app/app/src/components/athena/AIReviewCard.tsx   (CREATE)
  static/react-app/app/src/components/athena/index.ts (EDIT — export new card)
  static/react-app/app/src/components/panels/TVChartPanel.tsx   (EDIT — button + state + render)
```

Each file has a single responsibility:
- `aiChartReview.ts` — capture + downscale + POST; **never** sends Engine A fields.
- `AIReviewCard.tsx` — read-only render of backend response.
- `TVChartPanel.tsx` edit — wires button to helper, holds response state, renders card. Reuses the existing `chartCaptureRef` / canvas composite at lines 1340–1379.

---

## Phase 0 — Backend verification

### Task 0.1: Run backend test suite for AI chart review

**Files:**
- Test: `tests/test_ai_chart_review.py`, `tests/test_ai_review_safety.py`

- [ ] **Step 1: Run targeted tests**

Run:
```bash
python -m pytest tests/test_ai_chart_review.py tests/test_ai_review_safety.py -v
```

Expected: all tests PASS. The two files contain the 27 backend tests enumerated in spec §14 (route validation, prompt grep, freshness carve-out, normalizer fallback, concordance table, persistence schema, dedup, mismatch warnings).

- [ ] **Step 2: If any test fails, STOP**

Do **not** start frontend work until backend is green. If failures appear:
1. Capture the failing test name + assertion output.
2. Read the relevant `ai_review/*.py` module.
3. Open a new task at the end of this plan and fix before continuing.
4. Re-run the suite. Continue only when all green.

No code change in this task if tests already pass. No commit.

---

### Task 0.2: Confirm `AI_CHART_REVIEW.ENABLED` gating

**Files:**
- Read: `config.py:344-370`
- Read: `athena_app/api/routes_ai_chart_review.py:33-37`

- [ ] **Step 1: Verify ENABLED defaults to False**

Run:
```bash
python -c "from config import CONFIG; print('ENABLED=', CONFIG['AI_CHART_REVIEW']['ENABLED']); print('DEFAULT_PROVIDER=', CONFIG['AI_CHART_REVIEW']['DEFAULT_PROVIDER']); print('MAX_TOKENS=', CONFIG['AI_CHART_REVIEW']['MAX_TOKENS']); print('MAX_IMAGE_BYTES=', CONFIG['AI_CHART_REVIEW']['MAX_IMAGE_BYTES']); print('DEDUP_WINDOW_SECONDS=', CONFIG['AI_CHART_REVIEW']['DEDUP_WINDOW_SECONDS'])"
```

Expected stdout (values match spec §11):
```
ENABLED= False
DEFAULT_PROVIDER= anthropic
MAX_TOKENS= 1500
MAX_IMAGE_BYTES= 2097152
DEDUP_WINDOW_SECONDS= 60
```

- [ ] **Step 2: Confirm the route returns 503 when disabled**

This is already covered by the test suite, but visually inspect `routes_ai_chart_review.py:36-37`:

```python
if not cfg.get("ENABLED"):
    return jsonify({"error": "AI chart review disabled"}), 503
```

If the lines do not match, STOP and open a fix task.

- [ ] **Step 3: No commit, no code change.**

This task is read-only verification. Move to Phase 1.

---

## Phase 1 — Frontend response types

### Task 1.1: Add AI chart review response types to `athena.ts`

**Files:**
- Modify: `static/react-app/app/src/types/athena.ts` (append at end of file)

- [ ] **Step 1: Verify current end of file**

Run:
```bash
tail -5 static/react-app/app/src/types/athena.ts
```

Expected last entry is the `BacktestResult`-like object ending with `[k: string]: unknown; }`. If different, locate a sensible append point (must be at file scope, not inside another interface).

- [ ] **Step 2: Append type block**

Append the following to the very end of `static/react-app/app/src/types/athena.ts`:

```typescript

// ============================================================================
// AI Chart Review v1 — POST /api/ai/chart-review
// Mirrors normalized AI response + Engine-A-vs-AI concordance from
// ai_review/normalizer.py and ai_review/concordance.py.
// Read-only; never used for execution.
// ============================================================================

export type AIChartReviewVerdict = 'VALID' | 'CAUTION' | 'INVALID' | 'NO_TRADE';
export type AIChartReviewHumanAction =
  | 'take'
  | 'wait'
  | 'reject'
  | 'needs_fresher_data'
  | 'needs_better_rr';
export type AIChartReviewConcordanceState =
  | 'agree'
  | 'partial'
  | 'disagree'
  | 'unknown';
export type AIChartReviewDivergenceType =
  | 'none'
  | 'visual_contradiction'
  | 'atr_rr_issue'
  | 'freshness_issue'
  | 'entry_displacement'
  | 'missing_context'
  | 'other';

export interface AIChartReviewNormalized {
  verdict: AIChartReviewVerdict;
  confidence: number; // 0–100
  setup_type?: string;
  visual_confirmation?: string;
  visual_contradiction?: string;
  engine_a_alignment?: string;
  atr_rr_assessment?: string;
  freshness_assessment?: string;
  entry_quality?: string;
  supporting_reasons?: string[];
  risks?: string[];
  missing_context?: string[];
  human_action?: AIChartReviewHumanAction;
  raw_model_response?: string;
}

export interface AIChartReviewConcordance {
  engine: 'A';
  engine_a_direction?: 'LONG' | 'SHORT' | 'NONE';
  engine_a_score?: number | null;
  engine_a_threshold?: number | null;
  engine_a_passed?: boolean;
  ai_verdict?: AIChartReviewVerdict;
  ai_human_action?: AIChartReviewHumanAction;
  concordance: AIChartReviewConcordanceState;
  divergence_type: AIChartReviewDivergenceType;
  divergence_note?: string;
  should_flag_for_review: boolean;
}

export interface AIChartReviewEngineAContext {
  symbol?: string;
  timeframe?: string;
  asset_class?: string;
  asset_group?: string;
  direction?: 'LONG' | 'SHORT' | 'NONE';
  regime?: string;
  scan_timestamp?: string;
  candidate_timestamp?: string;
  latest_candle_ts?: string;
  chart_captured_at?: string;
  engine_a_provider?: string;
  chart_provider_hint?: string;
  provider_mismatch?: boolean;
  confluence_score?: number | null;
  max_score_override?: number | null;
  threshold?: number | null;
  passed?: boolean;
  factor_diagnostics?: Record<string, unknown>;
  multiplier_diagnostics?: Record<string, unknown>;
  equity_session?: {
    applied?: boolean;
    reason?: string | null;
    utc_hour?: number | null;
    multiplier?: number | null;
  };
  session_diagnostics?: Record<string, unknown>;
  directional_alignment?: Record<string, unknown>;
  atr?: {
    atr_value?: number | null;
    atr_tf?: string;
    atr_source?: string;
    atr_candle_last_ts?: string;
    atr_age_seconds?: number | null;
    atr_confirmed_only?: boolean;
    atr_cache_hit?: boolean;
    atr_freshness_status?: 'fresh' | 'expected_lag' | 'stale' | 'unknown';
    max_expected_age_seconds?: number;
  };
  geometry?: {
    candidate_entry?: number | null;
    current_price?: number | null;
    stop_loss?: number | null;
    take_profit?: number | null;
    risk_points?: number | null;
    reward_points?: number | null;
    rr?: number | null;
    price_displacement_from_candidate_entry?: number | null;
    sl_tp_source?: string;
  };
  freshness?: {
    cache_hit?: boolean;
    bucket_lag?: number | null;
    stale_warnings?: string[];
  };
  mismatch_warnings?: string[];
  [k: string]: unknown;
}

export interface AIChartReviewResponse {
  review_id: string | null;
  provider: string;
  model: string | null;
  latency_ms?: number | null;
  engine_a_context: AIChartReviewEngineAContext;
  ai_review: AIChartReviewNormalized;
  concordance: AIChartReviewConcordance;
  timestamps: {
    scan_timestamp?: string | null;
    chart_captured_at?: string | null;
    latest_candle_ts?: string | null;
  };
  mismatch_warnings: string[];
  dedup_hit: boolean;
}

export interface AIChartReviewScreenshotMeta {
  width: number;
  height: number;
  native_chart: true;
  visible_range_start?: string;
  visible_range_end?: string;
  chart_timeframe: string;
  overlays: string[];
  captured_at: string;
}

export interface AIChartReviewRequest {
  symbol: string;
  timeframe: string;
  provider?: 'default' | 'anthropic';
  screenshot_base64: string;
  screenshot_meta: AIChartReviewScreenshotMeta;
}
```

- [ ] **Step 3: Type-check**

Run from `static/react-app/app/`:
```bash
npx tsc --noEmit
```

Expected: PASS with no errors. If `npx tsc` is not in PATH, use `./node_modules/.bin/tsc --noEmit` from that directory.

- [ ] **Step 4: Commit**

```bash
git add static/react-app/app/src/types/athena.ts
git commit -m "feat(types): add AIChartReview response types"
```

---

## Phase 2 — Frontend helper

### Task 2.1: Create `aiChartReview.ts` capture + downscale + POST helper

**Files:**
- Create: `static/react-app/app/src/lib/aiChartReview.ts`

This helper has three exported functions:
1. `downscaleToCap(canvas, maxW, maxH)` — pure, returns a new canvas capped at 1280×720 preserving aspect ratio.
2. `canvasToDataUrl(canvas)` — wraps `toBlob` + `FileReader` to return a `data:image/png;base64,...` URL.
3. `postChartReview(req)` — typed POST to `/api/ai/chart-review`.

The helper source **must not** reference any of the forbidden Engine A field names. Spec test F1 grep-enforces this. Forbidden substrings in the helper source: `engineAScore`, `confluence`, `threshold`, `atr`, `rr`, `multiplier`, `passed`, `engineAContext`. (Words that happen to contain these as substrings, e.g. `Authorization`, are fine — see the grep rule in Step 5.)

- [ ] **Step 1: Write the helper file**

Create `static/react-app/app/src/lib/aiChartReview.ts` with this exact content:

```typescript
// AI Chart Review v1 — frontend helper.
//
// Responsibilities:
//   1. Downscale a canvas to <= 1280x720 preserving aspect ratio.
//   2. Encode a canvas to a "data:image/png;base64,..." URL.
//   3. POST { symbol, timeframe, provider, screenshot_base64, screenshot_meta }
//      to /api/ai/chart-review.
//
// SECURITY CONTRACT (enforced by spec test F1):
//   This file MUST NOT send any server-trusted Engine A field from the browser.
//   No Engine A score, threshold, ATR, RR, multiplier, passed flag, regime,
//   factor diagnostics — anything Engine A — is ever in the request body.
//   Only the screenshot + symbol + timeframe + provider are sent.

import { apiClient } from './apiClient';
import type {
  AIChartReviewRequest,
  AIChartReviewResponse,
  AIChartReviewScreenshotMeta,
} from '@/types/athena';

const ENDPOINT = '/api/ai/chart-review';

export const AI_CHART_REVIEW_DEFAULTS = Object.freeze({
  MAX_WIDTH: 1280,
  MAX_HEIGHT: 720,
});

/**
 * Downscale a source canvas to fit within (maxW, maxH) preserving aspect ratio.
 * If the source already fits, returns the source canvas unchanged.
 */
export function downscaleToCap(
  source: HTMLCanvasElement,
  maxW: number = AI_CHART_REVIEW_DEFAULTS.MAX_WIDTH,
  maxH: number = AI_CHART_REVIEW_DEFAULTS.MAX_HEIGHT,
): HTMLCanvasElement {
  const w = source.width;
  const h = source.height;
  if (w <= maxW && h <= maxH) return source;

  const scale = Math.min(maxW / w, maxH / h);
  const targetW = Math.max(1, Math.round(w * scale));
  const targetH = Math.max(1, Math.round(h * scale));

  const off = document.createElement('canvas');
  off.width = targetW;
  off.height = targetH;
  const ctx = off.getContext('2d');
  if (!ctx) {
    throw new Error('downscaleToCap: 2D context unavailable on offscreen canvas');
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(source, 0, 0, w, h, 0, 0, targetW, targetH);
  return off;
}

/**
 * Encode a canvas to a data URL "data:image/png;base64,...".
 * Resolves with the data URL string or rejects on encoding failure.
 */
export function canvasToDataUrl(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('canvasToDataUrl: PNG export returned no blob'));
        return;
      }
      const reader = new FileReader();
      reader.onerror = () =>
        reject(new Error('canvasToDataUrl: FileReader failed to read blob'));
      reader.onload = () => {
        const result = reader.result;
        if (typeof result !== 'string') {
          reject(new Error('canvasToDataUrl: FileReader produced non-string result'));
          return;
        }
        resolve(result);
      };
      reader.readAsDataURL(blob);
    }, 'image/png');
  });
}

/**
 * Build a screenshot_meta object. The backend trusts ONLY `native_chart` and
 * `captured_at` for safety logic; the rest is for diagnostics in the persisted
 * review row. All Engine A data comes from the server, never from this object.
 */
export function buildScreenshotMeta(args: {
  width: number;
  height: number;
  chart_timeframe: string;
  overlays: string[];
  visible_range_start?: string;
  visible_range_end?: string;
}): AIChartReviewScreenshotMeta {
  return {
    width: args.width,
    height: args.height,
    native_chart: true,
    chart_timeframe: args.chart_timeframe,
    overlays: [...args.overlays],
    visible_range_start: args.visible_range_start,
    visible_range_end: args.visible_range_end,
    captured_at: new Date().toISOString(),
  };
}

/**
 * POST to /api/ai/chart-review. The request body intentionally contains only
 * symbol, timeframe, provider, screenshot_base64, screenshot_meta — no Engine A
 * fields. Throws on non-2xx (apiClient surfaces backend `error` strings).
 */
export async function postChartReview(
  body: AIChartReviewRequest,
): Promise<AIChartReviewResponse> {
  return apiClient.post<AIChartReviewResponse>(
    ENDPOINT,
    body as unknown as Record<string, unknown>,
  );
}
```

- [ ] **Step 2: TypeScript check**

Run from `static/react-app/app/`:
```bash
npx tsc --noEmit
```

Expected: PASS. If failure mentions `@/types/athena`, verify Task 1.1 committed.

- [ ] **Step 3: Smoke-test `downscaleToCap` in Node**

The skill's preferred test path requires a runner, which this project lacks. Substitute a one-off smoke script that exercises the pure function via JSDOM-free arithmetic only.

Create `static/react-app/app/scripts/smoke-aiChartReview.mjs`:

```javascript
// One-off smoke for downscaleToCap aspect-ratio math.
// Mirrors the formula inside aiChartReview.ts. Pure math, no DOM.
function expectedDims(w, h, maxW = 1280, maxH = 720) {
  if (w <= maxW && h <= maxH) return { w, h };
  const scale = Math.min(maxW / w, maxH / h);
  return { w: Math.max(1, Math.round(w * scale)), h: Math.max(1, Math.round(h * scale)) };
}

const cases = [
  { in: [800, 600], out: { w: 800, h: 600 } },
  { in: [1280, 720], out: { w: 1280, h: 720 } },
  { in: [2560, 1440], out: { w: 1280, h: 720 } },
  { in: [3200, 1800], out: { w: 1280, h: 720 } },
  { in: [4000, 1500], out: { w: 1280, h: 480 } },
];

let failed = 0;
for (const c of cases) {
  const got = expectedDims(...c.in);
  const ok = got.w === c.out.w && got.h === c.out.h;
  console.log(ok ? 'PASS' : 'FAIL', c.in, '->', got, 'expected', c.out);
  if (!ok) failed++;
}
process.exit(failed === 0 ? 0 : 1);
```

Run:
```bash
node static/react-app/app/scripts/smoke-aiChartReview.mjs
```

Expected: all five lines print `PASS`, exit 0.

- [ ] **Step 4: Verify F1 contract — helper source contains no Engine A field names**

Run from repo root:
```bash
grep -nE '\b(engineAScore|confluence|threshold|multiplier|passed)\b' static/react-app/app/src/lib/aiChartReview.ts
```

Also check the soft tokens (substring match is fine in identifiers like `Authorization`):
```bash
grep -nE '\b(atr|rr|engineAContext)\b' static/react-app/app/src/lib/aiChartReview.ts
```

Expected: both `grep` commands print nothing. If the second grep matches an unrelated identifier (e.g. `arr`, `error`), STOP and rename the local variable in `aiChartReview.ts` to remove the substring. The contract is that the **literal Engine A field tokens** must not appear anywhere in the helper source.

- [ ] **Step 5: Commit**

```bash
git add static/react-app/app/src/lib/aiChartReview.ts static/react-app/app/scripts/smoke-aiChartReview.mjs
git commit -m "feat(ai-review): add aiChartReview helper (capture, downscale, POST)"
```

---

## Phase 3 — AIReviewCard component

### Task 3.1: Create `AIReviewCard.tsx`

**Files:**
- Create: `static/react-app/app/src/components/athena/AIReviewCard.tsx`
- Modify: `static/react-app/app/src/components/athena/index.ts` (one line)

The card is read-only. No execute button. Layout matches spec §13. Concordance pill colors:
- `agree` → `bg-emerald-500/15 text-emerald-300 border-emerald-500/40`
- `partial` → `bg-amber-500/15 text-amber-300 border-amber-500/40`
- `disagree` → `bg-rose-500/15 text-rose-300 border-rose-500/40`
- `unknown` → `bg-zinc-500/15 text-zinc-300 border-zinc-500/40`

- [ ] **Step 1: Verify UI primitives exist**

Run:
```bash
ls static/react-app/app/src/components/ui/card.tsx static/react-app/app/src/components/ui/badge.tsx
```

Expected: both files listed. (They are imported by `VisionReviewCard.tsx`.) If missing, STOP and report.

- [ ] **Step 2: Write the component**

Create `static/react-app/app/src/components/athena/AIReviewCard.tsx`:

```typescript
import { Sparkles } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type {
  AIChartReviewResponse,
  AIChartReviewConcordanceState,
} from '@/types/athena';

const CONCORDANCE_PILL: Record<AIChartReviewConcordanceState, string> = {
  agree:
    'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  partial:
    'bg-amber-500/15 text-amber-300 border-amber-500/40',
  disagree:
    'bg-rose-500/15 text-rose-300 border-rose-500/40',
  unknown:
    'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

const VERDICT_PILL: Record<string, string> = {
  VALID: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  CAUTION: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  INVALID: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  NO_TRADE: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
};

function show(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return value.trim() === '' ? fallback : value;
  return String(value);
}

function showList(items: unknown, fallback = '—'): string[] | null {
  if (!Array.isArray(items) || items.length === 0) return null;
  return items.map((it) => String(it));
}

function deltaSeconds(a?: string | null, b?: string | null): number | null {
  if (!a || !b) return null;
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return null;
  return Math.round(Math.abs(ta - tb) / 1000);
}

export interface AIReviewCardProps {
  response: AIChartReviewResponse;
}

export default function AIReviewCard({ response }: AIReviewCardProps) {
  const ai = response.ai_review;
  const c = response.concordance;
  const ts = response.timestamps;
  const ctx = response.engine_a_context;
  const atrFreshness = ctx?.atr?.atr_freshness_status;
  const atrTf = ctx?.atr?.atr_tf;
  const atrAge = ctx?.atr?.atr_age_seconds;
  const scanDelta = deltaSeconds(ts.scan_timestamp, ts.chart_captured_at);
  const verdictClass = VERDICT_PILL[ai.verdict] ?? VERDICT_PILL.NO_TRADE;
  const concordanceClass = CONCORDANCE_PILL[c.concordance];

  const supporting = showList(ai.supporting_reasons);
  const risks = showList(ai.risks);
  const missing = showList(ai.missing_context);
  const warnings = showList(response.mismatch_warnings);

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Sparkles className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold">AI Chart Review</span>
          <Badge className={`${verdictClass} text-[10px] border`}>
            {ai.verdict}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            confidence: {ai.confidence}
          </Badge>
          <Badge className={`${concordanceClass} text-[10px] border`}>
            {c.concordance}
          </Badge>
          {c.divergence_type !== 'none' && (
            <Badge variant="outline" className="text-[10px]">
              divergence: {c.divergence_type.replace(/_/g, ' ')}
            </Badge>
          )}
          <span className="text-[10px] text-muted-foreground font-mono ml-auto">
            {response.provider}/{show(response.model, '—')}
            {response.dedup_hit ? ' · cached' : ''}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
          <Row label="Human action" value={show(ai.human_action)} />
          <Row label="Setup type" value={show(ai.setup_type)} />
          <Row label="Visual confirmation" value={show(ai.visual_confirmation)} />
          <Row label="Visual contradiction" value={show(ai.visual_contradiction)} />
          <Row label="Engine A alignment" value={show(ai.engine_a_alignment)} />
          <Row label="ATR / RR assessment" value={show(ai.atr_rr_assessment)} />
          <Row
            label="Freshness assessment"
            value={
              ai.freshness_assessment
                ? ai.freshness_assessment
                : `ATR ${show(atrFreshness)} (${show(atrTf)}, age ${
                    atrAge == null ? '—' : `${atrAge}s`
                  })`
            }
          />
          <Row label="Entry quality" value={show(ai.entry_quality)} />
        </div>

        <ListBlock label="Supporting reasons" items={supporting} />
        <ListBlock label="Risks" items={risks} />
        <ListBlock label="Missing context" items={missing} />

        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px] text-muted-foreground border-t border-border/40 pt-2">
          <KV label="chart captured" value={show(ts.chart_captured_at)} />
          <KV
            label="scan timestamp"
            value={`${show(ts.scan_timestamp)}${
              scanDelta == null ? '' : ` (Δ ${scanDelta}s)`
            }`}
          />
          <KV label="latest candle" value={show(ts.latest_candle_ts)} />
        </div>

        {warnings && (
          <div className="text-[11px] text-warning border border-border/40 rounded-md p-2">
            <div className="font-semibold mb-1">Mismatch warnings</div>
            <ul className="list-disc list-inside space-y-0.5">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        {c.divergence_note && (
          <div className="text-[11px] text-muted-foreground border border-border/40 rounded-md p-2">
            <span className="font-semibold">Concordance note: </span>
            {c.divergence_note}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border/40 rounded-md px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="text-[11px] break-words">{value}</div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="font-mono">
      <span className="text-muted-foreground">{label}: </span>
      <span>{value}</span>
    </div>
  );
}

function ListBlock({
  label,
  items,
}: {
  label: string;
  items: string[] | null;
}) {
  if (!items) return null;
  return (
    <div className="text-[11px]">
      <div className="text-[10px] text-muted-foreground mb-1">{label}</div>
      <ul className="list-disc list-inside space-y-0.5">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Re-export from athena index**

Read current `index.ts`:
```bash
cat static/react-app/app/src/components/athena/index.ts
```

Append (preserving existing content) a single line:
```typescript
export { default as AIReviewCard } from './AIReviewCard';
```

If the file uses a different export style (e.g., `export *`), match its convention.

- [ ] **Step 4: TypeScript check**

Run from `static/react-app/app/`:
```bash
npx tsc --noEmit
```

Expected: PASS.

- [ ] **Step 5: Build sanity check**

Run from `static/react-app/app/`:
```bash
npm run build
```

Expected: build succeeds and emits a fresh bundle into `dist/assets/`. If the project does not emit to `dist/`, locate the bundle output directory listed in `vite.config.ts` and confirm a new `index-*.js` was produced.

- [ ] **Step 6: Commit**

```bash
git add static/react-app/app/src/components/athena/AIReviewCard.tsx static/react-app/app/src/components/athena/index.ts
git commit -m "feat(ai-review): add AIReviewCard component"
```

---

## Phase 4 — Wire the button into `TVChartPanel`

### Task 4.1: Add `captureChartCanvas` helper inside `TVChartPanel`

The existing `downloadChartScreenshot` at `TVChartPanel.tsx:1328-1380` composes the output canvas, then immediately exports it as a file. We need the **canvas itself** before the download, so we extract a helper that returns the composed canvas. Then `downloadChartScreenshot` is reduced to: `captureChartCanvas()` → toBlob → download.

**Files:**
- Modify: `static/react-app/app/src/components/panels/TVChartPanel.tsx:1328-1380`

- [ ] **Step 1: Re-read the current function**

```bash
sed -n '1320,1382p' static/react-app/app/src/components/panels/TVChartPanel.tsx
```

Confirm the function body matches the code shown in the audit at lines 1340–1379 (`outputCanvas`, `outputCtx`, `drawImage` loop, `toBlob` download). If it has drifted, adapt the edit in step 2 to the current structure but keep the same extract refactor shape.

- [ ] **Step 2: Refactor `downloadChartScreenshot` into a returning helper**

Replace the existing `downloadChartScreenshot` function (currently roughly lines 1328–1380) with **two** functions: `captureChartCanvas` (new) returns the composed canvas or `null`, and `downloadChartScreenshot` (kept) calls it. Use Edit, preserving the surrounding indentation:

Find this block (exact text — adjust if drift detected in Step 1):

```typescript
  function downloadChartScreenshot() {
    const captureEl = chartCaptureRef.current;
```

Use Edit to replace the whole function (closing brace included) with:

```typescript
  function captureChartCanvas(): HTMLCanvasElement | null {
    const captureEl = chartCaptureRef.current;
    if (!captureEl) {
      setChartError('Chart screenshot failed: capture container missing');
      return null;
    }
    const captureRect = captureEl.getBoundingClientRect();
    if (captureRect.width <= 0 || captureRect.height <= 0) {
      setChartError('Chart screenshot failed: capture area has zero dimensions');
      return null;
    }
    const canvases = Array.from(captureEl.querySelectorAll('canvas'));
    if (!canvases.length) {
      setChartError('Chart screenshot failed: no chart canvases found');
      return null;
    }
    const scale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const outputCanvas = document.createElement('canvas');
    outputCanvas.width = Math.max(1, Math.round(captureRect.width * scale));
    outputCanvas.height = Math.max(1, Math.round(captureRect.height * scale));
    const outputCtx = outputCanvas.getContext('2d');
    if (!outputCtx) {
      setChartError('Chart screenshot failed: canvas context unavailable');
      return null;
    }
    outputCtx.scale(scale, scale);
    outputCtx.fillStyle = '#0b0f14';
    outputCtx.fillRect(0, 0, captureRect.width, captureRect.height);
    for (const canvas of canvases) {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      outputCtx.drawImage(
        canvas,
        rect.left - captureRect.left,
        rect.top - captureRect.top,
        rect.width,
        rect.height,
      );
    }
    drawCaptureLabels(outputCtx, captureEl, captureRect);
    return outputCanvas;
  }

  function downloadChartScreenshot() {
    const outputCanvas = captureChartCanvas();
    if (!outputCanvas) return;
    outputCanvas.toBlob((blob) => {
      if (!blob) {
        setChartError('Chart screenshot failed: PNG export unavailable');
        return;
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const backendTf = TF_BACKEND_MAP[timeframe] || timeframe;
      const screenshotSymbol = pair.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toUpperCase() || 'chart';
      link.href = url;
      link.download = `${screenshotSymbol}-${backendTf}-chart.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }, 'image/png');
  }
```

- [ ] **Step 3: TypeScript check**

```bash
cd static/react-app/app && npx tsc --noEmit && cd -
```

Expected: PASS.

- [ ] **Step 4: Manual UI smoke**

Start the dev server (or use the existing `npm run dev` command in the repo). Open the React app, load any pair, click the existing **Screenshot** button. Expected: PNG download works exactly as before — the refactor is behaviour-preserving.

If the download breaks, STOP and revert before continuing.

- [ ] **Step 5: Commit**

```bash
git add static/react-app/app/src/components/panels/TVChartPanel.tsx
git commit -m "refactor(chart): extract captureChartCanvas helper from screenshot"
```

---

### Task 4.2: Add AI Review state + button + render

**Files:**
- Modify: `static/react-app/app/src/components/panels/TVChartPanel.tsx` (imports, state, button, render)

- [ ] **Step 1: Add imports**

Locate the existing icon imports near the top of `TVChartPanel.tsx`:
```bash
grep -n "from 'lucide-react'" static/react-app/app/src/components/panels/TVChartPanel.tsx | head -2
```

Edit the import line that includes `Camera` to also import `Sparkles`. For example, if the current line is:
```typescript
import { Camera } from 'lucide-react';
```

Change to:
```typescript
import { Camera, Sparkles } from 'lucide-react';
```

If `Sparkles` is already imported, skip this micro-edit.

Then add these imports near the other `@/lib/` and `@/types/` imports in the file (find a sensible spot just below the existing `apiClient`/`visionReview` imports):

```typescript
import {
  AI_CHART_REVIEW_DEFAULTS,
  buildScreenshotMeta,
  canvasToDataUrl,
  downscaleToCap,
  postChartReview,
} from '@/lib/aiChartReview';
import AIReviewCard from '@/components/athena/AIReviewCard';
import type { AIChartReviewResponse } from '@/types/athena';
```

- [ ] **Step 2: Add state + handler near the other React state declarations**

Locate the section in `TVChartPanel` where existing state like `chartError`, `loading`, etc., is declared (search for `const [chartError`). Append three new `useState` hooks right after the existing block:

```typescript
  const [aiReview, setAiReview] = useState<AIChartReviewResponse | null>(null);
  const [aiReviewLoading, setAiReviewLoading] = useState<boolean>(false);
  const [aiReviewError, setAiReviewError] = useState<string | null>(null);
```

Then, **just after `downloadChartScreenshot`** (added in Task 4.1), add the AI review handler:

```typescript
  async function runAIReview() {
    if (aiReviewLoading) return;
    setAiReviewError(null);
    setAiReview(null);
    const sourceCanvas = captureChartCanvas();
    if (!sourceCanvas) return;
    const downscaled = downscaleToCap(
      sourceCanvas,
      AI_CHART_REVIEW_DEFAULTS.MAX_WIDTH,
      AI_CHART_REVIEW_DEFAULTS.MAX_HEIGHT,
    );
    setAiReviewLoading(true);
    try {
      const dataUrl = await canvasToDataUrl(downscaled);
      const backendTf = TF_BACKEND_MAP[timeframe] || timeframe;
      const overlays: string[] = [];
      if (volumeBars) overlays.push('volume');
      if (vwapEnabled) overlays.push('vwap');
      if (ema20) overlays.push('ema20');
      if (ema21) overlays.push('ema21');
      if (ema50) overlays.push('ema50');
      if (ema200) overlays.push('ema200');
      if (dema200) overlays.push('dema200');
      if (atr14) overlays.push('atr14');
      if (rsi14) overlays.push('rsi14');
      if (adx14) overlays.push('adx14');
      overlays.push('candles');
      const meta = buildScreenshotMeta({
        width: downscaled.width,
        height: downscaled.height,
        chart_timeframe: backendTf,
        overlays,
      });
      const symbol = (pair || '').toUpperCase();
      if (!symbol) {
        throw new Error('No symbol selected');
      }
      const response = await postChartReview({
        symbol,
        timeframe: backendTf,
        provider: 'default',
        screenshot_base64: dataUrl,
        screenshot_meta: meta,
      });
      setAiReview(response);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'AI review failed';
      setAiReviewError(msg);
    } finally {
      setAiReviewLoading(false);
    }
  }
```

Notes for the executor:
- `pair`, `timeframe`, `volumeBars`, `vwapEnabled`, `ema20`, `ema21`, `ema50`, `ema200`, `dema200`, `atr14`, `rsi14`, `adx14`, `TF_BACKEND_MAP` are already in scope (they are used in the existing `downloadChartScreenshot` and the indicator switches at lines 1692–1704).
- If any of those identifiers is named differently in current code, match the existing names — grep first:
  ```bash
  grep -nE 'const \[(volumeBars|vwapEnabled|ema20|ema21|ema50|ema200|dema200|atr14|rsi14|adx14)' static/react-app/app/src/components/panels/TVChartPanel.tsx
  ```
- Only the **overlay names** are sent. Engine A fields are not.

- [ ] **Step 3: Add the AI Review button next to the existing Camera button**

Find the existing Camera button at roughly `TVChartPanel.tsx:1674-1684`:

```bash
grep -n "Camera className" static/react-app/app/src/components/panels/TVChartPanel.tsx
```

Add a sibling `<Button>` immediately after the existing Screenshot button (before its closing `</div>` at line 1685). Use Edit on the closing `</Button>` of the Camera block to append the new button:

Old text:
```typescript
              <Camera className="h-3.5 w-3.5" />
              Screenshot
            </Button>
          </div>
```

New text:
```typescript
              <Camera className="h-3.5 w-3.5" />
              Screenshot
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-2 text-xs"
              onClick={runAIReview}
              disabled={loading || aiReviewLoading || !candles?.length}
              aria-label="Run AI chart review"
              aria-busy={aiReviewLoading}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {aiReviewLoading ? 'Reviewing…' : 'AI Review'}
            </Button>
          </div>
```

- [ ] **Step 4: Render the card and error below the chart**

Find the `<CardContent>` that wraps the chart (`grep -n "<CardContent" static/react-app/app/src/components/panels/TVChartPanel.tsx`). Locate its closing `</CardContent>`. Insert the AI review card just before that closing tag (so it appears beneath the chart and any existing right-side panel):

```typescript
            {aiReviewError && (
              <div className="text-[11px] text-warning border border-border/40 rounded-md p-2">
                AI review error: {aiReviewError}
              </div>
            )}
            {aiReview && <AIReviewCard response={aiReview} />}
```

If the chart `CardContent` uses a `grid` layout (e.g. `grid xl:grid-cols-[...]`), wrap the two new lines in a `<div className="xl:col-span-2 space-y-2">` so the card spans the full width below the chart + side panel. Verify by inspecting the actual grid markup:

```bash
sed -n '1715,1735p' static/react-app/app/src/components/panels/TVChartPanel.tsx
```

If the grid is `xl:grid-cols-[minmax(0,1fr)_320px]`, use the col-span wrapper; otherwise drop the card in directly.

- [ ] **Step 5: TypeScript check**

```bash
cd static/react-app/app && npx tsc --noEmit && cd -
```

Expected: PASS.

- [ ] **Step 6: Build**

```bash
cd static/react-app/app && npm run build && cd -
```

Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add static/react-app/app/src/components/panels/TVChartPanel.tsx
git commit -m "feat(ai-review): wire AI Review button into TVChartPanel"
```

---

## Phase 5 — End-to-end manual verification

### Task 5.1: Backend curl smoke (env-gated)

**Files:**
- Read only.

- [ ] **Step 1: Confirm env**

Run:
```bash
echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:+set} AI_CHART_REVIEW_ENABLED=${AI_CHART_REVIEW_ENABLED}"
```

Expected: `ANTHROPIC_API_KEY=set AI_CHART_REVIEW_ENABLED=true` (or `1` / `True`).

If unset, ask the user before continuing. Do **not** put the API key on the command line history. The user should `export` it in their shell or place it in `.env`.

- [ ] **Step 2: Capture a real PNG**

The smallest valid PNG that passes validation is any real chart screenshot. Either:
- click the existing **Screenshot** button in the running React app to save `BTCUSDT-H4-chart.png`, OR
- use any existing PNG that is ≤ 2 MB.

Convert to a base64 data URL:
```bash
python -c "import base64,sys; b=open(sys.argv[1],'rb').read(); print('data:image/png;base64,'+base64.b64encode(b).decode())" /path/to/chart.png > /tmp/png_b64.txt
```

- [ ] **Step 3: Build the request body**

Create `/tmp/ai_review_req.json`:

```bash
python - <<'PY' > /tmp/ai_review_req.json
import json, datetime
with open('/tmp/png_b64.txt') as f:
    data_url = f.read().strip()
print(json.dumps({
    "symbol": "BTCUSDT",
    "timeframe": "H4",
    "provider": "default",
    "screenshot_base64": data_url,
    "screenshot_meta": {
        "width": 1280,
        "height": 720,
        "native_chart": True,
        "chart_timeframe": "H4",
        "overlays": ["candles","ema50","ema200","atr14"],
        "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
    },
}))
PY
```

- [ ] **Step 4: POST to the running server**

Start `athena.py` in another terminal, then:
```bash
curl -s -X POST -H 'Content-Type: application/json' -d @/tmp/ai_review_req.json http://127.0.0.1:5000/api/ai/chart-review | python -m json.tool > /tmp/ai_review_resp.json
cat /tmp/ai_review_resp.json
```

Expected fields per spec §5.7:
- `review_id`: non-empty UUID hex
- `provider`: `"anthropic"`
- `model`: e.g. `"claude-opus-4-7"`
- `engine_a_context.threshold`, `engine_a_context.atr.atr_value`, `engine_a_context.geometry.rr`: real values from server state (none are literal placeholders like `0`, `null`, or example strings; values reflect live scoring)
- `concordance.engine_a_passed`: matches the scanner pass/fail for this pair
- `ai_review.verdict` ∈ {`VALID`,`CAUTION`,`INVALID`,`NO_TRADE`}
- `mismatch_warnings`: array (likely empty)
- `dedup_hit`: `false`

If the response body contains the string `data:image/png;base64`, STOP — the screenshot base64 is leaking into the response, which violates spec §5.7.

- [ ] **Step 5: Verify persistence**

```bash
python -c "
import sqlite3, os, json
db = os.environ.get('ATHENA_AUDIT_DB', 'audit.db')
conn = sqlite3.connect(db)
row = conn.execute('SELECT review_id, symbol, timeframe, screenshot_hash, length(screenshot_meta_json), parse_success, json_extract(concordance_json, \"\$.concordance\") FROM ai_chart_reviews ORDER BY created_at DESC LIMIT 1').fetchone()
print(row)
meta = conn.execute('SELECT screenshot_meta_json FROM ai_chart_reviews ORDER BY created_at DESC LIMIT 1').fetchone()[0]
assert 'base64' not in meta, 'screenshot base64 leaked into screenshot_meta_json'
print('OK: no base64 substring in meta')
"
```

Expected: one row printed with a non-empty `screenshot_hash`, `parse_success=1`, and `OK: no base64 substring in meta`.

- [ ] **Step 6: Verify dedup**

POST the same body again within 60 seconds:
```bash
curl -s -X POST -H 'Content-Type: application/json' -d @/tmp/ai_review_req.json http://127.0.0.1:5000/api/ai/chart-review | python -c "import sys,json; r=json.load(sys.stdin); print('dedup_hit=',r.get('dedup_hit'))"
```

Expected: `dedup_hit= True`. Backend log should show **no** new `anthropic.messages.create` call (latency near-zero).

- [ ] **Step 7: No commit.**

This task is verification only.

---

### Task 5.2: Frontend end-to-end smoke

**Files:**
- Read only.

- [ ] **Step 1: Start dev server**

Run from `static/react-app/app/`:
```bash
npm run dev
```

Or use the existing `npm start` if that is the project's convention. Open the URL in a browser.

- [ ] **Step 2: Click AI Review on a real chart**

1. Pick a live symbol (e.g. `BTCUSDT` on H4).
2. Wait for candles to load (the button is disabled while `loading` or no candles).
3. Click **AI Review**.

Expected:
- The button shows `Reviewing…` and `aria-busy="true"`.
- Within a few seconds, the `AIReviewCard` renders beneath the chart with:
  - a verdict badge (one of VALID/CAUTION/INVALID/NO_TRADE)
  - a confidence badge `0–100`
  - a concordance pill (`agree`/`partial`/`disagree`/`unknown`)
  - all eight description rows (visual confirmation, contradiction, engine_a_alignment, atr_rr_assessment, freshness_assessment, entry_quality, setup_type, human_action)
  - three timestamps (chart captured, scan timestamp with Δ, latest candle)
  - mismatch warnings block (or empty)

- [ ] **Step 3: DevTools — confirm no Engine A leakage in request**

Open browser DevTools → Network → click **AI Review** → inspect the `chart-review` POST request body.

Expected: the body has exactly `symbol`, `timeframe`, `provider`, `screenshot_base64`, `screenshot_meta` and nothing else. Specifically, the keys `engineAScore`, `confluence`, `threshold`, `atr`, `rr`, `multiplier`, `passed`, `engineAContext` MUST NOT appear in the request body.

If any of those keys appears, STOP and revert Task 4.2.

- [ ] **Step 4: Click AI Review again within 60 s**

Expected: the card re-renders with the dedup-cached response. The provider badge shows `· cached`. The Network tab shows the response comes back near-instantly (no Anthropic latency).

- [ ] **Step 5: Disable the feature flag, refresh, click again**

In `config.local.yaml` (or `.env`), set `AI_CHART_REVIEW_ENABLED=false`, restart the backend, click AI Review. Expected: error banner `AI review error: AI chart review disabled` and no card.

Restore `AI_CHART_REVIEW_ENABLED=true` afterwards.

- [ ] **Step 6: No commit.**

Verification only.

---

## Self-review checklist (run before declaring complete)

1. **Spec coverage** — every spec §13 frontend item has a task:
   - PNG capture: Task 4.1.
   - downscale to 1280×720: Task 2.1 + 4.2.
   - POST to `/api/ai/chart-review`: Task 2.1 + 4.2.
   - `AIReviewCard` render: Task 3.1.
   - AI Review button in `TVChartPanel`: Task 4.2.
   - Response types in `athena.ts`: Task 1.1.
   - F1 contract (no Engine A fields in helper): Task 2.1 step 4 + Task 5.2 step 3.
   - F2 downscale aspect ratio: Task 2.1 step 3 (Node smoke).
   - F3 card renders verdict/confidence/concordance/timestamps: Task 5.2 step 2.

2. **Placeholder scan** — no `TBD`, `TODO`, `implement later`, `add appropriate handling`, or `Similar to Task N`. Spot-checked above.

3. **Type consistency**:
   - `AIChartReviewResponse` keys used in `AIReviewCard.tsx` (Task 3.1) match the interface definition (Task 1.1).
   - `AI_CHART_REVIEW_DEFAULTS`, `buildScreenshotMeta`, `canvasToDataUrl`, `downscaleToCap`, `postChartReview` exported in Task 2.1 and imported in Task 4.2 — names match.
   - `captureChartCanvas` introduced in Task 4.1 is called in Task 4.2 — names match.

4. **Non-touch list intact** — every task above only edits frontend files, plus a backend curl/SQL read in Phase 5. Engine A scoring, gates, kill switches, thresholds, and the legacy `chart_renderer.py` path are untouched.

---

## Risks during execution

- **`pair` value format mismatch** — `pair` in `TVChartPanel` may already be uppercase or may include a prefix. If `runAIReview` POSTs a malformed symbol, the backend returns 422 ("Engine A returned no result"). Resolution: inspect the actual `pair` value via `console.log` and match the format the scanner uses.
- **`TF_BACKEND_MAP` missing keys** — if the dev clicks AI Review while on a chart timeframe not present in `TF_BACKEND_MAP`, the request sends the raw timeframe. Backend should reject with 422. Acceptable.
- **CORS / proxy** — if dev server proxies `/api/*` to backend, no extra work. If not, set `VITE_API_BASE` in `.env.local` to the backend URL.
- **`aiReview` state persists across pair changes** — out of scope for v1, but if review for `BTCUSDT` is showing and the user switches to `ETHUSDT`, the stale card stays. Acceptable for v1; user can click AI Review again.
- **F1 grep false positives** — if a local helper variable inside `aiChartReview.ts` happens to contain `atr` as a substring (e.g. `iterator`, `error`), rename it to avoid the literal token.
