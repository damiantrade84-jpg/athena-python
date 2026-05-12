# Engine A Crypto Scoring — Source-Validated Recommendations

**Date:** 2026-05-10
**Scope:** Validate the original audit's correct claims against external research and current Athena code
**Status:** Research complete; recommendations below

---

## 1. ADX Thresholds (hard_fail=10, trend_min=15)

### Current Athena State
- `FACTOR_ADX_HARD_FAIL_CLASS.crypto: 10`
- `ADX_TREND_MIN_CLASS.crypto: 15`
- Linear ramp: 0.0 at ADX=10 → 1.0 at ADX=15
- Source preference: D1 ADX first, H4 fallback (`factor_scoring.py:866-962`)

### Research Findings
- **Wilder (1978) original standard:** ADX > 25 = trending, ADX < 20 = non-trending/range-bound
- **Investopedia / Tradeciety / LiberatedStockTrader:** All confirm ADX < 20 is universally considered "weak or no trend"
- **MindMathMoney:** "These thresholds can vary by market — cryptocurrencies might need different thresholds" but provides no specific crypto values
- **Gate.io crypto guide (2025):** Documents ADX climbing from 9.5→15.6 as "strong trend formation" — suggesting even practitioners treat 15 as the low end of trending

### Assessment
The linear ramp 10→15 means ADX=12.5 gets 0.5× multiplier and ADX=15 gets full 1.0×. This is **aggressive** — it grants full trend-strength credit at ADX=15, well below the traditional "trending" threshold of 20-25.

However, crypto's 24/7 nature and higher baseline volatility mean ADX readings are structurally higher than equities. The linear ramp (vs the old 3-tier step) already mitigates the worst-case: ADX=11 gets only 0.2×, not the old 0.65×.

### Recommendation: P2 — Monitor, don't change yet
- The linear ramp is a significant improvement over the old step function
- If backtests continue showing negative expectancy, **raise trend_min to 18** as a targeted experiment
- Do NOT raise hard_fail above 10 — that would block valid developing trends
- **Source:** Wilder (1978), Investopedia, Tradeciety

---

## 2. RSI Bounds (80/20 for crypto)

### Current Athena State
- `RSI_BOUNDS.crypto: { ob: 80, os: 20 }` (`config.yaml:261-263`)
- Used in `_momentum_quality()` for conviction sizing (`factor_scoring.py:445-565`)

### Research Findings
- **Wundertrading (2025):** "Adjust your overbought/oversold thresholds to 80/20 instead of the standard 70/30 to filter out minor fluctuations" — directly recommends 80/20 for crypto day trading
- **Changelly (2025):** "Widen your thresholds — for example, treat 80/20 as overbought/oversold instead of 70/30 — and confirm with other indicators"
- **Reddit r/Daytrading (2025):** Backtest of 5,000 trades: "The default 70/30 sucks" for crypto; recommends testing 80/20, 85/15, 90/10
- **Flicker Finance:** Confirms RSI > 70 is common in crypto trends and doesn't reliably signal reversal

### Assessment
**Athena's 80/20 is well-supported by practitioner research.** This is one of the most consistently recommended crypto-specific adjustments across multiple independent sources.

### Recommendation: ✅ No change needed
- Current 80/20 bounds are validated
- The graded RSI scoring (above/below 50 midline, not just extreme zones) in `_momentum_quality()` adds further nuance beyond simple OB/OS thresholds
- **Source:** Wundertrading, Changelly, Reddit backtest community

---

## 3. VWAP — Missing Indicator

### Current Athena State
- **No VWAP in Engine A** (`factor_scoring.py`)
- VWAP exists in `scalp_engine.py` (Engine D) and `market_structure.py` (Engine B structure), but not wired into factor scoring

### Research Findings
- **ArXiv 2502.13722 (2024):** "VWAP strategies have become a cornerstone for executing high-volume trades" in crypto. Study uses Binance perpetual futures (BTC, ETH, ADA) — directly relevant to Athena's Bybit perp setup. "VWAP is widely regarded for its fair and neutral calibration, making it an industry standard for comparing performance across market participants."
- **Hyrotrader / KuCoin / TradingView:** All confirm VWAP acts as dynamic support/resistance in crypto intraday trading
- **TradingView (CoinTelegraph):** "Algorithmic trading has become a go-to" — VWAP is a primary benchmark

