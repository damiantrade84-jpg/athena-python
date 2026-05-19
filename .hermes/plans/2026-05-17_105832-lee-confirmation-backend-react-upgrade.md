# Lee Confirmation Analyst Backend + React UI Upgrade Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after Damian approves it.

**Goal:** Add Lee as a read-only, second-opinion confirmation analyst inside Athena, with a backend confirmation endpoint and a React UI card/panel that shows whether live external context supports, weakens, blocks, or needs more data for a deterministic Athena trade signal.

**Architecture:** Athena deterministic gates remain the authority. Lee is an advisory analysis layer that consumes Athena's selected-signal packet plus read-only external context summaries, then returns a structured confirmation card. The UI displays Lee's verdict beside the existing AI Trading Agent/Strategist flow without creating any execution, order-routing, threshold-changing, or auto-override path.

**Tech Stack:** Flask routes + Python advisory modules + Pydantic contracts + pytest backend tests; React 19 + TypeScript + Vite + existing shadcn-style UI components for frontend; no new frontend test harness currently exists, so UI verification is `npm run build`/`npm run lint` unless a test stack is added later.

---

## Current Context Verified

- Repo: `C:/dev/athena-python`, branch expected `main`.
- Current AI route: `POST /api/ai/trade-chat` in `athena_app/api/routes_ai_agent.py`.
- Chat orchestrator: `ai_trade_chat.py`.
- Safety guard: `ai_agent_safety.py` forces read-only, `can_execute=False`, and downgrades unsafe execution language.
- AI contracts: `ai_contracts.py` has `AIReviewPacket`, `AIMarketIntelligenceContext`, and `AITradeDecision`.
- Read-only tool registry: `ai_tools.py`; tools expose `advisory_only=True`, `execution_allowed=False`.
- Existing UI panel: `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx`.
- API client: `static/react-app/app/src/lib/apiClient.ts`.
- React types: `static/react-app/app/src/types/athena.ts`.
- Existing backend tests:
  - `tests/test_ai_trade_chat.py`
  - `tests/test_routes_ai_agent.py`
- Frontend currently has scripts only: `dev`, `build`, `lint`; no Vitest/Jest test files found.

---

## Non-Negotiable Safety Rules

1. Lee cannot execute trades.
2. Lee cannot modify thresholds, risk settings, guardian status, kill switch, broker state, or position sizing.
3. Lee cannot upgrade a trade blocked by Athena gates into an executable trade.
4. Lee verdict is external/advisory only: `CONFIRM`, `WAIT`, `BLOCK`, or `NEED_MORE_DATA`.
5. If external data is missing, stale, partial, or failed, Lee must say that and return `NEED_MORE_DATA` or conservative `WAIT`, not invent news/macro facts.
6. Backend response must include `safety.read_only=True`, `safety.can_execute=False`, `safety.can_modify_thresholds=False`, `advisory_only=True`, `execution_allowed=False`.
7. UI copy must clearly say: **Lee confirmation is advisory-only; Athena deterministic gates remain authoritative.**
8. No Hermes bridge/tool execution in Phase 1 unless explicitly approved later. This plan builds the safe in-app confirmation layer first.

---

## Proposed Upgrade Shape

### New Backend Endpoint

`POST /api/ai/lee-confirmation`

Request:

```json
{
  "trace_id": "trace-1",
  "symbol": "BTCUSDT",
  "message": "Confirm this setup using external context.",
  "signal": { "...selected signal payload...": true },
  "include_news": true,
  "include_macro": true,
  "include_sentiment": true,
  "include_correlation": true
}
```

Response:

```json
{
  "schema_version": "lee_confirmation.v1",
  "session_id": "lee-confirmation-...",
  "trace_id": "trace-1",
  "symbol": "BTCUSDT",
  "lee_verdict": "WAIT",
  "confidence": "medium",
  "headline": "Technical setup is plausible, but macro/event risk is not clean enough to confirm yet.",
  "reason": "Athena has a watchlist/valid technical setup, but Lee found unresolved external risk or missing freshness.",
  "external_context": {
    "news_risk": { "status": "partial", "summary": "...", "items": [] },
    "macro_risk": { "status": "fresh", "summary": "...", "events": [] },
    "sentiment": { "status": "unavailable", "summary": "No sentiment source configured." },
    "volatility": { "status": "fresh", "summary": "..." },
    "correlation": { "status": "fresh", "summary": "..." }
  },
  "athena_context": {
    "deterministic_decision": "WATCHLIST",
    "gate_state": "not_executable_or_unknown",
    "risk_flags": []
  },
  "supports": [],
  "red_flags": [],
  "confirmation_needed": [],
  "missing_data": [],
  "final_advice": "Wait for the listed confirmations. Do not treat this as execution permission.",
  "safety": {
    "read_only": true,
    "can_execute": false,
    "can_modify_thresholds": false,
    "deterministic_gates_required": true
  },
  "advisory_only": true,
  "execution_allowed": false,
  "created_at": "..."
}
```

