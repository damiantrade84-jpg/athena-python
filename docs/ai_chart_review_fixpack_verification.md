# AI Chart Review Fix Pack — Verification Report

Companion to `ai_chart_review_fixpack_phase0_map.md`. Covers Phase 5 evidence and
the responses to the follow-up review findings F1–F5.

Date: 2026-08-12. All work below is **uncommitted** except the TV-chart commit
`7e35a685`, which is on `main`.

---

## Test results

Command (from repo root, `.venv` interpreter):

```bash
./.venv/Scripts/python.exe -m pytest tests/test_ai_chart_review.py tests/test_engine_b_chart_context.py tests/test_timeframe_routing.py tests/test_ai_review_gate_hygiene.py tests/test_ai_review_factor_completeness.py tests/test_ai_review_engine_b_structure_contract.py tests/test_ai_review_score_attribution.py tests/test_ai_review_fixpack_regressions.py tests/test_ai_review_watch_log.py tests/test_ai_review_freshness_gate.py tests/test_engine_a_v3_quant_scorer.py tests/test_routes_ai_stream.py -q
```

**265 passed, 1 failed.**

The single failure is `test_engine_a_v3_quant_scorer.py::test_score_pair_rebuilds_dynamic_trend_route_when_policy_changes`
(`scoringTimeframes.trend` is `["D1","H4","H1"]`, expected `["D1","H4"]`).
Attributed as pre-existing: it reproduces with `engine_a_v3/evaluator.py` and
`engine_a_v3/contract.py` stashed back to HEAD. It is Engine A quant scoring and
outside this fix pack.

