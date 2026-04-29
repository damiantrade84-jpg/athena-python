# Athena Research Lab — AI Review
**Run ID:** `run_20260429_080326_2366a3`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 08:05 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_080326_2366a3 | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds. **Do NOT copy to live gates or execute live.** All findings labeled per protocol. Samples are small (<30 penalized), single-symbol focus penalized, no multi-asset clusters. Overall edge thin; robustness low (avg 0.48). No fee collapses, but OOS often weak.

## 1. Strategy Family Performance
- **engine_b_proxy**: WEAK_CANDIDATE (best avg net_return 0.157, 4 configs, but MSFT-only; structure_filters shows promise on H4/H1).
- **mean_reversion**: WEAK_CANDIDATE (bollinger_touch helps mildly, avg net 0.027).
- **trend_momentum**: REJECT (ema_cross globally hurts; macd weak but OOS inconsistent).
- **breakout**: REJECT (session_opening_range poor OOS).
- **pullback**: REJECT (pullback_ema low WR, poor SQN).

## 2. Indicator Helped Most
**bollinger_touch** (WEAK_CANDIDATE): Highest pass_rate (16.7%), avg net -0.009 (least hurtful), avg SQN -0.08. Works on AAPL/NVDA M15 (32-45 trades).

## 3. Indicator Hurt Most
**ema_cross** (REJECT): Avg net -0.159, WR 14%, SQN -2.82, 432 configs all fail. Tiny samples on NVDA M15 dominate losses.

## 4. Asset Group
**stock** (WEAK_CANDIDATE): Only group tested (avg net 0.074, robustness 0.48). No forex/crypto data → NEEDS_MORE_DATA for cross-asset robustness.

## 5. Symbol
**MSFT** (STRONG_CANDIDATE): Best net 0.141 (4 configs, H4/H1), WR 34%, 59 avg trades. structure_filters shines (PF>1.5). AAPL/TSLA/NVDA WEAK_CANDIDATE (single-symbol penalty).

## 6. Timeframe
**H4** (WEAK_CANDIDATE): Best net 0.133, WR 40%, robustness 0.52. H1/M15 similar but lower returns/sharpe.

## 7. Session
**all** (NEEDS_MORE_DATA): Only tested. No session splits → cannot assess London/NY/Asia edge.

## 8. LONG vs SHORT
**both** (NEUTRAL/NEEDS_MORE_DATA): Only direction tested. No long/short split data.

## 9. Setups Collapsed After Fees
None (REJECT n/a): All gross-profitable setups survive fees (net_return ≈ gross in tops).

## 10. Setups with Too Little Sample (<30 trades)
Penalized 721/912 configs:
- **trend_momentum** (416): ema_cross NVDA M15 dominant.
- **engine_b_proxy** (104): structure_filters undersampled.
- **breakout** (94): session_opening_range.
- **mean_reversion** (84): bollinger_touch.
- **pullback** (23): pullback_ema.

## 11. Engine A (EMA/RSI+MACD/ADX)
- **Keep**: ADX gate (macd_direction WEAK_CANDIDATE at adx_min=0/20 survives OOS mildly on AAPL H1).
- **Remove/Demote**: EMA cross/alignment (REJECT: hurts globally, low WR/PF).
- **Tune**: RSI+MACD momentum (test rsi_confirm=False in macd; direction: raise ADX min>20 for filter). No live threshold changes.
- Not trustworthy: No forex data (Engine A forex-focused).

## 12. Engine B (Price-Action Checklist)
- **Keep**: structure_filters (STRONG_CANDIDATE on MSFT H4 strong_close_pct=0.7, PF 1.56, 57 trades).
- **Remove/Demote**: FVG detection (disabled in winners → REJECT enabled variants).
- **Tune**: strong_close_pct (0.7-0.8 band; test on non-MSFT stocks). Location/RR room strictness intact.
- Robust on H4 > H1; multi-TF cluster emerging.

## 13. Engine D (VP/OrderFlow)
**NEEDS_MORE_DATA** (TELEMETRY_BUG): Zero results. No crypto symbols/timeframes. Missing POC/VAH/VAL/CVD/VWAP/AAA data → untrustworthy.

## 14. Next Smallest Useful Test
Test engine_b_proxy structure_filters (strong_close_pct=0.7) on 4-6 more stocks (e.g., GOOGL, AMZN) + 1 forex (EURUSD) on H4/H1. 1-week run: validates MSFT cluster vs single-symbol penalty.

## 15. What Should NOT Be Tested Further Right Now
- ema_cross/ema_alignment (global REJECT, no edge).
- NVDA-only M15 trend_momentum (tiny samples, negative).
- Single-symbol mean_reversion without OOS lift.
- Anything without >30 trades/IS-OOS match.

**Overall Verdict**: Weak discovery (1.4% pass). MSFT H4 engine_b_proxy strongest signal → prioritize B tuning. Engines A/D need broader data. No live actions.

```json
{
  "overall_verdict": "WEAK_OVERALL: Thin edge on stocks (MSFT H4 engine_b_proxy). Penalize single-symbols/tiny samples. Prioritize Engine B validation.",
  "top_candidates": [
    {"strategy": "structure_filters (fvg_detection=False|strong_close_pct=0.7)", "symbol": "MSFT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "structure_filters (fvg_detection=False|strong_close_pct=0.8)", "symbol": "MSFT", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch (num_std=2.5|period=20)", "symbol": "AAPL", "tf": "M15", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "Global hurt: low WR/PF/SQN, many tiny samples"},
    {"strategy": "pullback_ema", "reason": "Low WR, poor OOS"},
    {"strategy": "session_opening_range", "reason": "Poor robustness"}
  ],
  "engine_a": {
    "keep": ["ADX gate (macd_direction)"],
    "remove_or_demote": ["EMA cross/alignment"],
    "tune": ["RSI+MACD: test rsi_confirm=False; raise ADX min>20"],
    "next_tests": ["Forex symbols (EURUSD) H1/H4 macd_direction"]
  },
  "engine_b": {
    "keep": ["structure_filters (strong_close_pct=0.7)"],
    "remove_or_demote": ["FVG detection (when enabled)"],
    "tune": ["strong_close_pct 0.7-0.8 on H4"],
    "next_tests": ["+4 stocks (GOOGL/AMZN) H4/H1"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto symbols (BTCUSDT) M15/H1 VP/OrderFlow full"]
  },
  "data_quality_warnings": ["No forex/crypto; stocks only. All eodhd — verify vs live telemetry."],
  "telemetry_warnings": ["Engine D missing entirely (TELEMETRY_BUG)"],
  "next_tiny_test": {
    "symbols": ["MSFT", "AAPL", "GOOGL", "EURUSD"],
    "timeframes": ["H4", "H1"],
    "strategy_families": ["engine_b_proxy"],
    "reason": "Validate structure_filters cluster beyond MSFT single-symbol"
  },
  "do_not_do_next": [
    "ema_cross variants",
    "NVDA M15 only",
    "Non-H4/H1 without >30 trades"
  ]
}
```