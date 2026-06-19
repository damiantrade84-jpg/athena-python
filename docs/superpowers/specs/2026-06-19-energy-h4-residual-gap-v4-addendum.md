# Energy H4 Residual-Gap Review v4 Addendum (WTI Oil + Nat Gas)

Supersedes the `commodity_h4_residual_gap_review_v3` run **for WTI Oil and Nat Gas
only**. The v1/v2/v3 review artifacts, every QA manifest, every per-symbol
forensic report, and all raw chunks are preserved unchanged. v4 introduces two
deterministic energy-session evidence flags and writes new artifacts under
`logs/commodity_data_audit/forensics/consolidated_v4/`.

## Why v3 left these UNRESOLVED

`classify_residual_event` required `d1_absent` for a holiday closure and required
`primary_peer_traded or provider_history_absence` for a provider gap. Two real
energy patterns satisfied neither branch and fell through to UNRESOLVED:

1. **WTI early-era holiday placeholder days** (2017-12-25, 2018-01-01,
   2018-03-30). The provider stamped a **zero-range** (`open==high==low==close`)
   live D1 plus flat placeholder H4 neighbours, so `d1_absent` was `False` even
   though the market was closed. Forensic proof: every *present* bar on the day
   is flat at the D1 price (58.42 / 60.03 / 64.83); real trading resumes only the
   next session; Brent (primary peer) is fully absent on the same dates.

2. **Nat Gas isolated non-holiday intraday gap** (2018-07-17 12:00 → 2018-07-18
   04:00). No holiday, no peer same-family corroboration, but D1 has a **real**
   range and the adjacent H4 bars trade normally; WTI traded continuously through
   the identical window. The day demonstrably traded → provider history gap.

## v4 evidence flags (deterministic, frozen-data only)

* `d1_holiday_placeholder` — `recognized_holiday AND D1 zero-range placeholder AND
  every present same-date H4 bar is a zero-range placeholder`. Treated as
  equivalent to `d1_absent` for the closure condition (a flat full day is not
  trading).
* `subject_day_traded` — `NOT recognized_holiday AND D1 real-range AND present
  same-date H4 bars are real trades (or none survive — never flat placeholders)`.
  Treated as provider-gap support alongside `primary_peer_traded` /
  `provider_history_absence`.

Both flags default `False`, so metals/PGM/Brent/Gasoline classifications and all
pre-existing tests are unchanged. Reopening gaps remain risk metadata, never
corruption (the corruption validator is unchanged and reported zero defects).

## Reproducibility

`tools/review_energy_reliable_gaps.py` rebuilds events from the frozen v3
artifact + read-only raw OHLC inspection. **No live MT5 call** — the frozen
targeted-refetch result (`subject_refetch_empty`) is reused verbatim (MT5 is
unavailable in this environment; re-fetching would be non-reproducible). Each v4
artifact records `prior_artifact.raw_source_unchanged_vs_prior` proving the raw
source hashes are byte-identical to v3.

## Outcome

* WTI Oil — 11/11 `LEGITIMATE_MARKET_CLOSURE` → **CLEAR_ON_FREEZE**.
* Nat Gas — 8 `LEGITIMATE_MARKET_CLOSURE` + 1 `PROVIDER_HISTORY_GAP`
  (5 bars / 13667 = 0.037% < 0.5%, isolated, rolling-90d 1.31% < 2%) →
  **CLEAR_WITH_GAP_EMBARGO**. Embargo interval 2018-07-17T12:00Z →
  2018-07-18T04:00Z. Candidate-mask generation stays
  `BLOCKED_MISSING_STRATEGY_EMBARGO_CONTRACT` until a `StrategyEmbargoContract`
  is supplied.
