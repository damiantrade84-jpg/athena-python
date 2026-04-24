# Threshold Audit Report

Total scanned symbols: 116
Total signals produced: 16

## Signal Rate By Asset Type
| asset type | scanned | signals | signal rate |
|---|---:|---:|---:|
| commodity | 21 | 6 | 28.6% |
| crypto | 31 | 0 | 0.0% |
| forex | 21 | 7 | 33.3% |
| index | 11 | 3 | 27.3% |
| stock | 32 | 0 | 0.0% |

## Engine A Distribution
`{'min': 0.0, 'p10': 0.0, 'p25': 1.07, 'median': 1.44, 'p75': 2.0328, 'p90': 2.34, 'max': 2.7}`
Current threshold: 2.1
Within 5% below threshold: 2.6%
Within 10% below threshold: 6.0%
Within 15% below threshold: 9.5%

## Engine B Distribution
`{'min': 0.0, 'p10': 0.75, 'p25': 1.0, 'median': 2.65, 'p75': 3.75, 'p90': 5.0, 'max': 6.75}`
Current threshold: 3.0
Within 5% below threshold: 0.0%
Within 10% below threshold: 8.6%
Within 15% below threshold: 11.2%

## Engine C Distribution
A_ONLY count: 87
B_ONLY count: 0
ALIGNED count: 7
CONFLICT count: 0
WATCHLIST count: 26
BLOCKED count: 83
Score distribution: `{'min': 0.0, 'p10': 0.0, 'p25': 0.2155, 'median': 0.288, 'p75': 0.414, 'p90': 0.468, 'max': 0.912}`

## Top Fail Reasons
### Engine A
| reason | count |
|---|---:|
| below_engine_a_threshold | 87 |
| low_confluence | 87 |
| dead_ranging | 42 |
| trend_state_dead_ranging | 42 |
| ranging | 36 |
| closed_exchange | 32 |
| trend_state_developing | 20 |
| scan_watchlist | 18 |

### Engine B
| reason | count |
|---|---:|
| engine_b_confidence_passed_false | 109 |
| structural_tp_too_close | 87 |
| engine_b_entry_ok_false | 78 |
| no_trigger_pattern | 78 |
| engine_b_location_ok_false | 58 |
| d1_pd_array_conflict | 57 |
| support_too_close | 41 |
| engine_b_structure_ok_false | 38 |
| resistance_too_close | 29 |
| engine_b_macro_ok_false | 11 |

## Engine B Checklist Funnel
Total scanned: 116
### Structural Verdict Counts
| reason | count |
|---|---:|
| CLEAR | 116 |

### Raw Score Distribution
`{'min': 0.0, 'p10': 0.75, 'p25': 1.0, 'median': 2.65, 'p75': 3.75, 'p90': 5.0, 'max': 6.75}`
### Confidence Passed Counts
| reason | count |
|---|---:|
| False | 109 |
| True | 7 |

### Checklist Fail Counts
| reason | count |
|---|---:|
| engine_b_confidence_passed_false | 109 |
| structural_tp_too_close | 87 |
| engine_b_entry_ok_false | 78 |
| no_trigger_pattern | 78 |
| engine_b_location_ok_false | 58 |
| d1_pd_array_conflict | 57 |
| support_too_close | 41 |
| engine_b_structure_ok_false | 38 |
| resistance_too_close | 29 |
| engine_b_macro_ok_false | 11 |
| bos_without_volume | 10 |
| sequence_counter_trend | 7 |
| forex_adx_below_min | 1 |

