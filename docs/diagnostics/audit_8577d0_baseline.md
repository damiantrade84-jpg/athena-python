# Audit 8577d0 — Baseline Evidence Report

Run date: 2026-04-28. Tools at `tools/audit_*.py`. Audit reference:
`plans/engine-d-scoring-ai-review-audit-8577d0.md`. **Read-only — no
thresholds or formulas were changed.**

## §7.1 Engine D funnel — 748 rows from `logs/scalp_audit/engine_d_funnel.jsonl`

| Metric | Value |
|---|---|
| Gate PASS | 17 |
| NO_SETUP | 211 |
| BLOCKED | 147 |
| NOT_CALLED | 373 |

Top skip reasons (excluding NOT_CALLED):

| Reason | Count |
|---|---:|
| `rr_below_min` | 56 |
| `no_setup:no_absorption_outside_va` | 46 |
| `no_setup:no_setup:balance_at_poc` | 44 |
| `no_setup:no_absorption_at_va_extreme` | 37 |
| `fee_guard_micro_stop` | 30 |
| `no_setup:no_setup:balance_at_lvn` | 22 |
| `no_setup:no_setup:balance_inside_va` | 22 |
| `counter_trend:LONG_vs_H1_SHORT` | 19 |
| `no_setup:cvd_against_reversion:SHORT_vs_LONG` | 17 |
| `no_setup:cvd_against_reversion:LONG_vs_SHORT` | 15 |

Setup-type distribution (PASS-eligible candidates): mean_reversion 91, trend_continuation 60, trend_extension 12.
Setup-grade distribution (passes only): A=1, B=8, C=8.

**Findings vs audit:**

- **Audit §1.4 (CVD hard-veto on non-crypto via candle-proxy) — partially confirmed.** 32 combined `cvd_against_reversion` rejections.
- **Audit §1.5 (forex VP proximity → rr_below_min) — confirmed.** `rr_below_min` is the single largest skip reason at 56×.
- **Audit §1.7 (trend-continuation SL too tight → fee_guard_micro_stop absorbs the symptom) — confirmed.** 30× fee_guard_micro_stop.
- **Audit §1.2 (mean-reversion bias from balance default) — confirmed.** mean_reversion (91) >> trend_extension (12).
- **Audit §1.3 (AAA never completes off MT5 → grade ceiling) — confirmed.** Only 1 grade-A pass in 748 rows.
- **New finding (cosmetic bug):** Doubled `no_setup:no_setup:` prefix observed 88 times across `balance_at_poc/at_lvn/inside_va` reasons. Caused by `f"no_setup:{setup.get('reason')}"` wrapping a reason that already starts with `no_setup:` from `_classify_setup`'s catch-all return.

## §7.2 AI review Engine D blindness — 50 rows from `logs/ai_review/ai_review_audit.jsonl`

| Field | Overall % None |
|---|---:|
| `engine_d_state` | **100.00%** |
| `engine_c_state` | 100.00% |
| `engine_b_state` | 62.00% |
| `engine_a_state` | 70.00% |
| `ai_changed_execution_permission=true` | 0.00% |
| `parse_success` | 100.00% |
| `schema_valid` | 80.00% |

Per `review_type`: `marcus_reid` 10 (engine_d=None 10/10), `engine_b_ai` 36 (engine_d=None 36/36, ai_review_state REJECT 31/36), `chart_vision` 4 (engine_d=None 4/4).

**Findings vs audit:**

- **Audit §4.1 — confirmed exactly.** 100% of AI reviews see `engine_d_state=None`.
- **Bonus finding — `engine_c_state=None` 100%.** Even Engine C consensus output is not propagated into AI prompts. Audit §4.8 partially anticipated this for `disagreement_diagnosis` but the field gap is broader.
- 0% of AI reviews changed execution permission — every review is advisory in practice. Useful to remember when sizing the §4.5 agreement-score concern.

## §7.3 Score-scale split — 116 funnel rows + 748 funnel-D rows

| Engine | Raw range | Median raw | Implied 0-1 norm | Median norm |
|---|---|---|---|---|
| Engine A (threshold current) | 1.05–2.40 | 1.80 | (raw/3) 0.35–0.80 | 0.60 |
| Engine B raw | 0.00–6.75 | 2.65 | (raw/max ≈ 6) 0.00–1.00 | 0.40 |
| Engine C final_conviction | 0.00–0.91 | 0.29 | already 0-1 | 0.29 |
| Engine D setup_score | 55–85 | 75.0 | (raw/100) 0.55–0.85 | 0.75 |

`final_scan_result` distribution: BLOCKED_RISK 61, NO_SETUP 33, A_ONLY 12, ALIGNED 4, B_NEAR_MISS 3, A_NEAR_MISS 3.

**Findings vs audit:**

- **Audit §1.9 / §4.4 — confirmed.** Engine A passes are clustered near `0.60` normalised, Engine D passes near `0.75` normalised. Both engines emit `confluenceScore` but the raw values (1.8 vs 75) differ by orders of magnitude. The debate prompt's heuristic at `signal_debate.py:36-43` cannot reliably tell them apart on raw value alone.

## §7.4 Factor diagnostics addon — 116 rows

| Asset class | n | `addon_unsupported=true` rows | directionalScore median | trendCoherence median |
|---|---:|---:|---:|---:|
| crypto | 31 | 0 (0.00%) | -2.03 | 0.50 |
| forex_or_metal | 25 | 0 (0.00%) | -2.03 | 0.80 |
| stock_or_index | 60 | 0 (0.00%) | +2.35 | 1.00 |