### UI Placement

Add a Lee confirmation area inside `AITradingAgentPanel.tsx`, likely as a third tab:

- Existing tab: `review`
- Existing tab: `brief`
- New tab: `lee` or `confirm`

The tab shows:

- Button: `Ask Lee for confirmation`
- Verdict badge: `CONFIRM` / `WAIT` / `BLOCK` / `NEED_MORE_DATA`
- Confidence badge
- Headline/reason
- External context cards: News, Macro, Sentiment, Volatility, Correlation
- Red flags and confirmations needed
- Safety notice
- Raw response collapsible for debugging

---

## Risk Classification

**High-risk advisory/trading change.**

Risk: LLM/advisory layer accidentally sounds like execution permission.
- File/function: `ai_trade_chat.py`, future `ai_lee_confirmation.py`, `ai_agent_safety.py`, UI copy.
- Failure mode: user interprets Lee `CONFIRM` as "place the trade now".
- Impact: unsafe manual trading decisions.
- Mitigation: response and UI must always carry advisory-only safety envelope; wording must say "confirmation of context, not execution permission".
- Test to prove it: backend test asserts `can_execute=False`, `execution_allowed=False`, and final advice does not contain unsafe execution verbs even if user says "execute/place/open".

Risk: Missing/stale external data is treated as clean confirmation.
- File/function: `ai_lee_confirmation.py`, external-context tools.
- Failure mode: source fetch fails but verdict returns `CONFIRM`.
- Impact: false confidence.
- Mitigation: fail closed; stale/failed source adds `missing_data` and prevents high-confidence `CONFIRM`.
- Test to prove it: monkeypatch external context provider to return unavailable/stale; assert verdict is `NEED_MORE_DATA` or `WAIT`, never `CONFIRM high`.

Risk: Lee overrides deterministic blocked gates.
- File/function: `ai_agent_safety.py`, `ai_lee_confirmation.py`.
- Failure mode: Athena gate blocked, Lee returns `CONFIRM`.
- Impact: undermines risk engine.
- Mitigation: deterministic gate state clamps verdict to `BLOCK` or `WAIT`; never `CONFIRM`.
- Test to prove it: blocked Engine D/guardian/fee/RR packet returns `lee_verdict != CONFIRM`.

Risk: UI wires confirmation button to wrong selected signal.
- File/function: `AITradingAgentPanel.tsx`.
- Failure mode: selected card changes but Lee confirms stale previous signal.
- Impact: wrong-symbol advice.
- Mitigation: reset Lee state on `symbol/traceId/signal` changes; request includes `signal` payload and resolved symbol/trace.
- Test/verification: manual browser check and TypeScript build; future UI test if harness added.

---

## Implementation Phases

## Phase 0 — Approval Gate

**Objective:** Do not implement until Damian approves the plan.

**Files:** none.

**Acceptance:** Damian confirms whether to start with Phase 1 exactly as planned, adjust scope, or add Hermes bridge earlier/later.

---

## Phase 1 — Backend Contracts and Deterministic Lee Confirmation Engine

### Task 1.1: Add Lee confirmation Pydantic contracts

**Objective:** Define stable response/request-adjacent models and enums without changing runtime behavior.

**Files:**
- Modify: `ai_contracts.py`
- Test: `tests/test_ai_lee_confirmation.py` (new)

**Contract to add:**

- `LeeVerdict = Literal["CONFIRM", "WAIT", "BLOCK", "NEED_MORE_DATA"]`
- `LeeConfidence = Literal["low", "medium", "high"]`
- `LeeExternalContextBlock`
- `LeeConfirmationCard`

