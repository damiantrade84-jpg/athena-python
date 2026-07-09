# Engine A V3 Parity Fixes Design

## Goal

Make Engine A V3 backtest, scan, chart review, and manual demo execution consume the same explicit decision contract, without changing strategy thresholds, weights, or live-execution mode.

## Scope and staging

This work is split into two independently testable stages.

### Stage 1: decision-contract and backtest truthfulness

1. Create one public V3 timeframe resolver shared by quant scoring, evaluator, and V3 backtest.
2. Add `entryTimeframe` to the V3 signal contract and serialize it to API payloads.
3. Make the V3 backtest use that resolved timeframe for decision prefixes, entries, exits, diagnostics, and reported funnel metadata.
4. Treat non-standard V3 validation modes as unsupported until a historical parameter-selection and embargo implementation exists. The backend must reject `walk_forward` and `purged_cv`; the UI must disable them for Engine A and explain why.
5. Add explicit backtest comparability metadata. A run without replayable live context must say it is not promotion-eligible/live-comparable rather than silently representing the live model.
6. Reuse V3 scan eligibility for deterministic conditions that are available historically (raw qualification, pair enabled state, direction conflict, and confidence). Report unavailable real-time gates separately rather than substituting wall-clock state into history.

### Stage 2: safety, configuration, and research integrity

1. Reject unsupported `PAIR_PROFILES.disable_filters` configuration and remove the eight inert entries from current setup.
2. Make the TV Chart obey a V3 scanner `signalTier`; block watchlist/skip rows before confirmation.
3. Make V3 manual execution preserve a scanner-produced watchlist rejection through refresh. The server must not turn a known watchlist row into executable solely because raw V3 evaluation returns `TRADE`.
4. Add V3 event-risk and macro-event hard-block checks to shared classification.
5. Correct the research-only timeframe diagnostic: decision availability is the walk bar close, and an entry is allowed only when the selected entry bar touches the submitted zone.

## Architecture

`engine_a_v3/timeframes.py` will expose a public resolver for the configured entry timeframe. `quant_scorer`, `evaluator`, and `backtest` will consume it rather than duplicating H1/H4 assumptions. The evaluator will place the resolved value on `EngineASetupSignal`; UI routing and chart-review metadata will use that payload field.

The public backtest endpoint will retain `standard` behavior. For V3, it will return a clear client error for unsupported validation modes instead of silently returning a standard run. V3 results will include a `comparability` block describing whether live-only context or live-only gates were replayed. No historical external context will be fabricated.

The scanner classifier remains the authority for V3 trade tiering. Stage 2 will consume its tier in the chart and execution path. Server refresh will preserve a scanner-derived non-trade tier as a hard rejection; it will retain existing freshness, demo-attestation, risk, kill-switch, and broker guards.

## Data flow

```text
pair + horizon
  -> resolve_v3_entry_timeframe
  -> evaluator.signal.entryTimeframe
  -> scan classifier / UI chart route / V3 backtest primary timeframe

V3 backtest
  -> standard only, or explicit unsupported-mode error
  -> comparability metadata for unreplayed live context/gates

scan watchlist
  -> TV Chart blocks execution
  -> server refresh retains non-trade tier rejection
```

## Error handling and safety rules

- Missing or invalid resolved timeframe fails V3 evaluation/backtest closed; it must not silently select another timeframe.
- Unsupported V3 validation modes return a structured 422-style error with a stable code.
- Unreplayed microstructure/carry/sentiment context never receives fabricated historical values. It is visible in comparability metadata and blocks promotion use.
- A V3 `watchlist` or `skip` scanner tier cannot become a manual-demo execution candidate during refresh.
- Event hard blocks are demotions, not score rewrites.
- Existing demo attestation, risk checks, kill switch, duplicate check, and broker handoff remain unchanged.

## Test strategy

Tests are written first and must fail before production changes.

- Parameterize entry-timeframe parity over default, `forex_exotics`, and `crypto_other`; assert evaluator, backtest funnel, and UI route agree.
- Assert V3 API requests with `walk_forward` and `purged_cv` fail explicitly while `standard` returns standard metadata.
- Assert a V3 backtest result with no replay provider reports non-comparability; no test may manufacture a passing context implicitly.
- Assert raw V3 `TRADE` plus a confidence/disabled-pair/direction-conflict demotion is excluded from the scan-eligible backtest cohort.
- Assert current `disable_filters` keys are rejected by config validation.
- Assert a V3 watchlist signal is blocked in TV helper and server refresh verification.
- Assert event and macro hard-block fields classify V3 as watchlist.
- Assert a diagnostic H4 signal cannot enter an H1 bar before H4 close and cannot fill outside its entry zone.

## Non-goals

- No threshold, weight, SL/TP, risk sizing, or strategy-rule recalibration.
- No live-trading enablement; current demo-only V3 execution policy remains.
- No claim of performance improvement without rerunning a valid research workflow after the implementation.
- No full legacy Engine A rewrite; unreachable legacy code is not refactored in this change.

## Acceptance criteria

1. The 15 configured H4-entry pairs have identical live evaluator, backtest, and initial chart entry timeframe.
2. Engine A V3 cannot silently claim walk-forward or purged-CV validation.
3. Every V3 backtest discloses context/gate comparability status.
4. Scan-watchlist V3 rows cannot reach manual demo execution through TV Chart refresh.
5. Current per-pair dead filter settings cannot be accepted silently.
6. The timeframe diagnostic has no decision-time look-ahead and no no-touch zone fill.