### Assessment
VWAP is the **single highest-impact missing indicator**. It's an institutional benchmark that provides:
1. Dynamic support/resistance levels independent of fixed EMAs
2. A fair-value anchor — price above/below VWAP indicates bullish/bearish intraday bias
3. Mean-reversion signals when price deviates significantly from VWAP

Since Athena already has VWAP calculation code in `scalp_engine.py`, the implementation cost is low.

### Recommendation: P1 — Add VWAP as a direction quality filter
- **Implementation:** Add daily/session VWAP to `indicators.py`, pipe into `factor_scoring.py` as a confirmation multiplier
- **Logic:** If LONG and price > VWAP → small bonus; if LONG and price < VWAP → small penalty (institutional flow is against you)
- **Scope:** Start with crypto only; H4 candles with daily-anchored VWAP
- **Expected impact:** Moderate improvement in signal quality by filtering out counter-flow entries
- **Source:** ArXiv 2502.13722, Hyrotrader, KuCoin

---

## 4. Stochastic RSI — Missing Indicator

### Current Athena State
- **No Stochastic RSI in Engine A**
- Stochastic cross exists in research lab (`factor_scoring.py:693-783`) but is `PAPER_TOOL_ONLY: true` and uses raw Stochastic, not StochRSI

### Research Findings
- **Wundertrading (2025):** StochRSI "offers heightened sensitivity and earlier warnings, perfect for traders who need to catch reversals quickly." But: "Its responsiveness comes at the cost of more potential false signals, requiring additional confirmation."
- **Investopedia:** StochRSI applies stochastic formula to RSI values; oscillates 0-1 with 0.8/0.2 as OB/OS thresholds
- **Bitsgap / Altrady:** Confirm StochRSI is useful for crypto but warn about false signals in strong trends

### Assessment
StochRSI is a **double-edged sword** for a system like Engine A:
- **Pro:** Would catch momentum shifts earlier than RSI(14) alone
- **Con:** Would increase false signals in trending markets — exactly where Engine A should be taking trades
- Engine A already has divergence detection in `_momentum_quality()` which serves a similar purpose (catching momentum exhaustion)

### Recommendation: P2 — Low priority, experimental only
- The existing divergence detection + graded RSI scoring already provides momentum-shift awareness
- If added, should be config-gated and backtest-compared against baseline
- Higher value in ranging/choppy regimes where Engine A already underperforms
- **Source:** Wundertrading, Investopedia, Bitsgap

---

## 5. Regime Detection Gap

### Current Athena State
- `detect_regime()` returns: TRENDING, RANGING, DEAD RANGING, DEVELOPING, HIGH_VOLATILITY
- Regime smoothing via `_get_smoothed_regime()` (3-bar confirmation)
- Used as:
  - **Binary gate:** Blocks DEAD RANGING + DEVELOPING in backtest (`ENGINE_A_BLOCKED_TREND_STATES`)
  - **Conviction floor:** `CONVICTION_FLOOR_BY_REGIME` adjusts floor per regime
  - **NOT used for:** Dynamic threshold modification, per-regime scoring rules

### Research Findings
- **PMC 10773860 (2023):** Bitcoin exhibits distinct bull/bear volatility regimes with structural breaks
- **Springer (2026):** "Statistical properties of Bitcoin returns exhibit significant structural changes over time corresponding to latent regimes in the market" — regime-aware adaptive forecasting outperforms static models
- **Wiley Futures (2025):** Documents percentage of time spent in bull vs bear regimes; regime-aware pairs trading outperforms
- **MDPI (2025):** Three-stage analysis of BTC/ETH tail risk evolution confirms market maturation with distinct regime shifts

### Assessment
The academic consensus is clear: **crypto markets exhibit distinct, detectable regimes, and regime-aware models outperform static ones.** Athena's current approach uses regime as a binary gate (block/no-block) rather than a dynamic modifier. This is the biggest structural gap between Athena and research-backed best practices.

### Recommendation: P1 — Wire regime-dependent dynamic thresholds
- **Phase 1 (low risk):** Use existing regime classification to adjust the score threshold dynamically:
  - TRENDING: threshold × 0.90 (easier to pass — trends are tradeable)
  - RANGING: threshold × 1.10 (harder to pass — mean-reversion dominates)
  - HIGH_VOLATILITY: threshold × 1.15 (harder — noise dominates signal)