**Findings vs audit:**

- **Observability gap — confirmed.** `signal_funnel.jsonl` only stores `directionalScore`, `nondirectionalScore`, `trendCoherence` from `factor_diagnostics`; the `addon_unsupported` boolean (which exists at `factor_scoring.py:737`) is **not** propagated into the funnel row. So `addon_unsupported=true` shows 0% empirically not because it never happens, but because the field is dropped. The audit's §4.6 concern (AI cannot tell whether addon was unsupported vs neutral) is therefore validated structurally.
- Stock/index pairs show much stronger directional confluence (median +2.35 vs forex/crypto -2.03) and higher coherence (1.00 vs 0.80/0.50). Audit §2.3 prediction (single H4 RSI/MACD reading drives 80% of conviction when addon is "unsupported") is consistent with this skew.

## §7.5 BTC bias multiplier asymmetry

| Field | Effective value |
|---|---|
| Source | `scoring.py` defaults (`BTC_BIAS_MULTIPLIERS` not in `config.yaml`) |
| Penalty (BTC opposes alt direction) | 0.85 → −15.00% |
| Boost (BTC aligns with alt direction) | 1.10 → +10.00% |
| Asymmetry (boost − (1−penalty)) | **−5.00 pp** |
| EV multiplier on 50/50 aligned/opposed split | 0.975 |
| Funnel rows carrying `btc_bias_applied`/`btc_bias_delta` | 0 / 116 |

**Findings vs audit:**

- **Audit §2.2 — partially refuted, partially confirmed.** The audit asserts the asymmetry is wrong-direction (boost > penalty cut). Empirically it is **already in the audit-recommended direction** (penalty −15% > boost +10%). The audit's other points stand: the boost still applies to *every* aligned alt regardless of correlation strength, and the asymmetry is small (5 pp).
- `btc_bias_applied` / `btc_bias_delta` exist on the in-memory `factor_result` dict (`scoring.py:497-505`) but are not propagated into `signal_funnel.jsonl`. Per-pair frequency of penalty vs boost application is therefore not measurable from current logs — would need a tracing add-on to confirm.

## What this evidence supports next

1. **Cosmetic fix candidate** (no behavioural change): doubled `no_setup:no_setup:` prefix at `scalp_engine.py:2402` — observed 88×, easy to fix with a 2-line guard. Already proposed in earlier diagnostic-dump plan; safe to action.
2. **Real audit findings backed by data** worth weighing for any future tuning: §1.4 CVD-proxy hard veto (32×), §1.5 forex VP proximity → rr_below_min (56×), §1.7 fee-guard masking structural SL issues (30×), §1.9/§4.4 score-scale split (medians 0.60 vs 0.75 normalised but raw 1.8 vs 75), §4.1 Engine D AI blindness (100%).
3. **Audit findings that observability cannot currently confirm**: §2.2 BTC bias per-application count, §2.3 addon weight redistribution effect (signal_funnel.jsonl drops the `addon_unsupported` flag), §4.6 AI prompt addon handling. Adding these fields to existing logs is a small change (one `dict.update`) but **was not done** as part of this run, per the audit's own read-only directive.

## Follow-up fixes (applied after baseline)

The two data-confirmed Engine D bugs were fixed in-place in `scalp_engine.py` on 2026-04-28 with regression tests in `tests/test_scalp_audit_8577d0_fixes.py`:

- **Doubled `no_setup:no_setup:` prefix** — `_classify_setup` catch-all at line 1499 now returns the bare classification (`"{market_state}_{location}"`); the caller at line 2402 prepends `no_setup:` exactly once. Eliminates the 88× observed malformed reasons going forward. Covered by `test_classify_setup_catchall_reason_does_not_have_double_prefix` and `test_classify_setup_catchall_reason_imbalance_also_not_prefixed`.
- **Engine D scale (§1.9 / §4.4)** — scalp signal now emits `confluenceScore = float(quality["score"])` (0-100 raw) with explicit `maxScore = 100.0` at lines 2601-2602. `signal_debate._signal_max_score` already respected an explicit `maxScore`, so "X/Y" ratios now read naturally and Engine D can no longer be silently confused with Engine A's 0-3 scale. Downstream consumers (`risk_engine._signal_quality_factor`, `auto_trader._current_combined_conviction`, `telegram_notify`) use the `score/maxScore` ratio, which is preserved (75/100 = 0.75/1.0). Covered by `test_scalp_signal_max_score_matches_engine_a_scale`, `test_signal_debate_picks_explicit_max_score_for_scalp`, `test_signal_debate_legacy_scalp_signal_still_handled`.

The audit's other Engine D "Improvement input" lines were already addressed in the preceding commit `2a7b3bf "fix(engine): apply audit corrections with safe config-gated controls"`:
- §1.4 CVD-proxy advisory (both `outside_va` and `at_va_extreme` branches)
- §1.5 ATR-based VP proximity (`VP_PROXIMITY_USE_ATR=True` default)
- §1.7 ATR-based trend-continuation SL fallback (`TREND_CONT_SL_ATR_MULT=1.5` default)
- §1.10 ATR-scaled SL buffer (`BUFFER_USE_ATR=True` default)
- §1.11 Symmetric MT5 absorption count guard via `_has_meaningful_absorption()`

Remaining audit items are design-level (§1.1, §1.2, §1.6, §1.8, §1.12) or purely tunable (§1.3 AAA contraction threshold), with no pure-bug component backed by current evidence.
