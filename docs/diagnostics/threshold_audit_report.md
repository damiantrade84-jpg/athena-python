# Threshold Audit Report

Total scanned symbols: 116
Total signals produced: 24

## Signal Rate By Asset Type
| asset type | scanned | signals | signal rate |
|---|---:|---:|---:|
| commodity | 21 | 6 | 28.6% |
| crypto | 31 | 0 | 0.0% |
| forex | 21 | 5 | 23.8% |
| index | 11 | 3 | 27.3% |
| stock | 32 | 10 | 31.2% |

## Engine A Distribution
`{'min': 0.0, 'p10': 0.0, 'p25': 1.07, 'median': 1.44, 'p75': 1.9618, 'p90': 2.2896, 'max': 2.5272}`
Current threshold: 2.1
Within 5% below threshold: 4.3%
Within 10% below threshold: 6.9%
Within 15% below threshold: 8.6%

## Engine B Distribution
`{'min': 0.0, 'p10': 1.0, 'p25': 1.75, 'median': 2.75, 'p75': 3.7625, 'p90': 5.0, 'max': 6.0}`
Current threshold: 3.0
Within 5% below threshold: 0.9%
Within 10% below threshold: 12.1%
Within 15% below threshold: 15.5%

## Engine C Distribution
A_ONLY count: 91
B_ONLY count: 0
ALIGNED count: 0
CONFLICT count: 0
WATCHLIST count: 29
BLOCKED count: 87
Score distribution: `{'min': 0.0, 'p10': 0.0, 'p25': 0.214, 'median': 0.288, 'p75': 0.39235, 'p90': 0.4579, 'max': 0.5054}`

## Top Fail Reasons
### Engine A
| reason | count |
|---|---:|
| below_engine_a_threshold | 89 |
| low_confluence | 89 |
| dead_ranging | 43 |
| trend_state_dead_ranging | 43 |
| ranging | 35 |
| trend_state_developing | 20 |
| scan_watchlist | 6 |

### Engine B
| reason | count |
|---|---:|
| engine_b_confidence_passed_false | 112 |
| structural_tp_too_close | 84 |
| engine_b_entry_ok_false | 78 |
| no_trigger_pattern | 78 |
| d1_pd_array_conflict | 55 |
| engine_b_location_ok_false | 50 |
| engine_b_structure_ok_false | 40 |
| support_too_close | 33 |
| resistance_too_close | 28 |
| engine_b_macro_ok_false | 11 |

### Engine C
| reason | count |
|---|---:|
| engine_b_missing_or_failed | 91 |
| both_engines_missing_or_below_floor | 25 |

### Risk/Freshness
| reason | count |
|---|---:|
| not_evaluated_threshold_audit_report_only | 116 |
| STALE_DATA_BLOCK:H1:stale_1_bucket | 8 |
| STALE_DATA_BLOCK:D1:stale_1_bucket | 1 |
| STALE_DATA_BLOCK:H4:stale_1_bucket | 1 |