- **Phase 2 (medium):** Per-regime factor weight adjustments (e.g., momentum weight higher in TRENDING, lower in RANGING)
- **Config-gate everything** behind `ENGINE_A_REGIME_DYNAMIC_THRESHOLDS: enabled: false` for safe A/B testing
- **Source:** PMC 10773860, Springer 2026, Wiley Futures 2025

---

## 6. Multi-Exchange Funding Composite

### Current Athena State
- `ENGINE_A_CRYPTO_DERIVATIVES_FEED: bybit` (`config.yaml:108`)
- `ENGINE_A_CRYPTO_DERIVATIVES_BINANCE_FALLBACK: false` — Binance fallback explicitly disabled
- Funding addon uses single-exchange funding rate (`factor_scoring.py:1042-1086`)

### Research Findings
- **Bitmex 2025 Derivatives Report:** Funding has structural floor around 0.01% with positive bias; cross-exchange divergences create "predictable trading opportunities"
- **Zipmex (2026):** "BTC funding averaged +0.51% (70.2% APR)" in Jan 2026 — shows sustained institutional long bias. Most CEXes settle every 8h at 00:00, 08:00, 16:00 UTC
- **ArbitrageScanner / Loris Tools / Coinglass:** All track cross-exchange funding divergences as tradable signals
- **AEA 2026 Conference Paper:** Documents basis risk and perpetual futures pricing divergences across exchanges

### Assessment
Single-exchange funding is a **single point of failure** for the derivatives addon. Bybit-specific anomalies (exchange-specific liquidations, whale positioning, insurance fund operations) can produce misleading funding signals. A composite would:
1. Reduce noise from exchange-specific anomalies
2. Detect genuine market-wide sentiment shifts (all exchanges agree)
3. Flag manipulation when one exchange diverges significantly

### Recommendation: P1 — Add Binance funding as cross-validation
- **Implementation:** Fetch Binance funding rate alongside Bybit; compute composite as weighted average or use Binance as confirmation gate
- **Logic:** If Bybit and Binance funding agree on direction → use the signal; if they disagree → downgrade to neutral
- **Config-gate:** `ENGINE_A_CRYPTO_DERIVATIVES_BINANCE_FALLBACK: true` already exists — just needs the composite logic
- **Source:** Bitmex 2025, Zipmex 2026, AEA 2026

---

## 7. ETH-Specific & Meme Coin Handling

### Current Athena State
- ETH uses same `crypto_eth` score group as other crypto (threshold 2.0)
- DOGE has `crypto_doge` score group with volatility scaler clamp (`max(1.0, vol_scaler)`)
- No specific handling for PEPE, SHIB, or other meme coins

### Research Findings
- **ArXiv 2502.13722:** ETH has different liquidity dynamics than BTC — included as separate asset in VWAP study
- **Changelly:** "Meme coins and low-cap altcoins may hit extreme RSI readings frequently without meaningful reversals"
- **General practitioner consensus:** ETH derivatives are more retail-driven; BTC derivatives are more institutional

### Assessment
ETH-specific handling is lower priority than the structural gaps above. Meme coin handling is already partially addressed by the `crypto_doge` volatility scaler clamp.

### Recommendation: P2 — Add meme coin score group
- Add `crypto_meme` score group for PEPE, SHIB, WIF, BONK with wider ATR% bands and higher threshold
- ETH-specific derivatives handling can wait until multi-exchange funding composite is in place
- **Source:** Changelly, ArXiv 2502.13722

---

## Priority Summary

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| **P0** | Remove dead `MIN_CONFLUENCE_CLASS` | Low | Cleanup | ✅ DONE |
| **P1** | Add VWAP as direction quality filter | Medium | High |
| **P1** | Wire regime-dependent dynamic thresholds | Medium | High |
| **P1** | Multi-exchange funding composite (Binance + Bybit) | Low | Medium |
| **P2** | Monitor ADX trend_min; raise to 18 if negative expectancy persists | Low | Medium |
| **P2** | Add Stochastic RSI (experimental, config-gated) | Low | Low |
| **P2** | Add meme coin score group | Low | Low |
| ✅ | RSI 80/20 bounds | — | No change needed |
| ✅ | ADX hard_fail=10 | — | No change needed |
| ✅ | Backtest/live threshold parity | — | Confirmed identical |
