# AGENTS.md — Sentinel Pro v4 Codex Operating Instructions

## Purpose

This repository contains Sentinel Pro v4, a paper-trading and trading-analysis system with multiple engines, AI review, broker/execution adapters, monitoring, backtesting, and UI/API consumers.

Correctness, safety, fail-closed behavior, and reproducibility are more important than speed, cosmetic cleanup, or clever shortcuts.

---

# Absolute Safety Rules

## Trading safety

This system is paper-only unless the user explicitly states otherwise.

Never bypass or weaken:

- risk gates
- freshness gates
- kill-switches
- execution approval gates
- broker safety checks
- score thresholds
- RR checks
- SL/TP validation
- position/balance validation
- stale-data protections
- monitor/audit logging

AI review cannot override execution gates.

No real-money order logic may be enabled without all of the following:

- at least 1 week of clean paper results
- explicit manual approval from the user
- tests proving fail-closed execution behavior
- broker adapter checks proving SL/TP and risk preservation

If any required safety field is missing, stale, malformed, null, false, or ambiguous, the system must reject by default.

## Engine and scoring safety

Scoring is locked unless the user explicitly asks to change it.

Do not change Engine A/B/D thresholds unless requested.

Do not hardcode trading thresholds, symbols, offsets, scoring constants, or safety gates in Python. Use `config.yaml` or the relevant YAML/config layer.

All changes must be:

- config-gated where appropriate
- default-safe
- minimally invasive
- covered by focused tests
- explainable by code path evidence

## Test safety

Never import `athena.py` in tests.

SQLite requirements:

- use WAL mode where applicable
- use 15s timeout where applicable
- avoid brittle tests that depend on global DB state

---

# Critical Codex Behavior

For any audit, review, debugging, or bug-finding task:

- Do not stop at the happy path.
- Do not stop after finding the first issue.
- Do not patch before producing a bug list unless explicitly asked.
- Trace producer-to-consumer contracts.
- Verify missing, false, null, stale, malformed, and empty payload cases separately.
- Treat missing safety fields as critical until proven fail-closed.
- Clearly separate confirmed bugs from suspicious patterns.
- Label any unchecked area as `not verified`.
- Do not claim a path is safe unless the execution guard, broker adapter, monitor, and audit/log path were inspected.
- Prefer evidence from code paths, tests, logs, command output, and concrete reproduction paths.
- If a test cannot be run, state why and provide the exact test that should be run.

For implementation or fixing tasks:

- Make the smallest safe change.
- Preserve existing behavior unless the existing behavior is the bug.
- Do not introduce hardcoded trading logic.
- Add or update focused tests.
- Run relevant tests when possible.
- Summarize before/after behavior and remaining risk.

---

# Audit Completion Contract

An audit is complete only when the response includes:

1. Files inspected
2. Functions/classes inspected
3. Execution paths traced
4. Commands/tests run
5. Areas not verified
6. Ranked bug list with evidence
7. Negative-case tests recommended or added

For every bug or suspected bug, include:

- Severity
- File/function
- Producer
- Consumer
- Trigger
- Actual behavior
- Expected behavior
- Impact
- Minimal fix
- Test to prove it

Never say “no bug found” unless the inspected scope and skipped scope are explicitly listed.

---

# Mandatory Audit Contract Checks

For every audit, do not stop at the intended happy path.

Trace the contract from producer to final consumer and prove how the system behaves when required fields are:

- missing
- false
- null
- stale
- malformed
- empty dict/list
- wrong type
- delayed
- partially populated

## 1. Fail-closed defaults

Explicitly check whether the system rejects by default when any of these are absent or invalid:

- gate result
- confirmation result
- freshness result
- score pass
- RR pass
- structure pass
- AI review result
- execution approval
- broker symbol
- SL/TP levels
- ATR source
- session filter
- kill-switch state
- risk configuration
- position state
- balance state

Flag any helper that returns any of the following from missing, empty, null, or malformed data:

- `True`
- `trade`
- `passed`
- `execute`
- `approved`
- `allow`
- `valid`
- `safe`

