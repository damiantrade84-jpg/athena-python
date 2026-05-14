# ATHENA AI Agent Phase 3

Phase 3 upgrades the AI Trading Agent chat from a packet reviewer into a read-only, tool-using trading-desk assistant.

## What It Does

The chat agent can answer signal follow-ups by calling local advisory tools:

- `get_signal_detail` - latest linked signal and canonical review packet.
- `get_market_intelligence_tool` - cached market intelligence context.
- `get_pair_context_tool` - pair-specific local context.
- `get_historical_analogues_tool` - similar setup scaffold from local learning/audit data.
- `get_chart_vision_tool` - latest structured Vision context already attached to the signal.
- `get_engine_context_tool` - Engine A/B/C/D context from the packet.
- `get_open_risk_state_tool` - runtime-supplied read-only risk state.
- `compare_symbols_tool` - available context for two symbols.
- `get_strategist_view_tool` - read-only Strategist view.
- `get_facts_used_tool` - compact evidence list and missing fields.

The planner in `ai_trade_chat.py` routes common questions such as “Why was this blocked?”, “Does Vision agree?”, “What would change your mind?”, and symbol comparisons to the relevant tool set.

## Safety Boundaries

The Phase 3 agent is advisory-only:

- It cannot execute trades.
- It cannot place, route, approve, or submit orders.
- It cannot change thresholds, config, strategy parameters, or risk settings.
- It cannot override guardian, freshness, kill switch, RR, spread, fee, or risk gates.
- It cannot upgrade a failed deterministic signal into an executable trade.

Every chat response passes through `validate_ai_chat_response()`, which forces:

- `read_only=true`
- `can_execute=false`
- `can_modify_thresholds=false`
- `deterministic_gates_required=true`

If a response says `VALID_SETUP` while deterministic gates, freshness, guardian, kill switch, RR, fee, spread, or risk state are not clean, it is downgraded.

## API

Primary endpoint:

`POST /api/ai/trade-chat`

Request:

```json
{
  "session_id": null,
  "trace_id": "optional",
  "symbol": "optional",
  "message": "What would change your mind?",
  "include_vision": true,
  "include_similar_setups": true,
  "comparison_symbol": "ETHUSDT"
}
```

Response includes:

- `decision`
- `answer`
- `market_read`
- `trade_thesis`
- `supports`
- `contradictions`
- `confirmation_needed`
- `invalidation`
- `historical_analogue_summary`
- `risk_warning`
- `vision_summary`
- `strategist_summary`
- `facts_used`
- `tool_calls`
- `missing_data`
- `contradiction_flags`
- `safety`

Conversation helpers:

- `GET /api/ai/conversations?symbol=...`
- `GET /api/ai/conversations/<thread_id>`
- `POST /api/ai/conversations/<thread_id>/title`

## UI

The React AI Trading Agent panel now shows:

- selected signal summary
- conversation history
- quick prompts
- compare-symbol flow
- assistant sections
- Market Intelligence and Vision cards
- Strategist summary when returned
- Data checked / tool transparency
- safety note

When no signal is selected, the panel stays usable for general questions and clearly states that trade-specific evidence requires a selected signal.

## Data Checked

“Data checked” means the response was built from returned tool calls and packet facts. If a source is unavailable or stale, the UI displays warnings rather than treating the missing source as confirmation.

## Known Limitations

- Tool routing is deterministic keyword routing, not model-planned tool calling.
- Chart Vision tool reads existing structured Vision context only; it does not trigger screenshot/chart generation.
- Similar setup evidence remains scaffold evidence. Samples under 20 are insufficient and do not permit calibrated probability claims.
- Market intelligence uses existing repo/local sources only and may return unavailable/partial data when local sources or API keys are absent.
- The chat answer currently uses deterministic fallback composition. Model-backed Marcus chat can be layered behind the same safety validator later.