**Required fields:**

- `schema_version: "lee_confirmation.v1"`
- `trace_id`, `symbol`
- `lee_verdict`
- `confidence`
- `headline`
- `reason`
- `external_context`
- `athena_context`
- `supports`
- `red_flags`
- `confirmation_needed`
- `missing_data`
- `final_advice`
- `safety`
- `advisory_only=True`
- `execution_allowed=False`

**TDD:**

1. Write test that instantiates model with `advisory_only=False` and `execution_allowed=True`.
2. Expected RED: model/class does not exist.
3. Implement validators that force `advisory_only=True`, `execution_allowed=False`, `safety.can_execute=False`.
4. Expected GREEN: test passes.

**Run:**

```bash
pytest tests/test_ai_lee_confirmation.py -q
```

---

### Task 1.2: Create deterministic external context normalizer

**Objective:** Convert existing Athena market intelligence and future source outputs into conservative Lee context blocks.

**Files:**
- Create: `ai_lee_confirmation.py`
- Test: `tests/test_ai_lee_confirmation.py`

**Functions:**

- `_context_block(name: str, raw: Any, status: str = "unavailable") -> dict`
- `_summarize_market_intelligence(packet_or_signal: dict) -> dict`
- `_build_external_context(signal: dict | None, packet: dict | None, options: dict) -> dict`

**Design:**

- Use existing `market_intelligence` in signal/packet first.
- Do not call new paid/external APIs yet.
- If data is missing, return status `unavailable` and add missing-data notes.
- Preserve future extension points for news/calendar/sentiment providers.

**TDD:**

- Test market intelligence with `freshness_status=fresh` maps into `macro_risk.status=fresh`.
- Test missing market intelligence maps to `missing_data` and `status=unavailable`.

---

### Task 1.3: Build `run_lee_confirmation(request)` deterministic core

**Objective:** Produce a Lee confirmation card using selected signal, AIReviewPacket, safety clamps, and external context.

**Files:**
- Modify: `ai_lee_confirmation.py`
- Test: `tests/test_ai_lee_confirmation.py`

**Function:**

```python
def run_lee_confirmation(request: dict[str, Any]) -> dict[str, Any]:
    ...
```

**Inputs:**

- `trace_id`
- `symbol`
- `signal`
- `message`
- `include_news`, `include_macro`, `include_sentiment`, `include_correlation`
- `_audit_db` optional later if conversation logging is added

**Logic:**

- Resolve selected signal using existing `ai_tools.get_signal_detail_tool`-style lookup or shared helper if practical.
- Build packet via existing `ai_tools`/`ai_orchestrator` path where possible.
- Determine Athena gate state:
  - blocked/failed gate -> `BLOCK`
  - missing signal/packet -> `NEED_MORE_DATA`
  - valid/watchlist but external context incomplete -> `WAIT`
  - valid technical + clean/fresh external context + no red flags -> `CONFIRM`
- Confidence:
  - `high` only when packet exists, deterministic gates clean enough, and external context is fresh/complete.
  - `medium` for partial but useful context.
  - `low` for missing/stale context.
- Always call/apply a Lee safety clamp before returning.

**TDD cases:**

1. Missing message should still be allowed? Decision: use default message if omitted; this endpoint is button-driven. Test default.
2. Missing symbol/signal returns `NEED_MORE_DATA` and missing-data notes.
3. Blocked deterministic gate returns `BLOCK`, never `CONFIRM`.
4. Fresh clean signal + fresh macro context can return `CONFIRM`.
5. Stale/unavailable context returns `WAIT` or `NEED_MORE_DATA`, never high-confidence confirm.
6. User prompt containing "place/open/execute" cannot produce execution language.

---

## Phase 2 — Backend Route

### Task 2.1: Register `/api/ai/lee-confirmation`

**Objective:** Expose the deterministic Lee card through Flask.

**Files:**
- Modify: `athena_app/api/routes_ai_agent.py`
- Test: `tests/test_routes_ai_agent.py`

**Implementation notes:**

- Import `run_lee_confirmation` from `ai_lee_confirmation.py`.
- Reuse route pattern from `/api/ai/trade-chat`.
- Parse `trace_id`, `symbol`, `message`, `signal` safely.
- Pass `audit_db` as `_audit_db` only if needed for future logging.
- Return JSON-safe payload using runtime `json_safe` when available.

