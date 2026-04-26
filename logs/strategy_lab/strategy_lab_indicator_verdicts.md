# Sentinel Pro — Engine A / B Feature Attribution Audit

**Matrix:** ENGINE_A=5,729 trades · ENGINE_B=1,802 trades
**Purpose:** Diagnostic only — no threshold or strategy changes.

---

## HEADLINE NUMBERS

| Engine | n | WR | PF | avgR |
|--------|---|----|----|------|
| ENGINE_A | 5,729 | 31.3% | 0.435 | -0.545 |
| ENGINE_B | 1,802 | 32.0% | 0.917 | -0.053 |
| EA without S&P 500 | 3,573 | 37.4% | 0.737 | -0.192 |
| EA H4 SHORT crypto | 286 | 40.9% | 1.127 | +0.080 |
| EB ob_at_zone + bos_mtf + SHORT | 45 | 51.1% | 2.176 | +0.541 |
| EB ob_at_zone + bos_mtf (any) | 92 | 41.3% | 1.380 | +0.222 |
| EB BTC/USDT SHORT | 122 | 39.3% | 1.336 | +0.193 |
| EB crypto_major score>=5 | 886 | 35.4% | 1.091 | +0.055 |

---

## ENGINE A — KEY FINDINGS

### 1. S&P 500 is destroying Engine A statistics [CRITICAL]
- S&P 500 = 2,156 of 5,729 trades (37.7%): WR=21.1% PF=0.166 avgR=-1.129
- Without S&P 500: WR=37.4% PF=0.737 avgR=-0.192
- Engine A has no useful edge on S&P 500 intraday.

### 2. H4 significantly outperforms H1
- H1: n=4,932  WR=30.2%  PF=0.385  avgR=-0.621
- H4: n=797    WR=37.6%  PF=0.896  avgR=-0.070
- H4 SHORT: n=358  WR=39.4%  PF=1.063  avgR=+0.040 (positive expectancy)
- H4 SHORT crypto: n=286  WR=40.9%  PF=1.127  avgR=+0.080 (positive expectancy)

### 3. LONG direction 3x worse than SHORT
- SHORT: n=2,397  WR=36.2%  PF=0.684  avgR=-0.242
- LONG:  n=3,332  WR=27.8%  PF=0.311  avgR=-0.762
- Same asymmetry in crypto_major (LONG -0.273 vs SHORT -0.128)

### 4. Addon factor is the strongest single factor signal
- addon neutral (~0): n=4,305  WR=28.4%  PF=0.351  avgR=-0.687
- addon positive (0.05-0.20): n=1,059  WR=41.1%  PF=0.879  avgR=-0.081 (+0.606R uplift)
- Best combo: addon>0.05 + trending + H4 + SHORT = n=42  PF=1.108  avgR=+0.072

### 5. Momentum factor is discrete and capped at 0.5 [DATA ISSUE]
- Only 5 distinct values: 0.0, 0.1, 0.2, 0.3, 0.5. Maximum observed = 0.5.
- Distribution: 0.0->164, 0.1->498, 0.2->34, 0.3->1510, 0.5->3523
- Higher is better but maximum is 0.5, not the documented 0-1.0 range.
- momentum=0.0: avgR=-0.978 | momentum=0.3: avgR=-0.637 | momentum=0.5: avgR=-0.423

### 6. Trend factor is near-binary (always strong)
- Values: -3.0, -2.6 to -2.2 range, +2.2 to +2.6 range, +3.0. No weak values.
- strong_bear (<-2): n=2,397  WR=36.2%  PF=0.684  avgR=-0.242
- strong_bull (>+2): n=3,332  WR=27.8%  PF=0.311  avgR=-0.762

### 7. Regime: trending is less bad, ranging is consistently negative
- ranging:  n=3,047  WR=28.8%  PF=0.366  avgR=-0.655
- trending: n=2,682  WR=34.0%  PF=0.527  avgR=-0.420
- Near-breakeven: score>=1.5 + trending + addon>0 = n=583  PF=0.903  avgR=-0.064

### 8. OOS slightly better than IS (no overfitting detected)
- IS: n=3,676  avgR=-0.617 | OOS: n=2,053  avgR=-0.415

---

## ENGINE B — KEY FINDINGS

### 1. OB at Zone + BOS MTF = strongest confirmed combo
- ob_at_zone + bos_mtf (any):   n=92   WR=41.3%  PF=1.380  avgR=+0.222
- ob_at_zone + bos_mtf (SHORT): n=45   WR=51.1%  PF=2.176  avgR=+0.541 (best in audit)
- ob_at_zone alone:              n=257  WR=36.6%  PF=1.144  avgR=+0.088
- bos_mtf_confirmed alone:       n=609  WR=34.8%  PF=1.093  avgR=+0.056

