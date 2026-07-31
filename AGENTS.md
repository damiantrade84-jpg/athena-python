---
description:
alwaysApply: true
---

# AGENTS.md — Athena / Sentinel Pro v4

Athena is a multi-engine trading analysis and execution-support repository. Default mode is paper/demo only unless the user explicitly approves live trading.

## Task routing — highest priority

Use the lightest workflow that can safely complete the request.

### Direct fix (default)

For a clear explanation, mechanical edit, localized bug fix, or change normally confined to three files or fewer:

- Do not create a plan, checklist, `PLANS.md`, task file, coverage map, or subagent.
- Do not load a skill unless the user explicitly invokes it by name.
- Read only the target file and the minimum caller, consumer, config, or test needed to understand the change.
- Patch directly, run the smallest relevant check once, and stop when the acceptance criteria are met.

### Scoped investigation

Use only when the root cause is unclear. Trace the suspected path narrowly; do not scan the whole repository or inspect every engine/surface. Expand scope only when current evidence requires it.

### Formal audit or parity workflow

Use a repository skill only when the user explicitly invokes `$skill-name` or explicitly requests the complete formal workflow represented by that skill. Do not chain multiple skills unless the user names each one.

### Plan mode

Create a written plan only when the user asks for one, or when the work is a migration, architecture change, new subsystem, significant refactor, or multi-stage change spanning more than five production files. Keep small plans in the response. Do not create or update plan/task files unless requested.

## Context and token budget

- Current source is authoritative. Do not load old audits, plans, task files, logs, generated artifacts, archived reports, or backtest outputs unless the user names them or they are required evidence.
- Do not enumerate or read all skills. Repository skills are manual-only.
- No broad `rg` sweeps, repository inventories, dependency installs, service starts, database scans, browser runs, or backtest matrices unless directly required.
- No subagents for ordinary implementation or single-surface review.
- Do not repeat a command that already passed unless relevant code changed.
- Stop once the requested behavior is implemented and minimally verified.

## Current system boundaries

- Bybit is the primary crypto venue for trade buckets, microstructure, live ticks, levels, and paper execution.
- Binance microstructure is disabled by default when Bybit is primary. Enable only with `MICROSTRUCTURE_BINANCE_FEEDS_ENABLED: true` or `TRADE_BUCKET_EXCHANGE: binance`.
- Binance candle/live-price support is separate from Binance microstructure.
- MT5 D1 fetch has a bounded read-only retry for small stale-history windows; freshness and risk gates still reject stale results.
- Engine A, B, C, D, ASE, Research Lab, UI, and execution paths remain independent unless the task explicitly requires integration.
- ASE is standalone under `athena_ase/`, demo/paper only, and must not import Engine A scoring or indicators.

## Authoritative timeframe structure

- `timeframe_policy.py` (`POLICY_VERSION = timeframe_policy.v4`) and `resolve_timeframe_policy()` are the policy source of truth. `market_structure.resolve_engine_b_tfs()` adapts that result for Engine B callers. Do not treat legacy `TIMEFRAME_ROUTING`, `ENGINE_B_FOREX_STRUCTURE_TF`, or hardcoded style tables as authoritative.
- The policy ladder is slow-to-fast: `D1 > H4 > H1 > M30 > M15 > M5 > M1`. `M1` is the terminal rung and may appear only in scalp/engine-d-native templates. Roles are distinct and ordered: `regime`, `bias`, `structure`, `setup`, `trigger`, `execution`.
- For Engine B, `structure` controls the structural zones/space gate and structural ATR; `setup`/`trigger` control entry confirmation; `execution` controls entry timing. Never use a faster trigger or execution timeframe to replace missing structure data, ATR, or higher-timeframe bias.
- The normal Engine B intraday policy is `D1` regime, `H4` bias, `H4` structure/zone/ATR, `H1` setup, and `M15` trigger/execution (universal Engine A/B role ladder in `timeframe_policy.py`: `_UNIVERSAL_REGIME=D1`, `_UNIVERSAL_BIAS=H4`, `_UNIVERSAL_STRUCTURE=H4`, `_UNIVERSAL_SETUP=H1`, `_UNIVERSAL_TRIGGER=M15`). M5 authority is declarative via `m5Policy` (`conditional` = M15 confirmation + M5-eligibility required, emitted with `m5Role=refinement`; `disabled` = `m5Role=disabled`); speed never promotes M5 execution, and `allow_dynamic_m5_execution` is deprecated and ignored. Production execution is live-quote based (`executionMode=live_quote`); the emitted `executionTf` is advisory execution context. Speed adaptation may modify `setup`/`trigger` only — never regime/bias/structure, never execution.
- Engine D is a separate scalp contract: `H1` bias, `M15` confirmed structure/volume-profile, `M5` context/orderflow, and `M1` execution by default (`SCALP_ENGINE.EXECUTION_TIMEFRAME` may select M1 or M5). The policy's scalp/engine-d-native template is `H1` regime, `M15` bias/structure, `M5` setup, `M1` trigger with live-quote execution. Engine B policy promotion must not alter Engine D timeframes or scoring.
- Missing, stale, or ambiguous timeframe data remains fail-closed; lower-timeframe diagnostics never substitute for required higher-timeframe data.
- Preserve the emitted provenance fields: `timeframePolicyVersion`, `policyKey`, `regimeTf`, `biasTf`, `structureTf`, `setupTf`, `triggerTf`, `executionTf`, `m5Role`, `executionMode`, and `m5Policy`.

## Non-negotiable safety

- Read current source before claims or patches. Do not rely on memory, old audits, or generated logs unless the user names them.
- Use the smallest safe diff. Do not alter thresholds, weights, gates, SL/TP, RR, strategy semantics, execution behavior, or unrelated code unless requested.
- Engine A and Engine B scoring must not affect each other. Never write one engine's score, pct, or gate state into the other engine's score fields. Cross-engine consensus lives only in explicitly named blend/annotation fields (`combinedConviction`, `engine_b_*`) and must use graded totals (score/max_possible) — never binary gate outputs like `gate_pct`, which are 100 for every passing signal and silently saturate blends and headline scores.
- Never bypass risk, freshness, kill switch, execution approval, broker, audit, or deterministic safety gates.
- AI is advisory only. It cannot execute, approve orders, mutate config, or override deterministic gates.
- Never read, print, modify, or commit `.env`, secrets, API keys, tokens, or credentials.
- Treat `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, `mt5_executor.py`, `bybit_executor.py`, broker feeds, freshness checks, and config gates as safety-critical. Preserve fail-closed behavior.

## Verification budget

Choose one smallest relevant verification command by default:

- Documentation/comment/format-only change: no pytest. Validate syntax or formatting only when applicable.
- Localized Python behavior: one specific test case; use one test file only when a specific case is not available.
- Localized frontend behavior: one targeted typecheck, test, or build command—not all three unless the first check cannot verify the change.
- Config-only change: parse/load the affected config; do not run unrelated tests.
- Safety-critical behavior: run the smallest focused test covering the modified gate or branch. A second command is allowed only when it verifies a distinct modified safety boundary.

Never run full test suites, broad `-k` selections, multi-file test batches, live trading, broker actions, long research jobs, or full backtest matrices unless the user explicitly requests them or the task is specifically release/CI validation.

If no suitable test exists or the environment blocks it, report `not verified`; do not create unrelated tests or broaden scope merely to produce a green command.

Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
## Final response

Report only what is useful: summary, files changed, the exact check run, and any material unverified risk. For read-only questions or trivial edits, omit empty sections.