**TDD cases:**

1. Route returns status 200 and `schema_version == "lee_confirmation.v1"`.
2. Route returns `safety.can_execute is False`.
3. Route with `message: "execute this"` still returns `execution_allowed is False` and final advice does not say to execute.
4. Route with provided selected signal resolves from request payload even if runtime cache misses.

**Run:**

```bash
pytest tests/test_ai_lee_confirmation.py tests/test_routes_ai_agent.py -q
```

---

## Phase 3 — Optional LLM Narrative Layer for Lee, Still Grounded

**Important:** This is optional. I recommend not doing it until deterministic card is green.

### Task 3.1: Add Lee narrative from structured card only

**Objective:** Let configured LLM produce a warmer Lee wording while preserving deterministic card fields.

**Files:**
- Modify: `ai_lee_confirmation.py`
- Test: `tests/test_ai_lee_confirmation.py`

**Function:**

- `_try_lee_confirmation_llm(card: dict, request: dict) -> tuple[str | None, str]`

**Rules:**

- LLM can only fill/replace `narrative` or `answer`, not verdict/safety/gates.
- LLM prompt includes hard instruction: never execution permission, never override Athena gates.
- If LLM output contains unsafe execution verbs, discard and use deterministic narrative.
- If API key/model missing, deterministic fallback.

**Tests:**

- Monkeypatch fake LLM unsafe output: must fall back.
- Monkeypatch fake LLM safe output: `narrative` changes but `lee_verdict` and safety fields unchanged.

---

## Phase 4 — Frontend Types and API Client

### Task 4.1: Add TypeScript types

**Objective:** Type the Lee request/response shape for React.

**Files:**
- Modify: `static/react-app/app/src/types/athena.ts`

**Types:**

- `LeeVerdict`
- `LeeConfidence`
- `LeeExternalContextBlock`
- `LeeConfirmationRequest`
- `LeeConfirmationResponse`

**Verification:**

```bash
cd static/react-app/app && npm run build
```

---

### Task 4.2: Add API client method

**Objective:** Add a typed helper to call the new endpoint.

**Files:**
- Modify: `static/react-app/app/src/lib/apiClient.ts`

**Function:**

```ts
export function postLeeConfirmation(payload: LeeConfirmationRequest): Promise<LeeConfirmationResponse> {
  return apiClient.post<LeeConfirmationResponse>(
    '/api/ai/lee-confirmation',
    payload as unknown as Record<string, unknown>,
  );
}
```

**Verification:**

```bash
cd static/react-app/app && npm run build
```

---

## Phase 5 — React UI Integration

### Task 5.1: Add Lee verdict styling helpers

**Objective:** Display verdicts consistently and conservatively.

**Files:**
- Modify: `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx`

**Helpers:**

- `leeVerdictClass(verdict?: string): string`
- `externalContextStatusClass(status?: string): string`

**Colors:**

- `CONFIRM`: emerald, but copy says advisory only.
- `WAIT`: blue/amber.
- `BLOCK`: rose.
- `NEED_MORE_DATA`: amber.

**Verification:** TypeScript build.

---

### Task 5.2: Create `LeeConfirmationCard` component inside the panel file first

**Objective:** Render the response without introducing a new component file until stable.

**Files:**
- Modify: `AITradingAgentPanel.tsx`

**Props:**

```ts
function LeeConfirmationCard({
  response,
  loading,
  error,
  onConfirm,
}: {
  response: LeeConfirmationResponse | null;
  loading: boolean;
  error: string | null;
  onConfirm: () => void;
})
```

**Rendered sections:**

- Header: `Lee Confirmation Analyst`
- Safety line: `Read-only second opinion — Athena gates remain authoritative.`
- Button: `Ask Lee for confirmation`
- Verdict and confidence badges
- Headline/reason
- External context cards
- Supports/red flags/confirmation needed/missing data
- Final advice
- Raw details collapsible

**Verification:**

```bash
cd static/react-app/app && npm run build
cd static/react-app/app && npm run lint
```

---

### Task 5.3: Add Lee tab state and request wiring

**Objective:** Wire button to backend with the currently selected signal.

**Files:**
- Modify: `AITradingAgentPanel.tsx`

**State:**