### 2. Score 5+ is the validated gate
- score 3: n=249  WR=27.7%  PF=0.714  avgR=-0.194
- score 4: n=601  WR=29.5%  PF=0.789  avgR=-0.144
- score 5: n=564  WR=34.9%  PF=1.095  avgR=+0.056 [breakeven line]
- score 6: n=336  WR=35.1%  PF=1.052  avgR=+0.032
- score 7: n=48   WR=29.2%  PF=0.826  avgR=-0.120 (degrading at high scores)
Current min_score=5 gate is validated by data.

### 3. ENGULFING is the only profitable trigger; STRONG_CLOSE is the worst
- ENGULFING:    n=411  WR=34.5%  PF=1.073  avgR=+0.045 (only PF>1.0)
- REJECTION:    n=410  WR=34.4%  PF=0.982  avgR=-0.011
- INSIDE_BREAK: n=133  WR=31.6%  PF=0.892  avgR=-0.069
- NONE:         n=77   WR=29.9%  PF=0.842  avgR=-0.101
- STRONG_CLOSE: n=771  WR=29.7%  PF=0.817  avgR=-0.121 (worst, most common)

### 4. Liquidity sweep is HARMFUL
- sweep absent:  n=1,692  WR=32.4%  PF=0.932  avgR=-0.043
- sweep present: n=110    WR=26.4%  PF=0.699  avgR=-0.209 (-0.166R uplift)

### 5. Zone_touched is HARMFUL
- zone absent:  n=1,000  WR=34.6%  PF=1.026  avgR=+0.016 (positive when NOT touched)
- zone present: n=802    WR=28.8%  PF=0.788  avgR=-0.140 (-0.157R uplift)

### 6. SHORT outperforms LONG
- SHORT: n=823  WR=34.0%  PF=1.028  avgR=+0.017 (positive expectancy)
- LONG:  n=979  WR=30.3%  PF=0.828  avgR=-0.113

### 7. BTC/USDT is the only consistently profitable pair
- BTC total: n=290  WR=38.6%  PF=1.175  avgR=+0.104
- BTC SHORT: n=122  WR=39.3%  PF=1.336  avgR=+0.193
- BTC LONG:  n=168  WR=38.1%  PF=1.065  avgR=+0.040
- XRP total: n=272  WR=35.3%  PF=1.036  avgR=+0.021 (marginal)
- ETH total: n=312  WR=30.1%  PF=0.898  avgR=-0.062
- SOL total: n=260  WR=29.2%  PF=0.776  avgR=-0.158
- LTC total: n=260  WR=26.9%  PF=0.729  avgR=-0.189

### 8. FVG bonus and Volume strength always ZERO [CRITICAL DATA BUG]
- fvg_bonus=0.0 and volume_strength=0.0 for ALL 1,802 EB records
- ENGINE_B_PROFILE_SCORING_ENABLED=true per CLAUDE.md but not captured in matrix
- Fix write path in backtest_pair_naked() before assessing VP bonus scoring

### 9. rr_target always 2.0 [DATA BUG]
- All 1,802 records have rr_target=2.0. Field not reading actual computed RR.

### 10. Ranging beats trending in Engine B (opposite of Engine A)
- ranging:  n=1,096  WR=33.5%  PF=0.960  avgR=-0.025
- trending: n=706    WR=29.8%  PF=0.854  avgR=-0.098

---

## CROSS-COMPARISON

Engine B beats Engine A on every dimension:

| Segment | EA avgR | EB avgR | Winner |
|---------|---------|---------|--------|
| crypto_major | -0.203 | -0.049 | B |
| ranging | -0.655 | -0.025 | B |
| trending | -0.420 | -0.098 | B |
| LONG | -0.762 | -0.113 | B |
| SHORT | -0.242 | +0.017 | B |
| BTC/USDT | -0.336 | +0.104 | B |
| LTC/USDT | -0.179 | -0.189 | A (marginal) |

Symbol recommendations:
- BTC/USDT: Engine B strongly preferred (+0.104 vs -0.336)
- XRP/USDT: Engine B preferred (+0.021 vs -0.109)
- ETH/USDT: Engine B preferred (-0.062 vs -0.103)
- BNB/USDT: Engine B preferred (-0.030 vs -0.285)
- S&P 500: Investigate / review for disable (EA avgR=-1.129)
- LTC/USDT: Both lose — review for disable
- SOL/USDT: Both lose — review for disable

---

## FEATURE VERDICTS

