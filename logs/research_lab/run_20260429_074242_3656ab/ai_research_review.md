# Athena Research Lab — AI Review
**Run ID:** `run_20260429_074242_3656ab`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:44 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

### Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_074242_3656ab | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds (worse than live). **Do NOT execute live, copy thresholds to live gates, or deploy without multi-symbol OOS validation + forward testing.** All findings labeled per protocol. Robustness penalized for <30 trades, single-symbol, net-negative, IS/OOS decay. No telemetry bugs detected (all binance_rest data complete). Prefer clusters over outliers.

#### 1. Strategy Family Performance
- **pullback**: STRONG_CANDIDATE (highest avg net_return 0.8162 across 11 configs; clusters on BNB/ETH D1 with >50 trades, good robustness >0.77; survives fees/OOS).
- **trend_momentum**: WEAK_CANDIDATE (avg net 0.6164; some ema_alignment/macd wins on BNB/SOL D1, but ema_cross mostly fails; single-symbol heavy).
- **mean_reversion**: WEAK_CANDIDATE (bollinger_touch cluster on H4 crypto; high WR 0.73 but lower net 0.3361; small samples <40 trades).
- **engine_b_proxy**: REJECT (structure_filters weak on SOL D1; low pass_rate 0.20, hurts net).
- **breakout**: REJECT (session_opening_range weak; poor OOS, single-symbol SOL).

#### 2. Indicator Helped Most
**bollinger_touch**: STRONG_CANDIDATE (50% pass_rate; 4 STRONG/6 WEAK; high WR 0.68 avg, positive SQN 0.30; H4 clusters on SOL/ETH/ADA; robust edge in mean_reversion).

#### 3. Indicator Hurt Most
**ob_bos**: REJECT (1.25% pass_rate; avg net -0.36, SQN -1.49; globally destructive in engine_b_proxy).

#### 4. Asset Group
**crypto**: WEAK_CANDIDATE (only group tested; avg net 0.5451, robustness 0.5283; no forex/data for comparison — NEEDS_MORE_DATA for cross-asset).

#### 5. Symbol
**BNB/USDT**: STRONG_CANDIDATE (top net 0.8206; 17 configs, pullback_ema/macd_direction clusters >50 trades; multi-family edge).
- **SOL/USDT**: WEAK_CANDIDATE (net 0.5792; high expectancy 0.084 but single-symbol penalty).
- Others (BTC/ETH/ADA): REJECT (weaker net/OOS; <30 trade avg on some).

#### 6. Timeframe
**D1**: STRONG_CANDIDATE (net 0.6556; 63 configs, pullback/trend_momentum clusters; better OOS survival).
- **H4**: WEAK_CANDIDATE (higher WR 0.547 but net 0.2551; bollinger_touch only; small samples).

#### 7. Session
**all**: NEEDS_MORE_DATA (only session tested; no granularity — lacks session-specific splits like London/NY).

#### 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (only direction tested; no long/short split — cannot assess bias).

#### 9. Setups Collapsed After Fees
None. All gross-profitable candidates survive fees (net_return >= gross in tops).

#### 10. Setups with Too Little Sample (<30 trades)
- **trend_momentum** (254 configs): REJECT (ema_cross heavy; e.g., SOL H4 <30).
- **engine_b_proxy** (77): REJECT (structure_filters).
- **mean_reversion** (62): REJECT (bollinger_touch edges but penalize).
- **breakout** (20): REJECT.
- **pullback** (3): NEEDS_MORE_DATA.

#### 11. Engine A (3-factor: EMA coherence, RSI+MACD momentum, ADX)
- **Keep**: macd_direction (STRONG_CANDIDATE on BNB D1; adx_min=20 gate adds edge).
- **Remove/Demote**: ema_cross (REJECT; 80% fail, hurts net -0.15 avg; low WR 0.22).
- **Tune**: EMA alignment (WEAK; test ema_short=20/mid=50/long=200 cluster upward from 0.49 robustness; ADX min=0-20 insensitive — raise to 25+; RSI_confirm hurts in crosses).
- No live threshold changes. **Not trustworthy**: Single-symbol (BNB-heavy).

