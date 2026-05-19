# Master calibration evidence report

_Generated: 2026-05-19 18:08 UTC. Research-only — compares **accepted closed trades** (audit) vs **rejected scan diagnostics** (JSONL). No live tuning._

## 1. Dataset summary

| Metric | Value |
| --- | --- |
| Accepted (closed) trade count | **466** |
| Rejected / diagnostic event count | **68** |
| ENGINE_A accepted | **276** |
| ENGINE_B accepted | **190** |
| ENGINE_A rejected/diagnostic | **10** |
| ENGINE_B rejected/diagnostic | **58** |
| Accepted date range | **2026-03-30** → **2026-05-19** |
| Diagnostic date range | **n/a** (timestamps mostly null in current export) |
| Matrix groups (accepted ∪ rejected keys) | **59** |
| Groups with evidence_strength=strong (both n≥30) | **0** |
| Groups with evidence_strength=weak | **4** |
| Groups with evidence_strength=insufficient | **55** |

### Critical sample-size warning

- Rejected events meet minimum n≥30 for engine-level comparison.
- **Accepted trades** come from `audit.db` export (`sweep_input_from_audit.json`).
- **Rejected setups** come from `calibration_events.jsonl` normalization — separate pipeline, not the same population as closed trades.
- **Do not tune live thresholds** from this report alone.

### Inputs

- **accepted_trades:** `logs\calibration\sweep_input_from_audit.json`
- **baseline_analysis:** `logs\calibration\full_baseline_real\baseline_analysis.md`
- **diagnostic_events:** `logs\calibration\diagnostics_normalized\diagnostic_events_normalized.json`

## 2. Engine A evidence

### By asset_class

| asset_class | accepted_n | avg_R | win_rate | rejected_n | top_rejection | avg_dist_to_threshold | near_threshold | far_below_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (blank) | 238 | -0.0692 | 63.87% | 0 | n/a | n/a | n/a | n/a | weak |
| forex | 28 | -0.1788 | 50.00% | 6 | below_engine_a_threshold | -1.6633 | 0.00% | 100.00% structurally below | insufficient |
| crypto | 4 | -1.0800 | 0.00% | 2 | below_engine_a_threshold | -0.8000 | 0.00% | 100.00% structurally below | insufficient |
| stock | 3 | -0.5800 | 33.33% | 2 | (blank) | 0.1500 | 100.00% | 0.00% structurally below | insufficient |
| commodity | 2 | -0.1000 | 0.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| index | 1 | -0.1000 | 0.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
### By score_group

| score_group | accepted_n | avg_R | win_rate | rejected_n | top_rejection | avg_dist_to_threshold | near_threshold | far_below_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (blank) | 276 | -0.0982 | 60.51% | 10 | below_engine_a_threshold | -1.1280 | 20.00% | 80.00% structurally below | weak |
### By regime

| regime | accepted_n | avg_R | win_rate | rejected_n | top_rejection | avg_dist_to_threshold | near_threshold | far_below_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LOW_VOLATILITY | 111 | -0.0663 | 66.67% | 0 | n/a | n/a | n/a | n/a | weak |
| TRENDING | 96 | -0.1039 | 63.54% | 8 | below_engine_a_threshold | -0.8875 | 25.00% | 75.00% structurally below | weak |
| TREND_PULLBACK | 25 | -0.1460 | 48.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| RANGING | 15 | -0.1521 | 46.67% | 2 | below_engine_a_threshold | -2.0900 | 0.00% | 100.00% structurally below | insufficient |
| DEVELOPING | 14 | -0.2964 | 42.86% | 0 | n/a | n/a | n/a | n/a | insufficient |
| DEAD RANGING | 7 | -0.0614 | 42.86% | 0 | n/a | n/a | n/a | n/a | insufficient |
| LONDON_BREAKOUT | 4 | 0.1850 | 50.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| (blank) | 3 | 0.0100 | 33.33% | 0 | n/a | n/a | n/a | n/a | insufficient |
| EXPANSION | 1 | n/a | 100.00% | 0 | n/a | n/a | n/a | n/a | insufficient |

## 3. Engine B evidence

### By style

