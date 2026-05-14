# AI Evaluation — Phase 5 Implementation

## What AI Evaluation Measures

The AI Evaluation layer measures whether AI advisory surfaces (Marcus, Vision, Debate, Strategist, Chat, Engine B AI, Engine C AI, Research Agent) are actually helping or hurting trading outcomes.

- **Win rate when AI positive**: When AI says "go", how often does the trade win?
- **Avg R when AI positive**: When AI says "go", what is the average R multiple?
- **Useful blocks**: AI blocked a trade that would have lost money.
- **False blocks**: AI blocked a trade that would have won money.
- **Harmful allows**: AI approved a trade that lost money.
- **Parse/schema failure rate**: How often does the AI output malformed data?
- **Stale context rate**: How often does AI review stale/expired data?
- **Contradiction rate**: How often does AI detect contradictions in its own input?

## How Outcomes Are Linked

The `ai_outcome_linker.py` module connects AI review records to actual trade outcomes:

1. **Load** AI review samples from JSONL logs (`ai_review_audit.jsonl`, `ai_agent_chat.jsonl`)
2. **Link** by trace_id → audit_log ticket, or symbol + timestamp proximity (±24h)
3. **Fallback** to learning_log (SQLite) for execution-linked outcomes
4. If no matching outcome found: `sample_valid=false`, `missing_outcome_reason` is set

### Linking Priority

1. `trace_id` direct match in audit_log or learning_log
2. `symbol` + timestamp proximity in audit_log
3. symbol + timestamp proximity in learning_log

## Definitions

### Useful Block
AI blocked or downgraded the trade (decision was NEGATIVE or BLOCKED), and the actual trade outcome was a LOSS.

### False Block
AI blocked or downgraded the trade, but the actual trade outcome was a WIN.

### Harmful Allow
AI allowed or confirmed the trade (decision was POSITIVE), but the actual trade outcome was a LOSS.

### Stale Context
AI reviewed data where `data_freshness` or `vision_freshness` was not "FRESH" or was missing.

### Insufficient Sample
Fewer than 10 samples for a surface. Metrics are not reliable. Recommendations are suppressed.

## How to Read the AI Performance Panel

The AI Performance panel is available in the sidebar as "AI Perf" (`/aiPerformance`).

### Overview Tab
- KPI cards: total samples, valid outcomes, overall win rate, avg R, surfaces count
- Summary text
- Best/Worst AI behaviors lists
- Recommendations (all marked `DO NOT AUTO-APPLY`)

### Surfaces Tab
One card per AI surface with:
- Sample count and linked outcome count
- Win rate when AI was positive
- Avg R when AI was positive
- Block/useful/false/harmful counts
- Parse failure rate, stale context rate
- Sample warnings

### Vision Tab
- Confirms win rate: when Vision confirmed, how often did the trade win?
- Contradicts loss rate: when Vision contradicted, how often did the trade lose?
- Stale Vision rate: how often was Vision data stale?
- Right-edge reliability

### Debate Tab
- Total blocks, useful blocks, false blocks, harmful allows
- Parse failures, safety fallback events

### Strategist Tab
- Concur win rate: when Strategist concurred, how often did the trade win?
- Object accuracy: when Strategist objected, how often did the trade lose?
- False objections

### Weekly Tab
- Generate weekly report (summary of last 7 days)
- What AI got right/wrong
- Recommendations

## Why Recommendations Are Advisory Only

- All evaluation reports set `do_not_auto_apply=True` at the Pydantic validator level
- No evaluation endpoint can modify config or thresholds
- The AI Performance UI shows `ADVISORY ONLY` badges and safety notices

## Limitations

- Outcomes can only be linked for trades that actually executed (paper or live)
- Backtest outcomes are not linked unless explicitly marked as simulated
- Small samples (<10 per surface) produce unreliable metrics
- Chat interactions without trade context cannot be linked to outcomes
- The linker uses ±24h time window for symbol-based matching, which may include unrelated trades
- JSONL logs are not indexed — performance degrades with very large log files (>100K records)
