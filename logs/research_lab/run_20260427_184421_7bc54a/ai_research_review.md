# Athena Research Lab — AI Review
**Run ID:** `run_20260427_184421_7bc54a`  **Model:** `grok-4-1-fast-reasoning`  **Generated:** 2026-04-27 18:46 UTC

> AI-powered analysis.
> Backtest discovery only — NOT a live execution recommendation.

---

### Athena Pro v4 Backtest Discovery Decision Memo
**Run ID:** run_20260427_184421_7bc54a | **Analyst:** Quantitative Research | **Date:** 2026-04-27  
**Key Caveats:** Backtest thresholds (BT_MIN) are lower than live; results include marginal trades. Do NOT execute live or copy thresholds to production gates (e.g., Engine A forex floor stays at 2.1). All findings penalized for small samples (<30 trades), single-symbol dependence, net-negative after fees, IS/OOS decay. No clusters span forex/equities (crypto-only data). Robustness scores low overall (avg 0.54). Data from binance_rest only—no live telemetry.

1. **Best strategy family:** mean_reversion **STRONG_CANDIDATE** (bollinger_touch cluster on H4 across SOL/ADA/ETH/BNB; high WR 0.65+, PF>1.5, multi-symbol). breakout **WEAK_CANDIDATE** (session_opening_range/prev_day_hl on BTC/ETH H4; OOS decay on BTC). All others (trend_momentum, pullback, engine_b_proxy) **REJECT** (net-negative or single-symbol).

2. **Indicator helped most:** bollinger_touch **STRONG_CANDIDATE** (4 strong/weak on H4 crypto; avg net +0.08, pass_rate 43%; multi-symbol edge).

3. **Indicator hurt most:** ema_cross **REJECT** (540 configs, all net -0.25, WR<20%, hurts trend_momentum family).

4. **Asset group worked best:** crypto **WEAK_CANDIDATE** (only group tested; avg net +0.16 but OOS weak at +0.01; no forex/equity comparison = NEEDS_MORE_DATA).

5. **Symbol worked best:** SOL/USDT **STRONG_CANDIDATE** (highest net 0.33, WR 0.61; bollinger_touch H4). BTC/USDT **REJECT** (OOS negative across setups).

6. **Timeframe worked best:** H4 **STRONG_CANDIDATE** (30/47 valid; avg net +0.22, multi-family edge). H1/M15 **REJECT** (low net, small samples).

7. **Session worked best:** all **NEEDS_MORE_DATA** (only session tested; no London/NY/Asia split).

8. **LONG or SHORT better:** both **NEEDS_MORE_DATA** (only direction tested; no long/short separation).

9. **Setups collapsed after fees:** None **REJECT** (no gross-profitable/net-negative cases; all valid are net-positive).

10. **Setups with too little sample:** trend_momentum **REJECT** (315 configs <30 trades, e.g., ema_cross M15 BTC), engine_b_proxy **REJECT** (108, e.g., ob_bos), mean_reversion **REJECT** (90, e.g., rsi_extreme), breakout **REJECT** (85, e.g., ny_breakout).

11. **Engine A (EMA trend/RSI+MACD/ADX):** Keep RSI+MACD momentum **WEAK_CANDIDATE** (macd_direction adx_min=0 H1 ETH strong, but single-symbol/OOS weak). Remove EMA coherence **REJECT** (ema_cross all rejects). Tune ADX gate higher (>20) **NEEDS_MORE_DATA** (no clear edge). Overall **REJECT** for live changes—single-symbol, no cluster.

12. **Engine B (price-action checklist):** Keep structure_filters **WEAK_CANDIDATE** (strong on ADA H4, weak BNB; high trades 190+). Remove OB/BOS **REJECT** (0 pass, net -0.28). Tune FVG detection off/strong_close_pct=0.8 **WEAK_CANDIDATE** (proxy edge). Overall **NEEDS_MORE_DATA**—test non-crypto.

13. **Engine D (VP/OrderFlow scalping):** No results **TELEMETRY_BUG** (no POC/VAH/CVD/VWAP/AAA; vwap_deviation neutral but untested). **REJECT** all until data provided—not trustworthy due to missing telemetry.

14. **Next smallest useful test:** H4 bollinger_touch (num_std=2.0-2.5, period=20) on 2-3 more crypto (e.g., XRP/LINK/DOGE) + 1 forex pair (EURUSD); add long/short split. (Tiny: 10-20 runs, checks multi-symbol cluster.)

15. **What should NOT be tested further:** ema_cross **REJECT** (universal loser), ob_bos/ny_breakout **REJECT** (0 pass), M15 anything **REJECT** (tiny samples, low net).

**Overall:** Weak crypto H4 mean-reversion edge; no broad robustness. Prioritize multi-asset validation before engine tweaks. No live actions.

```json
{
  "overall_verdict": "Narrow H4 mean-reversion edge (bollinger_touch) on crypto multi-symbols; trend_momentum/breakout weak; no Engine D data.",
  "top_candidates": [
    {"strategy": "bollinger_touch", "symbol": "SOL/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "ADA/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "bollinger_touch", "symbol": "ETH/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "structure_filters", "symbol": "ADA/USDT", "tf": "H4", "label": "STRONG_CANDIDATE"},
    {"strategy": "macd_direction", "symbol": "ETH/USDT", "tf": "H1", "label": "STRONG_CANDIDATE"}
  ],
  "rejected_setups": [
    {"strategy": "ema_cross", "reason": "All 540 configs net-negative, low WR"},
    {"strategy": "ob_bos", "reason": "0 pass rate, net -0.28"},
    {"strategy": "prev_day_hl", "reason": "OOS decay, avg net -0.13"},
    {"strategy": "ny_breakout", "reason": "0 pass, hurts performance"}
  ],
  "engine_a": {
    "keep": ["RSI+MACD momentum (macd_direction adx_min=0)"],
    "remove_or_demote": ["EMA trend coherence (ema_cross)"],
    "tune": ["ADX gate (>20)"],
    "next_tests": ["Multi-symbol H1/H4 macd_direction"]
  },
  "engine_b": {
    "keep": ["structure_filters (fvg=False, strong_close=0.8)"],
    "remove_or_demote": ["OB/BOS logic"],
    "tune": ["Strong close threshold"],
    "next_tests": ["Non-crypto structure_filters H4"]
  },
  "engine_d": {
    "keep": [],
    "remove_or_demote": [],
    "tune": [],
    "next_tests": ["Full VP/OF suite on crypto M5/H1"]
  },
  "data_quality_warnings": ["Crypto-only; no forex/equity"],
  "telemetry_warnings": ["No Engine D data; binance_rest only—no live flow"],
  "next_tiny_test": {
    "symbols": ["SOL/USDT", "ADA/USDT", "XRP/USDT", "EURUSD"],
    "timeframes": ["H4"],
    "strategy_families": ["mean_reversion"],
    "reason": "Validate bollinger_touch cluster multi-symbol"
  },
  "do_not_do_next": [
    "ema_cross variants",
    "M15 trend_momentum",
    "ob_bos/ny_breakout"
  ]
}
```