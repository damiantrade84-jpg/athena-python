# CLAUDE.md - Athena / Sentinel Pro v4 (Claude Code)

Primary repo-level startup instructions for Claude Code.

# Claude Code Instruction Boundary

Claude Code must use this file and `.claude/skills/**/SKILL.md` only for repo instructions.

Do not import, read, summarize, follow, or modify `AGENTS.md` unless the user explicitly asks for Codex/AGENTS.md maintenance.

`AGENTS.md` is reserved for Codex and other agent-compatible tooling only.

Do not use `.cursor/**`, `.agents/**`, or global/user-profile agent skills for this repo unless the user explicitly asks.

## Core rules


- Never bypass risk gates, freshness checks, kill switches, execution approvals, broker safety checks, RR checks, SL/TP validation, audit logging, or deterministic safety rules.
- Never disable, weaken, or work around `engine_a_trade_gate.py`, `ENGINE_A_TRADE_EVIDENCE_*` thresholds, or the `ENGINE_A_TRADE_ENABLED_*` maps. Populating evidence or flipping enablement is a user decision made outside Claude Code. Engine A is currently research-only for ALL asset classes by design (`config.yaml` `ENGINE_A_TRADE_ENABLED_BY_CLASS`, AB6 reasons in comments) — zero Engine A live trades is expected behavior, not a bug. The deliberate per-pair escape hatch is `ENGINE_A_TRADE_ENABLED_OVERRIDES` (user-only).
- AI is advisory only. AI review, Marcus, Vision, Strategist, AI Agent chat, and similar-setup logic cannot execute trades, approve orders, mutate config, or override deterministic gates.
- Engine A, Engine B, Engine C, Engine D, and **ASE** are separate unless the task explicitly concerns consensus, routing, or cross-engine payload handoff.
- Engine A and Engine B must not suppress each other. Engine C owns agreement, conflict, A-only, and B-only comparison.
- **ASE** (`athena_ase/`) is greenfield — zero reuse of Engine A indicators, scoring, weights, thresholds, or exit policies. Read **`docs/ASE_v2.1_Implementation_Spec.md`** before ASE work. All scan/backtest/shadow/demo paths must call `athena_ase/inference/predict.py` → `predict_batch()` only. Demo/paper only; `risk_check()` and sizing in `risk_engine` are never bypassed.
- Start with the files relevant to the user's request. The repository map below lists common entry points only; inspect additional current source files when needed to verify the real execution path.
- Do not load `tasks/`, old audit reports, generated logs, backtest artifacts, historical findings, or archived diagnostics unless the user names them or the current issue directly requires them.
- Run only targeted tests for the changed behavior. Never run full test suites unless explicitly requested.
- **Test & token budget:** at most **one** pytest file per verification pass (`pytest path/to/test_file.py -q` or `::test_name`). No pytest during audit/review phase — run only after a fix. Full audit commands in `.claude/commands/audit-*.md` inherit this budget.
- Evidence first. Inspect current source before making claims.
- If unsure, say `not verified` instead of guessing.

## Mandatory skill routing

