# Athena Research Lab — AI Review
**Run ID:** `run_20260429_073231_9128a7`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-29 07:34 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260429_073231_9128a7 | **Analyst:** Quantitative Research | **Date:** 2026-04-29  
**Scope:** 1140 configs across 5 crypto symbols (ADA/USDT, BNB/USDT, BTC/USDT, ETH/USDT, SOL/USDT), families (trend_momentum, pullback, breakout, mean_reversion, engine_b_proxy), TFs (M15/H1/H4). All sessions='all', direction='both'. Data source: binance_rest (no telemetry bugs noted, but single exchange limits trustworthiness).  
**Global Notes:** Samples often tiny (<30 trades: penalised heavily). No gross-profitable/net-negative after fees. OOS often weaker than IS (penalised). No multi-asset clusters; crypto-only. Robustness scores low overall (avg 0.57). **Do NOT execute live or copy thresholds to live gates—further validation required.**

## 1. Strategy Family Performance
- **mean_reversion**: STRONG_CANDIDATE (20/42 valid; highest count, solid win rates ~73%, clusters on H4 bollinger_touch across SOL/ADA/ETH/BNB; avg net_return 0.2025).
- **breakout**: WEAK_CANDIDATE (8 valid; high avg net_return 0.2633 but OOS decay on BTC/ETH prev_day_hl/session_opening_range; single-symbol heavy).
- **engine_b_proxy**: WEAK_CANDIDATE (4 valid; marginal on BNB H4 structure_filters).
- **trend_momentum**: REJECT (10 valid but mostly HURTS; ema_cross/momentum wrecked by fees/drawdowns).
- **pullback**: REJECT (0 valid).

## 2. Indicator Helped Most
**bollinger_touch** (num_std=2.0-2.5|period=20): STRONG_CANDIDATE (4 STRONG_CANDIDATE tops; 56% pass_rate, clusters H4 mean_reversion across 4 symbols; avg net 0.1001, high WR 65.9%).

## 3. Indicator Hurt Most
**ema_cross** (all params): REJECT (0% pass, avg net -0.2554, destroys edge across 540 configs; tiny samples, huge drawdowns).

## 4. Asset Group
**crypto**: WEAK_CANDIDATE (only group tested; avg net 0.1786, but single-class lacks robustness—NEEDS_MORE_DATA for forex/stocks).

## 5. Symbol
**SOL/USDT**: STRONG_CANDIDATE (highest net 0.3127, WR 62.5%; bollinger_touch H4 top-ranked).  
**ETH/USDT**: WEAK_CANDIDATE (net 0.2201). Others (ADA/BNB/BTC) REJECT (OOS fail or low robustness).

## 6. Timeframe
**H4**: STRONG_CANDIDATE (24 valid, highest net 0.2551; bollinger/breakout clusters).  
**H1**: WEAK_CANDIDATE (net 0.0901). **M15**: REJECT (tiny samples, OOS negative).

## 7. Session
**all**: NEEDS_MORE_DATA (only tested; no session splits—untrustworthy without London/NY/Asia breakdown).

## 8. LONG vs SHORT
**both**: NEEDS_MORE_DATA (only direction tested; no long/short split—cannot compare).

## 9. Setups Collapsed After Fees
None: REJECT (research_report confirms "No gross-profitable-but-fee-killed setups found.").

## 10. Setups with Too Little Sample (<30 trades)
- **trend_momentum** (301): REJECT (ema_cross/momentum; e.g., SOL M15 <25 trades).
- **engine_b_proxy** (106): REJECT (ob_bos/structure_filters).
- **mean_reversion** (87): REJECT (marginals like rsi_extreme/vwap_deviation).
- **breakout** (83): REJECT (session_opening_range/prev_day_hl).

## 11. Engine A (EMA trend/RSI+MACD/ADX)
- **Keep:** macd_direction (adx_min=0|fast=12|signal=9|slow=26 on ETH H1: STRONG_CANDIDATE, WR 46.6%, PF 1.42).
- **Remove/Demote:** ema_cross (REJECT, HURTS globally); pullback_ema/ema_alignment (REJECT).
- **Tune:** ADX gate higher (>20: WEAK_CANDIDATE on ETH H1); RSI_confirm=False (penalises momentum quality). No threshold changes—validate multi-symbol OOS first. Overall family REJECT (low robustness).

