# Commodity H4 Consolidated Residual-Gap Review Design

## Scope

Build a separate, research-only consolidated reviewer and gate evaluator for XPT/USD, XPD/USD, WTI Oil, Brent Oil, Nat Gas, and Gasoline H4 freezes. Existing raw bars, generic reviews, QA manifests, Engine A/B/D, TradingView, live routes, thresholds, and execution remain unchanged. The new workflow writes only new versioned review and gate artifacts and never interpolates or synthesizes bars.

## Versioned contracts

- Review schema: `commodity_h4_residual_gap_review_v3` (`v1` and `v2` are preserved as superseded development runs).
- Gate policy: `commodity_h4_gap_admissibility_v1`.
- Embargo contract: `commodity_gap_embargo_contract_v1`.
- Artifact names include the schema/policy version and are immutable; conflicting rewrites fail closed.
- Every event contains source hashes, exact missing timestamps, family policy, all gathered evidence, exactly one final classification, and reasons.

The six mutually exclusive classifications are `LEGITIMATE_MARKET_CLOSURE`, `ACQUISITION_DEFECT`, `PROVIDER_HISTORY_GAP`, `HISTORICAL_INTRADAY_DEGRADATION`, `DATA_CORRUPTION`, and `UNRESOLVED`. Acquisition defects and corruption have precedence over peer evidence. If independent evidence supports conflicting non-precedence classifications, the result is `UNRESOLVED`.

## Family-aware evidence

- XPT uses XPD as primary peer and XAU/XAG as secondary corroboration; XPD uses XPT primary and XAU/XAG secondary.
- WTI and Brent are primary peers.
- Gasoline uses WTI and Brent as contextual peers; peer presence alone cannot establish a closure.
- Nat Gas has no synthetic/self peer. It may establish closure only with recognized energy holiday/session evidence, absent D1, successful empty subject refetch, intact chunk provenance, and a clean reopening-gap corruption result.
- Energy closure policy is explicit and separate from metals holiday policy. Reopening gaps remain risk metadata unless the corruption validator reports a concrete integrity defect.

## Provider-gap admissibility

A dataset is eligible for `CLEAR_WITH_GAP_EMBARGO` only when all conditions hold:

1. Acquisition-defect, corruption, and unresolved counts are zero.
2. `missing_percentage = 100 * provider_gap_missing_expected_slots / reliable_era_expected_slots` is strictly less than `0.5`; numerator, denominator, unrounded decimal ratio, and display percentage are recorded.
3. Every provider-gap event contains at most 6 missing H4 expected-session bars.
4. Provider-gap events are separated by at least 20 calendar days, measured from the prior event's next-bar timestamp to the next event's previous-bar timestamp.
5. In every rolling 90-calendar-day window, provider-gap missing expected-session bars divided by reliable-era expected-session bars is strictly less than `2.0%`.
6. Exact missing timestamps are preserved and raw data remains unchanged.

Failing event length, separation, or rolling-rate criteria produces `BLOCKED_PROVIDER_DEGRADATION`. Historical gaps before the reliable-era boundary remain `HISTORICAL_INTRADAY_DEGRADATION` and do not enter the reliable-era provider-gap numerator.

## Gates

Gate precedence is deterministic:

1. Any `DATA_CORRUPTION` → `BLOCKED_CORRUPTION`.
2. Any `ACQUISITION_DEFECT` → `BLOCKED_ACQUISITION_DEFECT`.
3. Any `UNRESOLVED` → `BLOCKED_UNRESOLVED`.
4. Provider gaps failing admissibility → `BLOCKED_PROVIDER_DEGRADATION`.
5. Admissible provider gaps → `CLEAR_WITH_GAP_EMBARGO`.
6. No residual provider gaps and all other events legitimate/historical → `CLEAR_ON_FREEZE`.

The data-quality gate is independent of strategy availability. A dataset may be clear before any strategy exists.

## Strategy embargo contract and mask

`StrategyEmbargoContract` has no defaults and requires:

- `max_feature_lookback_bars: int` greater than zero;
- `post_gap_rewarm_bars: int` zero or greater;
- `max_label_holding_bars: int` zero or greater;
- `max_entry_delay_bars: int` zero or greater.

The validated values, contract version, review artifact hash, policy version, symbol, timeframe, and reliable-era source hashes are included in a stable run hash. Candidate-mask generation fails closed when the contract is missing or invalid.

For every provider gap, the deterministic mask rejects a candidate when:

- its inclusive feature lookback intersects a missing timestamp;
- its entry-delay, label, or maximum holding interval intersects a missing timestamp;
- its candidate timestamp falls within `post_gap_rewarm_bars` after the first observed bar following the gap.

The mask returns booleans plus reason codes and exclusion intervals. It never inserts bars, changes prices, or removes rows from the canonical raw store.

## Artifacts and tests

Create `athena_research/commodity_data_audit/consolidated_gap_review.py`, `athena_research/commodity_data_audit/gap_embargo.py`, and a read-only CLI under `tools/`. Add focused tests in one new test file covering energy closure without a same-family peer, WTI/Brent and Gasoline two-peer closure, admissible isolated gaps, sustained degradation, corruption/unresolved precedence, feature/label crossings, missing-contract failure, and unchanged bar sequences.

Generate corrected versioned artifacts under symbol-specific subdirectories of `logs/commodity_data_audit/forensics/consolidated_v3/`; preserve the superseded `v1`/`v2` runs and every earlier review, manifest, and raw file.
