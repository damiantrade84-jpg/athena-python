---
description:
alwaysApply: true
---

# Athena / Sentinel Pro v4 - shared AI operating contract

This repository is a multi-engine trading analysis and execution-support system. The
same engineering contract applies regardless of model, provider, IDE, or agent
runner. The default is paper/demo mode; live trading requires explicit user approval.

Read this file and the root `SKILL.md` before substantial work. Read a nested
`AGENTS.md` when one exists below the directory being changed. Manual workflow files
under `.agents/skills/` and `.claude/skills/` are explicit-only and do not replace
these always-on rules.

## Evidence and scope

- Treat every claim, model suggestion, old audit, log, and generated artifact as
  unverified until current source, focused tests, configuration, or runtime evidence
  confirms it. Label conclusions as `verified`, `not verified`, or `assumption`.
- Start at the named pair, warning, error, payload field, gate, or file. Trace the
  actual producer -> normalizer/resolver -> evaluator/classifier -> API/UI -> risk or
  execution consumer. Do not stop at a plausible symptom.
- Inspect negative paths relevant to the question: missing, null, false, empty,
  malformed, stale, delayed, duplicated, wrong-type, and conflicting inputs.
- Use the lightest workflow that can prove the request. Use a direct edit for a
  localized change; use a narrow path investigation when the cause is unclear; use a
  named manual skill only when the user explicitly requests that workflow.
- Do not broaden a small task into a repository inventory, full audit, subagent run,
  full test suite, long backtest, service start, browser run, or broker action.

## Worktree and secrets

- Inspect status and the relevant diff before editing. Existing changes belong to the
  user: preserve them, do not reset, clean, checkout, or overwrite unrelated files.
- Use `apply_patch` for hand edits. Keep diffs minimal and traceable to the request.
- Never read, print, copy, modify, or commit `.env`, credentials, API keys, tokens,
  database secrets, or private connection material. Use `.env.example` and redacted
  diagnostics only.
- Do not stage, commit, or push unless asked. If publishing is requested, stage
  explicit intended paths and inspect the staged diff; never include unrelated dirty
  work.

## Current repository boundaries

- `athena.py` is still the monolithic runtime and contains live scan/API wiring;
  `app.py`, `athena_runtime.py`, and `athena_app/` provide modular/testable paths.
  Do not assume a modular helper is the only consumer or that a passing helper test
  proves the running monolith changed.
- Engine A (factor confluence), Engine B (naked structure), Engine C (consensus/AI
  blend), Engine D (scalp), and ASE are independent. Do not copy scores, gates,
  indicators, thresholds, or execution semantics between engines unless an explicit
  contract requires it. ASE remains standalone under `athena_ase/` and demo/paper
  only.
- Engine B bias layer: `engine_b_hierarchy.py` (pure, no `market_structure`
  imports) evaluates the ICT top-down hierarchy — Daily (Weekly+Daily for swing
  style, W1 resampled from D1) HTF bias from sweep + displacement + FVG/OB
  narrative, then MTF (structure-TF) confirmation, then LTF (trigger-TF) entry.
  `ENGINE_B_BIAS_MODE` gates enforcement: `legacy` (checked-in default;
  diagnostics only), `hierarchical` (soft score weighting + conflict/counter-bias
  blocks), `strict` (sequential state machine). Daily FVGs/OBs are both bias
  contributors and opposing PD-array diagnostics; the conflict diagnostic and
  `d1_pd_array_penalty` are unchanged.
- Bybit is the primary crypto venue for candles, levels, live ticks, paper execution,
  and the configured trade-bucket path. Binance candle/live-price support is a
  separate path. `MICROSTRUCTURE_BINANCE_FEEDS_ENABLED` is false in the checked-in
  configuration; do not enable or treat Binance as primary without verifying current
  config and venue routing.
- MT5 live candles and broker state come through the current MT5 path (including
  `fetch_mt5`). Bounded read-only rereads can improve a small stale window, but stale,
  ambiguous, unavailable, or broker-invalid data must still fail closed at freshness
  and risk gates.
- EODHD is an overlay/enrichment source, not live OHLC authority. Live scoring may
  use only session-fresh CandleBuilder volume paths; delayed cached/REST intraday
  history is backtest evidence only. Preserve source labels and do not let delayed
  `eodhd_*` history pass a live gate.