### ENGINE_B — best features:
| Feature | n | avgR | PF | Confidence | Verdict |
|---------|---|------|----|-----------|---------|
| ob_at_zone + bos_mtf + SHORT | 45 | +0.541 | 2.176 | MEDIUM | KEEP_STRONG_EDGE |
| ob_at_zone + bos_mtf (any) | 92 | +0.222 | 1.380 | HIGH | KEEP_STRONG_EDGE |
| ob_at_zone | 257 | +0.088 | 1.144 | HIGH | KEEP_STRONG_EDGE |
| score>=5 | 564 | +0.056 | 1.095 | HIGH | KEEP_STRONG_EDGE |
| bos_mtf_confirmed | 609 | +0.056 | 1.093 | HIGH | KEEP_WEAK_EDGE |
| ENGULFING trigger | 411 | +0.045 | 1.073 | HIGH | KEEP_WEAK_EDGE |

### ENGINE_B — worst features:
| Feature | n | avgR | PF | Confidence | Verdict |
|---------|---|------|----|-----------|---------|
| liquidity_sweep | 110 | -0.209 | 0.699 | HIGH | HARMFUL |
| zone_touched | 802 | -0.140 | 0.788 | HIGH | HARMFUL |
| STRONG_CLOSE trigger | 771 | -0.121 | 0.817 | HIGH | HARMFUL |
| bos_volume_confirmed | 976 | -0.089 | 0.862 | HIGH | NEUTRAL/borderline |

### ENGINE_A — best segments:
| Feature | n | avgR | PF | Confidence | Verdict |
|---------|---|------|----|-----------|---------|
| H4 SHORT crypto | 286 | +0.080 | 1.127 | HIGH | KEEP_STRONG_EDGE |
| H4 SHORT (any) | 358 | +0.040 | 1.063 | HIGH | KEEP_WEAK_EDGE |
| addon_positive (0.05-0.20) | 1,059 | -0.081 | 0.879 | HIGH | NEUTRAL (best uplift +0.606R) |

### ENGINE_A — worst segments:
| Feature | n | avgR | PF | Confidence | Verdict |
|---------|---|------|----|-----------|---------|
| S&P 500 | 2,156 | -1.129 | 0.166 | HIGH | HARMFUL |
| LONG direction | 3,332 | -0.762 | 0.311 | HIGH | HARMFUL |
| H1 timeframe | 4,932 | -0.621 | 0.385 | HIGH | HARMFUL |
| ranging regime | 3,047 | -0.655 | 0.366 | HIGH | HARMFUL |

---

## MISSING DATA FIELDS

### ENGINE_A — need feature snapshot re-run:
rsi, macd, ema_alignment (per TF), adx, atr_pct, bb_width, volume_ratio,
session (London/NY/Asian), carry, funding_rate, cot, hurst

### ENGINE_B — need to add to backtest_pair_naked() write path:
bos_confirmed, bos_direction, fvg_present, fvg_unmitigated, sweep_direction,
checklist_structure, checklist_location, checklist_trigger, checklist_room,
checklist_macro, checklist_rr, adx, session, d1_conflict

### Critical bugs to fix before next matrix run:
1. fvg_bonus always 0 in matrix — fix write path in backtest_pair_naked()
2. volume_strength always 0 in matrix — same
3. rr_target always 2.0 — not reading actual execution RR

---

## RECOMMENDED NEXT TESTS

### Engine A:
1. Add feature_snapshot dict to backtest_runner.py capturing RSI, ADX, EMA per-TF, session, ATR pct, volume ratio
2. Investigate S&P 500 data quality before any index conclusions
3. Run LONG vs SHORT analysis separately per asset class
4. Test H4-only filter for crypto Engine A
5. Factor-level Pearson(factor, R) on OOS data with full feature snapshot

### Engine B:
1. Fix fvg_bonus and volume_strength write paths in backtest_pair_naked()
2. Fix rr_target write path to capture actual execution RR
3. Add checklist item-level booleans to trade records
4. Test ob_at_zone + bos_mtf + SHORT as HIGH-PRIORITY watchlist filter (PF=2.18)
5. Analyse STRONG_CLOSE trigger — most common but worst performing
6. Review LTC/USDT and SOL/USDT for potential disable decision
7. Add bos_confirmed, sweep_direction, d1_conflict, session fields

---

## FILES CREATED

- logs/strategy_lab/strategy_lab_feature_availability.json
- logs/strategy_lab/strategy_lab_engine_a_feature_attribution.json
- logs/strategy_lab/strategy_lab_engine_b_feature_attribution.json
- logs/strategy_lab/strategy_lab_engine_a_feature_ranking.csv
- logs/strategy_lab/strategy_lab_engine_b_feature_ranking.csv
- logs/strategy_lab/strategy_lab_engine_a_vs_b_comparison.csv
- logs/strategy_lab/strategy_lab_feature_interactions.csv
- logs/strategy_lab/strategy_lab_indicator_verdicts.md
- strategy_lab_feature_audit.py