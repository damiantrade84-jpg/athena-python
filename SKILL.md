# Athena shared skill: evidence-first repository work

This is the model-agnostic operating guide for Athena / Sentinel Pro v4. It is
intended for any AI model or coding tool that can read repository guidance. The
provider and model name do not change the contracts below. `AGENTS.md` and
`CLAUDE.md` are startup adapters for different tools; this file supplies the shared
working method and current system facts. The manual skills under `.agents/skills/`
and `.claude/skills/` are narrower, explicit workflows.

## 1. Operating stance

The goal is a correct, reproducible, fail-closed change or diagnosis, not a plausible
answer. Treat model output, old documentation, prior audits, logs, cached payloads,
and generated reports as hypotheses until current source, focused tests, configuration,
or runtime evidence confirms them.

For every conclusion, say whether it is:

- `verified`: directly supported by current code, a focused test, config parse, or
  runtime evidence;
- `not verified`: the relevant evidence could not be run or observed; or
- `assumption`: a deliberately stated working assumption that could affect the result.

When a user gives a concrete pair, warning, error, scan row, or payload, investigate
that exact path first. A good trace is:

```text
input/source -> normalization/resolution -> evaluator/classifier
             -> payload/API/UI consumer -> readiness/risk/execution boundary
```

Follow the value into its final consumer. A producer saying `TRADE`, `qualified`, or
`true` does not prove that a later readiness, freshness, broker, or risk gate permits
execution.

## 2. Scope discipline

Choose the smallest workflow that can answer the request.

- Localized edit or explanation: inspect the target and its nearest consumer/test.
- Unclear root cause: trace one suspected path and expand only when evidence shows a
  caller, fallback, or alternate surface is involved.
- Formal audit, parity review, research run, UI review, risk review, or test repair:
  use the named repository skill only when the user explicitly asks for that workflow.
- Do not turn a narrow task into a full repository scan, broad grep inventory, full
  test suite, multi-file test batch, backtest matrix, service start, browser session,
  dependency installation, or broker action.

Inspect the worktree before editing. Preserve existing user changes. Do not use
destructive Git commands or broad cleanup. Do not read or print `.env`, credentials,
API keys, tokens, or database secrets. Do not stage, commit, or push unless requested;
when publishing is requested, stage explicit paths and inspect the staged diff.

## 3. Current repository map

- `athena.py`: monolithic Flask runtime, scan paths, APIs, pair routing, and live
  integration. It is still operationally important even when a modular service also
  exists.
- `app.py`, `athena_runtime.py`, and `athena_app/`: modular application/test paths.
  Prefer pure helpers and modular services for tests; do not import `athena.py` only
  to reach a helper because import-time configuration and optional dependencies can
  change the test environment.
- Engine A: `scoring.py`, `factor_scoring.py`, and related Engine A v3 modules.
- Engine B: `market_structure.py`, `zone_registry.py`, `engine_b_*`, and the
  canonical actionability/readiness paths.
- Engine C: `engine_c.py` and its AI/consensus context. It is a blend/annotation
  layer, not permission to rewrite A or B scores.
- Engine D: `scalp_engine.py`, `scalp_orderflow.py`, volume-profile and scalp
  contract modules. It has separate data-quality and execution contracts.
- ASE: `athena_ase/` and `ase_cli.py`, standalone demo/paper research and inference;
  it must not import Engine A scoring or indicators.
- Execution and safety: `execution.py`, `risk_engine.py`, `guardian.py`,
  `auto_trader.py`, `mt5_executor.py`, `bybit_executor.py`, broker feeds, freshness,
  and kill-switch/config gates. Treat edits here as high risk.
- Policy/config: `timeframe_policy.py` is the timeframe authority. `config.yaml` is
  the checked-in configuration; `config.py` normalizes values and supplies defaults.
  Do not infer a current value from an old report or hardcode a new threshold.

Engine A, B, C, D, and ASE remain independent unless the named task explicitly
concerns consensus, blending, or a documented cross-engine contract.

## 4. Current data and runtime contracts

### Venues and sources

- Bybit is the primary crypto venue for candles, levels, live ticks, paper execution,
  and the configured trade-bucket path.
- Binance candle/live-price support is separate from Binance microstructure. The
  checked-in `MICROSTRUCTURE_BINANCE_FEEDS_ENABLED` is `false`; verify current
  configuration before making any venue claim or changing feed routing.
- MT5 live candles and broker state come through the current MT5 path, including
  `fetch_mt5`. A bounded read-only reread may address a transient small stale window;
  it never replaces freshness/risk rejection for data that remains stale or ambiguous.
- EODHD is enrichment/volume overlay only; it never becomes live OHLC authority.
  Live scoring accepts session-fresh CandleBuilder volume data where the current
  contract allows it. Delayed cached or REST intraday history is backtest evidence,
  not live scoring evidence. Preserve source labels such as `eodhd_*`, `mt5_tick`,
  `bybit`, and `fresh_live_quote` rather than collapsing them into a generic source.