- Tunable thresholds, risk limits, gates, symbols, and routing belong in the config
  layer (`config.yaml` plus `config.py` normalization/defaults). Do not hardcode a
  new value in Python or silently tune a locked threshold.

## Timeframe contract

- `timeframe_policy.py` (`POLICY_VERSION = timeframe_policy.v4`) and
  `resolve_timeframe_policy()` are authoritative. `market_structure` adapters are
  consumers, not a second policy source. Legacy routing tables and hardcoded style
  tables are not authoritative.
- The slow-to-fast ladder is `D1 > H4 > H1 > M30 > M15 > M5 > M1`. `M1` is permitted
  only by scalp/Engine-D-native templates. Roles are distinct: `regime`, `bias`,
  `structure`, `setup`, `trigger`, and `execution`.
- The universal Engine A/B ladder is `D1` regime, `H4` bias, `H4` structure/zone/ATR,
  `H1` setup, and `M15` trigger. Production execution is live-quote based; the
  emitted `executionTf` is advisory execution context. M5 is conditional refinement
  after M15 confirmation or disabled; it is not a replacement trigger or structure
  timeframe. The v4 `allow_dynamic_m5_execution` promotion is deprecated/ignored.
- Speed and liquidity state do not rewrite the authoritative Engine A/B role ladder
  in v4. They are recorded for diagnostics and M5 eligibility. Only an explicit,
  current resolver/config override may patch a role; verify that override rather than
  inferring policy from a signal label.
- Engine D has its own native contract: H1 regime/context, M15 bias/confirmed
  structure, M5 setup/context, and M1 trigger/execution by default. Do not let an
  Engine A/B policy change alter Engine D scoring or timeframes.
- Preserve and trace policy provenance: `timeframePolicyVersion`, `policyKey`,
  `regimeTf`, `biasTf`, `structureTf`, `setupTf`, `triggerTf`, `executionTf`,
  `m5Role`, `m5Policy`, and `executionMode`.

## Safety and cross-surface invariants

- Never bypass or weaken freshness, risk, kill-switch, guardian, execution approval,
  broker, audit, spread, fee, RR, SL/TP, sizing, or deterministic data-quality gates.
  Missing or ambiguous safety data rejects by default.
- Engine A and Engine B score fields remain independent. Cross-engine annotations may
  use explicitly named fields such as `combinedConviction` or `engine_b_*`, but use
  graded `score / max_possible` totals. Never use binary `gate_pct` as a quality
  blend; it is 100 for passing gates and saturates downstream scores.
- `decision=TRADE`, `qualified=true`, or a high quality score is not executable proof.
  Trace explicit `entryReadiness` and require `READY`, fresh live quote, valid
  levels, and every downstream deterministic gate. Scan/UI eligibility and execute-
  time readiness must agree.
- ATR, entry, and SL/TP provenance must travel together. If a timeframe mismatch is
  suspected, trace stamped signal provenance, live candle inputs, executable bid/ask
  entry, and SL before changing calculations.
- Spread caps and spread-to-SL checks are config-driven and have both scan/UI and
  broker defenses. The checked-in `MAX_EXECUTION_SPREAD_TO_SL_RATIO` is currently
  `0` (disabled); do not silently restore or tune it. If enabled by an explicit
  change, preserve the same fail-closed gate and provenance at every consumer.
- AI is advisory only. Any provider/model may explain, challenge, downgrade, or
  request review; it cannot execute, approve, mutate config, alter strategy
  parameters, or override deterministic gates.

## Verification and handoff

- Run the smallest relevant check once: a focused test, pure-helper check, syntax
  compile, config parse, or `git diff --check`. Do not repeat a passing command when
  the relevant code did not change.
- Tests should prefer modular/pure paths and must not import `athena.py` merely to
  test a helper. If optional dependencies or live-order environment guards block a
  full import, do not bypass production safety; test the pure path and report the
  blocked check.
- Code/config/universe changes are not active in an already-running Athena process.
  Restart and perform a fresh scan or payload check before claiming runtime behavior
  changed. Unit coverage does not prove broker or terminal activation.
- Before declaring done, report the exact files changed, the exact check run, what is
  verified, and material risk or runtime behavior that remains not verified.
