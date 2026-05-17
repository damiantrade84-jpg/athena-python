# AI Chat Display / Rendering / Feedback Fix — Report

Date: 2026-05-14
Scope: Targeted display-and-response fix for the existing "Discuss with AI" Chat
panel. No trading logic, gate, threshold, strategy parameter, or execution
behavior was changed.

---

## 1. Root cause (verified)

The chat panel is `AITradingAgentPanel` (in `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx`),
mounted into `SignalsPanel` (under the "Discuss with AI" button at
`SignalsPanel.tsx:770`) and into `LiveCockpitPanel` (under the "agent" tab).
The card root already used `bg-slate-950 shadow-lg`, so the previous "transparent
overlap" complaint was **visual section blending inside the panel**, not literal
z-index/transparency.

Three concrete defects were verified in the JSX/TSX of `AITradingAgentPanel.tsx`:

1. **Raw JSON dumps in main display**
   `formatValue()` did `JSON.stringify(value)` for objects. `DetailGrid` therefore
   rendered `source_status`, `style_ratings`, and tool-call `args` as inline
   JSON strings.

2. **Wrong section order**
   `AssistantResponse` rendered `MarketIntelligenceCard` and `VisionSummaryCard`
   *before* the assistant narrative, and the narrative itself was inside an
   `ExpandableSection` whose `defaultOpen` flag was the only thing keeping it
   visible.

3. **Data Checked card auto-expanded with mostly-empty content**
   `DataCheckedCard` had `defaultOpen` true, so the first thing visible was a
   half-empty status grid with `text-muted-foreground` placeholder text.

A fourth, advisory-only backend gap: `compose_trader_answer` never populated the
`selected_signal` field already declared in
`static/react-app/app/src/types/athena.ts:605`, so the new "Selected Signal"
card had nothing to display.

---

## 2. Changes (minimal, non-execution-path)

### Frontend — `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx`

- **Helpers rewritten**
  - Added `isScalar`, `isPlainObject`, `safeJson`.
  - Renamed `formatValue` → `formatScalar` so the name reflects intent
    (returns `"—"` for null/empty, "N items" / "N fields" hints only —
    never JSON).
  - Added `KeyValueRows` (compact opaque-row layout for `Record<string,unknown>`).
  - Added `RawDetailsBlock` (only renders inside an `ExpandableSection`,
    `max-h-64 overflow-auto`, `whitespace-pre-wrap`, so raw JSON is
    *opt-in* not visible by default).

- **`DetailGrid` filters non-scalars**
  Objects no longer get the `"N fields"` placeholder — they are filtered out
  of the grid entirely so the caller can route them to `KeyValueRows` or
  `RawDetailsBlock`.

- **`MarketIntelligenceCard` rewritten as a structured card**
  - `freshness_status` + `risk_regime` in the grid.
  - `calendar_within_72h` rendered as `ListBlock`.
  - `source_status` rendered as `KeyValueRows` (no JSON dump).
  - Warnings rendered as `ListBlock`.
  - Full object available behind `Raw market intelligence` (collapsed).

- **`VisionSummaryCard` rewritten similarly**
  - `right_edge_status` / `tf_alignment` / `freshness_status` /
    `execution_context` in the grid.
  - `style_ratings` via `KeyValueRows`.
  - `visible_obstacles` via `ListBlock`.
  - `memo` via `SectionCard`.
  - Full object available behind `Raw vision summary` (collapsed).

- **`ToolCallsCard` no longer dumps args**
  `args` is routed to `KeyValueRows`; the whole call object lives behind a
  `Raw tool call` expander.

- **`AssistantResponse` reordered to match the spec**
  The new visible order is:
  1. Decision badge row
  2. Selected Signal summary
  3. **Trading desk read** — the assistant narrative, now top-level and
     visible by default (no longer hidden in a collapsible).
  4. Market read + Trade thesis as supporting `SectionCard`s
  5. Final action
  6. Supports
  7. Contradictions + Contradiction flags
  8. Confirmation needed
  9. Invalidation
  10. Historical analogue / Compare summary
  11. Market Intelligence summary
  12. Vision Summary
  13. (Collapsed by default) Data checked, Tool transparency, Strategist summary, Raw response details
  14. Safety note

  An `answerText` fallback is computed from `market_read` / `trade_thesis` /
  `final_action` if `response.answer` is empty, so the Trading desk read is
  never blank when other structured fields exist.

