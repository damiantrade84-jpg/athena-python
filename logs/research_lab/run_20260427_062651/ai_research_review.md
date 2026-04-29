# Athena Research Lab — AI Review
**Run ID:** `run_20260427_062651`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 06:27 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

# Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_062651 | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveat:** These are discovery results at lowered BT_MIN thresholds. **Do NOT copy to live gates or execute live.** All findings labeled per protocol. No STRONG_CANDIDATE due to small samples (<30 penalized), single-symbol bias, OOS decay (many negative oos_return), and net returns marginal post-fees. Prefer clusters; here, weak signals only.

1. **Strategy family that worked best:** breakout (WEAK_CANDIDATE) — 11/121 passing configs, avg net_return 0.040, highest across families, clusters on AUD/USD H4 (e.g., session_opening_range, prev_day_hl). trend_momentum (WEAK_CANDIDATE) secondary (4 configs). mean_reversion (WEAK_CANDIDATE, thin). pullback (REJECT). All others REJECT due to poor robustness (<0.5 avg), OOS failure.

2. **Indicator that helped most:** bollinger_touch (WEAK_CANDIDATE) — 2/24 configs pass (8% rate), avg_sqn -1.23 (least hurtful), positive oos_return in some (e.g., GBP/USD H1: WR 61.5%, net 0.0092). HELPS verdict confirmed.

3. **Indicator that hurt most:** ema_alignment (REJECT) — 0/36 pass, avg_net_return -0.0655 worst, low WR 16.7%. pullback_ema (REJECT) close second (-0.0570). ema_cross (REJECT, 0/432 pass).

4. **Asset group that worked best:** forex (WEAK_CANDIDATE) — only group tested (17/768 valid), avg net 0.0333, but OOS -0.0194 flags caution. No cross-asset data.

5. **Symbol that worked best:** AUD/USD (WEAK_CANDIDATE) — 8/17 top configs, avg net 0.0541 > others, cluster on H4 breakout (e.g., session_opening_range PF 1.88). Penalized for symbol concentration; USD/JPY (WEAK_CANDIDATE, 3 configs) next. GBP/USD/EUR/USD single/thin (NEEDS_MORE_DATA).

6. **Timeframe that worked best:** H4 (WEAK_CANDIDATE) — 15/17 configs, avg net 0.0370, robust samples (avg 50 trades). H1/M15 (NEEDS_MORE_DATA, <30 trades, single configs).

7. **Session that worked best:** all (WEAK_CANDIDATE) — only tested, no granularity. NEEDS_MORE_DATA for session splits (e.g., London/NY).

8. **LONG or SHORT better:** both (WEAK_CANDIDATE) — only direction tested (17 configs). No long/short split; NEEDS_MORE_DATA.

9. **Setups collapsed after fees:** None (all gross-profitable candidates survive fees, e.g., top AUD/USD H4 gross/net similar). No penalization needed.

10. **Setups with too little sample:** 475/768 (<20-30 trades penalized): trend_momentum (332, e.g., ema_cross variants), mean_reversion (74, bollinger_touch thin), breakout (68, e.g., longer range_bars), pullback (1). All labeled NEEDS_MORE_DATA or REJECT.

11. **Engine A (EMA/RSI/MACD/ADX):** Keep: macd_direction (WEAK_CANDIDATE, adx_min=0 on USD/JPY/EUR H4; tune fast=12/slow=26 lower sensitivity). Remove/demote: ema_cross, pullback_ema, ema_alignment (REJECT, consistent net-negative/OOS fail). Tune: Raise ADX gate >20 (filters noise, but check OOS); test RSI_confirm=False (marginal lift). No threshold changes — validate multi-symbol first.

12. **Engine B (price-action checklist):** Keep: breakout proxies like session_opening_range/prev_day_hl (WEAK_CANDIDATE cluster on AUD/USD H4; integrate as BOS/OB location filter). Remove/demote: london_breakout/ny_breakout (REJECT, low pass). Tune: range_bars=6-12 (atr_expand_min=0-0.5 helps RR/room); add FVG/CHoCH confluence for checklist pass. No live changes.

13. **Engine D (VP/OrderFlow):** No findings (TELEMETRY_BUG — crypto-focused, absent in forex MT5 data). NEEDS_MORE_DATA on crypto symbols (POC/VAH/VAL, CVD). Keep current grades; do not tune without data.

14. **Next smallest useful test:** Multi-symbol H4 breakout cluster (AUD/USD + EUR/USD + USD/JPY + GBP/USD), add long/short split + London/NY sessions, focus session_opening_range (range_bars=6, atr_expand_min=0-0.5) + bollinger_touch filter. 4 symbols x 2 TF x 2 dir x 3 params = ~48 runs.

15. **What should NOT be tested further right now:** ema_cross/pullback_ema/ema_alignment (REJECT, 0 pass across 500+ configs, hurts SQN/net). M15 across board (tiny samples, low robustness). Single-symbol deep dives (penalize AUD/USD isolation).

## JSON Summary
```json
{
  "overall_verdict": "WEAK_SIGNALS_ONLY — breakout H4 on AUD/USD cluster shows tentative edge (PF>1.3, net>0.05), but OOS decay + small samples demand validation. No strong/live candidates.",
  "top_candidates": [
    {"strategy": "session_opening_range", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "prev_day_hl", "symbol": "AUD/USD", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "macd_direction", "symbol": "USD/JPY", "tf": "H4", "label": "WEAK_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "GBP/USD", "tf": "H1", "label": "WEAK_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "0/432 pass, avg_net -0.0494, hurts SQN"},
    {"strategy": "pullback_ema", "reason": "0/48 pass, avg_net -0.0570"},
    {"strategy": "ema_alignment", "reason": "0/36 pass, avg_net -0.0655 worst"}
  ],
  "engine_a": {
    "keep": ["macd_direction (adx_min=0)"],
    "remove_or_demote": ["ema_cross", "pullback_ema", "ema_alignment"],
    "tune": ["ADX >20 filter", "RSI_confirm=False"],
    "next_tests": ["Multi-symbol H4 macd_direction long/short"]
  },
  "engine_b": {
    "keep": ["session_opening_range (range_bars=6)", "prev_day_hl"],
    "remove_or_demote": ["london_breakout", "ny_breakout"],
    "tune": ["atr_expand_min=0-0.5", "Add FVG confluence"],
    "next_tests": ["H4 breakout + sessions (London/NY)"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Crypto symbols (BTC/ETH) VP+OrderFlow full grid"]
  },
  "data_quality_warnings": ["OOS consistently < IS (e.g., -0.0194 avg)", "Fees not killing but margins thin"],
  "telemetry_warnings": ["No Engine D/crypto data (TELEMETRY_BUG)", "MT5 forex only"],
  "next_tiny_test": {
    "symbols": ["AUD/USD", "EUR/USD", "USD/JPY", "GBP/USD"],
    "timeframes": ["H4"],
    "strategy_families": ["breakout"],
    "reason": "Validate cluster edge with sessions/long-short (48 runs)"
  },
  "do_not_do_next": [
    "ema_cross/pullback_ema variants",
    "M15 any family",
    "Single-symbol (e.g., AUD/USD only)"
  ]
}
```