### Top 20 Closest To Passing Engine B
| symbol | asset | score | threshold | passed | top fail reasons |
|---|---|---:|---:|---|---|
| USD/BRL | forex | 6.75 | 3 | True | bos_without_volume, d1_pd_array_conflict, structural_tp_too_close |
| GBP/JPY | forex | 6 | 3 | True | bos_without_volume, resistance_too_close, structural_tp_too_close |
| AUD/NZD | forex | 5 | 3 | True |  |
| AUD/JPY | forex | 5 | 3 | True | bos_without_volume, resistance_too_close, structural_tp_too_close |
| EUR/CHF | forex | 5 | 3 | False | bos_without_volume, engine_b_confidence_passed_false, structural_tp_too_close |
| NASDAQ-100 | index | 6 | 4 | True | bos_without_volume |
| S&P 500 | index | 6 | 4 | True | bos_without_volume |
| PYPL | stock | 6 | 4 | True | resistance_too_close, structural_tp_too_close |
| EUR/USD | forex | 4.8 | 3 | False | bos_without_volume, engine_b_confidence_passed_false, resistance_too_close, structural_tp_too_close |
| AMZN | stock | 5.8 | 4 | False | engine_b_confidence_passed_false, engine_b_location_ok_false |
| SPY | stock | 5.8 | 4 | False | engine_b_confidence_passed_false, engine_b_location_ok_false |
| EUR/JPY | forex | 4.75 | 3 | False | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false, no_trigger_pattern |
| Gasoline | commodity | 5.55 | 4 | False | bos_without_volume, d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_location_ok_false |
| Brent Oil | commodity | 5 | 4 | False | engine_b_confidence_passed_false, structural_tp_too_close |
| NVDA | stock | 5 | 4 | False | engine_b_confidence_passed_false, engine_b_entry_ok_false, engine_b_location_ok_false, no_trigger_pattern |
| IWM | stock | 5 | 4 | False | engine_b_confidence_passed_false, engine_b_location_ok_false |
| USO | stock | 5 | 4 | False | engine_b_confidence_passed_false, engine_b_entry_ok_false, no_trigger_pattern, structural_tp_too_close |
| USD/CHF | forex | 3.75 | 3 | False | bos_without_volume, d1_pd_array_conflict, engine_b_confidence_passed_false, structural_tp_too_close |
| Nickel | commodity | 4.75 | 4 | False | bos_without_volume, d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_location_ok_false |
| Wheat | commodity | 4.75 | 4 | False | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_location_ok_false, structural_tp_too_close |

