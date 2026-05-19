# Rejected calibration changes

_All rejected items remain research-only. No live config or execution change was applied._

## Rejected Threshold Promotions

| Change | Evidence | Why rejected |
| --- | --- | --- |
| Engine A score-group threshold change | `logs/calibration/sweeps/engine_a_thresholds/calibration_sweep.csv`; `logs/calibration/diagnostics_normalized/diagnostic_events_summary.csv` | Engine A diagnostics n=10, below n>=30; best tested R still negative |
| Engine A ADX trend/hard-fail changes | `logs/calibration/sweeps/engine_a_adx/calibration_sweep.csv` | input-json rows do not re-simulate ADX gate effects |
| Engine A volatility scaler changes | `logs/calibration/sweeps/engine_a_volatility/calibration_sweep.csv` | input-json rows do not re-simulate volatility factor effects |
| Engine B min_score change | `logs/calibration/sweeps/engine_b_min_score/calibration_sweep.csv` | best tested R remains negative and admitted trade count drops by 102 |
| Engine B min_rr change | `logs/calibration/sweeps/engine_b_rr/calibration_sweep.csv` | closed-trade export lacks per-trade RR gate metadata for accepted trades |
| Engine B min_room_atr change | `logs/calibration/sweeps/engine_b_room/calibration_sweep.csv` | closed-trade export lacks room/level metadata |
| Engine B regime multiplier change | `logs/calibration/sweeps/engine_b_regime_multiplier/calibration_sweep.csv` | input-json rows do not re-simulate regime gate effects |
| Engine B fallback TP toggle | `logs/calibration/sweeps/engine_b_fallback_tp/calibration_sweep.csv` | accepted export has `engine_b_fallback_tp_applied` populated for 0/466 rows |

## Rejected Evidence Patterns

| Pattern | Why rejected |
| --- | --- |
| Raw PnL winners | Policy requires robust metrics, sample-size checks, and diagnostics |
| Small buckets | Rows with sample-size warnings are not promotion evidence |
| Test pass/fail output | Tests are safety evidence only, not trading evidence |
| Cross-engine routing | Engine A and Engine B remain separate; routing belongs to Engine C and is out of scope |
| Overlay merge into `config.yaml` | User explicitly required proposed overlay only |

## Revisit Criteria

Revisit only after:

- Engine A diagnostics reach at least n>=30 for the target group.
- Accepted exports include real `score_group` where needed.
- Engine B accepted exports include real `level_mode`, `rr_required`, `rr_passed`, and `fallback_tp_applied`.
- A held-out/OOS sweep confirms expectancy or risk improvement without trade-count collapse.

---
---

## DI alignment opposed multiplier (0.3) — audit 2026-05-19

**Status:** REJECTED (insufficient outcome evidence)

**Hypothesis:** `di_align_mult=0.3` (DI opposed to EMA trend by >5 pts) over-penalizes
forex during momentum transitions; majority of sub-2.1 scores show opposed DI.

**Scan-time diagnostic evidence (same-day cohort audit):**
- Forex pairs scored: 21
- Below threshold 2.1: 21
- Sub-threshold with di_align=0.3: 16
- DI buckets (all): {'0.3': 16, '0.5': 3, '1.0': 2}

**Feed sanity:** H4 `plusDI`/`minusDI` from `calc_indicators_with_normalized` matches
`feed_status['di_align']` for all scored pairs (no indicator feed bug detected).

**Git history:** Opposed=0.3 since `3d2c3eed` (2026-05-15); not a May-18 regression.
Prior opposed=0.0 since `a80898f5` (2026-04-30).

**Reject reason:** n < 30 closed-trade outcomes per di_align bucket.
Scan-time funnel confirms bottleneck; outcome-linked calibration required before change.

**Future proposal (if evidence supports):** config-gated `ENGINE_A_DI_ALIGN_OPPOSED_MULT`
default 0.3; optional forex-only neutral band widening. `live_change_allowed=false` until n≥30.