Missing required safety data must not produce a passing result.

## 2. Payload handoff contracts

Trace scanner, backtest, engine, and consensus output into:

- `execution.py`
- `auto_trader.py`
- `risk_engine.py`
- broker executors/adapters
- order monitors
- audit/log writers
- API payloads
- UI consumers

Confirm required fields are present and validated at every boundary.

Do not assume that because one module creates a field, the final consumer receives it correctly.

## 3. Boolean presence versus truth

Inspect code that uses:

- `"key" in payload`
- `payload.get(...)`
- `dict.get(..., True)`
- fallback `{}`
- fallback `[]`
- fallback `True`
- truthy/falsy checks
- optional booleans
- default approval flags

Verify separately:

- key omitted
- key present with `False`
- key present with `None`
- key present with empty dict/list
- key present with invalid type
- key present with stale value

A presence check is not the same as a truth check.

## 4. Mode dispatch and early returns

For config-driven modes, prove which branches run and which branches are skipped.

Check especially:

- `tp_mode`
- backtest/live/paper toggles
- structure-gate switches
- AI review switches
- Engine A/B/C/D enablement
- broker/execution enablement
- session filters
- profile overrides
- pair/group overrides
- fallback threshold logic

Flag early returns that suppress required safety checks unless clearly intentional and tested.

## 5. Live versus backtest parity

Compare live/paper execution against backtest behavior for:

- SL calculation
- TP calculation
- ATR source
- score group
- session logic
- volume source
- feed source
- freshness gate
- RR check
- broker symbol mapping
- precision/rounding
- spread/slippage assumptions
- execution approval fields
- risk sizing
- monitor state transitions

Call out intentional divergence separately from bugs.

## 6. Execution safety handoff

Before saying an engine is safe, inspect:

- signal producer
- engine scoring output
- consensus/trust output
- execution guard
- risk engine
- level preservation
- broker adapter
- broker response handling
- order monitor
- audit/log write path
- UI/API status reporting

Engine-internal correctness is not enough.

## 7. Negative-case tests

Recommend or add focused tests for:

- omitted required flags
- explicit failed confirmations
- stale candles
- zero ATR
- invalid ATR
- missing broker symbol
- missing SL/TP levels
- rejected broker SL/TP updates
- partial fills
- failed order placement
- cancelled order
- delayed broker response
- duplicate signal
- duplicate order prevention
- bot restart recovery
- malformed payload
- missing score group
- missing freshness result
- kill-switch active
- disabled engine
- paper/live mode mismatch

If any of these checks were not performed, label that part of the audit as `not verified`.

---

# Primary Audit Route

For execution-safety audits, inspect in this order unless the user provides a narrower scope:

1. scanner / signal producer
2. engine scoring output
3. Engine C / consensus / trust resolver
4. approval payload
5. `execution.py`
6. `auto_trader.py`
7. `risk_engine.py`
8. broker adapter / executor
9. order monitor
10. audit/log persistence
11. API/UI consumer

For backtest/live parity audits, inspect in this order:

1. backtest signal generation
2. backtest SL/TP/RR calculation
3. live/paper signal generation
4. live/paper SL/TP/RR calculation
5. ATR source
6. score source
7. session filter
8. volume/feed source
9. broker symbol/precision handling
10. monitor/audit output

For AI/vision audits, inspect in this order:

1. AI prompt builder
2. chart/context payload
3. parser
4. footer token extraction
5. rating extraction
6. level extraction
7. AI review gate
8. execution handoff
9. logging/audit output

---

# Project Invariants

## Development

- No guessing.
- Find root causes.
- No temporary fixes.
- No cosmetic rewrites during bug fixes.
- Keep changes minimal.
- Avoid touching unrelated code.
- Use config instead of hardcoding.
- Add tests for every safety-relevant bug.
- Prefer simple, explicit code over clever abstractions.
- Preserve existing public contracts unless the contract is the bug.

## Data freshness

Freshness gate is mandatory.

Timeframe rules:

- Binance H4 offset: `0h`
- MT5 forex H4 offset: `2h`
- MT5 stocks H4 offset: `3h`
- D1 candles use UTC `00:00`

Data-source rules:

- MT5 data must use `fetch_mt5()`
- EODHD is volume-only for Engine D
- Do not silently mix feeds
- Do not allow stale feed fallback to pass execution safety

## AI behavior

Engine B AI is review-only.

AI must not:

- override gates
- override risk checks
- override freshness checks
- approve execution when required fields are missing
- mix Chart Vision and Lottery AI contracts
- mutate scoring thresholds unless explicitly requested

Chart Vision and Lottery AI are separate systems. Do not mix their prompts, parsers, tokens, ratings, or payload contracts.

Preserve exact Vision footer tokens:

- `RIGHT EDGE`
- `TF ALIGNMENT`
- `RATING`
- `LEVELS`

---

# Engines and Scoring

## Engine A — Factor Confluence

Purpose: Primary factor-confluence engine.

Scoring:

- `final_score`: normalized indicator confluence, range `0.0–3.0`
- directional score: trend component, `trend_score`
- nondirectional score: momentum quality, `mom_quality`

Threshold order:

1. profile override
2. pair/group YAML
3. 3-tier fallback

Key factors:

- BTC bias, conditional on correlation
- OI context for crypto
- intermarket confirmation

Relevant config keys:

- `ENGINE_A`
- `ENGINE_A_RESEARCH_LAB_FACTORS`
- `ENGINE_A_MEAN_REVERSION`

Audit concerns:

- score normalization
- missing score group
- wrong threshold source
- profile override not applied
- fallback threshold too permissive
- BTC bias applied when correlation condition fails
- OI context missing or stale
- live/backtest score mismatch

## Engine B — Naked Market Structure / SMC / ICT

Purpose: Naked market-structure engine.

Scoring:

- score/max_score as percentage
- regime-gated thresholds

Regime multipliers:

- `TRENDING = 0.90`
- `RANGING = 0.90`
- `HIGH_VOL = 0.85`
- `LOW_VOL = 1.15`

Checklist:

- swing sequence
- BOS
- liquidity sweeps
- FVG overlap
- zone quality
- trigger quality

Styles:

- scalp: H1
- intraday: H4
- swing: D1

Each style has:

- `min_score`
- `min_rr`

Relevant config keys:

- `NAKED_ENGINE.style_profiles`
- `NAKED_MAX_DAILY`
- `ENGINE_B_REGIME_MULTIPLIERS`

Audit concerns:

- AI review treated as execution approval
- missing style profile passes by default
- regime multiplier applied incorrectly
- min_score/min_rr mismatch
- structure gate skipped by early return
- live/backtest structure mismatch
- parser or payload omits required confirmation fields

## Engine C — Consensus Engine

Purpose: Compare Engine A and Engine B signals and resolve conflicts.

Expected outputs:

- calibrated probability
- trust verdict
- weight recommendation
- conviction modifier
- decision state

Trust verdicts:

- `trust_a`
- `trust_b`
- `trust_both`
- `trust_neither`

Weight recommendation:

- `{"A": x, "B": y}`
- weights must sum to `1.0`

Conviction modifier:

- categorical value: `UPGRADE`, `NEUTRAL`, `DOWNGRADE`
- mapped to float

Decision states:

- trade boolean
- tier
- sizing override
- disagreement diagnosis

Audit concerns:

- missing trust verdict passes by default
- weights do not sum to 1.0
- conflict resolution returns trade without proof
- `trust_neither` still allows execution
- conviction modifier silently upgrades invalid payload
- sizing override bypasses risk engine
- Engine A/B mismatch not logged
- disagreement diagnosis omitted from audit path

## Engine D — Scalp Lab / Volume Profile

Purpose: Volume Profile and Order Flow scalp engine.

Methodology:

- Fabio Valentini VP + Order Flow
- balance/imbalance
- VAL/VAH/POC/LVN

Setup types:

- Mean Reversion: price at value-area extreme, target POC
- Trend Continuation: pullback to LVN