- **Stacking / isolation**
  The root `Card` now carries `relative isolate` so any future portal/popover
  children get a clean stacking context; `AssistantResponse` also wraps in
  `relative isolate`. All cards remain on opaque `bg-slate-900` /
  `bg-slate-950` surfaces.

- **Removed dead code**
  `InfoCard` is no longer used and has been removed (its callers now build
  custom structured cards). `text-muted-foreground` removed from the chat
  body and replaced with `text-slate-400` for readability against the dark
  shell. (The Strategist Brief tab is unchanged.)

### Backend — `ai_trade_chat.py`

- New helper `_build_selected_signal_summary(resolved_signal, packet, request)`
  returns surface metadata only (symbol / trace_id / direction / engine / state
  / score / threshold / rr / entry / sl / tp / style). It is **purely
  presentational**: it does not call `run_ai_trade_review`, does not mutate
  packet, does not alter `decision`/`final_action`/`risk_warning`, and does not
  touch any gate.
- `run_trade_chat_turn` now includes the result as `selected_signal` in the
  `response.update({...})` block.
- The existing `response = validate_ai_chat_response(response, packet)` call
  runs after the update, so the advisory-only safety contract
  (`read_only=True`, `can_execute=False`, `can_modify_thresholds=False`,
  `deterministic_gates_required=True`) is unchanged.
- `AiSelectedSignalSummary` already existed in `types/athena.ts:605` with `[k: string]: unknown` so the new field is type-compatible.

---

## 3. Hard constraints — verified compliance

| Constraint | Status |
| --- | --- |
| Do not change trading thresholds | OK — no `config.yaml` / `config.py` change |
| Do not change strategy logic | OK — no engine A/B/C/D, scoring, or `factor_scoring` change |
| Do not change order placement | OK — no `execution.py`/`auto_trader.py`/`mt5_executor.py`/`bybit_executor.py` change |
| Do not change backend risk gates | OK — no `risk_engine.py`/`ai_agent_safety.py` change |
| Do not create duplicate chat system | OK — same `AITradingAgentPanel` component, same `/api/ai/trade-chat` route |
| Use existing Chat with AI location | OK — file untouched at `components/ai/AITradingAgentPanel.tsx`, mount points in `SignalsPanel` and `LiveCockpitPanel` unchanged |
| Keep chat read-only / advisory | OK — `ai_agent_safety.validate_ai_chat_response` still enforces `read_only=True`, `can_execute=False`, `can_modify_thresholds=False`, `deterministic_gates_required=True` |
| `VALID_SETUP` cannot be preserved on failed gates | Untouched — handled by `validate_ai_chat_response` |
| Similar setups N < 20 cannot produce probability claim | Untouched — `_similar_summary` still enforces this |

---

## 4. Verification

- **Frontend build**
  `cd static/react-app/app && npm run build`
  → `tsc -b && vite build` succeeded; 2479 modules transformed; only the
  pre-existing chunk-size warning (unrelated).

- **Backend chat tests**
  `python -m pytest tests/test_ai_trade_chat.py -q --basetemp=./.pytest_tmp`
  → **7 passed**. Covers:
  - `plan_tool_calls` routing
  - `run_trade_chat_turn` deterministic fallback (no model key)
  - failed deterministic gate cannot return `VALID_SETUP`
  - insufficient similar setups → no probability claim

- **What was NOT verified**
  Interactive browser smoke of the rendered card (no dev server run for this
  pass). The static type system and the existing test suite cover the wiring;
  visual confirmation in the running app is the operator's call.

---

## 5. Files touched

- `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx`
- `ai_trade_chat.py`
- `tasks/ai_chat_display_rendering_fix_report.md` (this file)

No other files were modified.