| style | accepted_n | avg_R | win_rate | rejected_n | top_gate_failure | rr_actual_vs_required | fallback_tp_rate | structural_tp_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| intraday | 104 | -0.0513 | 55.77% | 0 | n/a | n/a | n/a | n/a | weak |
| scalp | 75 | 0.0145 | 62.67% | 0 | n/a | n/a | n/a | n/a | weak |
| swing | 11 | -0.1536 | 27.27% | 0 | n/a | n/a | n/a | n/a | insufficient |

### By asset_class

| asset_class | accepted_n | avg_R | win_rate | rejected_n | top_gate_failure | rr_actual_vs_required | fallback_tp_rate | structural_tp_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (blank) | 189 | -0.0256 | 57.14% | 28 | checklist | avg actual 2.00 vs req 1.50 | 0.00% | 100.00% | weak |
| stock | 1 | -0.9900 | 0.00% | 4 | min_score | avg actual 2.00 vs req 1.00 | n/a | n/a | insufficient |

### By regime

| regime | accepted_n | avg_R | win_rate | rejected_n | top_gate_failure | rr_actual_vs_required | fallback_tp_rate | structural_tp_rate | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RANGING | 125 | -0.0663 | 56.80% | 16 | min_score | avg actual 2.00 vs req 1.50 | 0.00% | 100.00% | weak |
| TRENDING | 32 | 0.1241 | 59.38% | 38 | checklist | avg actual 2.00 vs req 1.00 | n/a | n/a | weak |
| LOW_VOLATILITY | 12 | 0.0227 | 50.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| DEAD RANGING | 6 | -0.2983 | 50.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| HH_HL | 4 | -0.1900 | 50.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| DEVELOPING | 3 | 0.4667 | 100.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| LH_LL | 3 | 0.1600 | 66.67% | 0 | n/a | n/a | n/a | n/a | insufficient |
| EXPANSION | 2 | -0.4000 | 50.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| NONE | 2 | -0.1000 | 50.00% | 0 | n/a | n/a | n/a | n/a | insufficient |
| TREND_PULLBACK | 1 | -0.1900 | 0.00% | 0 | n/a | n/a | n/a | n/a | insufficient |

### By level_mode

_Accepted trades lack `level_mode` in audit export. Rejected level_mode appears only in diagnostic JSONL (current n=58). **Insufficient** for structural vs fallback RR comparison at group level._

## 4. Cross-engine comparison

| regime | A_n | A_avg_R | B_n | B_avg_R | better_engine |
| --- | --- | --- | --- | --- | --- |
| (blank) | 3 | 0.0100 | 0 | n/a | n/a |
| DEAD RANGING | 7 | -0.0614 | 6 | -0.2983 | A better |
| DEVELOPING | 14 | -0.2964 | 3 | 0.4667 | B better |
| EXPANSION | 1 | n/a | 2 | -0.4000 | n/a |
| HH_HL | 0 | n/a | 4 | -0.1900 | n/a |
| LH_LL | 0 | n/a | 3 | 0.1600 | n/a |
| LONDON_BREAKOUT | 4 | 0.1850 | 0 | n/a | n/a |
| LOW_VOLATILITY | 111 | -0.0663 | 12 | 0.0227 | B better |
| NONE | 0 | n/a | 2 | -0.1000 | n/a |
| RANGING | 15 | -0.1521 | 125 | -0.0663 | B better |
| TRENDING | 96 | -0.1039 | 32 | 0.1241 | B better |
| TREND_PULLBACK | 25 | -0.1460 | 1 | -0.1900 | A better |

**Interpretation (accepted trades only, n≥30 regimes):**

- **LOW_VOLATILITY:** ENGINE_A n=111 avg R=-0.06630630630630632; ENGINE_B n=12 avg R=0.02272727272727272
- **TRENDING:** ENGINE_A n=96 avg R=-0.10393617021276595; ENGINE_B n=32 avg R=0.1240625
- **RANGING:** ENGINE_A n=15 avg R=-0.15214285714285714; ENGINE_B n=125 avg R=-0.06631147540983606