New test files added by this pack:

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_ai_review_gate_hygiene.py` | 13 | P2-1, P2-2, P2-3 |
| `tests/test_ai_review_factor_completeness.py` | 9 | P2-4 |
| `tests/test_ai_review_engine_b_structure_contract.py` | 12 | P1-5, P1-6 |
| `tests/test_ai_review_score_attribution.py` | 8 | P2-6 |
| `tests/test_ai_review_fixpack_regressions.py` | 20 | Phase 5 scans 1–3, P2-8, F4 |
| `tests/test_ai_review_watch_log.py` | 12 | Phase 4 WATCH transitions |
| `tests/test_ai_review_freshness_gate.py` | 9 | F1, F5 |

No backtests were run; this is review-layer work.

---

## Phase 5 regression fixtures

Built from the three audited scans, asserting the audited numbers directly.

**Scan 3** (`live 65.92, entry 65.5865, SL 62.7437, TP1 68.4293, TP2 69.8507`):

| Assertion | Value |
|-----------|-------|
| `rr_live_tp1` | 0.79 |
| `rr_at_signal_tp1` | 1.00 |
| `rr_live_tp2` | 1.24 |
| `rr_live_blended` (half vol at TP2) | 1.02 |
| `zone_status` | `ABOVE_ZONE` |
| `entryQuality` | < 40 |
| displacement | 1.98 ATR |
| WATCH zone (EMA21 ± 0.5 ATR) | 63.57 – 64.52 |
| RR at watch | > 3.0 |

**Scan 2**: `SI=F` vs `XAG/USD` hard-rejects with no declared alias;
`DEMO_UNVALIDATED` cannot reach `setup_label == "TRADE"`; `active_session_utc`
PASSes at 06:38 UTC with a 01:00 UTC confirmed candle.

**Scan 1**: 1429s capture skew flagged; empty `factorDiagnostics` scores
`unverifiable` (never 84).

**P2-8**: an M30 capture into an H4 review context yields
`status == "not_comparable_timeframe"`, and an H4 capture with differing values
yields `values_differ` — confirming the chart ATR slot reads the capture rather
than copying the H4 value.

---

## Review findings F1–F5

### F1 — accepted, fixed

Correct and serious. `review_timestamp` is stamped `now()` at context assembly
(`ai_review/engine_a_context.py`), so capture-vs-review is always small on a
normal submit and the old string-matching filter could never fire on the audited
case. The `not engine_ctx.get("review_timestamp")` branch was dead, and the
candidate-age advisory was never matched.

Replaced with `evaluate_review_freshness()` in `ai_review/timestamp_contract.py`,
returning a structured decision consumed by the route.

**Policy decision — corrected 2026-08-12 after a live `stale_candidate_levels`
rejection on AUD/NZD.** Candidate age is **advisory, not blocking**
(`ENFORCE_CANDIDATE_AGE_PRE_PROVIDER` defaults false; set it true for the
stricter behaviour).

The original decision rested on a wrong premise — that entry/SL/TP are all copied
from the origin candidate. They are not. `ai_review/engine_a_context.py` takes
`price`, `stop_loss` and `take_profit` from the live re-analysis (`signal`);
only `candidate_entry` comes from `origin`, and it is labelled as such alongside
`price_displacement_from_candidate_entry`. Blocking on age therefore rejected
reviews whose levels were current, which is the ordinary workflow: a candidate
opened from a scan card is routinely older than the window.

The genuine risk from an old candidate is entry/trigger-zone drift, and that is
measured directly rather than inferred from a clock — `zone_status`
(ABOVE_ZONE/BELOW_ZONE clears `execution_permitted`), `rr_live` recomputed at
live price, and the displacement field. Those remain blocking.
`levels_provenance` is still emitted on every review naming both clocks.

Missing `captured_at` now also blocks rather than warning — an unprovable capture
age cannot be treated as fresh.

### F2 — accepted, no code change; needs a decision

Correct, and it restates the scope call made in `gate_hygiene.py`. Engine A still
evaluates `active_session_utc` against the last confirmed candle at
`engine_a_v3/setups.py:426`. The review panel now renders it honestly and
preserves Engine A's own verdict on each gate's `engineValue`, but the engine's
internal decision is unchanged.

**Open for the user.** Changing it edits Engine A and would let more setups
through the session gate live. Not done unilaterally.

### F3 — accepted; root causes differ per test

The report attributed these to `ai_review/engine_a_context.py:727`. That file was
modified by this pack, but the function at that line was not:
`resolve_chart_review_analyze_style` hashes identically at HEAD and in the working
tree (`ae74f6b21a3c9f58`). Actual causes:

1. **Two `timeframe_route` tests — unrealistic fixture.** `_resolve()` returned
   `display = symbol`, so `"BTCUSDT"` classified as `crypto_other` (swing-only
   since `766d0135`, 2026-07-16) instead of `crypto_btc` (intraday). Verified:
   `bare → (crypto_other, swing)`, `real → (crypto_btc, intraday)`. **Fixed the
   fixture**; the assertions were correct.
2. **`analyze_style` and Engine B style tests — intentional behaviour change.**
   Session-bound classes moved to the intraday ladder in `2a1171e0`
   (2026-08-07, "Balance Engine A/B TF ladders"), with the rationale stated at
   `style_resolver.py:108-115`. The assertions date from `d17d188a`
   (2026-05-22). **Updated the assertions**, citing the commit, and added a
   still-swing case so both branches stay covered.
3. **`entryTf == "H1"` — matches neither current ladder.** `resolve_timeframe_policy`
   returns `setup=H4` for intraday *and* for swing. **Replaced the hardcoded rung
   with a route-mirrors-policy assertion** rather than pinning either value.

### F4 — partially accepted; premise corrected, real gap fixed

The stated premise — that `routes_ai_chart_review.py:594` is the sole call site —
is wrong. `score_entry_quality` has a second call site at `ai_review/summary.py:100`
which does pass `ai_review`, so the text branch is reachable.

The conclusion was nonetheless right for a different reason: `summary.py` then
**overrode** that result with the stamped `entry_quality_score`, which is the
pre-provider pass computed with `ai_review=None`. The text-aware score was
computed and discarded.

Two fixes: the override is removed (the stamped value is now only a fallback), and
the route re-runs `attach_review_geometry` post-review with the model's payload so
`check_sl_inside_invalidation_thesis` actually receives `requiredConfirmation`
instead of always falling back to `ema50`.

### F5 — accepted, fixed

The parity guard is now unconditional. A review with neither a candidate nor a
resolved pair has nothing to prove the chart symbol against, which is a reject,
not a skip.

---

## Open items — not fixed, needing a decision

1. **Engine A session gate** (F2). `_with_session_gate` evaluates against the
   confirmed candle. Fixing it changes live signal generation.
2. **Timeframe ladder discrepancy.** `timeframe_policy.py` defines
   `_UNIVERSAL_SETUP = Timeframe.H1` and `CLAUDE.md` documents the universal
   ladder as `D1 regime, H4 bias, H4 structure, H1 setup, M15 trigger`. But
   `resolve_timeframe_policy` returns `setup=H4` for both styles:

   | style | regime | bias | structure | setup | trigger |
   |-------|--------|------|-----------|-------|---------|
   | intraday | H4 | H4 | H4 | **H4** | M15 |
   | swing | D1 | D1 | D1 | **H4** | H1 |

   Neither matches the documented ladder. Either the docs/template or the
   resolver is wrong. Engine A territory — reported, not changed.
3. **Pre-existing quant-scorer failure**, see Test results above.
4. **Two bundle-marker suites** (`test_frontend_aurora_bundle`,
   `test_ai_review_static_bundle`) have been failing against the committed
   bundle since before this work; confirmed identical at HEAD via a temporary
   worktree.
5. **No runtime verification.** Nothing here has been exercised against a running
   Athena process. Code and config changes are not active in an already-running
   process; a restart and fresh scan are required before claiming runtime
   behaviour changed.