- `leeResponse`
- `leeLoading`
- `leeError`
- add activeTab union: `'review' | 'brief' | 'lee'`

**Request payload:**

```ts
postLeeConfirmation({
  trace_id: resolvedTraceId,
  symbol: resolvedSymbol,
  message: 'Confirm this Athena setup using external context. Return advisory-only verdict.',
  signal: signal || null,
  include_news: true,
  include_macro: true,
  include_sentiment: true,
  include_correlation: true,
})
```

**Reset rule:**

- Existing `useEffect` that resets messages on signal changes must also clear Lee response/error/loading.

**Verification:**

- Change selected signal -> Lee card clears.
- Request sent includes current `signal`, `trace_id`, `symbol`.
- If backend fails, show error banner and keep UI usable.

---

## Phase 6 — End-to-End Verification

### Backend targeted tests

```bash
pytest tests/test_ai_lee_confirmation.py tests/test_routes_ai_agent.py tests/test_ai_trade_chat.py -q
```

Expected:

- All tests pass.
- No existing Marcus/trade-chat safety regression.

### Frontend build/lint

```bash
cd static/react-app/app && npm run build
cd static/react-app/app && npm run lint
```

Expected:

- TypeScript passes.
- ESLint passes or any pre-existing lint findings are documented separately.

### Manual browser smoke test

1. Start Athena backend normally.
2. Start React app (`npm run dev`) or use existing served build path.
3. Select a live/paper signal.
4. Open AI Trading Agent panel.
5. Click Lee tab.
6. Click `Ask Lee for confirmation`.
7. Confirm:
   - verdict appears,
   - selected symbol/trace is correct,
   - safety notice visible,
   - no execution language,
   - blocked/stale setups do not show confident green confirmation.

---

## Files Likely to Change

Backend:

- `ai_contracts.py`
- `ai_lee_confirmation.py` (new)
- `athena_app/api/routes_ai_agent.py`
- `tests/test_ai_lee_confirmation.py` (new)
- `tests/test_routes_ai_agent.py`
- Maybe `ai_agent_safety.py` only if Lee needs a shared safety clamp beyond local validation.
- Maybe `ai_tools.py` only if we extract reusable read-only external-context helpers.

Frontend:

- `static/react-app/app/src/types/athena.ts`
- `static/react-app/app/src/lib/apiClient.ts`
- `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx`

Not planned in Phase 1:

- Broker/exchange code.
- Risk/guardian threshold mutation.
- Order execution routes.
- Hermes bridge process.
- New paid data-provider dependencies.

---

## Open Questions for Damian

1. Should Lee's verdict wording be strict (`CONFIRM/WAIT/BLOCK/NEED_MORE_DATA`) or more trading-desk styled (`Supports / Caution / Reject / Data gap`) in the UI while keeping strict enum internally?
2. Which external source should be first real provider after the deterministic Phase 1 shell?
   - existing Athena `market_intelligence` only first,
   - news API,
   - economic calendar,
   - crypto sentiment/funding,
   - macro assets (DXY/yields/VIX/gold/BTC dominance).
3. Should Lee live inside the existing AI Trading Agent panel as a tab, or should she get a separate right-side `Lee Copilot` panel?
4. Should Phase 3 LLM narrative be included immediately, or should we ship deterministic cards first and add the narrative after safety tests are stable?
5. Should the endpoint be named `/api/ai/lee-confirmation` or a more generic `/api/ai/confirmation-check`?

---

## Recommended First Build Slice

I recommend this sequence:

1. Phase 1 contracts + deterministic Lee engine.
2. Phase 2 backend route.
3. Phase 4 frontend types/client.
4. Phase 5 UI tab.
5. Only after green tests/build: optional Phase 3 Lee narrative.

Reason: this keeps the delicate trading safety boundary clean. We prove the card and route are deterministic and fail-closed before any LLM wording or Hermes bridge is allowed near it.

---

## Approval Checklist Before Implementation

- [ ] Endpoint name approved.
- [ ] UI location approved.
- [ ] Verdict labels approved.
- [ ] External-source scope approved for first slice.
- [ ] LLM narrative now vs later decided.
- [ ] Confirm no execution/broker/risk setting changes in this upgrade.

Once approved, implementation should follow strict TDD: write failing backend tests first, implement minimal code, run targeted tests, then frontend typing/build, then manual smoke test.
