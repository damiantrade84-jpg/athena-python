# Lee Confirmation Reconciled Implementation Plan

> **For Hermes:** This plan supersedes the first Lee confirmation draft where it conflicts. Implement with TDD and targeted backend/frontend verification only.

**Goal:** Add a Lee/Hermes advisory confirmation card to Athena without creating any execution authority, gate override, threshold mutation, guardian mutation, or client-trusted confirmation path.

**Architecture:** React sends `trace_id`, `symbol`, and an optional UI snapshot. Backend must resolve the authoritative signal from server-side runtime state first. A controlled Lee reasoning adapter may produce warm narrative from a strict evidence packet, but Athena backend validation/finalization remains authoritative and can only downgrade or fail closed.

**Non-negotiables:**
- Athena deterministic gates remain above Lee.
- Lee/Hermes/GPT-5.5 is advisory/read-only only.
- UI must not display visible `CONFIRM`; use safer copy such as `Context supports`.
- No client-payload-only trade-specific confirmation.
- Stale React responses must never overwrite the currently selected signal.

---

## Phase 1 — Backend tests first

Create focused tests for:

1. `/api/ai/lee-confirmation` returns a stable `lee_confirmation.v1` shape for a server-verified signal.
2. Client-only `signal` payload, with no server match, returns `NEED_MORE_DATA` and `trade_specific_confirmation_allowed=false`.
3. Client/server symbol or trace mismatch returns `NEED_MORE_DATA` with a mismatch warning.
4. Blocked deterministic/risk gates cannot become `CONTEXT_SUPPORTS` even if Lee adapter proposes support.
5. Invalid JSON/timeout/exception from Lee adapter returns `NEED_MORE_DATA` and keeps advisory-only safety fields.
6. Unsafe execution wording from the adapter is discarded or downgraded.

Expected initial failure: imports/route/models do not exist yet.

## Phase 2 — Contracts and engine

Add Lee contracts to `ai_contracts.py`:

- `LeeSafetyEnvelope`
- `LeeExternalContext`
- `LeeReasoningDraft`
- `LeeConfirmationResponse`

Use strict defaults:

- `schema_version="lee_confirmation.v1"`
- `advisory_only=True`
- `execution_allowed=False`
- `trade_specific_confirmation_allowed=False` by default unless server signal verified and gates clean enough
- `safety.read_only=True`
- `safety.can_execute=False`
- `safety.can_modify_thresholds=False`
- `safety.can_modify_guardian=False`
- `safety.deterministic_gates_required=True`

Use internal verdict enum:

- `CONTEXT_SUPPORTS`
- `WAIT`
- `CONTEXT_BLOCKS`
- `NEED_MORE_DATA`

Visible UI labels must map these to safer labels, never `CONFIRM`.

Add `ai_lee_confirmation.py`:

- `resolve_authoritative_signal(...)` equivalent should live in route or reusable helper; it must only mark `server_verified_signal=True` when runtime lookup matched.
- `run_lee_confirmation(request, signal, server_verified_signal, adapter=None)` builds evidence, calls adapter, validates result, and finalizes safety.
- `HermesLeeReasoningAdapter` is controlled and optional for now. In local/test mode it can return a deterministic draft. If real Hermes integration is unavailable/invalid, fail closed instead of pretending.
- `finalize_lee_confirmation(...)` clamps any unsafe/unsupported adapter output.

## Phase 3 — Route trust boundary

Add route in `athena_app/api/routes_ai_agent.py`:

`POST /api/ai/lee-confirmation`

Route rules:

1. Parse `trace_id`, `symbol`, optional `signal` snapshot.
2. Resolve server signal with `_find_signal(runtime, trace_id, symbol)`.
3. If no server signal: return `NEED_MORE_DATA`, not trade-specific support.
4. If client snapshot exists, compare trace/symbol against server signal. Mismatch: fail closed.
5. Call `run_lee_confirmation(...)` only with the server-verified signal.
6. Return `runtime.json_safe` response if available.

## Phase 4 — React types and API client

Add types in `static/react-app/app/src/types/athena.ts`:

- `LeeConfirmationVerdict`
- `LeeConfirmationRequest`
- `LeeConfirmationResponse`
- `LeeSafetySummary`

Add `postLeeConfirmation(payload, options?)` to `apiClient.ts`; support `AbortSignal` via `RequestInit` so the UI can cancel stale requests.

## Phase 5 — React Lee tab with race guard

Update `AITradingAgentPanel.tsx`:

- tabs become `review | brief | lee`
- add Lee response/loading/error state
- add `useRef` request sequence + `AbortController`
- reset/abort Lee request when selected `symbol`, `traceId`, or `signal` changes
- before setting Lee response, verify request sequence and selected signal key still match
- display visible labels:
  - `CONTEXT_SUPPORTS` → `Context supports`
  - `WAIT` → `Wait / needs confirmation`
  - `CONTEXT_BLOCKS` → `Lee flags risk`
  - `NEED_MORE_DATA` → `Data gap`
- show safety copy prominently: `Lee is advisory only — not execution approval.`

## Phase 6 — Verification

Run targeted backend tests:

```bash
pytest tests/test_ai_lee_confirmation.py tests/test_routes_ai_agent.py tests/test_ai_trade_chat.py -q
```

Run frontend verification:

```bash
cd static/react-app/app && npm run build
cd static/react-app/app && npm run lint
```

Do not run full repo tests unless Damian asks. Do not commit unless Damian asks.
