# Engine A Visual Trade Review Payload Contract

Purpose: document the current Engine A payload truth source for a future Trader's-Eye vs Engine A panel. This is report-only. It does not change scoring, thresholds, Engine B, Engine D, or execution.

## Current Source Contract

`factor_scoring.compute_factor_scores()` returns Engine A v2 on a 0 to 3.0 cap. The final score is multiplicative through trend, ADX, volatility, session, DI alignment, directional ramp, VWAP, funding/carry cost, and conviction, with bounded adjustments layered afterward. Do not present it as an additive weighted-contribution model.

The direct return dict currently includes:

- Core: `final_score`, `direction`, `regime`, `factor_scores`, `weights`, `asset_type`, `score_group`, `engine_a_asset_diagnostics`
- Trend and directional diagnostics: `trend_coherence`, `directional_score`, `nondirectional_score`, `unweighted_directional_sum`, `directional_ramp_multiplier`, `effective_min_directional`, `min_directional_threshold`
- ADX and DI: `adx_value`, `adx_source`, `adx_multiplier`, plus `feed_status.di_align`
- Momentum: `momentum_quality`, `filtered_indicators`
- Addon: `addon_type`, `addon_value`, `addon_unsupported`, `research_lab_value`, `research_lab_detail`, `research_lab_score_uplift`, `feed_status.addon`
- Multipliers and adjustments: `session_multiplier`, `equity_session_multiplier`, `volatility_regime_multiplier`, `mean_reversion_value`, `mean_reversion_detail`, `intermarket_confirmation`, `intermarket_engine_a_delta`, `structure_context_adjustment`, `engine_a_correlated_overlay_guard`, `feed_status`

Source evidence:

- `factor_scoring.py:2392-2457` returns the successful `compute_factor_scores()` payload.
- `factor_scoring.py:2522-2583` returns the hard-abort zero payload.
- `scoring.py:943-1010` maps Engine A into legacy/scanner-facing fields and `factorDiagnostics`.
- `athena.py:12326-12408` builds the scanner/API signal payload with `confluenceScore`, `factorScores`, `factorDiagnostics`, risk levels, and thresholds.
- `scanner.py:2364-2408` adds scan thresholds and threshold-progress fields.
- `engine_c.py:367-380` still checks legacy separate `derivatives`, `cot_boost`, `carry`, and `carry_tilt` keys, but current Engine A factor scoring does not emit them as separate factor scores.

## Evidence Table