## 12. Engine B (Price-Action: BOS/CHoCH/FVG/OB/Swing/Location/Trigger/RR)
- **Keep:** structure_filters (fvg_detection=False|strong_close_pct=0.8 on BNB H4: WEAK_CANDIDATE, 190 trades).
- **Remove/Demote:** ob_bos (REJECT, HURTS, 0% pass).
- **Tune:** Relax FVG/strong_close for crypto H4; add RR/room gates. Proxy family WEAK_CANDIDATE—needs live proxy telemetry.

## 13. Engine D (VP/OF Scalping: POC/VAH/VAL/Absorption/CVD/VWAP/AAA; Crypto-Focused)
- **Keep:** vwap_deviation (std_threshold=1.5 on ADA H1/ETH M15: WEAK_CANDIDATE, extreme SQN 5.7+).
- **Remove/Demote:** None specific.
- **Tune:** Lower std_threshold for scalping; integrate AAA sequence. **NEEDS_MORE_DATA** (no direct VP/POC/CVD/Absorption tested; untrustworthy without orderflow telemetry).

## 14. Next Smallest Useful Test
Test bollinger_touch (num_std=2.0-2.5|period=20) + macd_direction filters on H4 across 3+ symbols (add XRP/USDT, LINK/USDT) with session splits (London/NY). Reason: Builds on strongest cluster, checks multi-symbol robustness/OOS.

## 15. What Should NOT Be Tested Further Right Now
- ema_cross/RSI_confirm variants: REJECT (consistent HURTS, tiny samples).
- M15 trend_momentum/breakout: REJECT (insufficient trades, OOS fail).
- Single-symbol outliers (e.g., BTC prev_day_hl: OOS decay).
- NY/london_breakout: REJECT (0% pass or neutral).

**Final Call:** Promising mean_reversion H4 crypto cluster (bollinger_touch), but no robust multi-symbol/timeframe edge yet. Prioritise validation over expansion. All results backtest-only—live telemetry essential.

```json
{
  "overall_verdict": "WEAK_CANDIDATE: Mean_reversion H4 bollinger_touch shows cluster edge in crypto, but penalised for single-asset/OOS weakness. Engines need targeted validation.",
  "top_candidates": [
    {"strategy": "bollinger_touch (num_std=2.0|period=20)", "symbol": "SOL/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch (num_std=2.5|period=20)", "symbol": "ADA/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "macd_direction (adx_min=0|fast=12|signal=9|slow=26)", "symbol": "ETH/USDT", "tf": "H1", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch (num_std=2.5|period=20)", "symbol": "ETH/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "structure_filters (fvg=False|strong_close=0.8)", "symbol": "BNB/USDT", "tf": "H4", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross (all params)", "reason": "HURTS globally: negative net, high drawdowns, 0% pass_rate"},
    {"strategy": "ob_bos", "reason": "HURTS: 0% pass, poor WR/net"},
    {"strategy": "ny_breakout", "reason": "HURTS: 0% pass, negative returns"},
    {"strategy": "prev_day_hl (low ATR expand)", "reason": "OOS decay on BTC/ETH"}
  ],
  "engine_a": {
    "keep": ["macd_direction (adx_min=0)"],
    "remove_or_demote": ["ema_cross", "pullback_ema", "ema_alignment"],
    "tune": ["ADX >20 for momentum filter", "RSI_confirm=False"],
    "next_tests": ["Multi-symbol H4 macd_direction OOS validation"]
  },
  "engine_b": {
    "keep": ["structure_filters (fvg=False)"],
    "remove_or_demote": ["ob_bos"],
    "tune": ["Strong_close_pct relax for H4 crypto", "Add RR gates"],
    "next_tests": ["Session-split structure_filters on top symbols"]
  },
  "engine_d": {
    "keep": ["vwap_deviation (std=1.5)"],
    "remove_or_demote": [],
    "tune": ["Lower std_threshold; add CVD/Absorption"],
    "next_tests": ["VP/POC full backtest on M15/H1 crypto"]
  },
  "data_quality_warnings": ["Crypto-only (no forex/stocks)", "All sessions='all' (no splits)", "Many tiny samples (<30)"],
  "telemetry_warnings": ["Binance_rest only—no live orderflow/VP telemetry", "Missing IS/OOS splits in some summaries"],
  "next_tiny_test": {
    "symbols": ["SOL/USDT", "ETH/USDT", "ADA/USDT"],
    "timeframes": ["H4"],
    "strategy_families": ["mean_reversion"],
    "reason": "Validate bollinger_touch cluster robustness with session/direction splits"
  },
  "do_not_do_next": [
    "M15 trend_momentum",
    "ema_cross variants",
    "Single-symbol breakout"
  ]
}
```