### Top 20 structural_tp_too_close Diagnostics
| symbol | asset | dir | entry | SL | TP | dist TP | dist SL | RR | ATR | ATR to TP | target side ok | TP source | fail reason |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| AUD/CHF | forex | LONG | 0.56124 | 0.554657 | 0.574407 | 0.0131665 | 0.00658326 | 2 | 0.00121326 | 10.8521 | True | not logged | structural_tp_too_close |
| EUR/USD | forex | LONG | 1.17191 | 1.147 | 1.17452 | 0.00260969 | 0.0249142 | 0.1047 | 0.00235612 | 1.1076 | True | not logged | structural_tp_too_close |
| GBP/USD | forex | LONG | 1.35208 | 1.33506 | 1.36344 | 0.0113568 | 0.017016 | 0.6674 | 0.00300598 | 3.7781 | True | not logged | structural_tp_too_close |
| AUD/USD | forex | LONG | 0.71514 | 0.709134 | 0.717098 | 0.00195752 | 0.00600567 | 0.3259 | 0.00230567 | 0.849 | True | not logged | structural_tp_too_close |
| EUR/GBP | forex | LONG | 0.86673 | 0.865128 | 0.86782 | 0.00109009 | 0.00160195 | 0.6805 | 0.00106796 | 1.0207 | True | not logged | structural_tp_too_close |
| USD/CAD | forex | SHORT | 1.36738 | 1.38982 | 1.35871 | 0.00867333 | 0.022443 | 0.3865 | 0.00205303 | 4.2246 | True | not logged | structural_tp_too_close |
| EUR/JPY | forex | LONG | 186.747 | 186.021 | 187.346 | 0.598675 | 0.725875 | 0.8248 | 0.272875 | 2.194 | True | not logged | structural_tp_too_close |
| GBP/JPY | forex | LONG | 215.463 | 213.628 | 219.133 | 3.66994 | 1.83497 | 2 | 0.375968 | 9.7613 | True | not logged | structural_tp_too_close |
| USD/CHF | forex | SHORT | 0.78491 | 0.795484 | 0.78357 | 0.00134026 | 0.0105735 | 0.1268 | 0.00206352 | 0.6495 | True | not logged | structural_tp_too_close |
| AUD/JPY | forex | LONG | 113.939 | 112.766 | 116.286 | 2.34663 | 1.17332 | 2 | 0.275316 | 8.5234 | True | not logged | structural_tp_too_close |
| USD/MXN | forex | SHORT | 17.4038 | 17.4876 | 17.3739 | 0.0299332 | 0.0837821 | 0.3573 | 0.0502547 | 0.5956 | True | not logged | structural_tp_too_close |
| USD/SGD | forex | SHORT | 1.27588 | 1.28168 | 1.27434 | 0.00154324 | 0.00580205 | 0.266 | 0.0019947 | 0.7737 | True | not logged | structural_tp_too_close |
| USD/BRL | forex | SHORT | 5.03577 | 5.06944 | 4.96843 | 0.0673391 | 0.0336696 | 2 | 0.0233396 | 2.8852 | True | not logged | structural_tp_too_close |
| USD/ZAR | forex | SHORT | 16.5236 | 16.7557 | 16.3806 | 0.143078 | 0.232031 | 0.6166 | 0.0956606 | 1.4957 | True | not logged | structural_tp_too_close |
| EUR/CHF | forex | SHORT | 0.91982 | 0.924984 | 0.918836 | 0.00098414 | 0.00516448 | 0.1906 | 0.00138448 | 0.7108 | True | not logged | structural_tp_too_close |
| USD/INR | forex | LONG | 94.409 | 92.2993 | 95.4192 | 1.01021 | 2.10968 | 0.4788 | 0.213117 | 4.7402 | True | not logged | structural_tp_too_close |
| XAG/USD | commodity | SHORT | 75.797 | 78.9936 | 73.8344 | 1.96264 | 3.19662 | 0.614 | 1.33374 | 1.4715 | True | not logged | structural_tp_too_close |
| WTI Oil | commodity | LONG | 98.097 | 91.6795 | 100.757 | 2.6602 | 6.41748 | 0.4145 | 2.61632 | 1.0168 | True | not logged | structural_tp_too_close |
| Brent Oil | commodity | LONG | 106.767 | 98.4055 | 112.976 | 6.20944 | 8.36154 | 0.7426 | 2.50902 | 2.4748 | True | not logged | structural_tp_too_close |
| Copper | commodity | LONG | 6.0881 | 5.99663 | 6.52524 | 0.437145 | 0.0914706 | 4.7791 | 0.0445706 | 9.8079 | True | not logged | structural_tp_too_close |

### Top 20 no_trigger_pattern Diagnostics
| symbol | asset | verdict | BOS/CHoCH | OB | FVG | sweep | breaker | zone retest | rejection | displacement | tf | latest candle | missing condition | classification |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AUD/CHF | forex | CLEAR | False | True | True | False | False | False | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| NZD/USD | forex | CLEAR | False | True | True | False | False | True | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| GBP/USD | forex | CLEAR | False | False | False | False | False | False | False | True | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| USD/JPY | forex | CLEAR | False | False | True | False | False | False | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| EUR/GBP | forex | CLEAR | False | True | False | False | False | True | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| EUR/JPY | forex | CLEAR | False | True | True | False | False | True | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| GBP/AUD | forex | CLEAR | False | True | True | False | False | True | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| USD/MXN | forex | CLEAR | False | False | True | False | False | False | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| USD/ZAR | forex | CLEAR | False | False | True | False | True | False | False | True | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| USD/INR | forex | CLEAR | False | False | True | False | False | False | False | False | H4 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Nat Gas | commodity | CLEAR | False | True | True | False | False | False | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| WTI Oil | commodity | CLEAR | False | False | True | False | False | False | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Copper | commodity | CLEAR | False | False | True | False | False | True | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Lead | commodity | CLEAR | False | True | False | False | False | True | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Aluminium | commodity | CLEAR | False | False | False | False | False | True | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Zinc | commodity | CLEAR | False | True | False | False | False | False | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Cattle | commodity | CLEAR | False | False | True | False | False | False | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Cocoa | commodity | CLEAR | False | False | True | False | False | False | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Coffee | commodity | CLEAR | False | True | True | False | False | True | False | False | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |
| Corn | commodity | CLEAR | False | True | True | False | False | False | False | True | H1 | not logged | no_price_action_trigger, entry_ok_false | not logged |