| Field wanted by panel | Actual source field | Available? | Notes |
|---|---|---:|---|
| `symbol` | `signal.symbol`, fallback `signal.display` or `signal.pair` | Yes in scanner signal | `compute_factor_scores()` itself does not return symbol. |
| `timeframe` | None in current Engine A result/signal | No | Style exists as `signal.style`, but this is not a chart timeframe. Adapter reports unavailable. |
| `direction` | `engine_a_result.direction`, `signal.direction` | Yes | Hard-abort result can set trade direction to null and preserve `diagnostic_direction`. |
| `final_score` | `engine_a_result.final_score`, `scoring.result.score`, `signal.confluenceScore` | Yes | Scanner may include guarded news/structure adjustments after factor scoring. |
| `max_score` | Engine A v2 contract, `signal.maxScore` in scanner | Yes | Schema uses 3.0. Signal carries `maxScore`; adapter defaults to the v2 cap if absent. |
| `threshold` | `signal.threshold`, `signal.liveThreshold`, `signal.scanThresholdEffective`, `signal.scanThreshold` | Signal only | `compute_factor_scores()` does not resolve scan threshold. |
| `trend_block.trend_score` | `factor_scores.trend`, fallback `directional_score` | Yes | This is not a sum of raw visible votes. |
| `trend_block.trend_coherence` | `trend_coherence`, `factorDiagnostics.trendCoherence` | Yes | Contains current trend-coherence diagnostics. |
| `trend_block.directional_ramp_multiplier` | `directional_ramp_multiplier`, `factorDiagnostics.directionalRampMult`, asset diagnostics | Yes | Multiplicative ramp. |
| `trend_block.min_directional_threshold` | `min_directional_threshold`, `factorDiagnostics.minDirectionalThreshold` | Yes | Diagnostic threshold only. Adapter does not change it. |
| `trend_block.effective_min_directional` | `effective_min_directional`, `factorDiagnostics.effectiveMinDirectional` | Yes | Diagnostic threshold only. |
| `momentum_block.momentum_quality` | `momentum_quality`, `factorDiagnostics.momentumQuality`, `factor_scores.momentum` | Yes | Raw momentum subcomponent scores are not exposed. |
| `momentum_block.filtered_indicators` | `filtered_indicators` | Only in direct compute result | Lost in `scoring.py`/scanner signal. |
| `adx_di_block.adx_value` | `adx_value`, `factorDiagnostics.regimeLabelsDualCapture.trendStateAdxValue` | Yes | Direct compute is best source. |
| `adx_di_block.adx_source` | `adx_source`, `factorDiagnostics.regimeLabelsDualCapture.trendStateAdxSource` | Yes | Direct compute is best source. |
| `adx_di_block.adx_multiplier` | `adx_multiplier`, `factorDiagnostics.adxMultiplier` | Yes | Multiplicative. |
| `adx_di_block.di_alignment_multiplier` | `feed_status.di_align`, `factorDiagnostics.diAlignMult` | Yes | Stored as a feed-status string in direct compute result. |
| `addon_block.addon_type` | `addon_type` | Only in direct compute result | Not preserved by scanner except indirectly through `feedStatus.addon`. |
| `addon_block.addon_value` | `addon_value`, `factor_scores.addon`, `signal.factorScores.addon` | Yes | Do not split into derivatives/carry/COT without new fields. |
| `addon_block.addon_unsupported` | `addon_unsupported` | Only in direct compute result | Adapter maps unsupported to `UNAVAILABLE`, not `NEUTRAL`. |
| `addon_block.research_lab_value` | `research_lab_value`, `factorDiagnostics.researchLabValue`, `factor_scores.research_lab` | Yes | Research value is separate from addon type, but contributes through addon path. |
| `addon_block.feed_status` | `feed_status`, `factorDiagnostics.feedStatus` | Yes | Best source for addon support/error state. |
| `multiplier_chain.session_multiplier` | `session_multiplier`, `feed_status.session` | Direct compute | Scanner preserves only feed status, not a top-level numeric. |
| `multiplier_chain.equity_session_multiplier` | `equity_session_multiplier` | Direct compute | Scanner does not preserve as a dedicated numeric field. |
| `multiplier_chain.volatility_regime_multiplier` | `volatility_regime_multiplier`, asset diagnostics | Direct compute | Scanner may preserve detail in `engineAAssetDiagnostics`. |
| `multiplier_chain.directional_ramp_multiplier` | `directional_ramp_multiplier`, `factorDiagnostics.directionalRampMult` | Yes | Multiplicative. |
| `multiplier_chain.vwap_filter` | `feed_status.vwap_filter` | Conditional | Present only when VWAP filter is enabled. Missing means unavailable, not neutral. |
| `multiplier_chain.volatility_scaler` | `feed_status.vol_scaler`, asset diagnostics | Yes | Volatility is exposed as scaler/regime diagnostics, not as a raw factor vote. |
| `adjustments.research_lab_detail` | `research_lab_detail`, `factorDiagnostics.researchLabDetail` | Yes | Direct details are retained into scanner diagnostics. |
| `adjustments.mean_reversion_value` | `mean_reversion_value`, `factor_scores.mean_reversion` | Yes | Detail dict is direct-compute only. |
| `adjustments.mean_reversion_detail` | `mean_reversion_detail` | Direct compute only | Lost in scanner signal. |
| `adjustments.intermarket_confirmation` | `intermarket_confirmation`, `signal.intermarketConfirmation` | Yes | Rich dict, if intermarket context was enabled. |
| `adjustments.intermarket_engine_a_delta` | `intermarket_engine_a_delta`, `intermarketConfirmation.engineADelta` | Yes when present | Direct scalar can be lost if only reduced confirmation is retained. |
| `adjustments.structure_context_adjustment` | `structure_context_adjustment`, `factorDiagnostics.explicitStructureContext` | Yes | Direct compute has one path; `athena.py` can apply an explicit structure pass later. |
| `adjustments.engine_a_correlated_overlay_guard` | `engine_a_correlated_overlay_guard`, `factorDiagnostics.engineACorrelatedOverlayGuard` | Yes | Guard detail only, not an execution approval. |
| `risk_block.entry` | `signal.entry`, fallback `signal.price` | Signal only | Engine A factor scoring does not produce entry. |
| `risk_block.sl` | `signal.sl` | Signal only | Produced after level calculation. |
| `risk_block.tp` | `signal.tp`, fallback `signal.tp1` | Signal only | Scanner uses `tp1`/`tp2`. |
| `risk_block.atr` | `signal.atr` | Signal only | Level ATR, not a factor-score field. |
| `risk_block.atr_timeframe` | `signal.atrDiagnostics.atr_tf` | Signal only | Current ATR diagnostics use `atr_tf`. |
| `risk_block.rr` | `signal.rr`, fallback `signal.rr1` | Signal only | Scanner uses `rr1`/`rr2`. |

## Audit Answers