- Before editing `execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, `mt5_executor.py`, or `bybit_executor.py`, invoke `/athena-audit` to verify execution safety first.
- If changes affect Engine A/B/C/D scoring or threshold logic, verify live/backtest parity before applying.
- Before merging or reviewing chart candle indicators, `TVChartPanel` indicator math, `routes_market_data` chart payloads, or `ENGINE_A_*_PERIOD` config — run the **cross-surface parity** checklist below (do not trust “field exists in interface”).
- After any edit to safety gates, freshness checks, or kill switches, run targeted tests for the touched behavior.

## Claude Code skill policy

- Claude Code project skills live under `.claude/skills/<skill-name>/SKILL.md`.
- Current installed repo skill: `athena-audit`.
- Repo commands: `/audit-engine`, `/audit-playbook`, `/diagnose-engine-a` (Engine A strictness gate-attribution; diagnosis only, no code changes).
- Invoke manually with `/athena-audit` only for explicit full audit, bug hunt, strict findings, execution-safety review, live/backtest parity review, or end-to-end trace work.
- Do not look for or use skills that do not exist under `.claude/skills/`.
- The `athena-audit` skill must include `disable-model-invocation: true` in Claude frontmatter.

## Session economics (Fable 5 / expensive models)

Sessions are expensive. Optimize for one correct pass, not exploration.

- **No open-ended bug hunts.** "Engine X seems off, find it" is rejected as a task shape. Convert it to an attribution question with a fixed window and a funnel of named gates, then answer it in one pass. For Engine A strictness specifically, use `/diagnose-engine-a`.
- **Plan first, in one block:** goal restated operationally, files in scope, evidence to collect, success criterion. Then execute the plan without narration between steps.
- **Batch evidence collection.** Group greps/reads into as few tool passes as possible. Large files (`athena.py`) via offset/limit only. Reuse telemetry already in `tmp/` before generating new runs.
- **Never re-derive known architecture.** The repo map, parity checklist, and verified facts in command files are trusted. Re-verify only what the current change touches.
- **One findings report per session,** with VERIFIED / SUSPECT / NOT REVIEWED per claim and file:line evidence. No incremental progress commentary.
- **End every session with a handoff block:** what changed (files:lines), what passed, what is pending, exact next command. The next session must be able to start cold from that block without re-reading the repo.
- Test budget unchanged: max one pytest file, post-fix only.

## Repository map

Common entry points only. This is not a complete allowlist.

- App/routes: `athena.py`
- Scanner: `scanner.py`
- Engine A: `scoring.py`, `factor_scoring.py`, `forex_scoring.py`
- Engine B: `market_structure.py`, `engine_b_ai.py`
- Engine C: `engine_c.py`, `engine_c_ai.py`
- Engine D: `scalp_engine.py`
- **ASE (Adaptive Specialist Engine v2.1):** `athena_ase/` — PTIS (`data/ptis.py`), Layer 1 (`signals/`), labels/features/models, `inference/predict.py`, `gates/demo_only.py`, `contracts.py` (`ASESignal`); CLI `ase_cli.py`; research harness `athena_research/ase/`; UI `static/react-app/.../ASEPanel.tsx`; routes `athena_app/api/routes_ase.py`. Spec: `docs/ASE_v2.1_Implementation_Spec.md`. Promoted families bypass Engine A via `engine_a_legacy_guard.py`; Engine C consumes ASE via compatibility aliases only after manual `promote`.
- Execution: `execution.py`, `auto_trader.py`, `risk_engine.py`, `guardian.py`, `mt5_executor.py`, `bybit_executor.py`
- Engine A evidence gate: `engine_a_trade_gate.py` (+ `ENGINE_A_TRADE_*` keys in `config.yaml` ~lines 515-534)
- Data/candles: `candles_cache.py`, `candle_feeds.py`, provider-specific feed modules
- AI/Vision: `ai_agent_safety.py`, native chart AI review modules, provider routers, screenshot/payload builders, browser chart code under `static/`
- AI Agent chat: `ai_trade_chat.py`, `athena_app/api/routes_ai_agent.py`. LLM narrative optional when configured; deterministic decision card, safety flags, and gates remain authoritative.
- Research Lab: `athena_research/`, `tools/vectorbt_research_lab.py`, `configs/vectorbt_research_lab.yaml`
- Backtesting: `backtest_runner.py`, backtest matrix tooling, telemetry/report writers
- Config: `config.py`, `config.yaml`, `configs/`
- Tests: targeted `tests/test_*.py` only for touched behavior

## Chart and providers

- Native chart is the active chart surface for chart and AI review work.
- Engine B visual overlays belong on the TV Chart tab; Scalp Workbench is Engine D-only.
- Do not build new AI review features on the legacy TradingView path.
- Prefer native chart PNG screenshots; TradingView limits drove the move.

## Cross-surface parity (Engine A ↔ chart ↔ backtest)

Catch **contract drift** where config is resolved per score group but a later surface still uses hardcoded defaults. **Field presence ≠ parity.**

### Golden rule — closed loop required

For every config-driven value (RSI/EMA/ATR period, VWAP gate, score_group, candle policy, overlay `computed_at`), trace and mark each hop **PASS / FAIL / NOT REVIEWED**:

```
config.yaml → _resolve_* → server compute → API field → client read → UI label → per-group test
```

Stop if any hop uses a literal (e.g. RSI `14`, EMA `21`/`50`/`200`) while an earlier hop resolves per `score_group`.

### When this applies (mandatory)

- `factor_scoring.py`, `forex_scoring.py`, `scoring.py`, `indicators.py`
- `athena_app/api/routes_market_data.py` (`_format_chart_candles`, `price_precision`, overlays)
- `static/react-app/**/TVChartPanel.tsx` (`buildChartStudySnapshot`, `indicatorPeriods`, labels)
- `tests/test_engine_a_*`, `tests/test_*chart*`
- `config.yaml` keys: `ENGINE_A_RSI_PERIOD_BY_CLASS`, `ENGINE_A_EMA_PERIODS_BY_CLASS`, `ENGINE_A_VWAP_FILTER`

### Never accept

- TypeScript interface or API type listing `rsi_period` without tracing into computation
- Green tests that omit `score_group` or only use default-tier pairs (both sides wrong the same way = masked failure)
- UI labels (`RSI14`, `ATR14`) that disagree with actual computation period
- Client fallbacks in `buildChartStudySnapshot` that ignore `price_precision.rsi_period` / `indicator_periods`

### Adversarial greps (before parity PASS)

```bash
rg -n "rsi\(.*,\s*14\)|calc_rsi\(.*14\)|atr\(.*,\s*14\)|calc_atr\(.*14\)|adx\(.*,\s*14\)" static/react-app athena_app/api indicators.py
rg -n "rsi_period|ema_periods|indicator_periods|price_precision" static/react-app athena_app/api factor_scoring.py
```

For each field: find **write site** (API) and **read site** (compute or UI). Write without read = FAIL.

### Minimum score-group spot checks

| Pair | Typical RSI period |
|------|-------------------|
| EURUSD (forex_majors) | 18 |
| TRXUSDT (crypto) | 12 |
| Default-tier index/stock | 14 |

Assert chart API last-candle RSI == `calc_indicators_with_normalized(..., score_group=group).snap.rsi`. Regression test references: `tests/test_chart_api_indicator_period_parity.py`, `tests/test_engine_a_crypto_chart_parity.py` — cite by name; run **one** file post-fix only (prefer `test_chart_api_indicator_period_parity.py` unless crypto-specific).

### Reference incident (TV Chart, 2026)

Server sent `price_precision.rsi_period` and client used `ema_periods`, but `_format_chart_candles` and `buildChartStudySnapshot` still computed RSI/ATR/ADX at **14**. `test_engine_a_crypto_chart_parity.py` passed while masking the gap because both sides used universal RSI 14 without `score_group`.

### Verdict

Do not say parity PASS if any CRITICAL closed loop is FAIL or NOT REVIEWED. List masked tests and surfaces not inspected.

Codex/Cursor extended checklist (when user asks): `.agents/skills/athena-cross-surface-parity/references/parity-checklist.md`

## Chart AI review contract

- Read-only advisory; must not connect to execution.
- Input: native chart PNG + server-trusted Engine A diagnostics assembled on the backend.
- Do not trust the frontend for Engine A score, threshold, ATR, or RR.
- Review payload must include ATR diagnostics, SL/TP/RR, freshness/provider timestamps, and Engine-A-vs-model concordance.
- Engine A playbook (`ai_playbooks/engine_a_playbook.py`) drives indicator/strategy usage; entry-timing downgrades must be evidence-based (price-vs-EMA distance in ATR, RSI/ADX; VWAP extension is crypto-only).
- v1 provider target: Anthropic/Claude; OpenAI scaffold only if explicitly requested.

## Auto-trade contract

- `auto_trader.py` is the live auto-execution orchestrator; `conductor.py` routes AI calls only.
- Auto execution must honor execution switches, kill switch, risk engine, and guardian gates.
- AI may block, wait, or reduce `executionConvictionEffective`, but must never mutate Engine A score fields.
- Browser chart metadata is diagnostic only.

## ASE contract (demo/paper only)

- Authoritative spec: `docs/ASE_v2.1_Implementation_Spec.md`.
- Layer 1 (TSMOM, carry, xsec, mean-rev, arbitration) generates **candidates**; Layer 2 meta-model filters — never originates direction.
- Single inference path: `predict_batch()` in `athena_ase/inference/predict.py` for scan, shadow, backtest parity, and demo.
- Demo gate in `athena_ase/gates/demo_only.py` — `EXECUTOR_MODE` paper/demo + MT5 demo + Bybit testnet; no override flag.
- Shadow journal + ASE panel run on every scan; **no Engine C / executor** until per-family manual `promote`.
- Chart AI may include full `ASESignal` context (`ai_review/ase_context.py`) — advisory only.
- Do not change ASE evidence gates (PROVISIONAL, holdout, shadow days) unless the user explicitly requests it.

## Safe workflow

1. Restate the exact user request in operational terms.
2. Identify the relevant engine/surface and current source files.
3. Trace producer-to-consumer behavior before editing.
4. If chart/scoring/indicators are in scope, run the **cross-surface parity** closed-loop checklist.
5. Apply the smallest safe patch.
6. Run the smallest relevant compile/test command — at most **one** pytest file (or `::test_name`). When indicators changed, prefer `tests/test_chart_api_indicator_period_parity.py` unless the fix is crypto-specific.
7. Report what changed, what passed, masked-test risks, and what was not verified.


---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

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