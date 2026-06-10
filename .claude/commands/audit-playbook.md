---
description: Audit AI playbooks and chart review payloads — strategy/indicator parity with engines, candle reading, server-trusted context, advisory-only.
---

# Athena Playbook / AI Review Audit

Audit target: AI playbooks and the chart AI review pipeline. This is a single-surface audit — do NOT audit engine scoring math, execution gates, or chart rendering beyond what the playbooks claim about them.

## Hard rules (non-negotiable)

1. **Scope only.** Read only the files listed below (plus the specific config keys named). Never load `tasks/`, logs, old audits, generated artifacts, or memory files.
2. **Token discipline.** Do NOT run any tests during the audit phase. Tests run ONLY after a fix is applied, and only the single targeted test file for that fix (`pytest tests/test_X.py -q`). Never run the full suite. Do not re-read files already read. If multiple fix-verification tests are named, run only the **one** file that matches the fix — not the whole list.
3. **Evidence only — no guessing, no hallucinations.** Every finding cites file + function + line from current source read in this session. Anything not inspected is `not verified`.
4. **AI is advisory-only.** Playbooks and reviews must never connect to execution, approve orders, mutate config, or override deterministic gates. Flag ANY coupling as CRITICAL.
5. **Improvements beyond CLAUDE.md are welcome** — list as suggestions (config-gated, default-safe), separated from bug findings.
6. **Backend → UI rule.** If a fix changes a review payload field, trace it to its React read-site in `static/react-app/` and update it in the same change; then run only the frontend type-check/build.

## Files in scope

- `ai_playbooks/engine_a_playbook.py` (`get_engine_a_playbook`)
- `ai_playbooks/engine_b_playbook.py` (`get_engine_b_playbook`)
- `ai_playbooks/engine_d_scalp_playbook.py` (`get_engine_d_scalp_playbook`)
- `ai_playbooks/contracts.py` (`PLAYBOOK_SCHEMA_VERSION`, decision enums)
- `ai_playbooks/trade_skill_normalizer.py`
- `ai_review/engine_a_context.py` (`assemble_engine_a_context`)
- `ai_review/context_diagnostics.py`, `ai_review/concordance.py`, `ai_review/payload_schema.py`
- `athena_app/api/routes_ai_chart_review.py`
- Read-only cross-reference (to verify playbook claims, not to audit): `factor_scoring.py` indicator resolvers, `indicators.calc_levels`, `market_structure.py` structural level names, `scalp_engine.py` setup/grade names.

## Checks

### 1. Playbook ↔ engine parity (strategies and indicators)

For each playbook, verify every strategy name, entry model, indicator, and period it tells the AI to use matches what the engine ACTUALLY computes today:

- Engine A playbook `indicatorUsage` (EMA/RSI/ATR/VWAP/RR/Engine B context) vs `factor_scoring` resolvers — periods are per `score_group` (`ENGINE_A_RSI_PERIOD_BY_CLASS`, `ENGINE_A_EMA_PERIODS_BY_CLASS`); the playbook must not assert universal literals (RSI 14, EMA 21/50/200) as fact for all pairs.
- Engine A entry models (`CONFLUENCE_CONTINUATION`, `PULLBACK_TO_STRUCTURE`, etc.) and `mustRejectIf` rules vs actual abort/gate names in `compute_factor_scores` / `scoring._classify_signal`.
- Engine B playbook BOS/CHoCH/sweep/zone models and RR/zone invalidation rules vs `market_structure.py` (`analyze_structure_direction`, `resolve_engine_b_execution_levels`) — structural SL/TP field names (`recommended_stop_loss`, `recommended_take_profit`, `tp_source`) must match.
- Engine D scalp playbook (Fabio/Carmine, session context, effort-vs-result, location checklist, structural stops) vs `scalp_engine.py` (`_classify_setup`, `calculate_scalp_levels`, `ai_quality_grade`, grade thresholds).
- Per-pair balance: playbooks must not embed guidance valid for only one pair/group (e.g. crypto-only VWAP extension presented as universal). VWAP extension downgrades are crypto-only.

### 2. Candle reading

- The AI must be told what basis the chart series uses: `indicator_basis: confirmed_only` (from `_format_chart_candles`). Verify the playbook/prompt does not instruct the AI to read the forming candle as confirmed, and that last-candle semantics in the prompt match the payload.
- Entry-timing downgrades must be evidence-based per CLAUDE.md: price-vs-EMA distance measured in ATR, RSI/ADX values from the payload — not eyeballed from the image alone.

### 3. Server-trusted payload (no frontend trust)

- `routes_ai_chart_review.py`: client may send only `symbol`, `timeframe`, `screenshot_base64`, `screenshot_meta`, `provider`. Engine A score, threshold, ATR, SL/TP/RR, freshness must come from `assemble_engine_a_context` (server recompute via `analyze_pair`) — flag CRITICAL if any trusted numeric is read from the request body.
- Payload completeness: ATR diagnostics (`atrDiagnostics` / `atr_source`), SL/TP/RR, freshness/provider timestamps, and Engine-A-vs-model concordance (`ai_review/concordance.py`) must all be present in the assembled context.
- `payload_schema.py` / `context_diagnostics.py`: missing/null/stale fields must degrade explicitly (marked unavailable), never silently filled with defaults the AI would treat as real.

### 4. Advisory-only enforcement

- No code path from playbook output or review verdict into `execution.py`, `auto_trader.py`, order placement, or config mutation. The AI may block/wait/reduce conviction downstream but must never raise it past deterministic gates or mutate Engine A score fields.
- `trade_skill_normalizer.py`: normalization must not invent decisions or upgrade severity/confidence when fields are missing.

## Procedure

1. List the exact files you will read before reading them.
2. Read playbooks first; extract every concrete claim (indicator, period, strategy name, field name, threshold).
3. Verify each claim against the engine/source cross-references. Mark each claim PASS / FAIL / NOT REVIEWED.
4. Trace the review route producer → consumer (`routes_ai_chart_review.py` → `assemble_engine_a_context` → prompt builder → provider call).
5. Apply fixes only for confirmed bugs (smallest safe diff). After each fix run ONLY its targeted test: `tests/test_ai_chart_review.py`, `tests/test_chart_analysis_prompt_wiring.py`, or `tests/test_tv_chart_panel.py` as appropriate.

## Output contract

1. **Coverage map** — files read, files in scope NOT read (and why).
2. **Claim parity table** — playbook claim → engine source evidence → PASS/FAIL/NOT REVIEWED.
3. **Findings** — severity, file + function + line, why real, expected behavior, minimal fix, one regression test.
4. **Improvements (beyond current CLAUDE.md rules)** — suggestions only, config-gated, default-safe.
5. **Not verified** — explicit list. Never claim "no issues" for anything on this list.