1. `compute_factor_scores()` returns the direct snake_case dict listed above, with a separate `_zero_result()` dict for hard aborts.
2. Scanner/API signal preserves core score/direction, `factorScores`, `factorWeights`, `factorDiagnostics`, thresholds, price/SL/TP/ATR/RR, and some intermarket/news fields.
3. Direct fields lost or renamed after `scoring.py`/scanner include `final_score` to `score` then `confluenceScore`, `factor_scores` to `factorScores`, many diagnostics into camelCase `factorDiagnostics`, and direct-only fields such as `addon_type`, `addon_unsupported`, `mean_reversion_detail`, and `filtered_indicators`.
4. Engine A exposes `unweighted_directional_sum`, but not raw unweighted per-factor directional votes. `scoring.py` creates legacy `votes` from factor-score signs, not from raw factor internals.
5. Engine A exposes `momentum_quality` and `filtered_indicators`; it does not expose raw momentum component scores/weights as first-class fields.
6. Engine A does not expose derivatives/carry/COT as separate current `factor_scores`; it exposes `addon_type`, `addon_value`, and `feed_status`.
7. Volume is exposed mainly through `filtered_indicators.volume_ratio` and a bounded internal adjustment that is not returned as its own field. Volatility is exposed as `volatility_regime_multiplier`, `feed_status.vol_scaler`, and asset diagnostics.
8. Microstructure is currently visible through `filtered_indicators` when direct compute output is available. It is not preserved into the scanner signal.
9. Engine A exposes enough to explain the main pass path at a high level: score, trend coherence, momentum quality, addon value/type, multipliers, feed status, threshold in signal, and risk levels in signal. It does not expose enough for a true raw factor-vote panel.
10. If the panel needs true raw factor votes later, add explicit source fields for per-timeframe trend votes/weights, raw momentum subcomponent values and contributions, addon subfactor values for funding/OI/carry/COT, volume adjustment, macro/intermarket pre/post deltas, base score before multipliers, each multiplier input reason, and pre/post score stages.

## Cross-Sectional Ranking Fields (Engine A V3)

Ranking is applied after scoring by `engine_a_v3/cross_sectional.py` and stamped
onto the signal by `_attach_annotation`. It is selection metadata, not a factor.

| Field wanted by panel | Actual source field | Available? | Notes |
|---|---|---:|---|
| `ranking.enabled` / `ranking.applied` | `signal.crossSectionalRanking.enabled` / `.applied`, mirrored at `factorDiagnostics.crossSectionalRanking` | Only when the config block is enabled | Absent or `applied=false` means the unchanged absolute-threshold path. Report as `UNAVAILABLE`, never as a failed rank. |
| `ranking.group` | `crossSectionalRanking.groupKey` + `.groupBy` | Yes when applied | `groupBy` is `score_group` (default), `asset_type`, or `custom`. |
| `ranking.method` | `crossSectionalRanking.method` + `.topN` / `.percentile` | Yes when applied | `top_n` keeps `rank <= topN`; `percentile` keeps the top slice of the eligible cohort. |
| `ranking.rank` | `crossSectionalRanking.rank` / `.cutoff` / `.eligibleCount` / `.groupSize` | Yes when applied | Render as `rank / eligibleCount` within `groupKey`. Never recompute. |
| `ranking.accepted` | `crossSectionalRanking.accepted` + `.reason` | Yes when applied | Reasons: `rank_within_cutoff`, `cross_sectional_rank_below_cutoff`, `group_below_min_size`, `cross_sectional_below_min_score_floor`, plus eligibility reasons. |
| `ranking.score` | `crossSectionalRanking.rankingScore` + `.tieBreakers` | Yes when applied | The score the cohort was sorted on, with the configured tie-breakers. |

Rules for any panel or reviewer surface:

- Ranking never changes `final_score`, the four V3 components, or direction, and
  can only subtract from the promoted set — never add to it.
- A single-pair backtest is a universe of one: ranking reduces to `MIN_SCORE_FLOOR`.
- Do not re-rank client-side and do not compare against pairs not in the payload.

## Timeframe Interpretation

The reviewed chart timeframe and the policy roles are separate contracts. Roles
(`regimeTf` / `biasTf` / `structureTf` / `setupTf` / `triggerTf`, plus
`atrDiagnostics.atr_tf`) are server-supplied and authoritative; the chart
interval is presentation. A panel must not infer, correct, or override a role
from the chart, and must not flag a Daily Engine B bias rung
(`hierarchicalBias.applied=true`) as inconsistent with an H4 structure zone —
that is the designed hierarchical shape: HTF bias -> MTF confirmation -> LTF
entry.

## Adapter Contract

`ai_review.engine_a_panel_adapter.build_engine_a_visual_audit_payload(engine_a_result, signal=None)` is a report-only adapter. Field aliases live in ``FIELD_SPECS`` inside that module and should stay aligned with the evidence table above.

Rules:

- It does not import `factor_scoring`, `scoring`, `scanner`, Engine B/D, or execution modules.
- It does not calculate a raw score.
- It does not create additive weighted-contribution fields.
- It reports missing fields in `unavailable_fields`.
- It maps addon unsupported to `UNAVAILABLE`, not `NEUTRAL`.
- It records every populated field in `source_field_map`.
