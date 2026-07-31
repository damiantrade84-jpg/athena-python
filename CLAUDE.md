# CLAUDE.md — Athena / Sentinel Pro v4

Claude Code repository instructions. Current source is authoritative. Default mode is paper/demo only unless the user explicitly approves live trading.

## Task routing — highest priority

### Direct fix (default)

For explanations, mechanical edits, localized bugs, and changes normally confined to three files or fewer:

- Do not enter plan mode or create a checklist, task file, coverage map, or subagent.
- Do not invoke a project skill unless the user explicitly names it.
- Read only the target file and the minimum caller, consumer, config, or test needed.
- Patch directly, run the smallest relevant check once, and stop when the request is satisfied.

### Investigation and planning

- For unclear bugs, trace only the suspected path and expand scope only when evidence requires it.
- Create a written plan only when the user asks, or for a migration, architecture change, new subsystem, significant refactor, or multi-stage change spanning more than five production files.
- Do not create or update `PLANS.md`, `tasks/todo.md`, or `tasks/lessons.md` unless requested.

### Skills

Project skills are manual-only. Invoke `/athena-audit` only when the user explicitly requests that workflow. Do not read `AGENTS.md`, `.agents/**`, `.cursor/**`, old audits, plans, logs, generated artifacts, or archived reports unless the user asks for cross-tool maintenance or the current task requires a named artifact.

## System boundaries

- Bybit is the primary crypto venue for trade buckets, microstructure, live ticks, levels, and paper execution.
- Binance microstructure is off by default when Bybit is primary; Binance candle/live-price paths are separate.
- MT5 D1 stale-history retry is bounded and read-only; freshness/risk gates remain fail-closed.
- Engine A, B, C, D, ASE, Research Lab, UI, and execution paths stay independent unless integration is explicitly in scope.
- ASE is standalone under `athena_ase/`, demo/paper only, and must not reuse Engine A scoring or indicators.

## Authoritative timeframe structure

- `timeframe_policy.py` (`POLICY_VERSION = timeframe_policy.v4`) and `resolve_timeframe_policy()` are the policy source of truth. `market_structure.resolve_engine_b_tfs()` adapts that result for Engine B callers. Do not treat legacy `TIMEFRAME_ROUTING`, `ENGINE_B_FOREX_STRUCTURE_TF`, or hardcoded style tables as authoritative.
- The policy ladder is slow-to-fast: `D1 > H4 > H1 > M30 > M15 > M5`. Roles are distinct and ordered: `regime`, `bias`, `structure`, `setup`, `trigger`, `execution`.
- For Engine B, `structure` controls the structural zones/space gate and structural ATR; `setup`/`trigger` control entry confirmation; `execution` controls entry timing. Never use a faster trigger or execution timeframe to replace missing structure data, ATR, or higher-timeframe bias.
- The normal Engine B policy is the universal `D1` regime, `H4` bias, `H4` structure/zone/ATR, `H1` setup, and `M15` trigger/execution ladder. Conditional M5 remains refinement-only and cannot replace the M15 authority; production execution is live-quote based.
- Engine D is a separate scalp contract: `H1` bias, `M15` confirmed structure/volume-profile, `M5` context/orderflow, and `M1` execution by default (`SCALP_ENGINE.EXECUTION_TIMEFRAME` may select M1 or M5). Engine B policy promotion must not alter Engine D timeframes or scoring.
- `M1` is an Engine D operational execution timeframe, not an authoritative Engine B policy rung. Missing, stale, or ambiguous timeframe data remains fail-closed; lower-timeframe diagnostics never substitute for required higher-timeframe data.
- Preserve the emitted provenance fields: `timeframePolicyVersion`, `policyKey`, `regimeTf`, `biasTf`, `structureTf`, `setupTf`, `triggerTf`, `executionTf`, `m5Role`, and `executionMode`.

## Safety and edit discipline

- Inspect current files before claims or patches. Do not infer from old audits, memory files, or stale docs.
- Make the smallest safe change. Do not alter thresholds, weights, gates, SL/TP, RR, strategy semantics, or unrelated code unless requested.
- Engine A and Engine B scoring must not affect each other. Never write one engine's score, pct, or gate state into the other engine's score fields. Cross-engine consensus lives only in explicitly named blend/annotation fields (`combinedConviction`, `engine_b_*`) and must use graded totals (score/max_possible) — never binary gate outputs like `gate_pct`, which are 100 for every passing signal and silently saturate blends and headline scores.
- Never bypass risk, freshness, kill switch, execution approval, broker, audit, or deterministic safety checks.
- AI is advisory only and cannot execute, approve orders, mutate config, or override deterministic gates.
- Never read, print, modify, or commit `.env`, secrets, API keys, tokens, or credentials.
- Treat execution, risk, guardian, auto-trader, broker adapters, feeds, freshness checks, and config gates as safety-critical; preserve fail-closed behavior.

## Verification budget

Run one smallest relevant command by default:

- Docs/comments only: no pytest.
- Localized Python: one specific test case, otherwise one test file.
- Localized frontend: one targeted typecheck, test, or build command.
- Config-only: parse/load the affected config.
- Safety-critical change: one focused test for the modified boundary; a second command only for a distinct changed boundary.

Do not run full suites, broad test globs, multi-file batches, long backtests, live services, broker actions, or dependency installs unless explicitly requested or required for release/CI validation. Do not repeat a passing command unless relevant code changed. Report `not verified` when blocked.

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

Give a concise summary, changed files, exact check run, and material unverified risk. Omit empty sections for read-only or trivial work.
