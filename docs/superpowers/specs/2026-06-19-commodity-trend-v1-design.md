# Commodity H4 Trend Continuation V1 Design

## Scope and safety boundary

`COMMODITY_H4_TREND_CONTINUATION_V1` is research-only. It may read the authoritative commodity gate and frozen normalized artifacts, but it must not write raw data, import production scoring or execution code, or infer missing higher-timeframe data.

## Frozen strategy contract

The strategy uses D1 context and H4 signals for the eight authoritative qualifying clusters. The fixed parameters are EMA20/50/200, ADX14 with floor 20, ATR14, a 20-bar breakout range, 1.5 ATR stop, 2.5 ATR target, 18-bar maximum hold, and one-bar maximum entry delay. Pullback, breakout, contraction, expansion, cost, chronological holdout, and symbol-holdout definitions are exactly those supplied by the user on 2026-06-19.

The embargo contract is versioned and hashed with the specification: feature lookback 120 H4 bars, post-gap rewarm 120, label holding 18, and entry delay 1.

## Fail-closed preflight

Before signal or performance code runs, the preflight must verify:

- the authoritative table reports exactly eight qualifying clusters and raw hashes unchanged;
- XPD/USD is excluded;
- each selected cluster has authoritative H4 review evidence;
- each selected cluster has H4 and D1 normalized files and manifests;
- normalized rows are confirmed and required masks/reviews exist;
- file hashes agree with their manifests.

Any missing D1 artifact is `BLOCKED_DATA`. No D1 resampling from H4 is permitted because that would create an unregistered derived context source and would not satisfy the requested frozen D1 evidence.

## Current evidence boundary

Repository inspection on 2026-06-19 found a frozen D1 normalized artifact only for Copper. XAU/USD, XAG/USD, XPT/USD, WTI Oil, Brent Oil, Nat Gas, and Gasoline lack normalized D1 artifacts. Therefore this run must stop in Phase 0 and emit a versioned preflight report with primary verdict `BLOCKED_DATA`. No strategy returns may be calculated or inspected.

## Outputs

The implementation creates a small research-only package that loads the gate, checks the artifacts without mutation, and writes a content-addressed preflight JSON report. Tests cover gate loading, XPD exclusion, D1 completeness, raw-hash status, deterministic hashing, and absence of production/execution imports.
