# Athena Research Lab — Report
**Run ID:** `run_20260426_192958`  **Generated:** 2026-04-26 19:31 UTC  
**Mode:** full  **Symbols:** 16  **Families:** ['trend_momentum', 'pullback', 'breakout', 'mean_reversion', 'volatility', 'engine_b_proxy', 'engine_d_proxy']

> **IMPORTANT:** These are backtest discovery findings at intentionally lower thresholds.
> Do NOT copy these thresholds into live engine gates.
> Label: **STRONG_CANDIDATE** / **WEAK_CANDIDATE** / **REJECT** / **NEEDS_MORE_DATA**

## Executive Summary
- Total strategy/param/symbol/TF combinations tested: **4512**
- Valid (pass robustness): **175** (3.9%)
- Strong candidates: **10**

## Which Strategy Family Works Best?
family | count | avg_net_return | avg_wr | avg_pf | avg_sqn
--- | --- | --- | --- | --- | ---
pullback | 15 | 0.1967 | 0.2644 | 1.7805 | 0.8204
engine_b_proxy | 16 | 0.1671 | 0.4170 | 1.6476 | 0.8302
mean_reversion | 25 | 0.1528 | 0.6583 | 2.2012 | 1.1403
breakout | 46 | 0.0954 | 0.4048 | 1.3138 | 0.5564
trend_momentum | 16 | 0.0907 | 0.3744 | 1.3145 | 0.4722
engine_d_proxy | 40 | 0.0881 | 0.3897 | 1.2356 | 0.4953
volatility | 17 | 0.0313 | 0.4397 | 1.4209 | 0.5321

**Best family:** `pullback` (avg net return 0.197)

## Which Symbol Works Best?
symbol | count | avg_net_return | avg_wr
--- | --- | --- | ---
XAG/USD | 18 | 0.3481 | 0.3330
SOL/USDT | 7 | 0.2802 | 0.6252
BTC/USDT | 11 | 0.2306 | 0.4655
XAU/USD | 17 | 0.1243 | 0.3690
WTI Oil | 15 | 0.1064 | 0.4368
MSFT | 6 | 0.1008 | 0.3888
ETH/USDT | 18 | 0.0875 | 0.4628
NAS100 | 9 | 0.0530 | 0.4138
GER40 | 15 | 0.0423 | 0.4224
AUD/USD | 20 | 0.0396 | 0.4309

## Which Timeframe Works Best?
timeframe | count | avg_net_return | avg_wr
--- | --- | --- | ---
H4 | 96 | 0.1608 | 0.4303
M15 | 12 | 0.0777 | 0.5739
H1 | 67 | 0.0443 | 0.3966

## Which Direction Works Better?
direction | count | avg_net_return | avg_wr
--- | --- | --- | ---
both | 175 | 0.1105 | 0.4273

## Which Indicators Help / Hurt?
**Helpful indicators/strategies:**
strategy_name | pass_rate | avg_net_return | avg_sqn
--- | --- | --- | ---
bollinger_touch | 0.2500 | 0.0080 | -0.4503
vwap_deviation | 0.0104 | 0.2648 | 3.3846

**Globally weak across tested configs:**
strategy_name | pass_rate | avg_net_return
--- | --- | ---
ema_scalp_pullback | 0.0417 | -0.1914
macd_direction | 0.1042 | -0.1200
micro_breakout | 0.1875 | -0.1075
pullback_ema | 0.0781 | -0.0782
prev_day_hl | 0.0694 | -0.1217

## Which Setups Collapse After Fees?
No gross-profitable-but-fee-killed setups found.

## Which Setups Had Too Little Sample?
2747 configs had insufficient trades.  
family | count
--- | ---
trend_momentum | 1377
engine_b_proxy | 358
mean_reversion | 306
breakout | 296
engine_d_proxy | 195
volatility | 191
pullback | 24

## Recommended Research Queue
Please use the Autopilot console on the dashboard to generate comprehensive research vectors.

## Confirmed / Weakened / Rejected After Validation
Validation tracks are executed sequentially.

## Conditional Edge Candidates
No conditional edges detected.

## Engine A Findings
Best `trend_momentum` config: `macd_direction` params=`adx_min=0|fast=12|signal_period=9|slow=26`
  - Symbol: `ETH/USDT` TF: `H1` Direction: `both`
  - Win rate: 48.2%  PF: 1.48  Robustness: 0.80  Status: `STRONG_CANDIDATE`
  - **Action:** Validate on additional symbols/windows before considering Engine changes.
Best `pullback` config: `pullback_ema` params=`pullback_period=50|rsi_reclaim=True|rsi_threshold=50|trend_period=200`
  - Symbol: `US500` TF: `H1` Direction: `both`
  - Win rate: 23.1%  PF: 1.17  Robustness: 0.62  Status: `WEAK_CANDIDATE`
  - **Action:** Validate on additional symbols/windows before considering Engine changes.

## Engine B Findings
Best `engine_b_proxy` config: `structure_filters` params=`fvg_detection=False|strong_close_pct=0.7`
  - Symbol: `MSFT` TF: `H4` Direction: `both`
  - Win rate: 36.2%  PF: 1.56  Robustness: 0.75  Status: `STRONG_CANDIDATE`
  - **Action:** Validate on additional symbols/windows before considering Engine changes.

## Engine D Findings
Best `engine_d_proxy` config: `micro_breakout` params=`atr_sl_mult=0.5|fee_guard_r=0.5|range_bars=3`
  - Symbol: `XAU/USD` TF: `H4` Direction: `both`
  - Win rate: 40.7%  PF: 1.13  Robustness: 0.69  Status: `STRONG_CANDIDATE`
  - **Action:** Validate on additional symbols/windows before considering Engine changes.

## What Should NOT Be Tested Further Right Now?
- `trend_momentum`: 575 configs rejected — insufficient edge at current parameters
- `engine_d_proxy`: 245 configs rejected — insufficient edge at current parameters
- `engine_b_proxy`: 202 configs rejected — insufficient edge at current parameters
- `breakout`: 186 configs rejected — insufficient edge at current parameters
- `volatility`: 176 configs rejected — insufficient edge at current parameters
- `pullback`: 153 configs rejected — insufficient edge at current parameters
- `mean_reversion`: 53 configs rejected — insufficient edge at current parameters

## Recommended Next Tiny Test
Run `macd_direction` on `ETH/USDT` `H1`.

---
*Generated by Athena Research Lab v1.0 — 2026-04-26 19:31 UTC*
*Backtest discovery findings only.  Not a live execution recommendation.*