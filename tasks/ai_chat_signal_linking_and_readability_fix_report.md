# AI Chat Signal Linking + Readability Fix

## 1. Root cause

The Live Cockpit "Chat with AI" tab opened `AITradingAgentPanel` and POSTed to
`/api/ai/trade-chat`, but the request body only carried `symbol` and
`trace_id`. The backend `run_trade_chat_turn` had no way to receive a full
signal payload, and its tool layer only resolved a packet by looking the
trace_id / symbol up in the runtime scan caches. When the runtime cache had no
matching entry, `_packet_from_tool_results` returned `None` and
`compose_trader_answer` fell back to the static placeholder copy
("No linked signal packet is available." / "Select a signal or provide a
trace_id for signal-specific analysis."). That is exactly the screenshot the
user reported. The UI also used `bg-card/50`, `bg-muted/10`, `bg-muted/20`,
and warning/short tinted backgrounds with low alpha, which produced washed-out
cards and badges on top of the dark cockpit background.

## 2. Files changed

Backend
- `ai_trade_chat.py` — added `_resolve_chat_context()` priority resolver, fed
  resolved signal into `plan_tool_calls()` via context, added
  `context_resolution` diagnostic block to the response, and injected the
  resolved signal into per-tool args so `_find_signal` short-circuits to it.
- `athena_app/api/routes_ai_agent.py` — `/api/ai/trade-chat` now reads the
  optional `signal` payload from the request body and forwards it.
- `tests/test_routes_ai_agent.py` — added 4 focused tests for context
  resolution (trace_id, request_signal_payload, symbol_only, read-only
  envelope under request_signal_payload).

Frontend
- `static/react-app/app/src/types/athena.ts` — added
  `AiTradeChatSignalPayload`, added `signal` field to `AiTradeChatRequest`,
  added `AiContextResolutionSummary` and `context_resolution` to
  `AiTradeChatResponse`.
- `static/react-app/app/src/components/ai/AITradingAgentPanel.tsx` —
  accepts new `signal` prop, sends it on every chat request, resolves
  symbol / trace_id from `signal` as a fallback, and adopts solid
  slate-950 / slate-900 backgrounds with amber accents and high-contrast
  badges across every sub-card.
- `static/react-app/app/src/components/panels/LiveCockpitPanel.tsx` —
  introduced `buildAgentSignalPayload(row)` and passes it into the
  agent tab via `<AITradingAgentPanel ... signal={...} />`.
- `static/react-app/app/src/components/panels/SignalsPanel.tsx` — passes
  the selected scan signal payload to the agent panel as the new `signal`
  prop. (Scans tab already had symbol/trace_id; we now also forward the
  whole payload.)

## 3. How selected signal context is passed UI → backend

`LiveCockpitPanel` (Agent tab) and `SignalsPanel` build a flat object derived
from the active row / selected `EngineASignal`:

- `trace_id`, `symbol`, `pair`, `display`, `type`/`asset_type`
- `direction`, `engine`, `engine_source`, `style`, `timeframe`
- `score`, `threshold`, `rr`, `rr1`, `min_rr`
- `entry`, `price`, `sl`, `tp`, `tp1`, `tp2`
- `latest_price`, `spread`, `spread_pips`
- nested `engine_a`, `engine_b`, `engine_c`, `engine_d`
- `vision`, `ai_review`, `dataFreshness`, `freshness_status`, `levels`

`apiClient.postAiTradeChat()` now sends this as the `signal` field, alongside
the existing `trace_id`, `symbol`, and `message`.

## 4. Backend context resolution order

`ai_trade_chat._resolve_chat_context(request)` runs once per turn and returns
both the resolved signal and a diagnostic block. Priority:

1. **trace_id** — `ai_tools._find_signal(signal_id=trace_id)` against the
   runtime caches (`last_scan_results`, `live_dashboard_scalp_cache`, etc.).
   Mode = `"trace_id"`.
2. **request_signal_payload** — if the trace_id lookup misses or no trace_id
   was given, the request body's `signal` dict is used verbatim.
   Mode = `"request_signal_payload"`.
3. **latest_symbol_signal** — `ai_tools._find_signal(symbol=symbol)` runtime
   lookup by symbol. Mode = `"latest_symbol_signal"`.
4. **symbol_only** — only a symbol was provided. Mode = `"symbol_only"`.
5. **none** — nothing usable. Mode = `"none"`.

The resolved signal is then passed to every tool call in `plan_tool_calls`
via `signal=resolved_signal`. `_find_signal` short-circuits on it, so
`build_packet_for_signal` always sees a real signal when one was provided,
and `compose_trader_answer` no longer falls back to the placeholder copy.

The response now includes:

```
"context_resolution": {
  "mode": "trace_id|request_signal_payload|latest_symbol_signal|symbol_only|none",
  "trace_id_received": bool,
  "signal_payload_received": bool,
  "resolved_symbol": "...",
  "resolved_engine": "...",
  "warnings": []
}
```

## 5. UI readability changes

All changes are scoped to `AITradingAgentPanel.tsx`. Other dashboard areas and
the AI Review card were not restyled.

- Outer panel: `border-amber-700/40 bg-slate-950 shadow-lg rounded-xl
  text-slate-100`.
- Decision badge: solid emerald/rose/blue/amber dark backgrounds, light
  foreground text, `font-semibold`.
- Section cards: solid `bg-slate-900` (default), `bg-emerald-950/70`,
  `bg-rose-950/70`, `bg-amber-950/70` for tone variants; titles in
  `text-amber-400`, body in `text-slate-100`.
- Detail grid rows: `bg-slate-900 border-slate-700`, labels `text-slate-400`,
  values `text-slate-100`.
- Info / strategist / data-checked / tool-call cards: solid `bg-slate-900`
  with amber titles.
- Expandable section: solid dark background, amber title, slate-100 contents.
- Badges (ListBlock and inline): `bg-slate-900 text-slate-100 border-slate-600`,
  except status-coded ones which use emerald/rose/amber dark variants.
- Compare input + textarea + quick-prompt buttons: solid `bg-slate-950`
  textarea/input with bright text, `placeholder:text-slate-500`,
  amber focus ring; Send button is amber on slate.
- Empty / loading / safety banners: amber-900/80 amber-200 for the safety
  envelope, slate-900 for the empty / loading rows.
- Message cards: user messages in `border-amber-600/40 bg-slate-900`,
  assistant messages in `border-slate-700 bg-slate-950`, role badges
  contrasted accordingly.

## 6. Tests / build run and results

Backend (project-local basetemp to work around the user's TEMP perms):

```
python -m pytest tests/test_ai_trade_chat.py tests/test_routes_ai_agent.py -v --basetemp=.pytest-tmp
```

Result: **18 passed** (14 pre-existing + 4 new context-resolution tests).

New tests:
- `test_trade_chat_resolves_from_trace_id_lookup` —
  `context_resolution.mode == "trace_id"` and the placeholder phrase is
  absent from `answer` / `market_read`.
- `test_trade_chat_uses_signal_payload_when_runtime_lookup_misses` —
  `context_resolution.mode == "request_signal_payload"` when the request
  carries a `signal` dict but the runtime cache is empty; placeholder phrase
  absent.
- `test_trade_chat_symbol_only_fallback_when_no_signal` —
  `context_resolution.mode == "symbol_only"` when neither trace_id nor
  payload is given.
- `test_trade_chat_request_signal_payload_remains_read_only` —
  `safety.can_execute == false` and `final_action` does not contain
  `"execute"` even when the user types "place this trade now."

Frontend:

```
npx tsc -b --pretty false   # in static/react-app/app
```

Result: exit code `0` (no type errors).

## 7. AI Review unchanged

The "AI" tab in Live Cockpit (`AiReviewCard` in `LiveCockpitPanel.tsx`) and
the AI Review rendering in `SignalsPanel` were not modified. They continue to
render `LdAiReview` from `row.aiReview` / `aiReview` exactly as before. The
chat panel is wired only to the separate "Agent" tab. No routes, no payload
parsers, no badges, and no copy strings tied to AI Review were touched.

## 8. Trading-logic / threshold / order-placement: no change

- No edits to `execution.py`, `auto_trader.py`, `risk_engine.py`,
  `mt5_executor.py`, `bybit_executor.py`, `config.yaml`, or scoring files.
- `validate_ai_chat_response` still pins
  `read_only=true / can_execute=false / can_modify_thresholds=false /
  deterministic_gates_required=true` on every response. The new
  `context_resolution` field is purely additive; the safety validator does
  not strip unknown fields.
- The chat tools and chat orchestrator remain advisory-only. The
  `compare_symbols` tool's signal payload only forwards `left_signal` (no
  execution side effects). No tool talks to brokers.

## 9. Remaining limitations

- The frontend payload uses the LdSymbolRow shape (camelCase keys for engine
  sub-objects). `build_ai_review_packet` reads many alias keys but a small
  number of long-tail fields (e.g. `factorScores`, `signalClass`,
  `combinedConviction`, `MAX_SL_PCT`) are not surfaced by the live-dashboard
  snapshot today, so packet completeness from the cockpit will still report
  some `missing_fields`. This is honest data and the AI surfaces it as
  "Missing data" rather than inventing values — by design.
- Strategist brief sub-tab body kept its existing layout; the readability
  changes are limited to the chat ("Tool Chat") sub-tab and shared building
  blocks (`SectionCard`, `InfoCard`, `ExpandableSection`, `DetailGrid`,
  `ListBlock`, `SafetyCard`) which the brief shares — so the brief inherits
  the improved contrast automatically without restyling its own layout.
- Backend test environment requires `--basetemp=.pytest-tmp` on this user's
  machine because the default Windows TEMP path returns
  `PermissionError: [WinError 5]`. Tests themselves are unaffected.