Grading:

- A: full
- B: half
- C: quarter
- D: skip

Three-pillar gate:

- Market State
- Location
- Aggression

All three pillars must align.

Session logic:

- NY open skip
- London cash open
- session mode: NY/London/Asia/All

Relevant config keys:

- `SCALP_ENGINE`
- `BT_*`

Audit concerns:

- any missing pillar passes
- grade D still produces trade
- session skip not enforced
- volume source mixed incorrectly
- EODHD used beyond volume-only role
- live/backtest setup mismatch
- target POC missing or stale
- VAL/VAH/POC/LVN malformed or absent
- aggression defaults to true

---

# Vision / Chart Analysis

Input:

- chart screenshots
- H4/H1/D1 context
- algorithmic context

Expected output:

- RIGHT EDGE status
- TF alignment
- per-style ratings
- levels

Model/config:

- `VISION_MODEL`
- expected model: `grok-4.3`
- expected output budget: 800–1100 tokens
- temperature from `AITemperatureConfig`

Parser contract:

- exact footer tokens are required
- do not rename or reorder parser-critical tokens unless explicitly requested
- preserve:
  - `RIGHT EDGE`
  - `TF ALIGNMENT`
  - `RATING`
  - `LEVELS`

Audit concerns:

- footer token parser too permissive
- malformed AI output passes
- missing levels pass
- RIGHT EDGE review state treated as confirmation
- Chart Vision mixed with Lottery AI
- AI result overrides deterministic gates
- per-style rating applied to wrong timeframe
- parser fallback defaults to confirm/trade/pass

---

# Workflow

## Audit mode

When asked to audit, review, inspect, or find bugs:

1. Inspect first; do not patch unless explicitly asked.
2. Build a producer-to-consumer contract map.
3. Check fail-closed behavior.
4. Check boolean presence versus truth.
5. Check mode dispatch and early returns.
6. Check live/paper/backtest parity where relevant.
7. Check execution handoff.
8. Produce a ranked bug list.
9. Recommend or add negative-case tests.
10. Only then suggest fixes.

Do not ask for confirmation before reading files or running safe local inspection commands.

Do not waste time on broad commentary before inspecting code.

## Implementation mode

When asked to fix or implement:

1. Create a short plan for non-trivial changes.
2. Make the smallest safe change.
3. Keep changes config-gated and default-safe.
4. Add or update focused tests.
5. Run relevant tests.
6. Summarize changed behavior.
7. List remaining risk.

Do not ask for hand-holding when the code, logs, or failing tests provide enough information.

## Planning

Use a plan for:

- architectural changes
- 3+ step tasks
- execution-safety changes
- risk/scoring changes
- broker adapter changes
- AI parser/prompt changes
- backtest/live parity fixes
- database or persistence changes

A plan should include:

- files likely involved
- risk areas
- verification steps
- expected tests

Do not over-plan simple, obvious fixes.

## When to stop and re-plan

Stop and re-plan when:

- tests contradict expected behavior
- the requested change risks execution safety
- code paths differ materially from the assumed architecture
- required files are missing
- required commands fail for environmental reasons
- a proposed fix would weaken safety gates
- a proposed fix would change scoring thresholds

## Task tracking

Use task files only when the task is non-trivial or the user has asked for durable tracking.

When useful, update:

- `tasks/todo.md`
- `tasks/lessons.md`

For `tasks/todo.md`:

- write checkable items
- mark completed items
- add a short review/result section

For `tasks/lessons.md`:

- update after user corrections
- capture the pattern that caused the mistake
- write a rule that prevents recurrence
- keep lessons concise and actionable

Do not let task-file updates replace actual verification.

---

# Verification Rules

Before marking work complete, verify the relevant behavior.

Use the most focused available tests.

If test commands are unknown:

1. inspect README/docs
2. inspect `pyproject.toml`
3. inspect `pytest.ini`
4. inspect `package.json`
5. inspect CI config
6. infer the smallest safe test command

For Python tests, prefer focused commands first, for example:

```bash
pytest path/to/test_file.py -q