## Near Misses
| symbol | asset type | engine | score | threshold | fail reason |
|---|---|---|---:|---:|---|
| AUD/CHF | forex | Engine B | 2.5500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| EUR/GBP | forex | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| USD/CAD | forex | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, structural_tp_too_close |
| USD/SGD | forex | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, structural_tp_too_close |
| Brent Oil | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Nat Gas | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Copper | commodity | Engine B | 3.8000 | 4.0000 | engine_b_confidence_passed_false, engine_b_macro_ok_false, engine_b_structure_ok_false |
| Nickel | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Cocoa | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Cotton | commodity | Engine A | 1.7400 | 1.8000 | below_engine_a_threshold, low_confluence, ranging |
| Cotton | commodity | Engine B | 3.5500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Sugar | commodity | Engine A | 1.6300 | 1.8000 | below_engine_a_threshold, low_confluence, scan_watchlist |
| DAX 40 | index | Engine A | 1.3335 | 1.5000 | below_engine_a_threshold, low_confluence, ranging |
| DAX 40 | index | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, resistance_too_close |
| JPYX | index | Engine A | 1.4400 | 1.5000 | below_engine_a_threshold, dead_ranging, low_confluence |
| TSLA | stock | Engine B | 3.5500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| MSFT | stock | Engine A | 1.6400 | 1.8000 | below_engine_a_threshold, low_confluence, scan_watchlist |
| META | stock | Engine B | 3.5500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_location_ok_false |
| BA | stock | Engine A | 1.7176 | 1.8000 | below_engine_a_threshold, low_confluence, scan_watchlist |
| TLT | stock | Engine A | 1.6416 | 1.8000 | below_engine_a_threshold, dead_ranging, low_confluence |
| DIA | stock | Engine A | 1.5552 | 1.8000 | below_engine_a_threshold, low_confluence, scan_watchlist |
| SLV | stock | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false |
| ADA/USDT | crypto | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| DOT/USDT | crypto | Engine A | 2.3400 | 2.4000 | below_engine_a_threshold, dead_ranging, low_confluence |
| SUI/USDT | crypto | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, structural_tp_too_close |
| RENDER/USDT | crypto | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| TRX/USDT | crypto | Engine A | 2.2896 | 2.4000 | below_engine_a_threshold, dead_ranging, low_confluence |
| ICP/USDT | crypto | Engine B | 2.8000 | 3.0000 | engine_b_confidence_passed_false, engine_b_entry_ok_false, no_trigger_pattern |

## Shadow Threshold Simulation
### ENGINE_A
| setting | asset type | would pass |
|---|---|---:|
| current | commodity | 9 |
| current | forex | 5 |
| current | index | 3 |
| current | stock | 10 |
| current_minus_10pct | commodity | 11 |
| current_minus_10pct | crypto | 2 |
| current_minus_10pct | forex | 5 |
| current_minus_10pct | index | 4 |
| current_minus_10pct | stock | 13 |
| current_minus_15pct | commodity | 11 |
| current_minus_15pct | crypto | 2 |
| current_minus_15pct | forex | 5 |
| current_minus_15pct | index | 5 |
| current_minus_15pct | stock | 14 |
| current_minus_5pct | commodity | 10 |
| current_minus_5pct | crypto | 2 |
| current_minus_5pct | forex | 5 |
| current_minus_5pct | index | 4 |
| current_minus_5pct | stock | 11 |
### ENGINE_B
| setting | asset type | would pass |
|---|---|---:|
| current_minus_15pct | commodity | 9 |
| current_minus_15pct | crypto | 9 |
| current_minus_15pct | forex | 17 |
| current_minus_15pct | index | 4 |
| current_minus_15pct | stock | 16 |
| current | commodity | 3 |
| current | crypto | 5 |
| current | forex | 13 |
| current | index | 3 |
| current | stock | 13 |
| current_minus_10pct | commodity | 8 |
| current_minus_10pct | crypto | 9 |
| current_minus_10pct | forex | 16 |
| current_minus_10pct | index | 4 |
| current_minus_10pct | stock | 14 |
| current_minus_5pct | commodity | 4 |
| current_minus_5pct | crypto | 5 |
| current_minus_5pct | forex | 13 |
| current_minus_5pct | index | 3 |
| current_minus_5pct | stock | 13 |
### ENGINE_C
| setting | asset type | would pass |
|---|---|---:|

## Final Scan Result Distribution
| reason | count |
|---|---:|
| BLOCKED_RISK | 62 |
| A_ONLY | 24 |
| NO_SETUP | 22 |
| B_NEAR_MISS | 6 |
| A_NEAR_MISS | 2 |

## Recommendation
adjust specific gate - Engine B confidence/checklist is the leading bottleneck

Threshold audit mode is report-only. It does not change live, paper, freshness, or risk gates.