#### 12. Engine B (Price-action: BOS/CHoCH, FVG, OB, swings, location, trigger, RR)
- **Keep**: structure_filters FVG (WEAK_CANDIDATE SOL D1 fvg_detection=True; strong_close_pct=0.7).
- **Remove/Demote**: ob_bos (REJECT; globally hurts).
- **Tune**: Add bollinger_touch proxy as location filter (high WR synergy); strict checklist passes low — loosen FVG/strong_close for D1 crypto.
- **Not trustworthy**: Proxy only (no full Engine B); tiny samples.

#### 13. Engine D (VP/OrderFlow: POC/VAH/VAL, Absorb, CVD, VWAP, AAA; Crypto scalping)
- **Keep/Remove/Tune**: NEEDS_MORE_DATA (zero configs; no VP/OrderFlow in results — missing telemetry?).
- **Not trustworthy**: Absent from discovery (crypto-focused but untested here).

#### 14. Next Smallest Useful Test
Tiny mode: Add 2 forex majors (EURUSD, GBPUSD) on D1/H4; families=['pullback', 'mean_reversion']; split sessions (London/NY); long/short separate. Reason: Validate BNB pullback cluster cross-asset; check direction bias.

#### 15. What Should NOT Be Tested Further Right Now
- ema_cross (trend_momentum): REJECT (consistent loser, <30 trades rampant).
- ob_bos (engine_b_proxy): REJECT (destructive).
- H4-only breakout: REJECT (poor net/OOS).
- Single-symbol SOL/ADA deep dives: Penalize until multi-symbol.

**Overall:** Weak edges in pullback/bollinger D1 crypto (BNB). No live actions. Prioritize multi-symbol OOS.

```json
{
  "overall_verdict": "Pullback family (D1 BNB/USDT) shows strongest cluster; bollinger_touch helps mean_reversion H4. Trend_momentum mixed, others reject. Crypto-only — needs forex. No Engine D data.",
  "top_candidates": [
    {"strategy": "pullback_ema", "symbol": "BNB/USDT", "tf": "D1", "label": "STRONG_CANDIDATE"},
    {"strategy": "pullback_ema", "symbol": "ETH/USDT", "tf": "D1", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "SOL/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "macd_direction", "symbol": "BNB/USDT", "tf": "D1", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ob_bos", "reason": "Lowest pass_rate 1.25%, net -0.36, SQN -1.49"},
    {"strategy": "ema_cross", "reason": "80% reject, avg net -0.15, tiny samples"},
    {"strategy": "breakout", "reason": "Poor OOS, single-symbol SOL"}
  ],
  "engine_a": {
    "keep": ["macd_direction (adx_min=20)"],
    "remove_or_demote": ["ema_cross"],
    "tune": ["ema_alignment (raise ADX>20, test RSI_confirm=False)"],
    "next_tests": ["Multi-symbol D1 macd/pullback"]
  },
  "engine_b": {
    "keep": ["structure_filters (FVG + strong_close=0.7)"],
    "remove_or_demote": ["ob_bos"],
    "tune": ["Integrate bollinger_touch as location proxy; loosen FVG for D1"],
    "next_tests": ["Full checklist on D1 crypto sessions"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Add VP/OrderFlow scalping on crypto H4"]
  },
  "data_quality_warnings": ["Crypto-only; no forex comparison"],
  "telemetry_warnings": ["No Engine D results — potential missing data"],
  "next_tiny_test": {
    "symbols": ["EURUSD", "GBPUSD", "BNB/USDT"],
    "timeframes": ["D1", "H4"],
    "strategy_families": ["pullback", "mean_reversion"],
    "reason": "Cross-asset validation of pullback/bollinger; add session/direction splits"
  },
  "do_not_do_next": [
    "ema_cross variants",
    "ob_bos",
    "SOL-only H4 breakout"
  ]
}
```