### Top 20 d1_pd_array_conflict Diagnostics
| symbol | asset | dir | current price | conflict type | range | distance | ATR distance | side | entry/TP side | H4/H1 |
|---|---|---|---:|---|---|---:|---:|---|---|---|
| AUD/CHF | forex | LONG | 0.56124 | bearish_FVG | {'bottom': 0.56139, 'top': 0.56873} | 0.00382 | 0.929117 | above_price | tp_side | {'h1_sequence': 'HH_HL', 'h4_sequence': 'CONTRACTION'} |
| NZD/USD | forex | SHORT | 0.58792 | bullish_FVG | {'bottom': 0.57332, 'top': 0.58099} | 0.010765 | 1.82723 | below_price | tp_side | {'h1_sequence': 'HH_HL', 'h4_sequence': 'LH_LL'} |
| GBP/USD | forex | LONG | 1.35208 | bearish_FVG | {'bottom': 1.35824, 'top': 1.36247} | 0.008275 | 0.953184 | above_price | tp_side | {'h1_sequence': 'HH_HL', 'h4_sequence': 'LH_LL'} |
| EUR/GBP | forex | LONG | 0.86673 | bearish_OB | {'bottom': 0.86993, 'top': 0.8709} | 0.003685 | 1.46396 | above_price | tp_side | {'h1_sequence': 'EXPANSION', 'h4_sequence': 'LH_LL'} |
| EUR/GBP | forex | LONG | 0.86673 | bearish_FVG | {'bottom': 0.86787, 'top': 0.86927} | 0.00184 | 0.730988 | above_price | tp_side | {'h1_sequence': 'EXPANSION', 'h4_sequence': 'LH_LL'} |
| USD/CAD | forex | SHORT | 1.36738 | bullish_FVG | {'bottom': 1.3605, 'top': 1.36531} | 0.004475 | 0.792236 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'EXPANSION'} |
| EUR/JPY | forex | LONG | 186.747 | bearish_FVG | {'bottom': 186.961, 'top': 186.988} | 0.2275 | 0.263899 | above_price | tp_side | {'h1_sequence': 'HH_HL', 'h4_sequence': 'CONTRACTION'} |
| USD/CHF | forex | SHORT | 0.78491 | bullish_FVG | {'bottom': 0.78263, 'top': 0.7832} | 0.001995 | 0.338997 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'LH_LL'} |
| USD/SGD | forex | SHORT | 1.27588 | bullish_FVG | {'bottom': 1.26599, 'top': 1.27128} | 0.007245 | 1.31471 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'HH_HL'} |
| USD/BRL | forex | SHORT | 5.03577 | bullish_FVG | {'bottom': 4.95453, 'top': 4.97484} | 0.071085 | 1.70963 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'LH_LL'} |
| USD/ZAR | forex | SHORT | 16.5236 | bullish_FVG | {'bottom': 15.9987, 'top': 16.28471} | 0.381925 | 1.77378 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'EXPANSION'} |
| Nat Gas | commodity | SHORT | 2.536 | bullish_FVG | {'bottom': 2.391, 'top': 2.578} | 0.0515 | 0.536059 | below_price | tp_side | {'h1_sequence': 'EXPANSION', 'h4_sequence': 'HH_HL'} |
| WTI Oil | commodity | LONG | 98.097 | bearish_FVG | {'bottom': 102.9, 'top': 109.736} | 8.221 | 1.04323 | above_price | tp_side | {'h1_sequence': 'HH_HL', 'h4_sequence': 'EXPANSION'} |
| Nickel | commodity | LONG | 19123.8 | bearish_FVG | {'bottom': 19679.53, 'top': 20344.78} | 888.375 | 1.99515 | above_price | tp_side | {'h1_sequence': 'EXPANSION', 'h4_sequence': 'CONTRACTION'} |
| XPT/USD | commodity | SHORT | 2008.64 | bullish_OB | {'bottom': 1958.63, 'top': 1972.66} | 42.995 | 0.528273 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'LH_LL'} |
| XPT/USD | commodity | SHORT | 2008.64 | bullish_FVG | {'bottom': 1703.21, 'top': 1834.65} | 239.71 | 2.94528 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'LH_LL'} |
| Gasoline | commodity | LONG | 3.3658 | bearish_FVG | {'bottom': 3.4749, 'top': 3.7294} | 0.23635 | 1.46243 | above_price | tp_side | {'h1_sequence': 'HH_HL', 'h4_sequence': 'EXPANSION'} |
| Cattle | commodity | LONG | 2.42535 | bearish_FVG | {'bottom': 2.4705500000000002, 'top': 2.48292} | 0.051385 | 1.72367 | above_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'EXPANSION'} |
| Cattle | commodity | LONG | 2.42535 | bearish_FVG | {'bottom': 2.42434, 'top': 2.44125} | 0.007445 | 0.249736 | above_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'EXPANSION'} |
| XPD/USD | commodity | SHORT | 1494.14 | bullish_FVG | {'bottom': 1271.17, 'top': 1404.23} | 156.44 | 2.39621 | below_price | tp_side | {'h1_sequence': 'LH_LL', 'h4_sequence': 'EXPANSION'} |