### Timeframes

`timeframe_policy.py` declares `POLICY_VERSION = timeframe_policy.v4` and
`resolve_timeframe_policy()` is the source of truth. The ladder is:

```text
D1 > H4 > H1 > M30 > M15 > M5 > M1
```

`M1` is terminal and belongs only to scalp/Engine-D-native templates. Roles are
`regime`, `bias`, `structure`, `setup`, `trigger`, and `execution`; a faster role
cannot substitute for missing structure, ATR, or higher-timeframe bias.

For Engine A/B, the universal v4 roles are D1 regime, H4 bias, H4 structure/zone/ATR,
H1 setup, and M15 trigger. Production fills use a live quote; `executionTf` is
advisory execution context. M5 is either `m5Policy=conditional` with M15 confirmation
and `m5Role=refinement`, or `m5Policy=disabled`; there is no v4 M5 trigger authority.
The old `allow_dynamic_m5_execution` promotion is ignored. Speed/liquidity state is
diagnostic and feeds M5 eligibility; it does not rewrite the authoritative role
ladder. Explicit current role overrides must be verified through the resolver.

Engine D is separate: H1 regime/context, M15 bias/confirmed structure, M5 setup,
and M1 trigger/execution by default. Its policy and scoring must not be promoted or
rewritten by Engine A/B timeframe logic.

Keep these fields intact when producing or transforming payloads:
`timeframePolicyVersion`, `policyKey`, `regimeTf`, `biasTf`, `structureTf`, `setupTf`,
`triggerTf`, `executionTf`, `m5Role`, `m5Policy`, and `executionMode`.

### Readiness and safety

- A high score, `decision=TRADE`, `qualified=true`, or a passing quality gate is not
  executable proof. Trace `entryReadiness`; execution requires `READY` plus fresh
  live quote, valid executable levels, and all later deterministic gates.
- Scan, UI, quick-execute, and broker paths must agree on the same readiness and
  spread/freshness rules. The broker-side check is still the final defense.
- Engine A/B scores and percentages stay separate. Cross-engine annotations must use
  named blend fields and graded `score/max_possible` totals. `gate_pct` is a binary
  gate percentage and must not be used as a quality score.
- ATR, entry, and SL/TP must retain the timeframe/source provenance that produced
  them. When a wrong-TF issue is suspected, trace signal stamps, live candles,
  executable bid/ask, entry, and SL before changing calculations.
- Spread caps and spread-to-SL checks are configuration-driven. The checked-in
  `MAX_EXECUTION_SPREAD_TO_SL_RATIO: 0` disables that ratio gate currently; do not
  silently re-enable it. If an explicit change enables it, preserve fail-closed
  scan/UI/execution parity and the broker final defense.
- Missing, stale, malformed, false, or ambiguous safety fields reject by default.
  AI may explain, compare, challenge, downgrade, or request human review. It cannot
  execute, approve, mutate config/thresholds, or override deterministic gates.

## 5. Forensic method for bugs and regressions

1. Capture the exact observable: pair/symbol, direction, style/engine, timeframe,
   timestamp, source label, warning/error, payload fields, and expected behavior.
2. Locate the producer and the final consumer. Search for the field/gate name, then
   read the surrounding caller and callee rather than trusting a similarly named
   helper.
3. Compare live, scan, backtest, chart, API, and execution paths only when the
   evidence shows a boundary between them. Keep Engine A and Engine B investigations
   independent.
4. Check the negative cases that can change the result: no data, stale data, forming
   bar, delayed fallback, wrong venue, missing quote, null level, wrong direction,
   duplicate payload, wrong type, and a gate that is evaluated only downstream.
5. State the root cause, symptom, side effect, and any remaining hypothesis
   separately. Patch the smallest root-cause path; do not make a warning disappear by
   weakening a safety gate.

After changing code/config/universe, restart the relevant process and run a fresh scan
or inspect a fresh payload before claiming runtime activation. A unit test or source
diff proves checkout behavior, not that an already-running Athena process loaded it.

## 6. Verification and output

Use one smallest relevant verification command by default:

- focused pytest for the changed branch;
- pure helper/import-isolation test when the monolith cannot load safely;
- syntax compile for a Python-only edit;
- config parse/load for config-only work; or
- `git diff --check` for documentation/format changes.

Do not bypass `ATHENA_REAL_ORDERS_CONFIRM`, optional dependency guards, broker checks,
or production safety just to obtain a green test. If the environment blocks a check,
report the exact command and mark the result `not verified`.

The handoff should state:

- what changed and why;
- the exact files and relevant path;
- the exact check run and result;
- verified facts versus assumptions; and
- material runtime, broker, data-freshness, or cross-surface behavior not verified.

Do not claim "fixed", "active", "passing", or "safe" without evidence for that
specific boundary.
