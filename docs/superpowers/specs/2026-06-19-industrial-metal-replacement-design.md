# Industrial Metal Replacement Data Gate Design

## Goal and scope

Qualify exactly one replacement commodity cluster, or prove that Copper, Aluminium, Nickel, and Zinc all fail in that frozen order. This is research-data work only. No strategy returns, specialist logic, backtests, Engine A/B/D, execution, thresholds, TradingView, or live routes are inspected or modified.

## Candidate loop

For each candidate, use the verified Pepperstone MT5 alias identical to its canonical display name. Add only the active candidate to `PHASE_1_CANONICAL_SYMBOLS`, `PHASE_1_MT5_TERMINAL_ALIASES`, the authoritative finalizer cluster table, and the consolidated industrial-metals family policy. Run focused registry tests before acquisition.

Acquire H4 and D1 from 2014-01-01 through 2026-06-19 using Python 3.13 and direct read-only MT5. A single explicit timezone-aware UTC `as_of` is shared by both timeframes for that candidate. Existing immutable chunk, capture-state, provisional-bar, content-addressed snapshot, and metadata-policy protections remain authoritative.

Run era-aware QA, spread and placeholder checks, corruption validation, and consolidated residual-gap review. Industrial-metal closure is never automatic: it requires recognized holiday/session evidence, absent D1 where expected, empty targeted H4 refetch, intact chunk provenance, no acquisition/corruption defect, and no conflicting peer evidence. Available industrial candidates may corroborate one another; XAU/XAG are secondary context only.

If provider gaps satisfy the preregistered admissibility policy, emit `CLEAR_WITH_GAP_EMBARGO` with exact timestamps and leave candidate-mask generation blocked until a specialist supplies `StrategyEmbargoContract`. Never interpolate bars.

Stop immediately after the first `CLEAR_ON_FREEZE` or `CLEAR_WITH_GAP_EMBARGO`. If blocked, preserve all versioned raw/QA/review artifacts and advance. The authoritative finalizer records the selected replacement, hashes, H4/D1 counts, quality dates, spread coverage, event aggregates, final gate, and total qualifying count. Success requires exactly eight qualifying authoritative clusters.

## Safety and failure semantics

Each candidate is independently attempted. Missing aliases, MT5 failures, store conflicts, QA failures, corruption, provider degradation, or unresolved evidence remain explicit blocked results. No gate or threshold is weakened. Registry changes for attempted candidates remain because their preserved artifacts require canonical resolution; only the selected candidate counts toward the authoritative eight-cluster gate.