### Engine B Shadow Behaviour Simulation
| simulation | count |
|---|---:|
| structure valid but no trigger -> WATCHLIST_ENTRY_PENDING | 51 |
| structural TP too close -> next valid structural target exists | 43 |
| D1 conflict beyond 0.75 ATR -> watchlist candidate | 57 |
| all report-only simulations -> added execution signals | 0 |

### Recommended First Fix
adjust specific gate - Engine B confidence/checklist is the leading bottleneck
### Engine C
| reason | count |
|---|---:|
| engine_b_missing_or_failed | 87 |
| both_engines_missing_or_below_floor | 22 |
| Trade-ready | 4 |
| Score 1.4/2.1; Dead ranging regime | 1 |
| Score 1.46/2.1; Dead ranging regime | 1 |
| Score 1.07/1.8; Ranging regime; Exchange closed | 1 |

### Risk/Freshness
| reason | count |
|---|---:|
| not_evaluated_threshold_audit_report_only | 116 |
| STALE_DATA_BLOCK:H4:stale_1_bucket | 57 |

## Near Misses
| symbol | asset type | engine | score | threshold | fail reason |
|---|---|---|---:|---:|---|
| AUD/CHF | forex | Engine B | 2.5500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| EUR/GBP | forex | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| USD/CAD | forex | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, structural_tp_too_close |
| USD/SGD | forex | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, structural_tp_too_close |
| Nat Gas | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| XPT/USD | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_location_ok_false |
| Cocoa | commodity | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Cotton | commodity | Engine A | 1.7400 | 1.8000 | below_engine_a_threshold, low_confluence, ranging |
| Cotton | commodity | Engine B | 3.5500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| Sugar | commodity | Engine A | 1.6300 | 1.8000 | below_engine_a_threshold, low_confluence, scan_watchlist |
| DAX 40 | index | Engine A | 1.3335 | 1.5000 | below_engine_a_threshold, low_confluence, ranging |
| DAX 40 | index | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, resistance_too_close |
| JPYX | index | Engine A | 1.4400 | 1.5000 | below_engine_a_threshold, dead_ranging, low_confluence |
| MSFT | stock | Engine A | 1.6400 | 1.8000 | below_engine_a_threshold, closed_exchange, low_confluence |
| META | stock | Engine B | 3.5500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_location_ok_false |
| XOM | stock | Engine B | 3.7500 | 4.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| BA | stock | Engine A | 1.7176 | 1.8000 | below_engine_a_threshold, closed_exchange, low_confluence |
| DIA | stock | Engine A | 1.5552 | 1.8000 | below_engine_a_threshold, closed_exchange, low_confluence |
| TLT | stock | Engine A | 1.6416 | 1.8000 | below_engine_a_threshold, closed_exchange, dead_ranging |
| XRP/USDT | crypto | Engine B | 2.7500 | 3.0000 | d1_pd_array_conflict, engine_b_confidence_passed_false, engine_b_entry_ok_false |
| ETH/USDT | crypto | Engine A | 2.1244 | 2.4000 | below_engine_a_threshold, low_confluence, ranging |
| DOT/USDT | crypto | Engine A | 2.2300 | 2.4000 | below_engine_a_threshold, dead_ranging, low_confluence |
| TRX/USDT | crypto | Engine A | 2.1200 | 2.4000 | below_engine_a_threshold, low_confluence, ranging |
| ICP/USDT | crypto | Engine B | 2.8000 | 3.0000 | engine_b_confidence_passed_false, engine_b_entry_ok_false, no_trigger_pattern |

