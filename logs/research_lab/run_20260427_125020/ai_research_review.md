# Athena Research Lab — AI Review
**Run ID:** `run_20260427_125020`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 12:54 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_125020 | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds. **Do NOT execute live, copy thresholds to live gates, or deploy without forward validation.** All findings labeled per protocol. Robustness penalized for <30 trades, single-symbol, IS/OOS decay, fee drag. Focus on clusters across symbols/TFs.

## 1. Strategy Family Performance
- **breakout** (e.g., session_opening_range, prev_day_hl): WEAK_CANDIDATE. Highest avg net_return (0.272), but mostly BTC/ETH H4 single-symbol, OOS decay (e.g., BTC oos_return -0.123), small clusters.
- **mean_reversion** (e.g., bollinger_touch): STRONG_CANDIDATE. Top ranks (SOL/ADA/ETH H4), high WR (71%), multi-symbol cluster (3+ symbols), fee-surviving, robustness >0.7.
- **engine_b_proxy** (structure_filters): WEAK_CANDIDATE. Marginal net positive on ADA/SOL/BNB H4, high trade count (192+), but low expectancy (0.001), OOS weak.
- **trend_momentum** (ema_cross, macd_direction): REJECT. Consistent loser (avg net -0.223), tiny samples on M15, no OOS survival.
- **pullback** (pullback_ema): REJECT. Low WR (16%), no strong clusters.

## 2. Indicator Helped Most
**bollinger_touch** (num_std=2.0-2.5, period=20): STRONG_CANDIDATE. 4 strong/9 weak passes (43% pass_rate), avg net 0.079, multi-symbol H4 cluster (SOL/ADA/ETH/BNB), high WR 64%. Clear mean-reversion edge in crypto H4.

## 3. Indicator Hurt Most
**ema_cross** (all params): REJECT. 0 passes, avg net -0.223, WR 19%, 10k+ losing trades across params. Hurts trend_momentum family universally; penalize tiny M15 samples (<30).

## 4. Asset Group
**crypto**: STRONG_CANDIDATE. Only group tested (1140 rows), avg net 0.166, robustness 0.52. Multi-symbol spread shows edge potential vs. none.

## 5. Symbol
**SOL/USDT**: STRONG_CANDIDATE. Top net 0.330 (5 configs), high WR 62%, H4 mean_reversion cluster. **BTC/USDT**: WEAK_CANDIDATE (net 0.164, but OOS -0.042 decay). Others (ETH/ADA/BNB) WEAK_CANDIDATE or worse; penalize single-symbol reliance.

## 6. Timeframe
**H4**: STRONG_CANDIDATE. 33/48 valid, avg net 0.212, WR 48%, multi-family (mean_reversion/breakout). **H1**: WEAK_CANDIDATE (net 0.074, ETH/BNB only). **M15**: NEEDS_MORE_DATA (tiny samples <30, trend_momentum only).

## 7. Session
**all**: NEUTRAL (only tested). No session splits; NEEDS_MORE_DATA for London/NY/Asia to check robustness.

## 8. LONG vs SHORT
**both**: WEAK_CANDIDATE (only direction tested, avg net 0.166). No long/short split; NEEDS_MORE_DATA to isolate direction bias (crypto volatility may favor both).

## 9. Setups Collapsed After Fees
None. No gross-profitable but net-negative cases (all valid 48 survive fees). **TELEMETRY_BUG** if fees understated—verify binance_rest data.

## 10. Setups with Too Little Sample (<30 trades)
Penalized 590 configs:
- **trend_momentum** (311): ema_cross M15/BTC (16-29 trades) — REJECT.
- **engine_b_proxy** (107): structure_filters fringes — NEEDS_MORE_DATA.
- **mean_reversion** (89): bollinger_touch edges — NEEDS_MORE_DATA.
- **breakout** (83): session_opening_range variants — NEEDS_MORE_DATA.
Tiny samples untrustworthy; missing data on low-liq periods?

## 11. Engine A (EMA/RSI+MACD/ADX)
- **Keep:** MACD momentum (adx_min=0, H1 ETH): WEAK_CANDIDATE (PF 1.49, robustness 0.64). Tune ADX gate upward (20+ hurts less).
- **Remove/Demote:** EMA cross (all slow=50/100/200): REJECT (PF<0.5, net -0.09). RSI_confirm adds no edge.
- **Tune:** Increase ADX_min from 0 (filters noise); test EMA coherence D1/H4/H1 alignment >2.1 floor. No live threshold changes—validate OOS first.
- Not trustworthy: M15 ema_cross (tiny samples, telemetry suspect).