- **Both engines negative avg R** at overall level despite win rates >55% — weak expectancy dominates.
- **Groups where both fail (n≥30, avg R<0):** ENGINE_A intraday, scalp, swing; ENGINE_B intraday; ENGINE_A LOW_VOLATILITY/TRENDING; ENGINE_B RANGING.
- **B relatively better:** ENGINE_B TRENDING (n=32, avg R +0.124), ENGINE_B scalp (n=75, avg R +0.015).
- **Blocked-before-entry vs good closes:** *Cannot establish* — diagnostic n too small to join on regime/asset_class.
- **High pass / weak expectancy:** High win rate with negative avg R in multiple buckets (see accepted tables).

## 5. What is working against each group

See `logs/calibration/group_blocker_matrix.csv` for the full matrix. Preview (largest accepted n):

| group | engine | primary_blocker | secondary_blocker | accepted_avg_R | rejected_n | evidence | next_experiment |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ENGINE_A|(blank)|(blank)|LOW_VOLATILITY|intraday | ENGINE_A | n/a | n/a | -0.055 | 0 | weak | Outcome review: execution/exit quality and R skew; separate from gate calibratio... |
| ENGINE_A|(blank)|(blank)|TRENDING|intraday | ENGINE_A | n/a | n/a | 0.0278 | 0 | weak | Expand diagnostic + trade sample; walk-forward split before any parameter sweep.... |
| ENGINE_B|(blank)|(blank)|RANGING|scalp | ENGINE_B | n/a | n/a | -0.0458 | 0 | weak | Outcome review: execution/exit quality and R skew; separate from gate calibratio... |
| ENGINE_B|(blank)|(blank)|RANGING|intraday | ENGINE_B | n/a | n/a | -0.0731 | 0 | weak | Outcome review: execution/exit quality and R skew; separate from gate calibratio... |
| ENGINE_A|(blank)|(blank)|LOW_VOLATILITY|scalp | ENGINE_A | n/a | n/a | -0.2404 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_B|(blank)|(blank)|TRENDING|intraday | ENGINE_B | n/a | n/a | 0.0726 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_A|forex|(blank)|TREND_PULLBACK|intraday | ENGINE_A | n/a | n/a | -0.1645 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_A|(blank)|(blank)|TRENDING|swing | ENGINE_A | n/a | n/a | -0.225 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_A|(blank)|(blank)|TRENDING|scalp | ENGINE_A | n/a | n/a | -0.42 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_A|(blank)|(blank)|RANGING|intraday | ENGINE_A | n/a | n/a | -0.09 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_A|(blank)|(blank)|LOW_VOLATILITY|swing | ENGINE_A | n/a | n/a | 0.4137 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_B|(blank)|(blank)|LOW_VOLATILITY|scalp | ENGINE_B | n/a | n/a | 0.1343 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_B|(blank)|(blank)|RANGING|swing | ENGINE_B | n/a | n/a | -0.1886 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_A|(blank)|(blank)|DEAD RANGING|intraday | ENGINE_A | n/a | n/a | 0.0333 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |
| ENGINE_B|(blank)|(blank)|LOW_VOLATILITY|intraday | ENGINE_B | n/a | n/a | -0.1725 | 0 | insufficient | Enable CALIBRATION_DIAGNOSTICS and collect >=30 JSONL events for this group befo... |

## 6. Do not recommend direct live threshold changes

All rows in `group_blocker_matrix.csv` have `live_change_allowed=false`.
Recommend **controlled experiments** only: enable diagnostics collection, enrich exports, define OOS windows, then re-run sweeps on groups with n≥30 on **both** pipelines.

## 7. Recommended next experiments

1. Set `CALIBRATION_DIAGNOSTICS_ENABLED: true` and accumulate ≥30 events per major group.
2. Re-export audit trades with `asset_class`, `score_group`, `level_mode`, `fallback_tp_applied`.
3. Re-run `export_calibration_events.py` and this report builder on matched date ranges.
4. ENGINE_A: OOS sweep on score_pct / threshold distance (research harness only).
5. ENGINE_B: gate-failure cohort study (`structure_ok`, `min_score`, `rr_ok`) before fallback TP conclusions.