## Shadow Threshold Simulation
### ENGINE_A
| setting | asset type | would pass |
|---|---|---:|
| current | commodity | 9 |
| current | forex | 7 |
| current | index | 3 |
| current | stock | 10 |
| current_minus_10pct | commodity | 11 |
| current_minus_10pct | crypto | 1 |
| current_minus_10pct | forex | 7 |
| current_minus_10pct | index | 4 |
| current_minus_10pct | stock | 13 |
| current_minus_15pct | commodity | 11 |
| current_minus_15pct | crypto | 3 |
| current_minus_15pct | forex | 7 |
| current_minus_15pct | index | 5 |
| current_minus_15pct | stock | 14 |
| current_minus_5pct | commodity | 10 |
| current_minus_5pct | forex | 7 |
| current_minus_5pct | index | 4 |
| current_minus_5pct | stock | 11 |
### ENGINE_B
| setting | asset type | would pass |
|---|---|---:|
| current_minus_15pct | commodity | 9 |
| current_minus_15pct | crypto | 3 |
| current_minus_15pct | forex | 16 |
| current_minus_15pct | index | 4 |
| current_minus_15pct | stock | 14 |
| current | commodity | 5 |
| current | crypto | 1 |
| current | forex | 12 |
| current | index | 3 |
| current | stock | 12 |
| current_minus_10pct | commodity | 8 |
| current_minus_10pct | crypto | 3 |
| current_minus_10pct | forex | 15 |
| current_minus_10pct | index | 4 |
| current_minus_10pct | stock | 13 |
| current_minus_5pct | commodity | 5 |
| current_minus_5pct | crypto | 1 |
| current_minus_5pct | forex | 12 |
| current_minus_5pct | index | 3 |
| current_minus_5pct | stock | 12 |
### ENGINE_C
| setting | asset type | would pass |
|---|---|---:|
| current | forex | 3 |
| current | index | 2 |
| current | stock | 1 |
| current_minus_10pct | forex | 4 |
| current_minus_10pct | index | 2 |
| current_minus_10pct | stock | 1 |
| current_minus_5pct | forex | 4 |
| current_minus_5pct | index | 2 |
| current_minus_5pct | stock | 1 |

## Final Scan Result Distribution
| reason | count |
|---|---:|
| BLOCKED_RISK | 61 |
| NO_SETUP | 33 |
| A_ONLY | 12 |
| ALIGNED | 4 |
| B_NEAR_MISS | 3 |
| A_NEAR_MISS | 3 |

## Recommendation
adjust specific gate - Engine B confidence/checklist is the leading bottleneck

Threshold audit mode is report-only. It does not change live, paper, freshness, or risk gates.