## 12. Engine B (Price-Action Checklist: BOS/CHoCH/FVG/OB/Swing)
- **Keep:** structure_filters (fvg=False, strong_close=0.8): WEAK_CANDIDATE (high volume 192+ trades, net 0.10-0.24 H4 multi-symbol).
- **Remove/Demote:** ob_bos: REJECT (net -0.27, WR 27%). FVG detection hurts.
- **Tune:** Strong_close_pct >0.8 (location filter); add RR room gate. Proxy shows checklist edge but strict pass/fail too conservative—loosen for crypto H4.
- Cluster on ADA/SOL/BNB H4 promising.

## 13. Engine D (VP/OF: POC/VAH/VAL/Absorption/CVD/VWAP/Fabio AAA)
- **Keep:** None strong; **vwap_deviation**: NEEDS_MORE_DATA (neutral, 281 trades, no verdict).
- **Remove/Demote:** None explicit; crypto focus aligns but no VP/POC/CVD results—**TELEMETRY_BUG** (missing OrderFlow data?).
- **Tune:** Grade thresholds (A/B/C/D) for H4 crypto; test Absorption+CVD at POC/VAH. Sparse data untrustworthy.

## 14. Next Smallest Useful Test
Expand top cluster: **H4 mean_reversion bollinger_touch (num_std=2.0-2.5)** on 10+ crypto symbols (add LINK/DOGE/ATOM), add London/NY sessions, long/short split. 2-3x symbols, 1 TF/family—quick robustness check.

## 15. What Should NOT Be Tested Further Right Now
- **ema_cross** (Engine A trend_momentum): REJECT—universal loser, no param saves it.
- **M15 trend_momentum**: REJECT—tiny samples, no edge.
- **ob_bos/ny_breakout/london_breakout**: REJECT—consistent hurt, low WR.
- Single-symbol outliers without OOS (e.g., BTC breakout alone).
Prioritize clusters over one-offs.

**Overall:** Mean-reversion H4 crypto (bollinger_touch) is the robust cluster—prioritize validation. Trend families weak; Engine B proxy viable. No live actions.

```json
{
  "overall_verdict": "H4 mean_reversion (bollinger_touch) emerges as strongest cluster across SOL/ADA/ETH; breakout WEAK but OOS risky. Engine A demote EMA cross; B tune structure; D needs data.",
  "top_candidates": [
    {"strategy": "bollinger_touch", "symbol": "SOL/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "ADA/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "ETH/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "rsi_extreme", "symbol": "BNB/USDT", "tf": "H1", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "BNB/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "universal net loss, low WR, hurts Engine A"},
    {"strategy": "ob_bos", "reason": "net -0.27, hurts Engine B proxy"},
    {"strategy": "ny_breakout", "reason": "consistent hurt, low pass_rate"},
    {"strategy": "london_breakout", "reason": "consistent hurt, low pass_rate"}
  ],
  "engine_a": {
    "keep": ["macd_direction (adx_min=0, ETH H1)"],
    "remove_or_demote": ["ema_cross (all params)"],
    "tune": ["ADX_min upward from 0; EMA coherence >2.1"],
    "next_tests": ["H4 macd_direction multi-symbol OOS"]
  },
  "engine_b": {
    "keep": ["structure_filters (fvg=False, strong_close=0.8)"],
    "remove_or_demote": ["ob_bos"],
    "tune": ["strong_close_pct >0.8; add RR gate"],
    "next_tests": ["H4 structure multi-symbol, session splits"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": ["Grade A/B/C/D for H4 crypto POC/VAH"],
    "next_tests": ["vwap_deviation + CVD/Absorption full backtest"]
  },
  "data_quality_warnings": ["590 tiny samples (<30 trades); penalize M15"],
  "telemetry_warnings": ["Missing Engine D VP/OF details; vwap nan verdict; verify binance_rest fees"],
  "next_tiny_test": {
    "symbols": ["SOL/USDT", "ADA/USDT", "ETH/USDT", "LINK/USDT", "DOGE/USDT"],
    "timeframes": ["H4"],
    "strategy_families": ["mean_reversion"],
    "reason": "Validate bollinger_touch cluster with +5 symbols, sessions, long/short"
  },
  "do_not_do_next": [
    "ema_cross variants",
    "M15 trend_momentum",
    "single-symbol breakout (BTC only)"
  ]
}
```